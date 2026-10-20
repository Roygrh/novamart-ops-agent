"""Agent tools for NovaMart store operations.

Google ADK builds each tool's schema from the function signature and docstring,
so keep both precise. All date math uses the fixed REFERENCE_DATE so results are
deterministic regardless of when the repository is executed.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from .knowledge_base import retrieve_policy_sections

REFERENCE_DATE = "2026-10-18"

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "mock_db"
ORDERS_PATH = DATA_DIR / "orders.json"
INVENTORY_PATH = DATA_DIR / "inventory.json"
TICKETS_PATH = DATA_DIR / "escalation_tickets.json"

DAMAGE_VALUE_THRESHOLD = 150.00
LOST_PACKAGE_WAIT_HOURS = 24
TICKET_ID_START = 9001

# SOP-OPS-007 Section 5 issue categories, mapped to the policy IDs that may govern each.
ESCALATION_CATEGORIES: dict[str, set[str]] = {
    "DAMAGED_IN_TRANSIT_HIGH_VALUE": {"POL-ORD-003", "POL-RES-004"},
    "LOST_PACKAGE": {"POL-ORD-003"},
    "SERIAL_NUMBER_MISMATCH": {"PRD-CAT-006", "POL-ELC-002"},
    "BOPIS_INVENTORY_SYNC_ERROR": {"POL-ORD-003"},
}

# POL-PRC-005 Section 4 exclusions, keyed by inventory `promotion_type`.
EXCLUDED_PROMOTIONS = {
    "flash_sale": "Flash sales and lightning deals are excluded (POL-PRC-005, Section 4.2).",
    "black_friday": "Black Friday promotions are excluded (POL-PRC-005, Section 4.3).",
    "cyber_monday": "Cyber Monday promotions are excluded (POL-PRC-005, Section 4.3).",
    "bundle": "Bundle offers are excluded (POL-PRC-005, Section 4.4).",
}

_tickets_lock = threading.Lock()


def _load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _reference_date() -> date:
    return date.fromisoformat(REFERENCE_DATE)


def _find_order(order_id: str) -> dict | None:
    normalized = order_id.strip().upper()
    return next((o for o in _load_json(ORDERS_PATH) if o["order_id"] == normalized), None)


def _load_tickets() -> list[dict]:
    if not TICKETS_PATH.exists():
        return []
    text = TICKETS_PATH.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else []


def reset_escalation_tickets() -> None:
    """Clear the ticket store. Not exposed to the agent; used by evals and tests."""
    with _tickets_lock:
        TICKETS_PATH.write_text("[]\n", encoding="utf-8", newline="\n")


def search_policy_docs(query: str) -> dict:
    """Searches NovaMart's official policy and product documentation.

    Use this before answering any question about returns, refunds, restocking
    fees, damaged or lost orders, BOPIS holds, Final Sale or restricted items,
    price matching, product handling, or Operations escalation rules.

    Args:
        query: Natural-language search query. Include exact identifiers when
            known (e.g. "POL-ELC-002 restocking fee", "PROD-303 serial number").

    Returns:
        A dict with `status` ("SUCCESS" or "NO_RESULTS") and `results`, a list of
        matching sections, each with `policy_id`, `filename`, `section_number`,
        `section_title`, and `content`. Cite answers as "<policy_id>, Section <n>".
    """
    sections = retrieve_policy_sections(query, top_k=4)
    if not sections:
        return {
            "status": "NO_RESULTS",
            "query": query,
            "results": [],
            "message": (
                "No official NovaMart policy covers this query. Do not invent an answer; "
                "tell the employee the information is not in the store operations knowledge base."
            ),
        }
    return {
        "status": "SUCCESS",
        "query": query,
        "results": [
            {
                "policy_id": s["policy_id"],
                "filename": s["filename"],
                "document_title": s["title"],
                "section_number": s["section_number"],
                "section_title": s["section_title"],
                "content": s["content"],
            }
            for s in sections
        ],
    }


def get_order_status(order_id: str) -> dict:
    """Looks up a live NovaMart order by its order ID.

    Call this whenever an employee mentions an order ID. Returns the customer,
    store, channel, item (product_id, name, category, price), status, reported
    condition, receipt flag, and the computed number of days since purchase or
    delivery (relative to the system reference date). BOPIS orders also include
    hold-window details; lost-package cases include hours since the carrier
    Delivered scan.

    Args:
        order_id: The order identifier, e.g. "ORD-1001".

    Returns:
        A dict with `status` "FOUND" and an `order` object, or `status`
        "ORDER_NOT_FOUND" if no such order exists.
    """
    order = _find_order(order_id)
    if order is None:
        return {
            "status": "ORDER_NOT_FOUND",
            "order_id": order_id,
            "message": (
                f"No order with ID '{order_id}' exists. Ask the employee to re-check the "
                "order ID on the receipt or POS."
            ),
        }

    today = _reference_date()
    result = dict(order)
    result["reference_date"] = REFERENCE_DATE

    if "purchase_or_delivery_date" in order:
        start = date.fromisoformat(order["purchase_or_delivery_date"])
        result["days_since_purchase_or_delivery"] = (today - start).days

    if order.get("channel") == "BOPIS" and "ready_for_pickup_date" in order:
        ready = date.fromisoformat(order["ready_for_pickup_date"])
        expires = date.fromisoformat(order["hold_expires_end_of_date"])
        result["hold_day_number"] = (today - ready).days
        result["hold_days_remaining_including_today"] = (expires - today).days + 1
        result["hold_active"] = today <= expires

    return {"status": "FOUND", "order": result}


def _evaluate_price_match(listing: dict, product: dict, authorized: list[str]) -> tuple[bool, str]:
    if product.get("clearance") or product.get("final_sale"):
        return False, "Clearance and Final Sale items are excluded from price matching (POL-PRC-005, Section 4.5)."
    if listing["retailer"] not in authorized:
        return False, f"{listing['retailer']} is not an authorized competitor (POL-PRC-005, Section 2)."
    if listing.get("seller_type") != "first_party":
        return False, (
            "Third-party marketplace sellers are excluded; Amazon listings must be Sold and "
            "Shipped by Amazon.com (POL-PRC-005, Sections 2.1 and 4.1)."
        )
    if not listing.get("in_stock"):
        return False, "Competitor item must be currently in stock (POL-PRC-005, Section 3.3)."
    promotion = listing.get("promotion_type")
    if promotion in EXCLUDED_PROMOTIONS:
        return False, EXCLUDED_PROMOTIONS[promotion]
    if listing["price"] >= product["price"]:
        return False, "Competitor price is not lower than NovaMart's price; no match needed."
    return True, "Meets all POL-PRC-005 requirements (authorized retailer, first-party seller, in stock, no excluded promotion)."


def check_product_and_competitor_price(product_id: str, store_id: str = "STORE-NYC-01") -> dict:
    """Looks up product details, local store stock, and competitor prices.

    Use this to confirm a product's official price, category and return window,
    check whether a replacement unit is in stock at the store, and verify
    competitor price match requests under POL-PRC-005. Each competitor listing
    includes a deterministic `price_match_eligible` flag and the reason.

    Args:
        product_id: The NovaMart product identifier, e.g. "PROD-101".
        store_id: The store whose stock to check. Defaults to "STORE-NYC-01".

    Returns:
        A dict with `status` "FOUND" plus `product`, `store_stock`,
        `competitor_listings`, and `best_eligible_price_match` (or null), or
        `status` "PRODUCT_NOT_FOUND".
    """
    inventory = _load_json(INVENTORY_PATH)
    pid = product_id.strip().upper()
    sid = store_id.strip().upper()
    product = inventory["products"].get(pid)
    if product is None:
        return {
            "status": "PRODUCT_NOT_FOUND",
            "product_id": product_id,
            "message": f"No product with ID '{product_id}' exists in the NovaMart catalog.",
        }

    store = inventory["store_inventory"].get(sid)
    if store is None:
        store_stock = {"store_id": sid, "status": "STORE_NOT_FOUND"}
    else:
        stock = store.get(pid, {"on_hand": 0, "reserved_for_bopis": 0})
        available = stock["on_hand"] - stock["reserved_for_bopis"]
        store_stock = {
            "store_id": sid,
            "status": "FOUND",
            "on_hand": stock["on_hand"],
            "reserved_for_bopis": stock["reserved_for_bopis"],
            "available_for_sale_or_replacement": available,
            "in_stock": available > 0,
        }

    authorized = inventory["authorized_competitors"]
    listings = []
    for listing in inventory["competitor_listings"].get(pid, []):
        eligible, reason = _evaluate_price_match(listing, product, authorized)
        listings.append(
            {
                **listing,
                "price_match_eligible": eligible,
                "eligibility_reason": reason,
                "price_difference": round(product["price"] - listing["price"], 2),
            }
        )

    eligible_listings = [l for l in listings if l["price_match_eligible"]]
    best = min(eligible_listings, key=lambda l: l["price"]) if eligible_listings else None

    return {
        "status": "FOUND",
        "reference_date": REFERENCE_DATE,
        "product": {"product_id": pid, **product},
        "store_stock": store_stock,
        "authorized_competitors": authorized,
        "competitor_listings": listings,
        "best_eligible_price_match": best,
    }


def create_ops_escalation_ticket(
    order_id: str,
    issue_category: str,
    policy_id: str,
    store_id: str,
    summary: str,
) -> dict:
    """Creates an Operations Escalation Ticket after enforcing SOP-OPS-007 guardrails.

    Only call this when an SOP-OPS-007 Section 3 escalation trigger is met. Cases
    that store associates can resolve at the POS (e.g. damaged items valued at
    $150.00 or less) are rejected with self-service instructions instead.

    Args:
        order_id: The affected order ID, e.g. "ORD-1005".
        issue_category: Exactly one of "DAMAGED_IN_TRANSIT_HIGH_VALUE",
            "LOST_PACKAGE", "SERIAL_NUMBER_MISMATCH", "BOPIS_INVENTORY_SYNC_ERROR".
        policy_id: The governing policy, e.g. "POL-ORD-003" or "PRD-CAT-006".
        store_id: The store creating the ticket, e.g. "STORE-NYC-01".
        summary: Short factual description: product ID, value, what happened,
            and evidence collected (e.g. photos).

    Returns:
        A dict whose `status` is "CREATED" (with `ticket`), "ALREADY_ESCALATED"
        (with the existing `ticket`), "REJECTED_SELF_SERVICE",
        "REJECTED_WAIT_24H", "REJECTED_INVALID_INPUT", or "ORDER_NOT_FOUND".
    """
    category = issue_category.strip().upper()
    pid = policy_id.strip().upper()
    sid = store_id.strip().upper()

    missing = [
        name
        for name, value in (
            ("order_id", order_id),
            ("issue_category", issue_category),
            ("policy_id", policy_id),
            ("store_id", store_id),
            ("summary", summary),
        )
        if not value or not value.strip()
    ]
    if missing:
        return {
            "status": "REJECTED_INVALID_INPUT",
            "message": f"Missing required ticket fields per SOP-OPS-007 Section 4: {', '.join(missing)}.",
        }

    if category not in ESCALATION_CATEGORIES:
        return {
            "status": "REJECTED_INVALID_INPUT",
            "message": (
                f"'{issue_category}' is not an SOP-OPS-007 escalation category. Allowed: "
                f"{', '.join(ESCALATION_CATEGORIES)}. If none applies, the case is store self-service."
            ),
        }

    allowed_policies = ESCALATION_CATEGORIES[category]
    if pid not in allowed_policies:
        return {
            "status": "REJECTED_INVALID_INPUT",
            "message": (
                f"policy_id '{policy_id}' does not govern {category}. "
                f"Use one of: {', '.join(sorted(allowed_policies))}."
            ),
        }

    order = _find_order(order_id)
    if order is None:
        return {
            "status": "ORDER_NOT_FOUND",
            "order_id": order_id,
            "message": f"Cannot create a ticket: order '{order_id}' does not exist.",
        }

    item = order["item"]
    if category == "DAMAGED_IN_TRANSIT_HIGH_VALUE" and item["price"] <= DAMAGE_VALUE_THRESHOLD:
        return {
            "status": "REJECTED_SELF_SERVICE",
            "order_id": order["order_id"],
            "item_value": item["price"],
            "policy_references": ["POL-ORD-003, Section 3.2", "SOP-OPS-007, Section 2.2"],
            "message": (
                f"{item['product_id']} ({item['name']}) is valued at ${item['price']:.2f}, which is "
                f"<= ${DAMAGE_VALUE_THRESHOLD:.2f}. Do NOT escalate. The store associate is authorized "
                "to process an immediate replacement or full refund at the POS."
            ),
            "pos_steps": [
                "Look up the order_id in the POS and confirm the item and price.",
                "Inspect the damage and take photos for the store record.",
                "Select 'Transit Damage — Replacement' or 'Transit Damage — Refund' in the POS.",
            ],
        }

    if category == "LOST_PACKAGE":
        hours = order.get("hours_since_delivery_scan")
        if hours is not None and hours <= LOST_PACKAGE_WAIT_HOURS:
            return {
                "status": "REJECTED_WAIT_24H",
                "order_id": order["order_id"],
                "hours_since_delivery_scan": hours,
                "policy_references": ["POL-ORD-003, Section 4.2"],
                "message": (
                    f"Only {hours} hours have passed since the carrier Delivered scan. Ask the "
                    "customer to wait 24 hours before a ticket can be created."
                ),
            }

    with _tickets_lock:
        tickets = _load_tickets()
        existing = next(
            (t for t in tickets if t["order_id"] == order["order_id"] and t["issue_category"] == category),
            None,
        )
        if existing:
            return {
                "status": "ALREADY_ESCALATED",
                "ticket": existing,
                "message": (
                    f"Ticket {existing['ticket_id']} already exists for this order and issue. "
                    "Give the customer that reference; do not create a duplicate."
                ),
            }

        ticket = {
            "ticket_id": f"TKT-OPS-{TICKET_ID_START + len(tickets)}",
            "order_id": order["order_id"],
            "issue_category": category,
            "policy_id": pid,
            "store_id": sid,
            "summary": summary.strip(),
            "product_id": item["product_id"],
            "item_value": item["price"],
            "customer_name": order["customer_name"],
            "created_on": REFERENCE_DATE,
            "status": "OPEN",
            "assigned_to": "NovaMart Central Operations",
        }
        tickets.append(ticket)
        TICKETS_PATH.write_text(json.dumps(tickets, indent=2) + "\n", encoding="utf-8", newline="\n")

    return {
        "status": "CREATED",
        "ticket": ticket,
        "next_steps": [
            f"Give the customer ticket reference {ticket['ticket_id']} and explain Operations will contact them.",
            "Do not promise a refund, replacement, or timeline at the register (SOP-OPS-007, Section 6.2).",
            "Keep any damaged item at the store until Operations confirms its disposition (SOP-OPS-007, Section 6.3).",
        ],
    }
