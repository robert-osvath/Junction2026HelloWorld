# Team Hello World – Junction 2026 – Valio Aimo "Trusted Trade Agents"

Deadline: **Sunday 2026-11-15 10:00 (Helsinki)**. Team: Hardik (P1), Robert (P2), Yash (P3), Aviral (P4).
Tracker (source of truth for tickets): Notion → Hack Junction → Product Roadmap Timeline (T0–T15).
Plan + contracts page: https://app.notion.com/p/4176750c710381a390cbe658e59dd918
Solution page: https://app.notion.com/p/4176750c710381dd8f76d6f5751a2692

## Working agreement for Claude in this repo
- Show proposed file/structure changes before making large ones; keep answers short.
- Never commit secrets. API key lives in `.env` (`VALIO_API_KEY=...`), `.env` is gitignored.
- Build against mocks first; replace mocks when the owning lane lands the real thing.

## Git workflow (mandatory, no exceptions)
- **Never commit or push to `main` directly.** Every change goes on a feature branch named
  `<lane>/<ticket>-<slug>` (e.g. `p4/t13-audit-log`), branched from an up-to-date `main`.
- Changes reach `main` only through a PR (`gh pr create --base main`). No direct merges, no force-push to `main`.
- **Adversarial review before every PR, without fail:** run the `code-review` skill against
  `git merge-base main HEAD`, reviewing as a skeptic trying to break the change (wrong logic, contract
  drift from `contracts/`, missing tests, secrets, edge cases from the real data). Fix every real finding,
  re-run until clean, and put a short "Adversarial review" section in the PR body listing what was found
  and fixed (or why something was left). A PR without that section is not ready.
- `uv run pytest -q` must pass before opening the PR. Use the `pr` skill for the PR body.

## What we're building (core)
An Aimo-owned, open ordering interface that external purchasing agents use:
buyer agent request → catalog search → live price/availability (shortlist only) → jury → route to
auto-order / human approval / ranked substitutes → every step in a hash-chained audit log.

Jury = Tier 0 code gates → Tier 1 fast-model jurors (Spec, Delivery, Cost) → Tier 2 stronger model →
Tier 3 human. Hard rules are always code; LLMs only for fuzzy judgments and explanations.

Out of scope for now: pitch, "edge" features (intent ordering, forward-demand dashboard), sustainability juror.

## Data & API facts (https://junction26.valioaimo.fi/)
- APIs (header `X-API-Key`): `POST /api/price`, `POST /api/availability`; body
  `{customer, requestedDate: "YYYY-MM-DD", products: [ids]}`, 1–1,000 ids, undisclosed rate limits.
  Customer number doesn't matter. **No order API** – ordering is ours (simulated).
- Availability: `closingTime` (order cut-off), `salesBlock`, `logisticModel` Stock (real qty) /
  Terminal (supplier-shipped, qty always 999999 = unknown), `scheduleLines[0].availableDate`
  may be later than requested.
- Price: `catalogPrice`, `listPrice`, `comparisonPrice` per `comparisonUnit` (KG or L), tax, `netPriceFound`.
  Content per sales unit = catalogPrice.value / comparisonPrice.value.
- Files (download once): product JSON (17,546 products, 648 vendors, 349 unnamed category codes),
  product JSON schema, delivery reliability CSV (4,425 rows, 0–1), carbon CSV (~11k, category averages).
  Links: GET `/api/products/download`, `/api/delivery-reliability/download`, `/api/carbon-footprint/download` (follow redirects);
  schema has no API endpoint, direct link `/files/008f9a50ba9a6538d2e5bdf29d592af8/valio_aimo_junction26_product_data_schema.json`.
  `uv run scripts/download_data.py` fetches all 4. Site docs (`/docs`, `/redoc`) need the shared site password (ask the team; never commit it).
- Product fields of note: `salesUnit`, `allowedLotSize`, `vendorName`, `category`, `substitutions`
  (only 189 products), `synkkaData.names[]` (fi/en/sv), `classifications[]` groups:
  `allergen` (CONTAINS / MAY_CONTAIN, GS1 codes e.g. AM milk, AE egg, AW gluten, AY soy, AN nuts),
  `nonAllergen` (FREE_FROM), `nutritionalClaim` (e.g. FREE_FROM_LACTOSE, VEGAN),
  `packagingMarkedLabel` (certifications). Allergen data exists for ~54% of products.
- Demo anchor: ~85 lactose-free milk products, e.g. Valio Eila 10 L Novobox, product `463038`
  (VALIO OY TUORE, category 10400). Live 2026-11-14: sales unit `KI` = case of ~20 L
  (catalogPrice €31.31 / comparisonPrice €1.57/L), so **1,000 L = 50 units = €1,565.50** (list €36.83, 13.5% VAT).
  `logisticModel: Terminal` → qty 999999, stock evidence is only "estimated"; no delivery-reliability row.
  For requestedDate Mon 2026-11-16 the API returns `closingTime` Thu 2026-11-12 15:00 (already past) and
  availableDate 2026-11-16 — the jury must decide how to treat a past cut-off.
  Claims: FREE_FROM_LACTOSE, LOW_FAT, VEGETARIAN…; allergen CONTAINS AM (milk).

## Stack
Python 3.12 + FastAPI + pydantic v2, MCP Python SDK, MariaDB (catalog, vector search,
system-versioned audit table), Next.js (approval UI), Modal (hosting), CRACI (CI, `runs-on: craci`).
Models: Claude Haiku / Gemini Flash (fast jurors), Claude Sonnet (Tier 2), optional Featherless open-weight.

