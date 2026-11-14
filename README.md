# Trusted Trade Agents (Junction 2026, Team Hello World)

Plan and contracts: see `CLAUDE.md`. Shared models: `contracts/models.py`; example JSON: `contracts/fixtures/`.

## Setup
```bash
brew install uv python@3.12      # if ~/.local isn't writable, uv uses brew's 3.12
cp .env.example .env             # add VALIO_API_KEY
uv sync
uv run scripts/download_data.py  # -> data/products.json, product_schema.json, delivery_reliability.csv, carbon_footprint.csv
uv run python -m app.catalog.loader   # -> data/catalog.db (SQLite for now; ~1 s, 17,546 products)
```

## Run
```bash
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs
uv run pytest -q                         # smoke test over every endpoint
docker compose -f db/docker-compose.yml up -d   # MariaDB 11.4 on :3306 (aimo/aimo, db aimo + aimo_test)
# DB_URL in .env -> audit log in MariaDB (system-versioned); unset -> in-memory. DB_URL_TEST runs the MariaDB audit test.
```

## MCP server (T9)

With the API running, expose buyer-facing tools over stdio:

```bash
uv run python -m mcp_server.server
```

For MCP Inspector or Claude Desktop, use that command with this repository as
the working directory. Set `AIMO_API_URL` when the REST API is not at
`http://127.0.0.1:8000`. The tools are `search_products`, `get_offers`,
`place_order`, `get_order_status`, and `list_substitutes`.

### Vector search (T2)
`/catalog/search` blends Gemini embeddings (stored in `product_embeddings`) with full-text search when the DB has embeddings and `GEMINI_API_KEY` is set; otherwise it is text-only. Build/refresh vectors with `uv run python -m app.catalog.embed` (resumable, only changed products). Common queries are pre-embedded in the background at startup (`app/catalog/warm.py`).
Latency: < 200 ms for cached/pre-warmed queries; a new query pays one Gemini call (~0.3-0.5 s) and falls back to text-only if it exceeds `SEARCH_EMBED_TIMEOUT_S` (default 0.5).

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
  `uv run modal secret create aimo-api VALIO_API_KEY=... DB_URL='mysql://user:pass@host:port/aimo?ssl=true' CORS_ORIGINS=https://<vercel-app>.vercel.app GEMINI_API_KEY=...`
  then `uv run modal run modal_app.py::build_catalog` (data + catalog + Gemini embeddings into the
  `aimo-catalog` volume; incremental) and `uv run modal deploy modal_app.py`. Pushes to `main` deploy automatically once the repo secrets
  `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` exist (`.github/workflows/deploy.yml`).
- **Database:** MariaDB Cloud. Use an app user without `DELETE HISTORY`/`DROP` (see `db/migrations/000_init.sql`);
  tables are created by the app on startup.
- **UI on Vercel:** import the repo, root directory `web`, env `NEXT_PUBLIC_API_URL=<modal URL>`.
- **Live:** UI https://junction2026-hello-world.vercel.app · API https://weirdlyviral--aimo-trusted-trade-api.us-east.modal.run
- **Changing the Modal secret** (`modal secret create --force aimo-api ...`): the warm container keeps the old values,
  so run `uv run modal app stop aimo-trusted-trade -y && uv run modal deploy modal_app.py`. New UI origins must be
  added to `CORS_ORIGINS` (comma-separated).
