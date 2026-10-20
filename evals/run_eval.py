"""NovaMart Ops Agent evaluation suite.

Two layers:
  1. Component & guardrail checks (zero API calls): retrieval ranking, order date
     math, price-match rules, ticket gatekeeper guardrails, and out-of-scope empty
     retrieval. These run offline and must always pass.
  2. Live end-to-end agent cases: each case in evals/test_cases.json is sent to
     `root_agent` through ADK's InMemoryRunner. We capture the tool trajectory,
     check cited Policy IDs / keywords, abstention behavior, and the escalation
     tickets actually persisted by the gatekeeper tool.

Free-tier friendly: LLM calls are throttled (--rpm), per-minute 429s are retried
after the server-suggested delay, and a daily-quota 429 stops the live run cleanly
(remaining cases are marked SKIPPED) while still saving results.

Usage:
    python -m evals.run_eval                       # component checks + all live cases
    python -m evals.run_eval --mode component      # offline only, no API key needed
    python -m evals.run_eval --case TC04           # one live case (repeatable / comma list)
    python -m evals.run_eval --limit 3 --rpm 5     # first 3 live cases, 5 LLM calls/min
    python -m evals.run_eval --model gemini-3.5-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

EVALS_DIR = ROOT_DIR / "evals"
CASES_PATH = EVALS_DIR / "test_cases.json"
DEFAULT_OUTPUT = EVALS_DIR / "latest_results.json"

APP_NAME = "novamart_eval"
USER_ID = "eval_store_associate"

ABSTENTION_PATTERNS = [
    r"not (?:part of|included in|covered (?:by|in)|in) (?:the |our )?(?:novamart )?(?:store )?(?:operations )?knowledge base",
    r"knowledge base",
    r"outside (?:of )?(?:the |my )?scope",
    r"out of scope",
    r"(?:can(?:no|')t|unable to|not able to) (?:help|assist|answer|provide)",
    r"don't have (?:any )?(?:information|access)",
    r"not something i can",
]
POLICY_ID_PATTERN = re.compile(r"\b(?:POL|SOP|PRD)-[A-Z]{3}-\d{3}\b")


# --------------------------------------------------------------------------- #
# Component & guardrail checks (zero API calls)
# --------------------------------------------------------------------------- #


class CheckRecorder:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, group: str, check_id: str, description: str, passed: bool, detail: str = "") -> None:
        self.results.append(
            {"group": group, "id": check_id, "description": description, "passed": bool(passed), "detail": detail}
        )


def run_component_checks(cases: dict) -> list[dict]:
    from novamart_agent.knowledge_base import retrieve_policy_sections
    from novamart_agent.tools import (
        check_product_and_competitor_price,
        create_ops_escalation_ticket,
        get_order_status,
        reset_escalation_tickets,
        search_policy_docs,
        _load_tickets,
    )

    rec = CheckRecorder()

    # 1. Retrieval ranking and out-of-scope empty retrieval.
    for rc in cases["retrieval_checks"]:
        results = retrieve_policy_sections(rc["query"], top_k=4)
        ranked = [f"{r['policy_id']}#{r['section_number']}" for r in results]
        if rc.get("expect_empty"):
            rec.check("retrieval", rc["id"], f"out-of-scope query returns nothing: {rc['query']!r}",
                      not results, f"got {ranked}")
            continue
        top = results[0] if results else None
        if "expected_top" in rc:
            ok = top is not None and top["policy_id"] == rc["expected_top"]
            if ok and "expected_top_section" in rc:
                ok = top["section_number"] == rc["expected_top_section"]
            want = rc["expected_top"] + (f"#{rc['expected_top_section']}" if "expected_top_section" in rc else "")
            rec.check("retrieval", rc["id"], f"{rc['query']!r} ranks {want} first", ok, f"got {ranked}")
        if "expected_in_top_k" in rc:
            ok = any(r["policy_id"] == rc["expected_in_top_k"] for r in results)
            rec.check("retrieval", rc["id"], f"{rc['query']!r} returns {rc['expected_in_top_k']} in top 4",
                      ok, f"got {ranked}")

    oos = search_policy_docs("sick leave PTO balance")
    rec.check("retrieval", "R13", "search_policy_docs reports NO_RESULTS for out-of-scope query",
              oos["status"] == "NO_RESULTS" and not oos["results"], f"status={oos['status']}")

    # 2. Order lookup and date math against REFERENCE_DATE = 2026-10-18.
    expected_days = {"ORD-1001": 10, "ORD-1002": 8, "ORD-1003": 23, "ORD-1004": 2,
                     "ORD-1005": 3, "ORD-1006": 3, "ORD-1008": 4}
    for order_id, days in expected_days.items():
        res = get_order_status(order_id)
        got = res.get("order", {}).get("days_since_purchase_or_delivery")
        rec.check("orders", f"O-{order_id}", f"{order_id} is {days} days since purchase/delivery",
                  res["status"] == "FOUND" and got == days, f"got {got}")

    bopis = get_order_status("ORD-1007")["order"]
    rec.check("orders", "O-BOPIS", "ORD-1007 is on hold day 4, active, 2 days remaining incl. today",
              bopis["hold_day_number"] == 4 and bopis["hold_active"] and bopis["hold_days_remaining_including_today"] == 2
              and bopis["extension_granted"] is False,
              f"day={bopis['hold_day_number']} active={bopis['hold_active']} "
              f"remaining={bopis['hold_days_remaining_including_today']}")
    lost = get_order_status("ORD-1006")["order"]
    rec.check("orders", "O-LOST", "ORD-1006 Delivered scan is > 24h old (72h)",
              lost["hours_since_delivery_scan"] == 72, f"got {lost['hours_since_delivery_scan']}")
    rec.check("orders", "O-404", "unknown order returns ORDER_NOT_FOUND",
              get_order_status("ORD-9999")["status"] == "ORDER_NOT_FOUND")
    rec.check("orders", "O-NORM", "order IDs are normalized (whitespace / lowercase)",
              get_order_status("  ord-1001 ")["status"] == "FOUND")

    # 3. Pricing: restocking fee math and POL-PRC-005 price-match rules.
    drone = check_product_and_competitor_price("PROD-202")
    fee = round(drone["product"]["price"] * 0.15, 2)
    net = round(drone["product"]["price"] - fee, 2)
    rec.check("pricing", "P-RESTOCK", "PROD-202 open-box refund: 15% fee $67.50, net $382.50",
              (fee, net) == (67.50, 382.50), f"fee={fee} net={net}")

    espresso = check_product_and_competitor_price("PROD-101")
    by_seller = {l["seller_type"]: l for l in espresso["competitor_listings"]}
    best = espresso["best_eligible_price_match"]
    rec.check("pricing", "P-101-BB", "PROD-101 Best Buy $105.00 is the eligible price match",
              best is not None and best["retailer"] == "Best Buy" and best["price"] == 105.00,
              f"best={best and (best['retailer'], best['price'])}")
    rec.check("pricing", "P-101-MKT", "PROD-101 Amazon third-party marketplace $95.00 is ineligible",
              by_seller["third_party_marketplace"]["price_match_eligible"] is False,
              by_seller["third_party_marketplace"]["eligibility_reason"])
    tv = check_product_and_competitor_price("PROD-303")
    rec.check("pricing", "P-303", "PROD-303: Black Friday promo and unauthorized retailer both ineligible",
              tv["best_eligible_price_match"] is None,
              "; ".join(l["eligibility_reason"] for l in tv["competitor_listings"]))
    rec.check("pricing", "P-202-OOS", "PROD-202: out-of-stock Walmart listing ineligible, Amazon.com first-party eligible",
              drone["best_eligible_price_match"] is not None
              and drone["best_eligible_price_match"]["retailer"] == "Amazon.com",
              f"best={drone['best_eligible_price_match'] and drone['best_eligible_price_match']['retailer']}")
    parka = check_product_and_competitor_price("PROD-606")
    rec.check("pricing", "P-606", "PROD-606 clearance / Final Sale is excluded from price match",
              parka["best_eligible_price_match"] is None)
    rec.check("pricing", "P-404", "unknown product returns PRODUCT_NOT_FOUND",
              check_product_and_competitor_price("PROD-999")["status"] == "PRODUCT_NOT_FOUND")

    # 4. Ticket gatekeeper guardrails (writes to the ticket store, reset before and after).
    reset_escalation_tickets()
    try:
        r = create_ops_escalation_ticket("ORD-1004", "DAMAGED_IN_TRANSIT_HIGH_VALUE", "POL-ORD-003",
                                         "STORE-NYC-01", "Cracked water tank on PROD-101.")
        rec.check("gatekeeper", "G-SELF", "ORD-1004 ($120 <= $150) damage ticket is REJECTED_SELF_SERVICE",
                  r["status"] == "REJECTED_SELF_SERVICE", r["status"])
        r = create_ops_escalation_ticket("ORD-1005", "DAMAGED_IN_TRANSIT_HIGH_VALUE", "POL-ORD-003",
                                         "STORE-NYC-01", "Shattered panel on PROD-303, photos attached.")
        rec.check("gatekeeper", "G-HIGH", "ORD-1005 ($1,200) damage ticket is CREATED as TKT-OPS-9001",
                  r["status"] == "CREATED" and r["ticket"]["ticket_id"] == "TKT-OPS-9001",
                  f"{r['status']} {r.get('ticket', {}).get('ticket_id')}")
        r = create_ops_escalation_ticket("ORD-1005", "DAMAGED_IN_TRANSIT_HIGH_VALUE", "POL-ORD-003",
                                         "STORE-NYC-01", "Duplicate submission.")
        rec.check("gatekeeper", "G-DUP", "duplicate ticket returns ALREADY_ESCALATED with the same ID",
                  r["status"] == "ALREADY_ESCALATED" and r["ticket"]["ticket_id"] == "TKT-OPS-9001", r["status"])
        r = create_ops_escalation_ticket("ORD-1006", "LOST_PACKAGE", "POL-ORD-003",
                                         "STORE-NYC-01", "PROD-505 not received, Delivered scan 72h ago.")
        rec.check("gatekeeper", "G-LOST", "ORD-1006 lost package (72h) is CREATED as TKT-OPS-9002",
                  r["status"] == "CREATED" and r["ticket"]["ticket_id"] == "TKT-OPS-9002",
                  f"{r['status']} {r.get('ticket', {}).get('ticket_id')}")
        r = create_ops_escalation_ticket("ORD-1001", "REFUND_REQUEST", "POL-RET-001", "STORE-NYC-01", "x")
        rec.check("gatekeeper", "G-CAT", "non-SOP-OPS-007 category is REJECTED_INVALID_INPUT",
                  r["status"] == "REJECTED_INVALID_INPUT", r["status"])
        r = create_ops_escalation_ticket("ORD-1005", "DAMAGED_IN_TRANSIT_HIGH_VALUE", "POL-RET-001",
                                         "STORE-NYC-01", "Wrong policy.")
        rec.check("gatekeeper", "G-POL", "mismatched policy_id is REJECTED_INVALID_INPUT",
                  r["status"] == "REJECTED_INVALID_INPUT", r["status"])
        r = create_ops_escalation_ticket("ORD-1005", "DAMAGED_IN_TRANSIT_HIGH_VALUE", "POL-ORD-003",
                                         "STORE-NYC-01", "   ")
        rec.check("gatekeeper", "G-EMPTY", "missing summary is REJECTED_INVALID_INPUT",
                  r["status"] == "REJECTED_INVALID_INPUT", r["status"])
        r = create_ops_escalation_ticket("ORD-9999", "LOST_PACKAGE", "POL-ORD-003", "STORE-NYC-01", "x")
        rec.check("gatekeeper", "G-404", "ticket for unknown order returns ORDER_NOT_FOUND",
                  r["status"] == "ORDER_NOT_FOUND", r["status"])
        persisted = _load_tickets()
        rec.check("gatekeeper", "G-STORE", "exactly 2 tickets persisted (rejections never write)",
                  [t["ticket_id"] for t in persisted] == ["TKT-OPS-9001", "TKT-OPS-9002"],
                  f"got {[t['ticket_id'] for t in persisted]}")
    finally:
        reset_escalation_tickets()

    return rec.results


# --------------------------------------------------------------------------- #
# Live end-to-end agent evaluation
# --------------------------------------------------------------------------- #


class DailyQuotaExhausted(Exception):
    pass


def _quota_info(exc: BaseException) -> tuple[str, float | None] | None:
    """Classify an API error as ('daily'|'minute'|'unavailable', retry_delay_s) or None."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        text = str(exc)
        code = getattr(exc, "code", None)
        if code == 429 or "RESOURCE_EXHAUSTED" in text:
            delay = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", text)
            kind = "daily" if "PerDay" in text else "minute"
            return kind, float(delay.group(1)) if delay else None
        if code == 503 or "UNAVAILABLE" in text:
            return "unavailable", None
        exc = exc.__cause__ or exc.__context__
    return None


