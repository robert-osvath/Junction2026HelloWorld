"""In-process embedding matrix for vector neighbours (numpy dot product).

Loaded once per process per DB file and reloaded when the file's (mtime, size) changes, so a
finished `python -m app.catalog.embed` is picked up without a restart.
"""
import os
import threading
from dataclasses import dataclass

import numpy as np

from app.catalog import embeddings, store


@dataclass
class Matrix:
    ids: list[str]
    mat: np.ndarray  # (n, DIMS) float32, rows L2-normalised

    def neighbours(self, query: list[float], k: int) -> list[str]:
        q = np.asarray(query, dtype=np.float32)
        n = float(np.linalg.norm(q))
        if q.shape != (embeddings.DIMS,) or n == 0 or not len(self.ids):
            return []
        sims = self.mat @ (q / n)
        k = min(k, len(sims))
        top = np.argpartition(-sims, k - 1)[:k]
        return [self.ids[i] for i in top[np.argsort(-sims[top])]]


_lock = threading.Lock()
_cached: dict[str, tuple[tuple, Matrix | None]] = {}


def _load(conn) -> Matrix | None:
    blobs = store.embedding_blobs(conn, embeddings.MODEL)
    if not blobs:
        return None
    mat = np.frombuffer(b"".join(b for _, b in blobs), dtype=np.float32).reshape(len(blobs), embeddings.DIMS)
    return Matrix([i for i, _ in blobs], mat)


def get_matrix(conn) -> Matrix | None:
    """The matrix for this connection's DB, None if it has no embeddings."""
    path = store.db_file(conn)
    if path is None:  # in-memory: nothing to key a cache on
        return _load(conn)
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _cached.get(path)
        if hit and hit[0] == stamp:
            return hit[1]
    m = _load(conn)
    with _lock:
        _cached[path] = (stamp, m)
    return m
