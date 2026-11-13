"""Download the Valio Aimo data files into data/. Usage: uv run scripts/download_data.py"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE = "https://junction26.valioaimo.fi"
FILES = {
    "products.json": "/api/products/download",
    "delivery_reliability.csv": "/api/delivery-reliability/download",
    "carbon_footprint.csv": "/api/carbon-footprint/download",
    # no API endpoint for the schema; direct file link from the logged-in site home page
    "product_schema.json": "/files/008f9a50ba9a6538d2e5bdf29d592af8/valio_aimo_junction26_product_data_schema.json",
}
DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> int:
    load_dotenv()
    key = os.environ.get("VALIO_API_KEY")
    if not key:
        sys.exit("VALIO_API_KEY missing (copy .env.example to .env)")
    DATA.mkdir(exist_ok=True)
    with httpx.Client(base_url=BASE, headers={"X-API-Key": key}, follow_redirects=True, timeout=120) as c:
        for name, path in FILES.items():
            r = c.get(path)
            r.raise_for_status()
            (DATA / name).write_bytes(r.content)
            print(f"{name}: {len(r.content):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
