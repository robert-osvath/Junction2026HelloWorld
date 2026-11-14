import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.catalog.aimo import AimoClient, AimoError

FIX = Path(__file__).parent / "fixtures"
PRICE = json.loads((FIX / "aimo_price.json").read_text())
AVAIL = json.loads((FIX / "aimo_availability.json").read_text())
D = date(2026, 11, 17)


class Fake:
    def __init__(self, script=None):
        self.calls = []  # (path, body, headers)
        self.script = list(script or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        self.calls.append((path, body, request.headers))
        if self.script:
            return self.script.pop(0)
        src = PRICE if path.endswith("price") else AVAIL
        ids = set(body["products"])
        return httpx.Response(200, json=[x for x in src if x["product"] in ids])

    def n(self, suffix):
        return sum(1 for c in self.calls if c[0].endswith(suffix))


def make(fake, **kw):
    sleeps = []
    t = [0.0]
    c = AimoClient("k", transport=httpx.MockTransport(fake), clock=lambda: t[0],
                   sleep=sleeps.append, **kw)
    return c, sleeps, t


def test_parse_price_and_missing():
    c, _, _ = make(Fake())
    r = c.prices(["463038", "460002", "bogus"], D)
    assert set(r) == {"463038", "460002"}
    e = r["463038"]
    assert (e.catalog_price_eur, e.list_price_eur, e.comparison_price_eur) == (31.31, 36.83, 1.57)
    assert e.comparison_unit == "L" and e.vat_rate == 13.5 and e.net_price_found
    assert r["460002"].catalog_price_eur is None and not r["460002"].net_price_found


def test_parse_availability():
    fake = Fake()
    c, _, _ = make(fake)
    r = c.availability(["463038", "460001", "bogus"], D)
    assert "bogus" not in r
    eila = r["463038"]
    assert eila.logistic_model == "Terminal" and eila.quantity is None
    assert eila.closing_time.tzinfo is not None and eila.available_date == date(2026, 11, 16)
    st = r["460001"]
    assert st.logistic_model == "Stock" and st.quantity == 240 and not st.sales_block
    assert fake.calls[0][2]["x-api-key"] == "k"
    assert fake.calls[0][1] == {"customer": "1", "requestedDate": "2026-11-17",
                                "products": ["463038", "460001", "bogus"]}


def test_sales_block_and_no_schedule():
    c, _, _ = make(Fake())
    a = c.availability(["460002"], D)["460002"]
    assert a.sales_block and a.available_date is None


def test_20_ids_one_call_per_api_and_cache_hit():
    fake = Fake()
    c, _, _ = make(fake)
    ids = [str(i) for i in range(20)] + ["0", "1"]  # duplicates collapse
    c.prices(ids, D)
    c.availability(ids, D)
    assert fake.n("price") == 1 and fake.n("availability") == 1
    assert len(fake.calls[0][1]["products"]) == 20
    c.prices(ids, D)
    c.availability(ids, D)
    assert len(fake.calls) == 2
    c.prices(ids, date(2026, 11, 18))  # different date -> new call
    assert len(fake.calls) == 3


def test_partial_cache_only_requests_uncached():
    fake = Fake()
    c, _, _ = make(fake)
    c.prices(["463038"], D)
    c.prices(["463038", "460001"], D)
    assert fake.calls[1][1]["products"] == ["460001"]


def test_1500_ids_two_calls():
    fake = Fake()
    c, _, _ = make(fake)
    c.prices([str(i) for i in range(1500)], D)
    assert [len(x[1]["products"]) for x in fake.calls] == [1000, 500]


def test_cache_expiry():
    fake = Fake()
    c, _, t = make(fake)
    c.prices(["463038"], D)
    t[0] = 299
    c.prices(["463038"], D)
    assert len(fake.calls) == 1
    t[0] = 301
    c.prices(["463038"], D)
    assert len(fake.calls) == 2


def test_retry_429_retry_after_then_503_then_success():
    ok = httpx.Response(200, json=PRICE)
    fake = Fake([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(503), ok])
    c, sleeps, _ = make(fake)
    assert "463038" in c.prices(["463038"], D)
    assert sleeps == [7.0, 1.0] and len(fake.calls) == 3


def test_give_up_raises():
    fake = Fake([httpx.Response(500)] * 10)
    c, sleeps, _ = make(fake, max_retries=2)
    with pytest.raises(AimoError):
        c.prices(["463038"], D)
    assert len(fake.calls) == 3 and sleeps == [0.5, 1.0]


def test_4xx_no_retry():
    fake = Fake([httpx.Response(401, json={"detail": "Invalid or missing API key"})])
    c, sleeps, _ = make(fake)
    with pytest.raises(AimoError):
        c.prices(["463038"], D)
    assert len(fake.calls) == 1 and sleeps == []


def test_transport_error_retried():
    state = {"n": 0}

    def h(req):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=PRICE)

    c = AimoClient("k", transport=httpx.MockTransport(h), sleep=lambda s: None)
    assert "463038" in c.prices(["463038"], D)


def test_missing_key_raises_on_call(monkeypatch):
    monkeypatch.delenv("VALIO_API_KEY", raising=False)
    monkeypatch.setattr("app.catalog.aimo.load_dotenv", lambda *a, **k: None)
    fake = Fake()
    c = AimoClient(None, transport=httpx.MockTransport(fake))
    with pytest.raises(AimoError):
        c.prices(["463038"], D)
    assert fake.calls == []
