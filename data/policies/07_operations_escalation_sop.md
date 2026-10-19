# NovaMart Operations Escalation Standard Operating Procedure

- **Policy ID:** SOP-OPS-007
- **Effective Date:** 2026-01-15
- **Owner:** NovaMart Central Operations
- **Summary:** Defines the boundary between Store Self-Service and Operations Escalation, and lists the required fields for every Operations Escalation Ticket.

## 1. Purpose
1.1. Store associates resolve every case that written policy already covers. Operations handles only cases that require central investigation or authority.
1.2. Before escalating, the associate must identify the governing Policy ID and confirm the case meets a trigger in Section 3.

## 2. DO NOT Escalate to Operations (Store Self-Service)
Resolve these cases in-store, at the POS, following the cited policy:
2.1. Standard returns within the return window (`POL-RET-001`, `POL-ELC-002`).
2.2. Items damaged in transit with a value **<= $150.00** (`POL-ORD-003`).
2.3. BOPIS **48-hour hold extensions** requested before expiration (`POL-ORD-003`).
2.4. Competitor price checks and post-purchase price adjustments (`POL-PRC-005`).
2.5. Out-of-window denials and restricted-item denials clearly covered by policy (`POL-RET-001`, `POL-ELC-002`, `POL-RES-004`).
2.6. Lifetime Cast Iron Warranty replacements on `PROD-404` (`PRD-CAT-006`).

## 3. MUST Escalate to Operations via Ticket
Create an Operations Escalation Ticket for:
3.1. Items damaged in transit with a value **> $150.00** (`POL-ORD-003`, `POL-RES-004`). Attach photos.
3.2. Lost packages when **more than 24 hours** have passed since the carrier `Delivered` scan (`POL-ORD-003`).
3.3. **Serial number mismatches** on high-value electronics, such as `PROD-303` (`PRD-CAT-006`).
3.4. **Inventory sync errors** on BOPIS orders (`POL-ORD-003`).

## 4. Required Ticket Fields
Every ticket must include all of the following fields:
| Field | Description |
|---|---|
| `order_id` | The NovaMart online or in-store order identifier. |
| `issue_category` | One of the categories in Section 5. |
| `policy_id` | The governing policy, e.g., `POL-ORD-003` or `PRD-CAT-006`. |
| `store_id` | The identifier of the store creating the ticket. |
| `summary` | A short factual description: product ID, value, what happened, and evidence collected. |
Tickets missing any required field are returned to the store and not processed.

## 5. Issue Categories
5.1. `DAMAGED_IN_TRANSIT_HIGH_VALUE` — damaged item over $150.00.
5.2. `LOST_PACKAGE` — Delivered scan older than 24 hours, item not received.
5.3. `SERIAL_NUMBER_MISMATCH` — chassis, box, and receipt serials do not match.
5.4. `BOPIS_INVENTORY_SYNC_ERROR` — order shows ready but stock is missing or mismatched.

## 6. After Escalation
6.1. Give the customer the ticket reference and explain that Operations will contact them.
6.2. Do not promise a refund, replacement, or timeline at the register.
6.3. Keep any damaged item at the store until Operations confirms its disposition. Damaged `PROD-202` units must follow the LiPo ground hazmat rules in `PRD-CAT-006`.
