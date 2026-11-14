# Trusted Trade Agents (Junction 2026, Team Hello World)

Plan and contracts: see `CLAUDE.md`. Shared models: `contracts/models.py`; example JSON: `contracts/fixtures/`.

## Setup
```bash
brew install uv python@3.12      # if ~/.local isn't writable, uv uses brew's 3.12
cp .env.example .env             # add VALIO_API_KEY
uv sync
uv run scripts/download_data.py  # -> data/products.json, product_schema.json, delivery_reliability.csv, carbon_footprint.csv
```

## Run
```bash
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs
uv run pytest -q                         # smoke test over every endpoint
docker compose -f db/docker-compose.yml up -d   # MariaDB 11.4 on :3306 (aimo/aimo, db aimo + aimo_test)
# DB_URL in .env -> audit log in MariaDB (system-versioned); unset -> in-memory. DB_URL_TEST runs the MariaDB audit test.
```

## Approval inbox (T14)
```bash
uv run uvicorn app.main:app --port 8000   # API (CORS origins: CORS_ORIGINS, default http://localhost:3000)
uv run scripts/seed_demo.py               # creates placed / pending_approval / rejected demo orders
cd web && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3000
```

## Lanes
Each lane owns one router and replaces its mock with real code:
`app/catalog/router.py` (P1), `app/jury/router.py` (P2), `app/orders/router.py` (P4).
Until then, every endpoint returns the milk-scenario fixtures via `app.mocks.load(name)`.
Contract changes go in `contracts/models.py` + its fixture, and `pytest` must stay green.

## Workflow
Feature branch per change (`<lane>/<ticket>-<slug>` or `<lane>/chore-<slug>`), PR to `main`, adversarial `code-review` before every PR. Details in `CLAUDE.md` → Git workflow.

## Deploy (T15)
- **API on Modal:** `uv run modal setup` once, then create the secret
  `uv run modal secret create aimo-api VALIO_API_KEY=... DB_URL='mysql://user:pass@host:port/aimo?ssl=true' CORS_ORIGINS=https://<vercel-app>.vercel.app`
  and `uv run modal deploy modal_app.py`. Pushes to `main` deploy automatically once the repo secrets
  `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` exist (`.github/workflows/deploy.yml`).
- **Database:** MariaDB Cloud. Use an app user without `DELETE HISTORY`/`DROP` (see `db/migrations/000_init.sql`);
  tables are created by the app on startup.
- **UI on Vercel:** import the repo, root directory `web`, env `NEXT_PUBLIC_API_URL=<modal URL>`.
