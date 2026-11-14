"""Gemini embeddings (gemini-embedding-001, 768 dims) for catalog vector search.

`embed_documents` is for the offline build (app.catalog.embed), `embed_query` for search (LRU cached).
No key is only an error when a call is made, never at import. Search must never hang on this:
use `try_embed_query`, which returns None on any failure or when the time budget is exceeded.
"""
import hashlib
import math
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable

import httpx
from dotenv import load_dotenv

MODEL = "gemini-embedding-001"
DIMS = 768
BATCH_MAX = 100
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:batchEmbedContents"
BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 16.0)
MAX_RETRY_AFTER_S = 60.0
INGREDIENT_CHARS = 300
QUERY_CACHE_SIZE = 1024
DEFAULT_TIMEOUT_S = 0.5


class EmbeddingError(Exception):
    """Missing key, retries exhausted, or a non-retryable / malformed response."""


def doc_text(row) -> str:
    """Text embedded for one product: names, brand, trimmed English ingredients."""
    parts = [row["name_en"], row["name_fi"], row["brand"], (row["key_ingredients_en"] or "")[:INGREDIENT_CHARS]]
    return " | ".join(p.strip() for p in parts if p and p.strip())


def text_hash(text: str) -> str:
    return hashlib.sha256(f"{MODEL}:{DIMS}:{text}".encode()).hexdigest()


class GeminiEmbedder:
    def __init__(self, api_key: str | None = None, *, transport: httpx.BaseTransport | None = None,
                 max_retries: int = 5, sleep: Callable[[float], None] = time.sleep, timeout_s: float = 30.0):
        self._api_key = api_key
        self._http = httpx.Client(transport=transport, timeout=timeout_s)
        self._max_retries = max_retries
        self._sleep = sleep
        self.retries_429 = 0

    def _key(self) -> str:
        key = self._api_key
        if not key:
            load_dotenv()
            key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise EmbeddingError("GEMINI_API_KEY is not set")
        return key

    def embed(self, texts: list[str], task_type: str, *, max_retries: int | None = None,
              timeout_s: float | None = None) -> list[list[float]]:
        retries = self._max_retries if max_retries is None else max_retries
        if not texts:
            return []
        if len(texts) > BATCH_MAX:
            raise EmbeddingError(f"batch of {len(texts)} exceeds {BATCH_MAX}")
        body = {"requests": [{"model": f"models/{MODEL}", "content": {"parts": [{"text": t}]},
                              "taskType": task_type, "outputDimensionality": DIMS} for t in texts]}
        headers = {"x-goog-api-key": self._key()}
        last = "no attempt"
        for attempt in range(retries + 1):
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            try:
                r = self._http.post(URL, json=body, headers=headers,
                                    **({} if timeout_s is None else {"timeout": timeout_s}))
            except httpx.TransportError as e:
                last = f"transport error: {type(e).__name__}"
            else:
                if r.status_code == 200:
                    return self._parse(r, len(texts))
                if r.status_code != 429 and r.status_code < 500:
                    raise EmbeddingError(f"HTTP {r.status_code}: {r.text[:200]}")
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    self.retries_429 += 1
                    try:
                        ra = float(r.headers["Retry-After"])
                        if math.isfinite(ra):
                            delay = min(max(0.0, ra), MAX_RETRY_AFTER_S)
                    except (KeyError, ValueError):
                        pass
            if attempt < retries:
                self._sleep(delay)
        raise EmbeddingError(f"embedding failed after {retries + 1} attempts ({last})")

    @staticmethod
    def _parse(r: httpx.Response, n: int) -> list[list[float]]:
        try:
            vecs = [e["values"] for e in r.json()["embeddings"]]
        except (ValueError, KeyError, TypeError) as e:
            raise EmbeddingError("unexpected response shape") from e
        if len(vecs) != n or any(len(v) != DIMS for v in vecs):
            raise EmbeddingError("unexpected number or size of embeddings")
        return vecs


_embedder: GeminiEmbedder | None = None
_lock = threading.Lock()


def get_embedder() -> GeminiEmbedder:
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = GeminiEmbedder()
        return _embedder


def embed_documents(texts: list[str]) -> list[list[float]]:
    return get_embedder().embed(texts, "RETRIEVAL_DOCUMENT")


_cache: "OrderedDict[str, list[float]]" = OrderedDict()


def embed_query(text: str, timeout_s: float | None = None) -> list[float]:
    """Query vector (LRU cached). Search path: one retry and an HTTP timeout within the search budget,
    so a hung API can't pin the worker threads; the pre-warm passes a longer `timeout_s`."""
    key = " ".join(text.casefold().split())
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    vec = get_embedder().embed([text], "RETRIEVAL_QUERY", max_retries=1,
                               timeout_s=_timeout_s() if timeout_s is None else timeout_s)[0]
    with _lock:
        _cache[key] = vec
        while len(_cache) > QUERY_CACHE_SIZE:
            _cache.popitem(last=False)
    return vec


def cached(text: str) -> bool:
    with _lock:
        return " ".join(text.casefold().split()) in _cache


_dotenv_loaded = False


def has_key() -> bool:
    """Cheap check (no network, .env read once) so search skips the embed path entirely without a key."""
    global _dotenv_loaded
    if _embedder is not None and _embedder._api_key:
        return True
    if not _dotenv_loaded:
        load_dotenv()
        _dotenv_loaded = True
    return bool(os.environ.get("GEMINI_API_KEY"))


def clear_query_cache() -> None:
    with _lock:
        _cache.clear()


def _timeout_s() -> float:
    try:
        return float(os.environ.get("SEARCH_EMBED_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S


_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="embed-query")


def try_embed_query(text: str, timeout_s: float | None = None) -> list[float] | None:
    """Query vector, or None on any failure / when the budget is exceeded (the call keeps running in
    the background and lands in the cache, so a repeat query is fast)."""
    budget = _timeout_s() if timeout_s is None else timeout_s
    if not cached(text) and not has_key():
        return None
    fut = _pool.submit(embed_query, text)
    try:
        return fut.result(timeout=budget)
    except FutureTimeout:
        return None
    except Exception:  # EmbeddingError or anything unexpected: search falls back to text-only
        return None
