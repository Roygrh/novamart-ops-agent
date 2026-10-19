# NovaMart Internal Operations Support Agent — Architecture & Domain Specification

## 1. Business Context & Core Objective
NovaMart is a mid-size retail chain. Store employees frequently contact the central Operations team regarding product policies, returns, and order issues.
This project builds an internal AI support assistant using the Google AI stack (`google-adk` and `google-genai` via the Gemini API) that:
1. Answers employee questions grounded strictly in official NovaMart policy and product documents (`data/policies/`).
2. Looks up live order and inventory data using deterministic tools (`novamart_agent/tools.py` backed by `data/mock_db/`).
3. Closes the operational loop ("Smart Operations Gatekeeper"):
   - If an issue can be resolved in-store under company policy, the agent instructs the store associate step-by-step on how to resolve it at the POS without escalating to Operations.
   - If an issue requires Operations intervention (e.g., damaged items > $150, lost packages marked as delivered, or policy exceptions), the agent validates the rules and creates a structured escalation ticket via a tool (`create_ops_escalation_ticket`).
4. Declines gracefully when asked out-of-scope questions (e.g., HR, payroll, employee vacation, POS hardware IT support, or non-existent policies) instead of inventing answers.

## 2. Project Directory Structure
    novamart-ops-agent/
    ├── .env                        # Local environment variables (GOOGLE_API_KEY, GEMINI_MODEL)
    ├── .env.example                # Template for environment variables
    ├── requirements.txt            # Python dependencies
    ├── README.md                   # Setup instructions, architecture decisions, and roadmap
    ├── PRODUCTION_NOTE.md          # 1-page architecture note for Google Cloud & Gemini Enterprise
    ├── data/
    │   ├── policies/               # 7 official Markdown policy & product documents
    │   └── mock_db/                # JSON files simulating live orders, inventory, and tickets
    ├── novamart_agent/
    │   ├── __init__.py             # Loads root .env and exports agent
    │   ├── agent.py                # Google ADK root_agent definition and system instructions
    │   ├── knowledge_base.py       # Hybrid lexical/semantic retrieval over data/policies/
    │   └── tools.py                # Agent tools for policy search, order lookup, and ticket creation
    └── evals/
        ├── test_cases.json         # Evaluation test suite covering grounding, tools, and abstention
        └── run_eval.py             # Automated evaluation script

## 3. Canonical Product Catalog (Used across Policies and Mock Orders)
Always use these exact `product_id` values, names, categories, and prices across all documents and mock data:
- `PROD-101`: **BrewMaster Pro Espresso Machine** | Category: `Home & Kitchen` | Price: `$120.00` | Return Window: 30 days (`POL-RET-001`).
- `PROD-202`: **NovaAir FPV Drone 4K** | Category: `Electronics` | Price: `$450.00` | Return Window: 15 days (`POL-ELC-002`). Contains a Lithium-Polymer (LiPo) battery; damaged units cannot be shipped via standard air freight and must use ground hazmat return labels.
- `PROD-303`: **VisionMax 65" 4K OLED Smart TV** | Category: `Electronics` | Price: `$1,200.00` | Return Window: 15 days (`POL-ELC-002`). Store staff must verify that the serial number on the TV chassis matches the box and receipt before accepting any return.
- `PROD-404`: **Heritage Pre-Seasoned Cast Iron Cocotte** | Category: `Home & Kitchen` | Price: `$85.00` | Return Window: 30 days standard (`POL-RET-001`), plus a Lifetime Cast Iron Replacement Warranty for structural cracks under normal cooking use (handled directly in-store).
- `PROD-505`: **SonicPulse Pro In-Ear Wireless Earbuds** | Category: `Audio / Personal Care` | Price: `$160.00` | Restricted item (`POL-RES-004`): Non-returnable once opened due to hygiene regulations, unless defective on arrival.
- `PROD-606`: **Nordic Winter Parka (Clearance - 60% Off)** | Category: `Apparel (Final Sale)` | Price: `$64.00` (Original `$160.00`) | Restricted item (`POL-RES-004`): Marked `Final Sale`, non-returnable and non-refundable unless damaged in transit.

