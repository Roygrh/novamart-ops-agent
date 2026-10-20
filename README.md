# NovaMart Internal Operations Support Agent

An internal AI assistant for NovaMart store employees, built with **Google ADK** and the **Gemini API**. It answers policy questions using only official NovaMart documents, looks up live order and inventory data with deterministic tools, and acts as a **Smart Operations Gatekeeper**. It walks associates through issues they can resolve at the POS, and it opens a validated Operations ticket only when the SOP requires one.

---

## 1. The creative feature: the Smart Operations Gatekeeper

Most support bots stop at answering. This one closes the loop on the most expensive question in store operations: *"Do I need to call Operations?"*

| Situation | What the agent does | Example |
|---|---|---|
| Policy allows a store fix | Gives numbered POS steps, the refund amount and method, and mandatory inspections, with a citation for each rule | `ORD-1004`: espresso machine damaged in transit, $120 ≤ $150, so the associate gives an immediate replacement or refund at the register |
| SOP-OPS-007 trigger met | Calls `create_ops_escalation_ticket` in the same turn and returns the ticket ID and next steps | `ORD-1005`: shattered $1,200 TV, so the agent opens `TKT-OPS-9001` (`DAMAGED_IN_TRANSIT_HIGH_VALUE`) |
| Policy clearly says no | Declines with the reason and the alternative, and opens no ticket | `ORD-1003`: drone is 23 days old against a 15-day window, so the customer is directed to the manufacturer warranty |
| Out of scope | Explicitly abstains and never invents policy | PTO balance, jammed receipt printer |

**The guardrail lives in code, not just in the prompt.** `create_ops_escalation_ticket` enforces SOP-OPS-007 deterministically. Even if the model tries to escalate a $120 damage claim, the tool returns `REJECTED_SELF_SERVICE` with the POS steps instead. It also rejects:
- categories outside the four SOP categories,
- a `policy_id` that doesn't govern the category,
- lost-package claims made 24 hours or less after the delivery scan (`REJECTED_WAIT_24H`),
- unknown orders, and
- duplicates (`ALREADY_ESCALATED`).

The KPI this targets is the **false-escalation rate**: every self-service case the agent deflects saves Operations a ticket.

## 2. Architecture

```
Store employee
     │  natural-language question
     ▼
ADK root_agent (Gemini)  ── system instruction: ground → look up → gatekeep → abstain
     │
     ├── search_policy_docs ──────────► knowledge_base.py  (BM25 + ID/term boosting over data/policies/*.md)
     ├── get_order_status ────────────► data/mock_db/orders.json      (days computed vs REFERENCE_DATE)
     ├── check_product_and_competitor_price ► data/mock_db/inventory.json (stock + POL-PRC-005 eligibility)
     └── create_ops_escalation_ticket ► SOP-OPS-007 guardrails ► data/mock_db/escalation_tickets.json
```

| Path | Role |
|---|---|
| `data/policies/` | 7 policy documents (`POL-RET-001` … `SOP-OPS-007`) with metadata headers and numbered sections for precise citations |
| `data/mock_db/` | 8 canonical orders covering every policy intersection, store stock and competitor listings, and the ticket store |
| `novamart_agent/knowledge_base.py` | Section-level parser and BM25Okapi ranking with exact-ID and domain-term boosts. Returns **nothing** when a query has no lexical overlap with the corpus |
| `novamart_agent/tools.py` | The 4 ADK tools, with typed signatures and docstrings that ADK turns into tool schemas |
| `novamart_agent/agent.py` | `root_agent` and its system instruction (grounding, citation format, gatekeeper decision table, abstention guardrail) |
| `evals/` | Two-layer evaluation suite (see §4) |
| `PRODUCTION_NOTE.md` | One-page Google Cloud production architecture |

## 3. Quickstart