## Repo layout (T0 creates this)
```
contracts/        pydantic models shared by all lanes (Mandate, Need, Offer, Verdict, Order, AuditEvent)
app/              one FastAPI app, one router per lane
  catalog/        P1: loader, search, Aimo API client, offer builder  (T1–T4)
  jury/           P2: mandate gates, jurors, substitutes, model tests  (T5–T8)
  orders/         P4: order service + audit log                        (T12–T13)
  mocks/          fixture responses used until each lane is real
mcp_server/       P3: MCP tools wrapping the REST API                   (T9)
agent/            P3: buyer agent + demo runner                         (T10–T11)
web/              P4: Next.js approval inbox                            (T14)
db/               docker-compose MariaDB + migrations
data/             downloaded files (gitignored)
tests/
.github/workflows P4: CI on CRACI + Modal deploy                        (T15)
```

## Contracts (final, T0 – source of truth is `contracts/models.py`, fixtures in `contracts/fixtures/`)
```
Mandate  { id, buyer_name, buyer_type: hotel|hospital|restaurant, budget_per_order_eur,
           auto_approve_limit_eur, required_claims[], banned_allergens[], strict_allergens: bool,
           substitutes_allowed: bool }
Need     { text, product_intent, quantity, unit: L|kg|pcs, deliver_by: date, extra_constraints[] }
Offer    { product_id, name, sales_unit, units_needed, unit_price_eur, comparison_price_eur,
           comparison_unit, total_eur, available_date, closing_time, sales_block,
           logistic_model: Stock|Terminal,
           evidence { stock: verified|estimated|none, allergens: declared|missing, reliability: float|null } }
Verdict  { juror: tier0|spec|delivery|cost|tier2, decision: approve|escalate|reject,
           confidence: 0-1, reasons[], model: str|null }
Order    { id, mandate_id, need, lines: Offer[], verdicts: Verdict[],
           status: placed|pending_approval|substitutes_proposed|rejected, confirmation_no, substitutes: Offer[] }
AuditEvent { id, ts, order_id, actor, action, payload_hash, prev_hash, hash, human_summary }
# endpoint I/O added in T0
CatalogItem { product_id, name, vendor_name, sales_unit, category, claims[], allergens_contains[],
              allergens_may_contain[], allergen_data: bool }
JuryResult  { verdicts[], decision: auto_order|human_approval|substitutes|reject, chosen_product_id }
OffersRequest / JuryRequest / SubstitutesRequest / OrderRequest = the request bodies below
AuditVerify { ok, checked, broken_at }
```

## Endpoints (mock in T0, real later)
```
GET  /catalog/search?q=&claims=&exclude_allergens=&category=&limit=   P1
POST /offers            {need, product_ids, delivery_date} -> Offer[] P1
POST /jury/evaluate     {mandate, need, offers} -> Verdict[] + decision  P2
POST /substitutes       {mandate, need, offer} -> Offer[] (top 3)    P2
POST /orders            {mandate_id, need, lines} -> Order            P4
GET  /orders/{id}       POST /orders/{id}/approve | /reject           P4
GET  /audit?order_id=   GET /audit/verify                             P4
```

## Dev setup
`uv sync` (Python 3.12; if `~/.local` isn't writable, `brew install python@3.12` and uv uses it) ·
`uv run uvicorn app.main:app --reload` · `uv run pytest -q` · `docker compose -f db/docker-compose.yml up -d`.
Each lane replaces its mock in its own router: `app/catalog/router.py`, `app/jury/router.py`, `app/orders/router.py`;
fixtures served via `app.mocks.load(name)`.

## T0 – Contracts + repo skeleton (DONE, on main)
1. Create the layout above, `pyproject.toml`, `.env.example`, `.gitignore` (`.env`, `data/`).
2. `contracts/` pydantic models exactly as above (+ example JSON fixtures for each).
3. FastAPI app with all endpoints above returning fixtures from `app/mocks/` (milk scenario).
4. `db/docker-compose.yml` for MariaDB 11.x.
5. Script to download the 4 data files into `data/`.
6. Smoke test hitting every mock endpoint; README with run commands.
Done when every lane can call every other lane's mocks.

## Milestones
Sat 10:00 High tickets work in isolation · Sat 14:00 happy path end to end ·
Sat 22:00 stock-out + over-budget, UI, deploy · Sun 04:00 edge slot · Sun 08:00 freeze · Sun 10:00 submit.

## Skills (in .claude/skills)
Use these; ignore the rest unless asked.
- Building a ticket: `implement` (paste the Notion ticket spec), `tdd` for logic with clear
  inputs/outputs (T5 gates, T7 ranking, T12 state machine, T13 hash chain).
- Before every PR: adversarial `code-review` against the branch point (see Git workflow); `pr` to write the PR body.
- Something broken or slow: `diagnosing-bugs`.
- UI exploration (T14): `prototype`.
- Ending a session / passing work on: `handoff`.
- One-time setup: `git-guardrails-claude-code` (block force-push, reset --hard, etc.).
Not used here: to-tickets / to-spec / triage (tracker is Notion), TS-only skills
(setup-ts-deep-modules, migrate-to-shoehorn), writing-*, teach, scaffold-exercises, grill-*, wayfinder.