## 4. Specification for the 7 Policy Documents (`data/policies/`)
All documents must be written in clear, professional English, between 45 and 70 lines each, with explicit metadata headers (`Policy ID`, `Effective Date`, `Owner`, `Summary`) and numbered sections so the agent can cite them accurately.

1. `01_general_return_policy.md` (`Policy ID: POL-RET-001`):
   - Standard return window: **30 calendar days** from the delivery or purchase date for `Home & Kitchen`, `Apparel`, and general merchandise.
   - Items must be in new or gently used condition with original packaging and accessories.
   - With valid receipt/order ID: full refund to the **original payment method**.
   - Without receipt: eligible for a **Store Credit** only, capped at a maximum of **$50.00**, requiring a valid government-issued photo ID (limited to 2 no-receipt returns per customer per 12-month period).

2. `02_electronics_and_appliances_policy.md` (`Policy ID: POL-ELC-002`):
   - Applies to all `Electronics` (including `PROD-202` and `PROD-303`).
   - Return window: **15 calendar days** from delivery or purchase date.
   - **Open-Box Restocking Fee**: If the package seal is broken and the item is NOT defective, a **15% restocking fee** is deducted from the refund.
   - Defective electronics within 15 days qualify for a free direct replacement or 100% refund (no restocking fee).
   - After 15 days, store returns are strictly declined; customers are directed to the 1-year manufacturer warranty.

3. `03_damaged_lost_and_bopis_orders.md` (`Policy ID: POL-ORD-003`):
   - **BOPIS (Buy Online, Pick Up In Store)**: Orders marked `Ready for Pickup` are held at the customer service desk for **5 calendar days**. If not picked up by end of Day 5, the order is automatically canceled and refunded. Store associates may grant a one-time **48-hour hold extension** if requested before expiration.
   - **Damaged in Transit**:
     - If item value is **<= $150.00**: Store associates are authorized to process an immediate in-store replacement or full refund at the register without contacting Operations.
     - If item value is **> $150.00**: Store associates CANNOT override at the register; they must create an **Operations Escalation Ticket** with photos and the `order_id`.
   - **Lost Packages ("Delivered" but Not Received)**:
     - If carrier tracking shows `Delivered` less than 24 hours ago, ask the customer to wait 24 hours (carriers sometimes scan early).
     - If more than 24 hours have passed since the `Delivered` scan, the associate must create an **Operations Escalation Ticket** for carrier claim investigation.

4. `04_final_sale_and_restricted_items.md` (`Policy ID: POL-RES-004`):
   - Strictly non-returnable items:
     - Clearance merchandise marked **Final Sale** or discounted by **50% or more** (e.g., `PROD-606`).
     - Physical and digital **Gift Cards**.
     - **Activated digital software** or downloadable license keys.
     - Opened personal hygiene items, including **In-Ear Headphones/Earbuds** (`PROD-505`).
   - **Single Exception**: If a restricted or Final Sale item arrives physically damaged in transit from an online order, it is eligible for an identical replacement (or refund if out of stock) reported within **7 calendar days** of delivery.

5. `05_price_match_and_adjustments.md` (`Policy ID: POL-PRC-005`):
   - **Competitor Price Match**: NovaMart matches prices at the time of purchase against four authorized retailers only: **Amazon.com** (must be *Sold and Shipped by Amazon.com*, not third-party marketplace sellers), **Best Buy**, **Target**, and **Walmart**. The item must have the exact same `product_id`/model and be currently in stock at the competitor.
   - Exclusions: Marketplace third-party sellers, flash sales, Black Friday/Cyber Monday promotions, bundle offers, and clearance items.
   - **Post-Purchase Price Adjustment**: If NovaMart lowers its own price within **14 calendar days** of purchase, the customer can request a refund of the price difference at any store register.

