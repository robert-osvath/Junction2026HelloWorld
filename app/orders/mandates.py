"""Mandate registry: id -> Mandate, from fixtures until a real mandate store exists."""
import json

from app.mocks import FIXTURES
from contracts import Mandate

MANDATES = {m.id: m for m in (Mandate.model_validate(json.loads((FIXTURES / f"{n}.json").read_text()))
                              for n in ("mandate", "mandate_tight", "mandate_hospital"))}
# The Vimo cockpit overwrites this with the buyer's own limits on every draft (app/experience/router.py).
# Registered here too so its persisted orders stay actionable after a restart.
# shortcut: limits fall back to the cockpit defaults (EUR 500 / 2,000) after a restart; persist mandates to keep them.
MANDATES["mandate-vimo-live"] = MANDATES["mandate-hotel-001"].model_copy(update={
    "id": "mandate-vimo-live", "buyer_name": "Hotel North", "auto_approve_limit_eur": 500.0, "budget_per_order_eur": 2000.0,
    "required_claims": [], "banned_allergens": [], "strict_allergens": False})  # simple default: no claim/allergen rules
