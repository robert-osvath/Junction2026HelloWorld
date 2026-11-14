"""Mandate registry: id -> Mandate, from fixtures until a real mandate store exists."""
import json

from app.mocks import FIXTURES
from contracts import Mandate

MANDATES = {m.id: m for m in (Mandate.model_validate(json.loads((FIXTURES / f"{n}.json").read_text()))
                              for n in ("mandate", "mandate_tight"))}