Requires Python 3.11+ and a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then set GOOGLE_API_KEY (and optionally GEMINI_MODEL)
```

`.env`:
```
GOOGLE_GENAI_USE_VERTEXAI=0
GOOGLE_API_KEY=your_google_ai_studio_api_key_here
GEMINI_MODEL=gemini-3.8-flash
```

Run the agent:
```bash
adk run novamart_agent      # interactive terminal chat
adk web                     # dev UI at http://localhost:8000, shows tool calls and events
```

Try these prompts:
- `A customer returns an opened, working NovaAir drone bought 8 days ago. How much do I refund?`
- `Order ORD-1005 arrived with a shattered screen.`
- `Can I price match PROD-101? Customer shows $95 on Amazon and $105 at Best Buy.`
- `How many PTO days do I have left?` (the agent should decline)

## 4. Evaluation

```bash
python -m evals.run_eval --mode component     # offline: 40 checks, zero API calls, no key needed
python -m evals.run_eval                      # offline checks + all 11 live agent cases
python -m evals.run_eval --case TC04          # a single live case (repeatable or comma-separated)
python -m evals.run_eval --limit 3 --rpm 5    # first 3 cases, max 5 LLM calls/minute
python -m evals.run_eval --model gemini-3.5-flash --verbose
```

**Layer 1: component and guardrail checks (deterministic, offline).** These cover:
- retrieval ranking (for example, `POL-ELC-002 restocking fee` ranks `POL-ELC-002 §3` first),
- empty retrieval for out-of-scope queries,
- order date math against `REFERENCE_DATE = 2026-10-18`, plus the BOPIS hold window and lost-package hours,
- restocking-fee math and the POL-PRC-005 price-match rules (marketplace seller, unauthorized retailer, out of stock, Black Friday promo, clearance), and
- every ticket gatekeeper branch, including persistence.

**Layer 2: live end-to-end agent cases** (`evals/test_cases.json`). Each case runs through ADK's `InMemoryRunner` in a fresh session, with the ticket store reset by `reset_escalation_tickets()`. A case passes only if all of these hold:
- the expected tools were called and forbidden tools were not (`tool_trajectory`),
- the expected Policy IDs are cited and the keyword groups match (for example, `$67.50` and `$382.50`),
- the agent abstained, for out-of-scope cases, and
- the **ticket store ends up in the expected state**: exactly one ticket with the right category and policy, or none.

| ID | Scenario | Key assertion |
|---|---|---|
| TC01 | Open-box drone restocking fee | `$67.50` fee, `$382.50` refund, cites POL-ELC-002 |
| TC02 | No-receipt return | Store credit capped at `$50`, photo ID required |
| TC03 | `ORD-1004` damage ≤ $150 | Self-service, **no ticket** |
| TC04 | `ORD-1005` damaged TV | Ticket `DAMAGED_IN_TRANSIT_HIGH_VALUE` / POL-ORD-003 |
| TC05 | `ORD-1006` lost package, 72h | Ticket `LOST_PACKAGE` / POL-ORD-003 |
| TC06 | `ORD-1003` out of window | Decline, warranty referral, no ticket |
| TC07 | `ORD-1008` Final Sale | Decline, cites POL-RES-004 |
| TC08 | `PROD-101` price match | Honor Best Buy $105, reject the Amazon marketplace offer |
| TC09 | `ORD-1007` BOPIS hold | One-time 48-hour extension, no ticket |
| TC10–11 | PTO / receipt printer | Abstain, no policy cited, no order tools |

Results print as a summary table and are saved to `evals/latest_results.json`, with the full trajectory, tool result statuses, responses, and per-check details.

**Free-tier friendly.** A full live run makes about 35–40 LLM calls, and many free-tier models allow only 20 requests per day each. The runner handles this as follows:
- It throttles every LLM call (`--rpm`, default 8).
- It retries per-minute `429`/`503` errors after the server's `retryDelay`.
- On a daily-quota `429`, it stops cleanly, marks the remaining cases `SKIPPED`, and still saves results.
- To finish the remaining cases on another model, run `--case TC06,TC07,... --model <other>`.

**Latest run (2026-10-19).** All 40 component checks passed. All 11 live cases passed, spread across `gemini-3.7-flash` (TC01–05), `gemini-3.6-flash` (TC06–10), and `gemini-3.1-flash-lite` (TC11), because `gemini-3.8-flash` had used up its daily free-tier quota.

## 5. Key engineering decisions

- **Deterministic guardrails over prompt-only rules.** Thresholds ($150, 24h, 15/30-day windows) and the SOP category list live in Python, so a model mistake can't produce a wrong escalation. The prompt says *when* to call the tool, and the tool decides *whether* the ticket is allowed.
- **Lexical retrieval with ID boosting instead of embeddings.** With 7 short documents full of exact identifiers (`PROD-303`, `POL-ELC-002`), BM25 plus exact-match boosts is fully reproducible, needs no embedding calls or quota, and returns an empty list for queries with no overlap, which reinforces abstention. Section-level chunks keep citations precise ("POL-ELC-002, Section 3").
- **Fixed `REFERENCE_DATE = 2026-10-18`.** All day counts are computed against this date, so evals produce the same results whenever they run.
- **Tools return structured statuses** (`FOUND`, `ORDER_NOT_FOUND`, `REJECTED_SELF_SERVICE`, …) plus a human-readable `message`, so the model can branch on status and relay guidance verbatim.
- **Outcome-based evaluation.** Live cases assert on the persisted ticket store, not just the response text. That catches the costly failure, a wrong or missing escalation, even when the prose looks fine.

## 6. Assumptions

- The user is an authenticated store employee. `store_id` comes from the order (default `STORE-NYC-01`). In production it would come from identity claims.
- A single-item order is assumed. The order value is the item price, and the price shown is the price paid.
- Damage photos are collected out of band. The ticket `summary` records that evidence was requested.
- Ticket IDs are sequential from `TKT-OPS-9001` in a local JSON file guarded by a process lock. This suits a demo, not multi-instance deployments.
- Policies are the single source of truth. Anything they don't cover counts as out of scope, even when it is store-related (for example, POS hardware).

## 7. Known limitations

- Out-of-scope queries that share vocabulary with the policies still retrieve sections ("vacation **days**", "receipt **printer**"). In the live evals the system instruction handles these correctly, but retrieval alone doesn't guarantee abstention. A relevance-score floor or an intent classifier would close this gap (see below).
- Keyword and abstention checks are heuristic string and regex matches. An LLM judge would grade semantic correctness more robustly.
- The agent does not validate photo evidence. It trusts the associate's description of the damage.

## 8. What we would build next

1. **Managed RAG:** Move policies to Vertex AI Search, with layout-aware chunking and ACL inheritance, and keep BM25 as an offline fallback and test oracle.
2. **Real integrations:** OMS, POS, and ServiceNow behind Apigee, with idempotency keys on ticket creation and a refund-authorization handoff to the POS.
3. **Multimodal damage triage:** The associate uploads a photo, Gemini classifies the damage severity, and the image is attached to the ticket automatically.
4. **Out-of-scope intent router:** A lightweight classifier in front of the agent, plus a retrieval score floor, so abstention doesn't rely on the prompt alone.
5. **Evals at scale:** Use the Vertex AI Gen AI Evaluation Service with an LLM-judge for groundedness, multi-turn cases (the associate adds facts mid-conversation), adversarial prompt-injection cases, and CI gating on every prompt or policy change.
6. **Feedback loop:** Operations marks tickets as "store could have resolved", and those labels feed the false-escalation KPI and new golden cases.
7. **Localization:** Spanish-language store support, with citations kept against the English source policies.