6. `06_product_catalog_and_handling.md` (`Policy ID: PRD-CAT-006`):
   - Detailed product reference sheet covering `PROD-101` through `PROD-606` (exact prices, categories, return windows, and mandatory in-store inspection/handling procedures: serial number verification on `PROD-303`, LiPo ground hazmat return rules on `PROD-202`, and Lifetime Cast Iron Warranty on `PROD-404`).

7. `07_operations_escalation_sop.md` (`Policy ID: SOP-OPS-007`):
   - Standard Operating Procedure defining the boundary between Store Self-Service and Operations Escalation:
     - **DO NOT Escalate to Operations**: Standard returns within window, damaged items <= $150.00, BOPIS hold extensions, competitor price checks, or out-of-window denials clearly covered by policy.
     - **MUST Escalate to Operations via Ticket**: Damaged items > $150.00, lost packages > 24h after delivery scan, serial number mismatches on high-value electronics, or inventory sync errors on BOPIS orders.
   - Lists required fields for an escalation ticket: `order_id`, `issue_category`, `policy_id`, `store_id`, and `summary`.

## 5. Out-of-Scope Topics (For Abstention Testing)
Do NOT include any information in the policy documents about:
- Employee HR policies (paid time off, sick leave, payroll, shift scheduling, dress code, employee discount rates).
- IT hardware troubleshooting for store POS terminals, barcode scanners, or Wi-Fi routers.
- Corporate B2B wholesale contracts.

## 6. Mock Database Specification (`data/mock_db/`)
To make all tests and evaluations 100% deterministic regardless of when the repository is executed, use a fixed reference date across the system:
- `REFERENCE_DATE = "2026-10-18"`

Create three JSON files inside `data/mock_db/`:

### 6.1. `data/mock_db/orders.json`
Include these 8 canonical orders designed to test every policy intersection:
1. `ORD-1001`:
   - Customer: "Alice Carter" | Store: `STORE-NYC-01` | Channel: `In-Store`
   - Item: `PROD-101` (BrewMaster Pro Espresso Machine, `$120.00`, Category: `Home & Kitchen`)
   - Purchase/Delivery Date: `"2026-10-08"` (10 days ago relative to `2026-10-18`)
   - Status: `"Delivered"` | Condition Reported: `"Unopened"` | Has Receipt: `true`
   - Expected Policy Outcome: Eligible for full `$120.00` refund to original payment method in-store (`POL-RET-001`, within 30-day window). Do NOT escalate.
2. `ORD-1002`:
   - Customer: "Marcus Vance" | Store: `STORE-NYC-01` | Channel: `Online`
   - Item: `PROD-202` (NovaAir FPV Drone 4K, `$450.00`, Category: `Electronics`)
   - Purchase/Delivery Date: `"2026-10-10"` (8 days ago)
   - Status: `"Delivered"` | Condition Reported: `"Opened - Non-Defective"` | Has Receipt: `true`
   - Expected Policy Outcome: Eligible for return in-store (`POL-ELC-002`, within 15-day window), subject to a **15% open-box restocking fee** (`$67.50` fee -> `$382.50` net refund). Must use ground hazmat handling if shipped (`PRD-CAT-006`). Do NOT escalate.
3. `ORD-1003`:
   - Customer: "Elena Rostova" | Store: `STORE-NYC-01` | Channel: `Online`
   - Item: `PROD-202` (NovaAir FPV Drone 4K, `$450.00`, Category: `Electronics`)
   - Purchase/Delivery Date: `"2026-09-25"` (23 days ago)
   - Status: `"Delivered"` | Condition Reported: `"Defective Motor"` | Has Receipt: `true`
   - Expected Policy Outcome: Store return declined (`POL-ELC-002`, past the 15-calendar-day electronics window). Direct customer to the 1-year manufacturer warranty. Do NOT escalate to Operations (`SOP-OPS-007`).
