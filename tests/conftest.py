import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest

os.environ["DB_URL"] = ""  # app tests use the in-memory store, never the dev DB from .env

FIX = Path(__file__).parent / "fixtures"


def fake_aimo_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        src = json.loads((FIX / ("aimo_price.json" if request.url.path.endswith("price")
                                 else "aimo_availability.json")).read_text())
        ids = set(body["products"])
        return httpx.Response(200, json={**src, "products": [x for x in src["products"] if x["productId"] in ids]})
    return httpx.MockTransport(handler)


@pytest.fixture(scope="session", autouse=True)
def offline_catalog_and_aimo():
    """CI has no data/ and no API key: temp catalog from sample products + fake Aimo client."""
    from app.catalog import loader, store
    from app.catalog.aimo import AimoClient, get_client
    from app.main import app

    tmp = tempfile.TemporaryDirectory()
    old = os.environ.get("CATALOG_DB")
    os.environ["CATALOG_DB"] = str(Path(tmp.name) / "catalog.db")
    conn = store.connect()
    loader.load(conn, json.loads((FIX / "sample_products.json").read_text()), {}, {})
    conn.close()
    client = AimoClient("test-key", transport=fake_aimo_transport(), sleep=lambda s: None)
    app.dependency_overrides[get_client] = lambda: client
    yield
    app.dependency_overrides.pop(get_client, None)
    if old is None:
        os.environ.pop("CATALOG_DB", None)
    else:
        os.environ["CATALOG_DB"] = old
    tmp.cleanup()
