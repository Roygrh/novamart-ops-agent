# NovaMart Damaged, Lost, and BOPIS Orders Policy

- **Policy ID:** POL-ORD-003
- **Effective Date:** 2026-01-15
- **Owner:** NovaMart Operations — Fulfillment & Last-Mile Delivery
- **Summary:** Defines BOPIS hold periods and extensions, the $150.00 value threshold for damaged-in-transit resolution, and the 24-hour rule for packages marked Delivered but not received.

## 1. Scope
1.1. This policy applies to online orders shipped to customers and to BOPIS (Buy Online, Pick Up In Store) orders.
1.2. All steps require a valid `order_id`.

## 2. BOPIS Holds
2.1. Orders with status `Ready for Pickup` are held at the customer service desk for **5 calendar days**.
2.2. If the order is not picked up by the **end of Day 5**, it is automatically canceled and refunded to the original payment method.
2.3. Store associates may grant a **one-time 48-hour hold extension**, but only if the customer requests it **before** the hold expires.
2.4. Only one extension is allowed per order. Expired or already-extended orders cannot be extended.
2.5. Hold extensions are handled in-store and must not be escalated to Operations.
2.6. If the POS shows an order as `Ready for Pickup` but the item cannot be located or the system stock does not match the shelf (inventory sync error), the associate must create an Operations Escalation Ticket (see `SOP-OPS-007`).

## 3. Damaged in Transit
3.1. The item value used for this rule is the price paid per item, as shown on the order.
3.2. **Item value <= $150.00:** Store associates are authorized to process an **immediate in-store replacement or full refund** at the register, without contacting Operations.
   1. Look up the `order_id` and confirm the item and price.
   2. Inspect the damage and take photos for the store record.
   3. Select "Transit Damage — Replacement" or "Transit Damage — Refund" in the POS.
3.3. **Item value > $150.00:** Store associates **CANNOT override at the register**. They must create an **Operations Escalation Ticket** that includes:
   - photos of the damaged item and packaging, and
   - the `order_id`, plus all required fields in `SOP-OPS-007`.
3.4. Examples:
   - `PROD-101` ($120.00) or `PROD-404` ($85.00) arrives cracked: resolve in-store.
   - `PROD-202` ($450.00) or `PROD-303` ($1,200.00) arrives damaged: escalate via ticket.
3.5. Damaged `PROD-202` units contain a LiPo battery and must use a ground hazmat return label (see `PRD-CAT-006`).

## 4. Lost Packages ("Delivered" but Not Received)
4.1. Always check the carrier tracking status and the timestamp of the `Delivered` scan.
4.2. **Less than 24 hours since the Delivered scan:** Ask the customer to wait 24 hours, because carriers sometimes scan packages as delivered early. Do not create a ticket yet.
4.3. **More than 24 hours since the Delivered scan:** The associate must create an **Operations Escalation Ticket** for a carrier claim investigation.
4.4. Store associates must not issue a refund or replacement for lost packages at the register; resolution is decided by Operations after the carrier claim.

## 5. Restricted and Final Sale Items
5.1. Transit damage on Final Sale or restricted items is governed by the exception in `POL-RES-004` (7-day reporting window).
5.2. When that exception applies, the value threshold in Section 3 still determines whether the item is resolved in-store or escalated.

## 6. Related Policies
6.1. `POL-RES-004` — Final Sale and Restricted Items.
6.2. `PRD-CAT-006` — Product Catalog and Handling.
6.3. `SOP-OPS-007` — Operations Escalation SOP.