4. `ORD-1004`:
   - Customer: "David Kim" | Store: `STORE-NYC-01` | Channel: `Online`
   - Item: `PROD-101` (BrewMaster Pro Espresso Machine, `$120.00`, Category: `Home & Kitchen`)
   - Purchase/Delivery Date: `"2026-10-16"` (2 days ago)
   - Status: `"Delivered"` | Condition Reported: `"Damaged in Transit - Cracked Water Tank"` | Has Receipt: `true`
   - Expected Policy Outcome: Item value is `<= $150.00` (`$120.00`). Under `POL-ORD-003` and `SOP-OPS-007`, the store associate resolves this directly at the POS (immediate replacement or full refund) WITHOUT escalating to Operations.
5. `ORD-1005`:
   - Customer: "Sophia Martinez" | Store: `STORE-NYC-01` | Channel: `Online`
   - Item: `PROD-303` (VisionMax 65" 4K OLED Smart TV, `$1,200.00`, Category: `Electronics`, Serial: `SN-TV65-99881`)
   - Purchase/Delivery Date: `"2026-10-15"` (3 days ago)
   - Status: `"Delivered"` | Condition Reported: `"Damaged in Transit - Shattered Panel"` | Has Receipt: `true`
   - Expected Policy Outcome: Item value is `> $150.00` (`$1,200.00`). Under `POL-ORD-003` and `SOP-OPS-007`, associate CANNOT override at POS and MUST create an Operations Escalation Ticket with `issue_category: "DAMAGED_IN_TRANSIT_HIGH_VALUE"` and `policy_id: "POL-ORD-003"`.
6. `ORD-1006`:
   - Customer: "Liam O'Connor" | Store: `STORE-NYC-01` | Channel: `Online`
   - Item: `PROD-505` (SonicPulse Pro In-Ear Wireless Earbuds, `$160.00`, Category: `Audio / Personal Care`)
   - Purchase/Delivery Date: `"2026-10-15"` (Delivered scan 72 hours ago)
   - Status: `"Delivered"` | Condition Reported: `"Lost Package - Customer States Never Received"` | Hours Since Delivery Scan: `72`
   - Expected Policy Outcome: More than 24 hours have passed since carrier `Delivered` scan (`POL-ORD-003`). Associate MUST create an Operations Escalation Ticket with `issue_category: "LOST_PACKAGE"` and `policy_id: "POL-ORD-003"`.
7. `ORD-1007`:
   - Customer: "Hannah Abbott" | Store: `STORE-NYC-01` | Channel: `BOPIS`
   - Item: `PROD-404` (Heritage Pre-Seasoned Cast Iron Cocotte, `$85.00`, Category: `Home & Kitchen`)
   - Ready for Pickup Date: `"2026-10-14"` (Day 4 of 5-day BOPIS hold window; expires end of `"2026-10-19"`)
   - Status: `"Ready for Pickup"` | Extension Granted: `false`
   - Expected Policy Outcome: Eligible for a one-time 48-hour hold extension in-store (`POL-ORD-003`) without contacting Operations.
8. `ORD-1008`:
   - Customer: "Noah Williams" | Store: `STORE-NYC-01` | Channel: `Online`
   - Item: `PROD-606` (Nordic Winter Parka - Clearance 60% Off, `$64.00`, Category: `Apparel (Final Sale)`)
   - Purchase/Delivery Date: `"2026-10-14"` (4 days ago)
   - Status: `"Delivered"` | Condition Reported: `"Unopened - Wrong Size"` | Has Receipt: `true`
   - Expected Policy Outcome: Strictly non-returnable and non-refundable because it is marked `Final Sale` with >= 50% discount and is NOT damaged (`POL-RES-004`). Decline return in-store; do NOT escalate.

### 6.2. `data/mock_db/inventory.json`
Include store stock counts for `STORE-NYC-01` and competitor pricing for `PROD-101` through `PROD-606` so the agent can verify Price Match requests (`POL-PRC-005`):
- Include an example where `PROD-101` ($120.00) is listed at `$105.00` on `Best Buy` (In Stock, eligible for price match) and `$95.00` on a third-party Amazon Marketplace seller (Ineligible per `POL-PRC-005` because it is not Sold and Shipped by Amazon.com).

### 6.3. `data/mock_db/escalation_tickets.json`
Initialize as an empty JSON array `[]` (or read/write safely in memory/disk) to store tickets generated by `create_ops_escalation_ticket`.

## 7. Agent & Tooling Implementation Specification (`novamart_agent/`)

### 7.1. `novamart_agent/knowledge_base.py`
Build a lightweight, deterministic local retrieval module over `data/policies/*.md`:
- Parse each Markdown file into logical sections (by `## ` headings) while preserving document-level metadata (`Policy ID`, `Filename`, `Title`, `Summary`).
- Use `rank_bm25` (BM25Okapi) combined with exact token/ID boosting (so queries mentioning `POL-ELC-002`, `PROD-303`, `BOPIS`, or `restocking` rank the exact section at the top).
- Expose a clean function `retrieve_policy_sections(query: str, top_k: int = 4) -> list[dict]` returning `policy_id`, `filename`, `section_title`, and `content`.

### 7.2. `novamart_agent/tools.py`
Implement 4 well-typed Python tool functions with clear docstrings (Google ADK inspects function signatures and docstrings for tool calling):
1. `search_policy_docs(query: str) -> dict`:
   - Searches NovaMart's official policy and product documentation (`data/policies/`) and returns the top matching policy sections with their `policy_id` and section numbers.
2. `get_order_status(order_id: str) -> dict`:
   - Looks up a live order by `order_id` (e.g., `ORD-1001`) in `data/mock_db/orders.json`, computes `days_since_purchase_or_delivery` using `REFERENCE_DATE = "2026-10-18"`, and returns full order details (or a structured `ORDER_NOT_FOUND` status).
3. `check_product_and_competitor_price(product_id: str, store_id: str = "STORE-NYC-01") -> dict`:
   - Looks up product details, local store stock, and current competitor listings from `data/mock_db/inventory.json` to support returns, replacements, and `POL-PRC-005` price matches.
4. `create_ops_escalation_ticket(order_id: str, issue_category: str, policy_id: str, store_id: str, summary: str) -> dict`:
   - Creative "Close the Loop" Gatekeeper Tool:
   - Allowed `issue_category` values must strictly match `SOP-OPS-007`:
     - `DAMAGED_IN_TRANSIT_HIGH_VALUE`
     - `LOST_PACKAGE`
     - `SERIAL_NUMBER_MISMATCH`
     - `BOPIS_INVENTORY_SYNC_ERROR`
   - Enforces deterministic business guardrails before creating a ticket:
     - Verifies `order_id` exists in `orders.json`.
     - If `issue_category == "DAMAGED_IN_TRANSIT_HIGH_VALUE"` and the order's item price is `<= 150.00` (e.g., `ORD-1004`), the tool REJECTS ticket creation (`status: "REJECTED_SELF_SERVICE"`) and instructs the associate to process the replacement/refund directly at the store POS per `POL-ORD-003` and `SOP-OPS-007`.
     - If valid, generates a deterministic ticket ID (e.g., `TKT-OPS-9001`), persists it, and returns the structured ticket confirmation.

### 7.3. `novamart_agent/agent.py`
Configure `root_agent` using `google.adk.agents.Agent`:
- Read `MODEL_NAME = os.environ["GEMINI_MODEL"]`.
- Register all 4 tools from `novamart_agent/tools.py`.
- Craft a rigorous System Instruction that requires the agent to:
  1. Always search official policies (`search_policy_docs`) before answering policy/product questions, and cite the exact `Policy ID` (e.g., `POL-RET-001`, Section 2.1).
  2. Always call `get_order_status` when an `order_id` is mentioned.
  3. Act as the **Smart Operations Gatekeeper**: guide the store employee step-by-step on POS self-service when policy allows, or proactively call `create_ops_escalation_ticket` when an escalation trigger in `SOP-OPS-007` is met.
  4. **Strict Abstention Guardrail**: If a question is outside the scope of NovaMart store operations, product policies, and orders (such as HR, payroll, PTO/vacation, employee discounts, or POS/IT hardware troubleshooting), explicitly decline to answer, state that the information is not part of the store operations knowledge base, and never fabricate policies.

## 8. Evaluation & Deliverables Specification (`evals/`, `PRODUCTION_NOTE.md`, `README.md`)

### 8.1. Automated Evaluation (`evals/test_cases.json` & `evals/run_eval.py`)
Create an evaluation suite with at least 8 representative test scenarios covering:
1. Pure policy grounding with numeric calculation (e.g., restocking fee on opened electronics).
2. No-receipt return rules and store credit cap (`$50.00`).
3. Live order lookup + self-service resolution (`ORD-1004`, damaged item `<= $150`).
4. Live order lookup + mandatory Operations escalation ticket creation (`ORD-1005`, damaged TV `> $150`, and `ORD-1006`, lost package `> 24h`).
5. Out-of-window electronics return denial (`ORD-1003`, 23 days old) + Final Sale denial (`ORD-1008`).
6. Price match verification against third-party marketplace vs authorized retailer (`PROD-101`).
7. Out-of-scope abstention tests (e.g., employee asking about sick leave/PTO balance or fixing a jammed receipt printer).

In `evals/run_eval.py`, execute each test case against `root_agent` (using ADK's `InMemoryRunner`), capture:
- Which tools were called (`tool_trajectory`).
- Whether expected Policy IDs/keywords were cited.
- Whether the agent properly abstained on out-of-scope prompts.
- Print a clean summary table (Pass/Fail per test case and overall score) and save results to `evals/latest_results.json`.

### 8.2. Production Note (`PRODUCTION_NOTE.md` — Max 1 Page)
Concise, architectural 1-page document covering:
1. **Gemini Enterprise vs. Custom Build (Hybrid Architecture)**: Use **Gemini Enterprise** out-of-the-box for store employee identity, chat UI, and managed RAG connectors over unstructured policy repositories (Google Drive, SharePoint, Confluence), combined with a **Custom ADK Agent deployed on Vertex AI Agent Engine** registered into Gemini Enterprise for transactional order lookups, ERP/OMS tool execution, and deterministic escalation guardrails.
2. **Connecting Real Client Data**: Managed connectors via Vertex AI Search (chunking, layout parser, ACL inheritance) for documents + Apigee / Private Service Connect APIs to NovaMart's Order Management System (OMS), POS, and ServiceNow/Jira for Operations ticketing.
3. **Security & Governance**: Workforce Identity Federation, document-level ACLs, Cloud DLP for PII redaction (customer phone/address/payment data), Vertex AI Model Armor (prompt injection & jailbreak defense), and strict IAM service-account scoping.
4. **Evaluation & Quality Monitoring Over Time**: CI/CD golden-dataset regression gates via Vertex AI Gen AI Evaluation Service, OpenTelemetry trace logging to BigQuery, online sampling for groundedness/hallucination rate, and business KPI tracking (Operations ticket deflection rate and false-escalation rate).

### 8.3. Repository Documentation (`README.md`)
Clear documentation covering:
- Architecture overview and the "Smart Operations Gatekeeper" creative business solution.
- Quickstart setup (`venv`, `.env`, running via `adk run novamart_agent`, `adk web`, and `python -m evals.run_eval`).
- Key engineering decisions and explicit assumptions.
- What we would build next with more time.