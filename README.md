# Trusted Trade Agents (Junction 2026, Team Hello World)

Plan and contracts: see `CLAUDE.md`. Shared models: `contracts/models.py`; example JSON: `contracts/fixtures/`.

## Setup
```bash
brew install uv python@3.12      # if ~/.local isn't writable, uv uses brew's 3.12
cp .env.example .env             # add VALIO_API_KEY
uv sync
uv run scripts/download_data.py  # -> data/products.json, delivery_reliability.csv, carbon_footprint.csv
```

## Run
```bash
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs
uv run pytest -q                         # smoke test over every endpoint
docker compose -f db/docker-compose.yml up -d   # MariaDB 11.4 on :3306 (aimo/aimo, db aimo)
```

## Lanes
Each lane owns one router and replaces its mock with real code:
`app/catalog/router.py` (P1), `app/jury/router.py` (P2), `app/orders/router.py` (P4).
Until then, every endpoint returns the milk-scenario fixtures via `app.mocks.load(name)`.
Contract changes go in `contracts/models.py` + its fixture, and `pytest` must stay green.
