"""Valio Aimo price/availability client (T3).

Only ever call with a search shortlist, never the full catalog. Batches <= 1000 ids, 5-minute
cache keyed (kind, product_id, date), retry with deterministic backoff on 429 / 5xx / transport errors.

NOTE: response parsing is deliberately tolerant (list or wrapped list; id under product/productId/id;
money as {"value": x} or a bare number) because the shapes were not verified against the live API.
"""
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Callable, Iterable, Literal
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

BATCH_SIZE = 1000
UNKNOWN_QTY = 999999
HELSINKI = ZoneInfo("Europe/Helsinki")
BACKOFF_S = (0.5, 1.0, 2.0, 4.0)
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


def _num(v: Any) -> float | None:
    if isinstance(v, dict):
        v = v.get("value", v.get("amount"))
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def _items(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        lists = [v for v in payload.values() if isinstance(v, list)]
        payload = lists[0] if lists else []
    return [x for x in payload if isinstance(x, dict)]


def _pid(item: dict) -> str | None:
    for k in ("product", "productId", "productNumber", "id"):
        if item.get(k) is not None:
            return str(item[k])
    return None


def _vat(item: dict) -> float | None:
    for k in ("tax", "vat", "vatRate", "taxRate"):
        if k in item:
            v = item[k]
            if isinstance(v, dict):
                v = v.get("rate", v.get("percentage", v.get("value")))
            return _num(v)
    return None


def _parse_price(item: dict, pid: str) -> PriceInfo:
    unit = item.get("comparisonUnit")
    return PriceInfo(
        product_id=pid,
        catalog_price_eur=_num(item.get("catalogPrice")),
        list_price_eur=_num(item.get("listPrice")),
        comparison_price_eur=_num(item.get("comparisonPrice")),
        comparison_unit=str(unit) if unit else None,
        vat_rate=_vat(item),
        net_price_found=bool(item.get("netPriceFound", False)),
    )


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=HELSINKI)


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _parse_availability(item: dict, pid: str) -> AvailabilityInfo:
    lines = item.get("scheduleLines") or []
    line0 = lines[0] if lines and isinstance(lines[0], dict) else {}
    model = item.get("logisticModel")
    model = model if model in ("Stock", "Terminal") else None
    qty = _num(item.get("quantity", line0.get("quantity")))
    if model == "Terminal" or qty == UNKNOWN_QTY:
        qty = None
    return AvailabilityInfo(
        product_id=pid,
        available_date=_parse_date(line0.get("availableDate")),
        closing_time=_parse_dt(item.get("closingTime")),
        sales_block=bool(item.get("salesBlock", False)),
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
                    return r.json()
                if r.status_code != 429 and r.status_code < 500:
                    raise AimoError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    try:
                        delay = max(0.0, float(r.headers["Retry-After"]))
                    except (KeyError, ValueError):
                        pass
            if attempt < self._max_retries:
                self._sleep(delay)
        raise AimoError(f"{path} failed after {self._max_retries + 1} attempts ({last})")

    def _fetch(self, kind: str, path: str, parse: Callable[[dict, str], Any],
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
            found = {}
            for item in _items(payload):
                pid = _pid(item)
                if pid in batch:
                    found[pid] = parse(item, pid)
            stamp = self._clock()
            with self._lock:
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
