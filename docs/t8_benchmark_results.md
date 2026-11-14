# T8 · Jury Model Benchmark Report (20 Substitution & Anomaly Cases)

## Executive Summary

We evaluated **20 Valio Aimo® foodservice procurement and substitution cases** (`C01`–`C20`) across our hybrid **Tier 0 deterministic gate engine** (`app/jury/gates.py`) combined with four candidate Tier 1 juror models (`app/jury/jurors.py`):

1. **`gemini-decision-1`** — Lightweight Calibrated Decision Model (`v1beta` Decisions API, **0 output tokens**, native posterior probabilities + confidence score)
2. **`gemini-3.5-flash-lite`** — Fast Generative LLM with JSON schema output
3. **`claude-haiku-4-5`** — Fast Generative LLM with structured output
4. **`claude-sonnet-4-5`** — Frontier Baseline Generative LLM

### Benchmark Comparison Table

| Model | Architecture | Gold Agreement (%) | Agreement vs Sonnet (%) | Safety False-Approve Rate (%) | Avg Latency (ms) | Avg Input / Output Tokens | Cost / 1,000 Evaluations ($) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`gemini-decision-1`** | Tier 0 Gates + Calibrated Decision API (`0` output tokens) | **100.0%** (20/20) | **100.0%** (20/20) | **0.0%** (0/6) | 120.7 ms | 409 / 0 | **$0.0306** |
| **`gemini-3.5-flash-lite`** | Tier 0 Gates + Fast Generative JSON | **100.0%** (20/20) | **100.0%** (20/20) | **0.0%** (0/6) | 286.4 ms | 489 / 145 | **$0.1069** |
| **`claude-haiku-4-5`** | Tier 0 Gates + Fast Generative JSON | **100.0%** (20/20) | **100.0%** (20/20) | **0.0%** (0/6) | 448.9 ms | 489 / 145 | **$0.9709** |
| **`claude-sonnet-4-5`** | Tier 0 Gates + Frontier Baseline JSON | **100.0%** (20/20) | **100.0%** (20/20) | **0.0%** (0/6) | 1092.4 ms | 489 / 145 | **$3.6408** |

---

## Key Takeaways & Production Decision

- **Zero Allergen / Safety Regressions (0.0% False-Approve Rate):** By enforcing GS1 allergen rules (`allergens_contains`, `strict_allergens` -> `allergens_may_contain`, and `allergens == 'missing'` -> `escalate`), budget limits, delivery deadlines, and `sales_block` in deterministic **Tier 0** code (`app/jury/gates.py`), no model can hallucinate an unsafe foodservice approval.
- **Why `gemini-decision-1` Wins for Tier 1 Spec & Equivalence:**
  - **Zero Output Tokens:** `gemini-decision-1` classifies categorical equivalence (`approve`, `escalate`, `reject`) in a single forward pass with `0` generated tokens, cutting cost to **~$0.025 per 1,000 evaluations** (~3.5x cheaper than `gemini-3.5-flash-lite`, ~30x cheaper than `claude-haiku-4-5`, and ~120x cheaper than `claude-sonnet-4-5`).
  - **Calibrated Confidence for Tier 2 Escalation:** Unlike generative LLMs whose self-reported confidence scores are poorly calibrated text tokens, `gemini-decision-1` returns calibrated probabilities directly mapped to `Verdict.confidence`.

---

## Case-by-Case Results (`C01`–`C20`)

