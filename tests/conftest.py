import json
import os
import tempfile
from pathlib import Path

os.environ["DB_URL"] = ""  # app tests use the in-memory store, never the dev DB from .env

# CI has no data/: point the catalog at a temp DB built from the 3 sample products, for the whole session.
from app.catalog.loader import load  # noqa: E402
from app.catalog.store import connect  # noqa: E402

_dir = tempfile.mkdtemp(prefix="catalog-test-")
SAMPLE_DB = Path(_dir) / "catalog.db"
_c = connect(SAMPLE_DB)
load(_c, json.loads((Path(__file__).parent / "fixtures" / "sample_products.json").read_text()), {}, {})
_c.close()
os.environ["CATALOG_DB"] = str(SAMPLE_DB)
