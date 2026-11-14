"""Vector search: fake embedder, no network. Axis-aligned unit vectors stand in for embeddings."""
import json
import os
import shutil
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.catalog import embed, embeddings, loader, store, vectors
from app.catalog.search import search
from app.main import app

FIX = Path(__file__).parent / "fixtures"


def axis(i: int, off: float = 0.0, j: int = 1) -> list[float]:
    v = [0.0] * embeddings.DIMS
    v[i] = 1.0
    v[j] = off
    return v


def put(conn, pid, vec):
    store.upsert_embeddings(conn, [(pid, embeddings.MODEL, "h", embed.to_blob(vec))])


@pytest.fixture
def conn(tmp_path, monkeypatch):
    copy = tmp_path / "catalog.db"
    shutil.copy(os.environ["CATALOG_DB"], copy)
    monkeypatch.setenv("CATALOG_DB", str(copy))
    embeddings.clear_query_cache()
    c = store.connect()
    yield c
    c.close()


@pytest.fixture
def with_vectors(conn, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    """Eila on axis 0, an oat drink with no text overlap with the query on axis 5."""
    conn.execute("INSERT INTO products (id, name_en, sales_unit, has_allergen_data) "
                 "VALUES ('oat', 'Oat barista', 'KI', 0)")
    conn.commit()
    store.rebuild_fts(conn)
    put(conn, "463038", axis(0))
    put(conn, "oat", axis(5))
    monkeypatch.setattr(embeddings, "embed_query", lambda t, *a, **k: axis(5, 0.1))
    return conn


def ids(items):
    return [i.product_id for i in items]


def test_hybrid_brings_in_product_without_text_overlap(with_vectors):
    assert ids(search(with_vectors, "something to whiten coffee"))[0] == "oat"


def test_hybrid_keeps_text_hits(with_vectors):
    assert "463038" in ids(search(with_vectors, "laktoositon maito"))


@pytest.mark.parametrize("failure", ["error", "timeout", "nokey"])
def test_fallback_identical_to_text_only(with_vectors, monkeypatch, failure):
    with_vectors.execute("DELETE FROM product_embeddings")  # text-only reference
    with_vectors.commit()
    baseline = ids(search(with_vectors, "maito"))
    put(with_vectors, "463038", axis(0))
    put(with_vectors, "oat", axis(5))
    monkeypatch.setenv("SEARCH_EMBED_TIMEOUT_S", "0.05")
    if failure == "nokey":
        monkeypatch.delenv("GEMINI_API_KEY")
        monkeypatch.setattr(embeddings, "_embedder", None)
        monkeypatch.setattr(embeddings, "_dotenv_loaded", True)

    def boom(t, *a, **k):
        if failure == "error":
            raise embeddings.EmbeddingError("down")
        if failure == "timeout":
            time.sleep(0.5)
        raise embeddings.EmbeddingError("GEMINI_API_KEY is not set")
    monkeypatch.setattr(embeddings, "embed_query", boom)
    t = time.perf_counter()
    assert ids(search(with_vectors, "maito")) == baseline
    assert time.perf_counter() - t < 0.4
    assert ids(search(with_vectors, "whiten coffee")) == []


def test_no_embeddings_is_text_only(conn, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_query", lambda t: pytest.fail("must not embed"))
    assert ids(search(conn, "lactose-free milk")) == ["463038"]


def test_filters_apply_to_vector_candidates(with_vectors):
    assert ids(search(with_vectors, "whiten coffee", category="nope")) == []


def test_query_embedding_cached(monkeypatch):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"embeddings": [{"values": [0.1] * embeddings.DIMS}]})
    monkeypatch.setattr(embeddings, "_embedder", embeddings.GeminiEmbedder(
        "k", transport=httpx.MockTransport(handler), sleep=lambda s: None))
    embeddings.clear_query_cache()
    a = embeddings.embed_query("Laktoositon  maito")
    assert embeddings.embed_query("laktoositon maito") == a
    assert len(calls) == 1


