"""Valio Aimo price/availability client (T3).

Only ever call with a search shortlist, never the full catalog. Batches <= 1000 ids, 5-minute
cache keyed (kind, product_id, date), retry with deterministic backoff on 429 / 5xx / transport errors.

Real shapes (verified live 2026-11-14): both responses are {"products": [{"productId": ...}, ...]}.
"""
import math
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Iterable, Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

BATCH_SIZE = 1000
UNKNOWN_QTY = 999999
BACKOFF_S = (0.5, 1.0, 2.0, 4.0)
MAX_RETRY_AFTER_S = 30.0  # never block a request thread longer than this on a server's Retry-After
DEFAULT_CUSTOMER = "1"


class PriceInfo(BaseModel):
    product_id: str
    catalog_price_eur: float | None = None
    list_price_eur: float | None = None
    comparison_price_eur: float | None = None
    comparison_unit: str | None = None
    vat_rate: float | None = None
    net_price_found: bool = False


class AvailabilityInfo(BaseModel):
    product_id: str
    available_date: date | None = None
    closing_time: datetime | None = None
    sales_block: bool = False
    logistic_model: Literal["Stock", "Terminal"] | None = None
    quantity: float | None = None


class AimoError(Exception):
    """Retries exhausted, or a non-retryable error (4xx other than 429, missing key)."""


def _val(v: dict | None) -> float | None:
    return None if not v or v.get("value") is None else float(v["value"])


def _items(payload: Any) -> list[dict]:
    """The `products` list, or AimoError if the 200 body isn't the documented shape."""
    items = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) and "productId" in i for i in items):
        raise AimoError(f"unexpected response shape: {str(payload)[:200]}")
    return items


def _parse_price(item: dict) -> PriceInfo | None:
    """None for unknown ids: the API answers them with {productId, error: true} and no prices."""
    if item.get("error") or "catalogPrice" not in item:
        return None
    return PriceInfo(
        product_id=item["productId"],
        catalog_price_eur=_val(item.get("catalogPrice")),
        list_price_eur=_val(item.get("listPrice")),
        comparison_price_eur=_val(item.get("comparisonPrice")),
        comparison_unit=item.get("comparisonUnit") or None,
        vat_rate=(item.get("tax") or {}).get("taxRate"),
        net_price_found=bool(item.get("netPriceFound", False)),
    )


def _parse_availability(item: dict) -> AvailabilityInfo | None:
    """None for ids with no logisticModel (unknown ids, or products with no schedule at all)."""
    model = item.get("logisticModel")
    if model not in ("Stock", "Terminal"):
        return None
    lines = item.get("scheduleLines") or []
    line0 = lines[0] if lines else {}
    qty = line0.get("availableAmount")
    if model == "Terminal" or qty == UNKNOWN_QTY:
        qty = None
    closing = item.get("closingTime")
    return AvailabilityInfo(
        product_id=item["productId"],
        available_date=line0.get("availableDate"),  # "YYYY-MM-DD"
        closing_time=closing,  # "2026-11-12T15:00:00+0200" (tz-aware)
        sales_block=bool(item.get("salesBlock")) or bool(line0.get("salesBlock")),
        logistic_model=model,
        quantity=qty,
    )


class AimoClient:
    def __init__(self, api_key: str | None = None, *, base_url: str = "https://junction26.valioaimo.fi",
                 transport: httpx.BaseTransport | None = None, ttl_s: float = 300,
                 max_retries: int = 4, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, customer: str = DEFAULT_CUSTOMER):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=30.0)
        self._ttl = ttl_s
        self._max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._customer = customer
        self._cache: dict[tuple[str, str, date], tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _key(self) -> str:
        key = self._api_key
        if not key:
            load_dotenv()
            key = os.environ.get("VALIO_API_KEY")
        if not key:
            raise AimoError("VALIO_API_KEY is not set")
        return key

    def _post(self, path: str, body: dict) -> Any:
        headers = {"X-API-Key": self._key()}
        last = "no attempt"
        for attempt in range(self._max_retries + 1):
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            try:
                r = self._http.post(self._base_url + path, json=body, headers=headers)
            except httpx.TransportError as e:
                last = f"transport error: {type(e).__name__}"
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        raise AimoError(f"{path} -> HTTP 200 with a non-JSON body: {r.text[:200]}") from e
                if r.status_code != 429 and r.status_code < 500:
                    raise AimoError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    try:
                        retry_after = float(r.headers["Retry-After"])
                    except (KeyError, ValueError):
                        pass  # missing, or an HTTP-date: keep the default backoff
                    else:
                        if math.isfinite(retry_after):
                            delay = min(max(0.0, retry_after), MAX_RETRY_AFTER_S)
            if attempt < self._max_retries:
                self._sleep(delay)
        raise AimoError(f"{path} failed after {self._max_retries + 1} attempts ({last})")

    def _fetch(self, kind: str, path: str, parse: Callable[[dict], Any],
               product_ids: Iterable[str], requested_date: date) -> dict[str, Any]:
        ids = list(dict.fromkeys(str(p) for p in product_ids))
        out: dict[str, Any] = {}
        missing: list[str] = []
        now = self._clock()
        with self._lock:
            for pid in ids:
                hit = self._cache.get((kind, pid, requested_date))
                if hit and now - hit[0] < self._ttl:
                    if hit[1] is not None:
                        out[pid] = hit[1]
                else:
                    missing.append(pid)
        for i in range(0, len(missing), BATCH_SIZE):
            batch = missing[i:i + BATCH_SIZE]
            payload = self._post(path, {"customer": self._customer,
                                        "requestedDate": requested_date.isoformat(),
                                        "products": batch})
            wanted = set(batch)
            found = {}
            for item in _items(payload):
                pid = str(item["productId"])
                info = parse(item)
                if pid in wanted and info is not None:
                    found[pid] = info
            stamp = self._clock()
            with self._lock:
                # drop expired entries so the cache doesn't grow without bound across dates
                expired = [k for k, (t, _) in self._cache.items() if stamp - t >= self._ttl]
                for k in expired:
                    del self._cache[k]
                for pid in batch:  # absent ids cached as None so repeats don't re-query
                    self._cache[(kind, pid, requested_date)] = (stamp, found.get(pid))
            out.update(found)
        return {pid: out[pid] for pid in ids if pid in out}

    def prices(self, product_ids: Iterable[str], requested_date: date) -> dict[str, PriceInfo]:
        return self._fetch("price", "/api/price", _parse_price, product_ids, requested_date)

    def availability(self, product_ids: Iterable[str], requested_date: date) -> dict[str, AvailabilityInfo]:
        return self._fetch("availability", "/api/availability", _parse_availability,
                           product_ids, requested_date)


_client: AimoClient | None = None
_client_lock = threading.Lock()


def get_client() -> AimoClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = AimoClient()
        return _client
