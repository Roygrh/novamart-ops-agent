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