def _make_throttle_plugin(rpm: float):
    from google.adk.plugins.base_plugin import BasePlugin

    class ThrottlePlugin(BasePlugin):
        """Spaces out every LLM call (not just every case) to stay under an RPM budget."""

        def __init__(self) -> None:
            super().__init__(name="eval_rate_limiter")
            self.min_interval = 60.0 / rpm if rpm > 0 else 0.0
            self._last_call = 0.0
            self.llm_calls = 0

        async def before_model_callback(self, *, callback_context, llm_request):
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
            self.llm_calls += 1
            return None

    return ThrottlePlugin()


def _keyword_groups_ok(text: str, groups: list[list[str]]) -> tuple[bool, list[list[str]]]:
    lowered = text.lower()
    missing = [g for g in groups if not any(alt.lower() in lowered for alt in g)]
    return not missing, missing


def _score_case(case: dict, response: str, trajectory: list[dict], tickets: list[dict]) -> dict:
    called = [t["name"] for t in trajectory]
    checks: dict[str, dict] = {}

    missing_tools = [t for t in case.get("expected_tools", []) if t not in called]
    checks["expected_tools_called"] = {"passed": not missing_tools, "detail": f"missing={missing_tools}"}

    bad_tools = [t for t in case.get("forbidden_tools", []) if t in called]
    checks["no_forbidden_tools"] = {"passed": not bad_tools, "detail": f"called={bad_tools}"}

    if case.get("expected_policy_ids"):
        cited = set(POLICY_ID_PATTERN.findall(response))
        missing_ids = [p for p in case["expected_policy_ids"] if p not in cited]
        checks["policy_ids_cited"] = {"passed": not missing_ids, "detail": f"cited={sorted(cited)} missing={missing_ids}"}

    if case.get("expected_keywords"):
        ok, missing = _keyword_groups_ok(response, case["expected_keywords"])
        checks["keywords_present"] = {"passed": ok, "detail": f"missing={missing}"}

    if case.get("expect_abstention"):
        lowered = response.lower()
        hit = next((p for p in ABSTENTION_PATTERNS if re.search(p, lowered)), None)
        checks["abstained"] = {"passed": hit is not None, "detail": f"matched={hit!r}"}
        cited = sorted(set(POLICY_ID_PATTERN.findall(response)))
        checks["no_fabricated_policy"] = {"passed": not cited, "detail": f"cited={cited}"}

    expected_ticket = case.get("expected_ticket")
    if expected_ticket is None:
        checks["no_ticket_created"] = {"passed": not tickets, "detail": f"tickets={[t['ticket_id'] for t in tickets]}"}
    else:
        match = [t for t in tickets if all(t.get(k) == v for k, v in expected_ticket.items())]
        checks["ticket_created"] = {
            "passed": len(match) == 1 and len(tickets) == 1,
            "detail": f"tickets={[(t['ticket_id'], t['issue_category'], t['policy_id']) for t in tickets]}",
        }

    checks["non_empty_response"] = {"passed": bool(response.strip()), "detail": f"{len(response)} chars"}
    return checks