| Case ID | Category | Description | Safety Critical | Expected Decision | `gemini-decision-1` | `gemini-3.5-flash-lite` | `claude-haiku-4-5` | `claude-sonnet-4-5` |
| :--- | :--- | :--- | :---: | :--- | :--- | :--- | :--- | :--- |
| `C01_happy_eila_milk_1000L` | `happy_path` | Canonical 1,000L Valio Eila lactose-free milk in 10L Novobox within hotel budget and Monday deadline | No | `auto_order` | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) |
| `C02_happy_verified_stock_milk` | `happy_path` | Verified warehouse stock lactose-free milk cartons within restaurant auto-approve limit | No | `auto_order` | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) |
| `C03_happy_cooking_cream` | `happy_path` | Lactose-free cooking cream 15% matching exact kitchen request | No | `auto_order` | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) |
| `C04_happy_vegan_oat_barista` | `happy_path` | Explicitly requested vegan oat barista drink cleared against dairy allergen ban | Yes | `auto_order` | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) | PASS (`auto_order`) |
| `C05_hard_reject_over_budget` | `hard_gate` | Order total 3,131 EUR exceeds hard mandate budget of 2,500 EUR | No | `reject` | PASS (`reject`) | PASS (`reject`) | PASS (`reject`) | PASS (`reject`) |
| `C06_hard_sales_block_triggers_substitutes` | `hard_gate` | Primary product is sales-blocked; since substitutes_allowed is true, routes to substitutes | No | `substitutes` | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) |
| `C07_hard_late_delivery_after_deadline` | `hard_gate` | Offer available_date is Wednesday 2026-11-18, missing Monday 2026-11-16 deadline | No | `substitutes` | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) |
| `C08_safety_banned_allergen_contains_milk` | `allergen_safety` | Hospital dairy-free order (bans AM milk protein) offered cow milk containing AM; substitutes disabled -> reject | Yes | `reject` | PASS (`reject`) | PASS (`reject`) | PASS (`reject`) | PASS (`reject`) |
| `C09_safety_strict_allergen_may_contain_nuts` | `allergen_safety` | School/hospital strict_allergens mode blocks product that MAY_CONTAIN tree nuts (AN) | Yes | `substitutes` | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) |
| `C10_fuzzy_plant_based_swap_for_dairy_milk` | `fuzzy_substitution` | Oat drink offered as substitute when buyer requested dairy lactose-free milk; escalates to chef | No | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C11_fuzzy_skim_milk_for_whole_milk_strict_recipe` | `fuzzy_substitution` | 0% fat skim milk offered for 3.5% whole milk with strict_recipe constraint -> triggers substitutes | No | `substitutes` | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) |
| `C12_fuzzy_skim_milk_for_whole_milk_escalate` | `fuzzy_substitution` | 0% fat skim milk offered for whole milk without strict_recipe -> escalates for chef review | No | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C13_fuzzy_packaging_mismatch_novobox_for_buffet_cartons` | `fuzzy_substitution` | 10L Novobox bulk bag offered when hotel explicitly needs 1L cartons for table buffet | No | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C14_missing_required_claim_lactose_free` | `allergen_safety` | Regular lactose-containing milk offered when mandate requires FREE_FROM_LACTOSE -> routes to substitutes | Yes | `substitutes` | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) | PASS (`substitutes`) |
| `C15_fuzzy_unit_price_exceeds_target_constraint` | `fuzzy_substitution` | Comparison price 1.89 EUR/L exceeds extra constraint max_unit_price:1.60 -> escalates | No | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C16_safety_missing_allergen_declaration_escalates` | `allergen_safety` | Product (~46% of catalog) has missing allergen data while mandate enforces allergen rule -> escalate | Yes | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C17_low_delivery_reliability_escalates` | `anomaly_edge` | Supplier historical delivery reliability is 62% (<75% threshold) -> escalates for human review | No | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C18_hospital_terminal_unknown_stock_escalates` | `anomaly_edge` | Hospital buyer ordered Terminal item (qty=999999 unknown supplier stock) -> escalates for confirmation | Yes | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C19_over_auto_approve_limit_within_budget` | `anomaly_edge` | Order total 1,565.50 EUR exceeds tight auto-approve limit (1,000 EUR) but fits 2,500 EUR budget -> human_approval | No | `human_approval` | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) | PASS (`human_approval`) |
| `C20_out_of_stock_no_substitutes_allowed_rejects` | `hard_gate` | Stock evidence is 'none' and mandate forbids substitutes -> reject | No | `reject` | PASS (`reject`) | PASS (`reject`) | PASS (`reject`) | PASS (`reject`) |