def test_embedder_retries_429_then_fails(monkeypatch):
    seq = iter([429, 500, 200])

    def handler(req):
        s = next(seq)
        return httpx.Response(s, json={"embeddings": [{"values": [0.1] * embeddings.DIMS}]})
    e = embeddings.GeminiEmbedder("k", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert len(e.embed(["x"], "RETRIEVAL_QUERY")) == 1 and e.retries_429 == 1
    bad = embeddings.GeminiEmbedder("k", transport=httpx.MockTransport(lambda r: httpx.Response(400)),
                                    sleep=lambda s: None)
    with pytest.raises(embeddings.EmbeddingError):
        bad.embed(["x"], "RETRIEVAL_QUERY")


def test_no_key_raises_on_call_not_import(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("app.catalog.embeddings.load_dotenv", lambda: None)
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.GeminiEmbedder().embed(["x"], "RETRIEVAL_QUERY")
    assert embeddings.try_embed_query.__name__  # import is fine


def test_matrix_reloads_when_db_changes(conn):
    put(conn, "463038", axis(0))
    m1 = vectors.get_matrix(conn)
    assert m1.ids == ["463038"]
    conn.execute("INSERT INTO products (id, name_en, sales_unit, has_allergen_data) VALUES ('x', 'X', 'KI', 0)")
    put(conn, "x", axis(3))
    assert vectors.get_matrix(conn).ids == ["463038", "x"]
    assert vectors.get_matrix(conn) is vectors.get_matrix(conn)  # unchanged file: cached


def test_incremental_build_by_text_hash(conn):
    calls = []

    def fake(texts):
        calls.append(len(texts))
        return [axis(i % 50) for i in range(len(texts))]
    n = conn.execute("SELECT count(*) FROM products").fetchone()[0]
    s = embed.build(conn, fake, out=lambda m: None)
    assert s["embedded"] == n and store.embedding_count(conn, embeddings.MODEL) == n
    assert embed.build(conn, fake, out=lambda m: None)["embedded"] == 0
    conn.execute("UPDATE products SET name_en = 'Changed name' WHERE id = '463038'")
    conn.commit()
    assert embed.build(conn, fake, out=lambda m: None)["embedded"] == 1
    assert calls == [n, 1]


def test_batches_of_100(conn):
    for i in range(250):
        conn.execute("INSERT INTO products (id, name_en, sales_unit, has_allergen_data) VALUES (?, ?, 'KI', 0)",
                     (f"p{i}", f"Prod {i}"))
    conn.commit()
    sizes = []
    embed.build(conn, lambda t: sizes.append(len(t)) or [axis(0)] * len(t), out=lambda m: None)
    assert max(sizes) == 100


def test_replace_catalog_keeps_embeddings(conn):
    put(conn, "463038", axis(0))
    loader.load(conn, json.loads((FIX / "sample_products.json").read_text()), {}, {})
    assert store.embedding_count(conn, embeddings.MODEL) == 1


def test_malformed_blob_skipped_and_never_crashes_search(with_vectors):
    with_vectors.execute("INSERT INTO product_embeddings VALUES ('460000', ?, 'h', ?)",
                         (embeddings.MODEL, b"x" * 1024))
    with_vectors.commit()
    m = vectors.get_matrix(with_vectors)
    assert "460000" not in m.ids and "oat" in m.ids
    assert ids(search(with_vectors, "something to whiten coffee"))[0] == "oat"


def test_only_malformed_blobs_is_text_only(conn, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    baseline = ids(search(conn, "maito"))
    conn.execute("INSERT INTO product_embeddings VALUES ('463038', ?, 'h', ?)", (embeddings.MODEL, b"x" * 1024))
    conn.commit()
    assert ids(search(conn, "maito")) == baseline


def test_vector_side_exception_falls_back(with_vectors, monkeypatch):
    def boom(c):
        raise ValueError("boom")
    monkeypatch.setattr(vectors, "get_matrix", boom)
    assert ids(search(with_vectors, "maito")) == ["463038"]


def _no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(embeddings, "_embedder", None)
    monkeypatch.setattr(embeddings, "_dotenv_loaded", True)


def test_no_key_does_not_submit_threads(with_vectors, monkeypatch):
    _no_key(monkeypatch)
    monkeypatch.setattr(embeddings._pool, "submit", lambda *a, **k: pytest.fail("submitted"))
    assert embeddings.try_embed_query("maito") is None


def test_warm_noop_without_key(with_vectors, monkeypatch):
    from app.catalog import warm
    _no_key(monkeypatch)
    monkeypatch.setattr(embeddings, "embed_query", lambda *a, **k: pytest.fail("embedded"))
    assert warm.warm() == 0


def test_warm_noop_without_embeddings(conn, monkeypatch):
    from app.catalog import warm
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr(embeddings, "embed_query", lambda *a, **k: pytest.fail("embedded"))
    assert warm.warm() == 0


def test_warm_fills_cache(with_vectors, monkeypatch):
    from app.catalog import warm
    monkeypatch.undo()  # real embed_query, fake HTTP transport below
    monkeypatch.setenv("CATALOG_DB", store.db_file(with_vectors))
    monkeypatch.setattr(embeddings, "_embedder", embeddings.GeminiEmbedder(
        "k", transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"embeddings": [{"values": [0.1] * embeddings.DIMS}]})), sleep=lambda s: None))
    embeddings.clear_query_cache()
    assert warm.warm(("kerma", "cream")) == 2
    assert embeddings.cached("kerma") and embeddings.cached("Cream")


# --- full catalog with real embeddings (data/catalog.db), skipped when absent ---

def _full_ready():
    p = store.DEFAULT_DB
    if not p.exists():
        return False
    c = store.connect(p)
    try:
        return store.embedding_count(c, embeddings.MODEL) > 17000
    finally:
        c.close()


full = pytest.mark.skipif(not _full_ready(), reason="needs data/catalog.db with embeddings")


@pytest.fixture
def full_db(monkeypatch):
    monkeypatch.setenv("CATALOG_DB", str(store.DEFAULT_DB))
    embeddings.clear_query_cache()


@full
@pytest.mark.parametrize("q", ["laktoositon maito", "lactose-free milk"])
def test_full_acceptance_top5_hybrid(q, full_db):
    if embeddings.try_embed_query(q, 10) is None:
        pytest.skip("no Gemini key / network")
    c = TestClient(app)
    r = c.get("/catalog/search", params={"q": q, "limit": 5, "quantity": 1000, "unit": "L"}).json()
    assert "463038" in [x["product_id"] for x in r]
    t = time.perf_counter()  # query embedding now cached
    assert c.get("/catalog/search", params={"q": q, "limit": 5, "quantity": 1000, "unit": "L"}).status_code == 200
    assert (time.perf_counter() - t) * 1000 < 200
