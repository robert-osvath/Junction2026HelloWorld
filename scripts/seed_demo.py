"""Seed the 3 demo orders: placed, pending_approval (over budget), rejected (over total)."""
import json
import os
from pathlib import Path

import httpx

API = os.getenv("API_URL", "http://localhost:8000")
FIX = Path(__file__).resolve().parent.parent / "contracts" / "fixtures"
need = json.loads((FIX / "need.json").read_text())
offer = json.loads((FIX / "offer.json").read_text())
over = {**offer, "units_needed": 100, "total_eur": round(100 * offer["unit_price_eur"], 2)}

for label, mandate, line in [("happy", "mandate-hotel-001", offer), ("over-budget", "mandate-hotel-tight", offer),
                             ("over-total", "mandate-hotel-001", over)]:
    r = httpx.post(f"{API}/orders", json={"mandate_id": mandate, "need": need, "lines": [line]}, timeout=60)
    r.raise_for_status()
    o = r.json()
    print(f"{label}: {o['id']} {o['status']}")
