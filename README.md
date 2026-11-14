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
# Pitch demo game "Milk Run": https://junction2026-hello-world.vercel.app/game (LIVE API via Vercel rewrites to Modal)
#   locally: http://localhost:8000/game/ (served by the API) or http://localhost:3000/game (Next dev, proxies to the API)
#   or offline: cd web/public && python3 -m http.server, open /game/  (DEMO DATA mode)
#   rehearsal params: ?autostart=1  ?reveal=1&beat=N (Space/→ advances)  ?end=1  ?shop=north|mega|corner|harbour
#   audio: edit web/public/game/audio/lines.json, then `uv run scripts/gen_game_audio.py [ids]` (needs ELEVENLABS_API_KEY)
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

## Vimo live-agent configuration

`agent_configs/vimo-live.json` is the reviewed source for the ElevenLabs agent's prompt, client tools and turn-taking settings.
Publishing replaces the live prompt and attaches exactly the tracked tools (updated in place by name, new ones created), so copy any dashboard-only edits into the file first. The dry run reads the live tools and lists each as keep / update / create.
It is not deployed with the API. Review the dry run, then explicitly publish it with an environment-provided
`ELEVENLABS_API_KEY`. The publisher refuses to update a branch unless ElevenLabs reports it receives 100% of live
traffic; deploy or merge the intended branch first.

```bash
uv run python scripts/publish_vimo_agent.py
uv run python scripts/publish_vimo_agent.py --apply
```

## Buyer demo (T10–T11)

After the catalog setup above, run all three P3 scenarios with one command:

```bash
uv run python scripts/demo_runner.py
```

The runner starts a disposable local API with an in-memory order/audit store,
then sends a natural-language hotel request through real stdio MCP tools and
asserts an auto-order, a stock-out with ranked substitutes, and an over-budget
order that a human approves. It also verifies the audit chain. Set
`AIMO_API_URL` to run the same command against a deployed API.

### Vector search (T2)
`/catalog/search` blends Gemini embeddings (stored in `product_embeddings`) with full-text search when the DB has embeddings and `GEMINI_API_KEY` is set; otherwise it is text-only. Build/refresh vectors with `uv run python -m app.catalog.embed` (resumable, only changed products). Common queries are pre-embedded in the background at startup (`app/catalog/warm.py`).
Latency: < 200 ms for cached/pre-warmed queries; a new query pays one Gemini call (~0.3-0.5 s) and falls back to text-only if it exceeds `SEARCH_EMBED_TIMEOUT_S` (default 0.5).

## Approval inbox (T14)
```bash
uv run uvicorn app.main:app --port 8000   # API (CORS origins: CORS_ORIGINS, default http://localhost:3000)
uv run scripts/seed_demo.py               # creates placed / pending_approval / rejected demo orders
cd web && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3000/approvals
```

## Demo passphrase gate (T16)
When `DEMO_PASSPHRASE` is set (Modal secret `aimo-api`, your local `.env`), every API call except `/health`,
`/auth/*` and the static `/game` + `/vimo` pages needs the `aimo_demo` cookie (`POST /auth/unlock {passphrase}`,
the web `/unlock` page) or the header `X-Demo-Pass: <passphrase>` (MCP client and `scripts/demo_runner.py`
send it from the env). Unset = gate off (CI, tests). Never commit the passphrase. Sessions last 7 days and
`/auth/lock` only clears the browser cookie: to revoke all sessions, change `DEMO_PASSPHRASE` or `DEMO_SESSION_SECRET`.
Web routes: `/` landing · `/vimo` buyer cockpit (`web/public/vimo`, also served by FastAPI at `/vimo`) ·
`/approvals` inbox · `/unlock` · `/game`. Next proxies the API paths to `NEXT_PUBLIC_API_URL` (`web/next.config.ts`).

## Lanes
Each lane owns one router and replaces its mock with real code:
`app/catalog/router.py` (P1), `app/jury/router.py` (P2), `app/orders/router.py` (P4).
Until then, every endpoint returns the milk-scenario fixtures via `app.mocks.load(name)`.
Contract changes go in `contracts/models.py` + its fixture, and `pytest` must stay green.

## Workflow
Feature branch per change (`<lane>/<ticket>-<slug>` or `<lane>/chore-<slug>`), PR to `main`, adversarial `code-review` before every PR. Details in `CLAUDE.md` → Git workflow.

## Deploy (T15)
- **API on Modal:** `uv run modal setup` once, then create the secret
  `uv run modal secret create aimo-api VALIO_API_KEY=... DB_URL='mysql://user:pass@host:port/aimo?ssl=true' CORS_ORIGINS=https://<vercel-app>.vercel.app GEMINI_API_KEY=... DEMO_PASSPHRASE=... ELEVENLABS_API_KEY=... ELEVENLABS_VOICE_ID=...`
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