async def _run_single_case(runner, agent_name: str, case: dict) -> tuple[str, list[dict]]:
    from google.genai import types

    session = await runner.session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    message = types.Content(role="user", parts=[types.Part(text=case["prompt"])])
    trajectory: list[dict] = []
    final_text = ""

    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        for call in event.get_function_calls() or []:
            trajectory.append({"name": call.name, "args": dict(call.args or {})})
        for resp in event.get_function_responses() or []:
            payload = resp.response or {}
            for entry in reversed(trajectory):
                if entry["name"] == resp.name and "result_status" not in entry:
                    entry["result_status"] = payload.get("status")
                    break
        if event.author == agent_name and event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text for p in event.content.parts if p.text and not getattr(p, "thought", False))
            if text.strip():
                final_text = text
    return final_text, trajectory


async def run_agent_cases(cases: list[dict], args) -> list[dict]:
    from google.adk.runners import InMemoryRunner

    from novamart_agent.agent import root_agent
    from novamart_agent.tools import _load_tickets, reset_escalation_tickets

    throttle = _make_throttle_plugin(args.rpm)
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME, plugins=[throttle])
    results: list[dict] = []
    quota_exhausted_reason: str | None = None

    try:
        for index, case in enumerate(cases, 1):
            base = {"id": case["id"], "scenario": case["scenario"], "prompt": case["prompt"]}
            if quota_exhausted_reason:
                results.append({**base, "status": "SKIPPED", "reason": quota_exhausted_reason})
                continue

            print(f"  [{index}/{len(cases)}] {case['id']} {case['scenario']} ...", flush=True)
            attempt = 0
            while True:
                attempt += 1
                reset_escalation_tickets()
                calls_before = throttle.llm_calls
                started = time.monotonic()
                try:
                    response, trajectory = await _run_single_case(runner, root_agent.name, case)
                except Exception as exc:  # noqa: BLE001 - classify API errors, record anything else
                    info = _quota_info(exc)
                    if info and info[0] == "daily":
                        quota_exhausted_reason = (
                            f"Daily free-tier quota exhausted for model '{root_agent.model}'. "
                            "Re-run later or pass --model with another model."
                        )
                        results.append({**base, "status": "SKIPPED", "reason": quota_exhausted_reason})
                        print(f"      ! {quota_exhausted_reason}")
                        break
                    if info and attempt <= args.max_retries:
                        delay = info[1] if info[1] is not None else min(10 * 2 ** (attempt - 1), args.max_wait)
                        delay = min(delay + 1, args.max_wait)
                        print(f"      ~ {info[0]} rate limit/unavailable; retrying in {delay:.0f}s "
                              f"(attempt {attempt}/{args.max_retries})", flush=True)
                        await asyncio.sleep(delay)
                        continue
                    results.append({**base, "status": "ERROR", "attempts": attempt,
                                    "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
                    print(f"      x ERROR: {type(exc).__name__}: {str(exc)[:200]}")
                    break

                tickets = _load_tickets()
                checks = _score_case(case, response, trajectory, tickets)
                passed = all(c["passed"] for c in checks.values())
                results.append({
                    **base,
                    "status": "PASS" if passed else "FAIL",
                    "tool_trajectory": trajectory,
                    "tickets_created": tickets,
                    "checks": checks,
                    "response": response,
                    "latency_s": round(time.monotonic() - started, 2),
                    "llm_calls": throttle.llm_calls - calls_before,
                    "attempts": attempt,
                })
                tools = " -> ".join(t["name"] for t in trajectory) or "(no tools)"
                failed = [name for name, c in checks.items() if not c["passed"]]
                print(f"      {'PASS' if passed else 'FAIL'}  tools: {tools}"
                      + (f"  failed: {', '.join(failed)}" if failed else ""))
                if args.verbose:
                    print("      --- response ---\n      " + response.replace("\n", "\n      "))
                break
            if args.case_delay and index < len(cases):
                await asyncio.sleep(args.case_delay)
    finally:
        reset_escalation_tickets()
        await runner.close()
    return results


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _print_component_table(results: list[dict]) -> None:
    print("\nComponent & guardrail checks (0 API calls)")
    print(f"  {'ID':<10} {'Group':<11} {'Result':<6} Description")
    print("  " + "-" * 92)
    for r in results:
        print(f"  {r['id']:<10} {r['group']:<11} {'PASS' if r['passed'] else 'FAIL':<6} {r['description']}")
        if not r["passed"]:
            print(f"  {'':<29}-> {r['detail']}")


def _print_agent_table(results: list[dict]) -> None:
    print("\nLive agent cases")
    print(f"  {'ID':<6} {'Result':<8} {'LLM':>4} {'Secs':>6}  Scenario")
    print("  " + "-" * 92)
    for r in results:
        llm = str(r.get("llm_calls", "-"))
        secs = f"{r['latency_s']:.1f}" if "latency_s" in r else "-"
        print(f"  {r['id']:<6} {r['status']:<8} {llm:>4} {secs:>6}  {r['scenario']}")


def _summarize(component: list[dict], agent: list[dict]) -> dict:
    executed = [r for r in agent if r["status"] in ("PASS", "FAIL", "ERROR")]
    summary = {
        "component_passed": sum(r["passed"] for r in component),
        "component_total": len(component),
        "agent_passed": sum(r["status"] == "PASS" for r in executed),
        "agent_executed": len(executed),
        "agent_skipped": sum(r["status"] == "SKIPPED" for r in agent),
        "tool_trajectory_accuracy": None,
        "abstention_accuracy": None,
    }
    traj = [r for r in executed if "checks" in r]
    if traj:
        ok = sum(r["checks"]["expected_tools_called"]["passed"] and r["checks"]["no_forbidden_tools"]["passed"]
                 for r in traj)
        summary["tool_trajectory_accuracy"] = round(ok / len(traj), 3)
    abst = [r for r in traj if "abstained" in r["checks"]]
    if abst:
        summary["abstention_accuracy"] = round(sum(r["checks"]["abstained"]["passed"] for r in abst) / len(abst), 3)
    return summary


def _select_cases(all_cases: list[dict], args) -> list[dict]:
    selected = all_cases
    if args.case:
        wanted = [c.strip().upper() for arg in args.case for c in arg.split(",") if c.strip()]
        known = {c["id"] for c in all_cases}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            sys.exit(f"Unknown case id(s): {', '.join(unknown)}. Available: {', '.join(sorted(known))}")
        selected = [c for c in all_cases if c["id"] in wanted]
    if args.limit is not None:
        selected = selected[: args.limit]
    return selected


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the NovaMart Ops Agent.")
    parser.add_argument("--mode", choices=["all", "component", "agent"], default="all",
                        help="component = offline checks only; agent = live cases only (default: all)")
    parser.add_argument("--case", action="append", help="Live case ID(s) to run, e.g. TC04 or TC04,TC05")
    parser.add_argument("--limit", type=int, help="Run only the first N selected live cases")
    parser.add_argument("--model", help="Override GEMINI_MODEL for this run")
    parser.add_argument("--rpm", type=float, default=8.0,
                        help="Max LLM calls per minute across the run (default 8; 0 disables throttling)")
    parser.add_argument("--case-delay", type=float, default=0.0, help="Extra seconds to wait between cases")
    parser.add_argument("--max-retries", type=int, default=3, help="Retries per case on per-minute 429/503")
    parser.add_argument("--max-wait", type=float, default=90.0, help="Cap (s) on a single retry wait")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Results JSON path")
    parser.add_argument("--verbose", action="store_true", help="Print full agent responses")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Quota errors are classified and reported by this script; silence ADK's duplicate tracebacks.
    logging.getLogger("google_genai").setLevel(logging.CRITICAL)
    logging.getLogger("google_adk").setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
    if args.model:
        os.environ["GEMINI_MODEL"] = args.model
    if args.mode == "component":
        # The agent module reads GEMINI_MODEL at import; offline checks never call it.
        os.environ.setdefault("GEMINI_MODEL", "offline-component-checks")
    elif not os.environ.get("GEMINI_MODEL") or not os.environ.get("GOOGLE_API_KEY"):
        sys.exit("GEMINI_MODEL and GOOGLE_API_KEY must be set (see .env.example), or use --mode component.")

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    selected = _select_cases(cases["agent_cases"], args)

    component_results: list[dict] = []
    agent_results: list[dict] = []

    print(f"NovaMart Ops Agent eval | reference date {cases['reference_date']} | mode={args.mode}")
    if args.mode in ("all", "component"):
        component_results = run_component_checks(cases)
        _print_component_table(component_results)

    if args.mode in ("all", "agent"):
        print(f"\nRunning {len(selected)} live case(s) on model '{os.environ['GEMINI_MODEL']}' "
              f"(throttle: {args.rpm:g} LLM calls/min)")
        agent_results = asyncio.run(run_agent_cases(selected, args))
        _print_agent_table(agent_results)

    summary = _summarize(component_results, agent_results)
    print("\nSummary")
    if component_results:
        print(f"  Component checks : {summary['component_passed']}/{summary['component_total']}")
    if args.mode in ("all", "agent"):
        print(f"  Live agent cases : {summary['agent_passed']}/{summary['agent_executed']} passed"
              + (f" ({summary['agent_skipped']} skipped)" if summary["agent_skipped"] else ""))
        if summary["tool_trajectory_accuracy"] is not None:
            print(f"  Tool trajectory  : {summary['tool_trajectory_accuracy']:.0%}")
        if summary["abstention_accuracy"] is not None:
            print(f"  Abstention       : {summary['abstention_accuracy']:.0%}")
    total = summary["component_total"] + summary["agent_executed"]
    passed = summary["component_passed"] + summary["agent_passed"]
    print(f"  Overall score    : {passed}/{total}" + (f" ({passed / total:.0%})" if total else ""))

    output = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reference_date": cases["reference_date"],
        "mode": args.mode,
        "model": os.environ.get("GEMINI_MODEL") if args.mode != "component" else None,
        "summary": summary,
        "component_checks": component_results,
        "agent_cases": agent_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nResults saved to {args.output.relative_to(ROOT_DIR) if args.output.is_relative_to(ROOT_DIR) else args.output}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
