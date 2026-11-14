"""Build/refresh product embeddings: `uv run python -m app.catalog.embed`.

Embeds only products whose text_hash is missing or changed, 100 per request, committing per batch
(so an interrupted run resumes). Needs GEMINI_API_KEY (env or .env).
"""
import sys
import time
from typing import Callable

import numpy as np

from app.catalog import embeddings, store


def to_blob(vec: list[float]) -> bytes:
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v))
    if n > 0:
        v = v / n
    return v.astype(np.float32).tobytes()


def build(conn, embed_documents: Callable[[list[str]], list[list[float]]] = embeddings.embed_documents,
          batch_size: int = embeddings.BATCH_MAX, out: Callable[[str], None] = print) -> dict:
    have = store.embedding_hashes(conn, embeddings.MODEL)
    todo = []
    for r in store.embedding_source_rows(conn):
        text = embeddings.doc_text(r)
        h = embeddings.text_hash(text)
        if have.get(r["id"]) != h:
            todo.append((r["id"], text, h))
    total, t0 = len(todo), time.perf_counter()
    out(f"{total} products to embed ({len(have)} already up to date)")
    batches = 0
    for i in range(0, total, batch_size):
        chunk = todo[i:i + batch_size]
        vecs = embed_documents([t for _, t, _ in chunk])
        store.upsert_embeddings(conn, [(pid, embeddings.MODEL, h, to_blob(v))
                                       for (pid, _, h), v in zip(chunk, vecs)])
        batches += 1
        out(f"  {min(i + batch_size, total)}/{total} ({time.perf_counter() - t0:.0f}s)")
    return {"embedded": total, "batches": batches, "seconds": time.perf_counter() - t0}


def main() -> int:
    conn = store.connect()
    try:
        stats = build(conn)
    except embeddings.EmbeddingError as e:
        print(f"embedding failed: {e} (progress so far is saved; re-run to resume)", file=sys.stderr)
        print(f"{store.embedding_count(conn, embeddings.MODEL)} products have embeddings", file=sys.stderr)
        return 1
    finally:
        emb = embeddings._embedder
        n429 = emb.retries_429 if emb else 0
    print(f"done: {stats['embedded']} embedded in {stats['batches']} batches, {stats['seconds']:.0f}s, "
          f"{n429} 429 responses, {store.embedding_count(conn, embeddings.MODEL)} rows total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
