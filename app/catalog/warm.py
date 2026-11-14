"""Startup pre-warm: load the vector matrix and embed common queries in a background thread, so the
first real requests hit the query cache (< 200 ms). No-op without GEMINI_API_KEY or embeddings."""
import logging
import threading

from app.catalog import embeddings, store, vectors

WARM_QUERIES = (
    "laktoositon maito", "lactose-free milk", "kerma", "cream", "vehnäjauho", "wheat flour",
    "kahvimaito", "coffee milk", "oat drink", "kaurajuoma", "vegan cheese", "gluten-free bread",
)
WARM_TIMEOUT_S = 10.0


def warm(queries=WARM_QUERIES) -> int:
    """Returns the number of queries embedded. Never raises."""
    log = logging.getLogger(__name__)
    try:
        if not embeddings.has_key():
            return 0
        conn = store.connect()
        try:
            if vectors.get_matrix(conn) is None:
                return 0
        finally:
            conn.close()
    except Exception:
        log.warning("vector warm-up skipped", exc_info=True)
        return 0
    done = 0
    for q in queries:
        try:
            embeddings.embed_query(q, timeout_s=WARM_TIMEOUT_S)
            done += 1
        except Exception as e:
            log.warning("warm-up query failed: %s", e)
    return done


def start_background() -> threading.Thread:
    t = threading.Thread(target=warm, name="vector-warm", daemon=True)
    t.start()
    return t
