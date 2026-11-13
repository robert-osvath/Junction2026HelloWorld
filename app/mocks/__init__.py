"""Serve contracts/fixtures/*.json as validated models until each lane is real."""
import json
from pathlib import Path

import contracts

FIXTURES = Path(contracts.__file__).parent / "fixtures"
MODELS = {"mandate": contracts.Mandate, "need": contracts.Need, "offer": contracts.Offer,
          "verdict": contracts.Verdict, "order": contracts.Order, "audit_event": contracts.AuditEvent,
          "catalog_item": contracts.CatalogItem, "jury_result": contracts.JuryResult}


def load(name: str):
    return MODELS[name].model_validate(json.loads((FIXTURES / f"{name}.json").read_text()))
