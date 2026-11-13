"""Command line.

  run-once     check every enabled target once. This is what cron and GitHub Actions call.
  demo-alert   force one in-stock transition and push a single ntfy message. No TinyFish call.
  estimate     print the daily cost sketch from the published price list. No network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rtx5090_watcher.check import demo_transition, run_once, utc_now
from rtx5090_watcher.config import load_config
from rtx5090_watcher.models import CheckResult
from rtx5090_watcher.notify import notifier_from_env
from rtx5090_watcher.pricing import (
    RUNS_PER_DAY_EVERY_10_MIN,
    BudgetGuard,
    count_calls_until_day_cap,
    fetch_urls_per_day,
    format_micros,
    format_price_cents,
    scenario_agent_micros,
)
from rtx5090_watcher.state import StateStore
from rtx5090_watcher.tinyfish import TinyFishClient, TinyFishError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtx5090-watch")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run-once", help="Check the configured targets once")
    _add_common(run)
    run.add_argument("--target", action="append", dest="targets", help="Check only this target id. Repeatable.")

    demo = sub.add_parser("demo-alert", help="Send one test alert from a forced in-stock transition")
    _add_common(demo)
    demo.add_argument("--topic", help="ntfy topic. Defaults to NTFY_TOPIC.")
    demo.add_argument("--target-id", default="bestbuy-fe")

    estimate = sub.add_parser("estimate", help="Print the cost sketch. Does not call TinyFish.")
    _add_common(estimate)
    estimate.add_argument("--agent-targets", type=int, default=None, help="How many targets escalate. Default: every target whose tier can use the Agent, minus one Fetch success.")
    estimate.add_argument(
        "--steps",
        type=int,
        default=4,
        help="Agent steps per finished escalation. A manual Best Buy FE read was 4 on 13 Nov 2026. That day's watcher pass was cancelled at 8.",
    )

    args = parser.parse_args(argv)
    try:
        config = load_config(Path(args.config))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Could not load {args.config}: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "estimate":
        print(render_estimate(config, args.agent_targets, args.steps))
        return 0
    if args.cmd == "demo-alert":
        return _demo(config, args)
    return _run(config, args)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="targets.json")
    parser.add_argument("--state", default="state/watcher.json")


def _run(config, args) -> int:
    api_key = os.environ.get("TINYFISH_API_KEY", "").strip()
    if not api_key:
        print(
            "TINYFISH_API_KEY is not set. Fetch and Agent both require it as the X-API-Key header.\n"
            "Create a key at https://agent.tinyfish.ai/api-keys then run:\n"
            "  PYTHONPATH=src TINYFISH_API_KEY=your_key python3 -m rtx5090_watcher run-once",
            file=sys.stderr,
        )
        return 2
    notifier = notifier_from_env()
    if not notifier.topic:
        print("NTFY_TOPIC is not set. A qualifying transition will be printed, not pushed.", file=sys.stderr)
    store = StateStore(Path(args.state))
    client = TinyFishClient(api_key)
    try:
        results = run_once(config, client, notifier, store, utc_now(), args.targets)
    except TinyFishError as exc:
        print(str(exc), file=sys.stderr)
        if exc.status == 402:
            print("Add funds at https://agent.tinyfish.ai/wallet and run again.", file=sys.stderr)
        return 2 if exc.status in {401, 402} else 1
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_results(results))
    _print_unsent(results)
    return 0


def _demo(config, args) -> int:
    topic = args.topic if args.topic is not None else os.environ.get("NTFY_TOPIC", "").strip()
    notifier = notifier_from_env(topic or None)
    state_path = Path(args.state)
    if args.state == "state/watcher.json":
        state_path = Path("state/demo-alert.json")
    try:
        target = config.by_id(args.target_id)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    store = StateStore(state_path)
    result = demo_transition(target, store, notifier, utc_now())
    print(render_results([result]))
    if result.alert_status == "sent":
        print(json.dumps({"ntfy": notifier.last_response}, indent=2))
        return 0
    _print_unsent([result])
    if result.alert_status == "failed":
        print("ntfy did not accept the message. Payload is above.", file=sys.stderr)
        return 1
    print("Set NTFY_TOPIC or pass --topic to deliver the payload above.", file=sys.stderr)
    return 2


def render_results(results: list[CheckResult]) -> str:
    header = f"{'target':<22} {'tier':<8} {'in_stock':<9} {'price':>12} {'latency_ms':>10} {'est_cost':>10}  alert"
    lines = [header, "-" * len(header)]
    total = 0
    public = []
    for result in results:
        total += result.estimated_cost_micros
        price = format_price_cents(result.price_cents)
        latency = "—" if result.latency_ms is None else str(result.latency_ms)
        lines.append(
            f"{result.target_id:<22} {result.tier:<8} {str(result.in_stock):<9} {price:>12} "
            f"{latency:>10} {format_micros(result.estimated_cost_micros):>10}  {result.alert_status}"
        )
        public.append(
            {
                "target": result.target_id,
                "retailer": result.retailer,
                "tier": result.tier,
                "escalated": result.escalated,
                "fetch_error": result.fetch_error,
                "in_stock": result.in_stock,
                "price": None if result.price_cents is None else result.price_cents / 100,
                "qualifies": result.qualifies,
                "alert": result.alert_status,
                "latency_ms": result.latency_ms,
                "estimated_cost_usd": result.estimated_cost_micros / 1_000_000,
                "steps": result.steps,
                "detail": result.detail,
                "url": result.url,
            }
        )
    lines.append(f"estimated cost this run: {format_micros(total)}")
    lines.append("--- json ---")
    lines.append(json.dumps(public, indent=2))
    return "\n".join(lines)


def render_estimate(config, agent_targets: int | None, steps: int) -> str:
    enabled = [target for target in config.targets if target.enabled]
    fetch_targets = len(enabled)
    can_escalate = [target for target in enabled if target.tier != "fetch"]
    if agent_targets is None:
        # Newegg's in-stock search was readable by Fetch on 13 Nov 2026.
        # The sketch assumes that one Fetch success and everyone else escalates.
        agent_targets = max(0, len(can_escalate) - 1)
    urls = fetch_urls_per_day(fetch_targets)
    per_run = scenario_agent_micros(agent_targets, steps)
    per_day_uncapped = per_run * RUNS_PER_DAY_EVERY_10_MIN

    def factory(spent: int) -> BudgetGuard:
        return BudgetGuard(
            config.budgets.per_run_micros,
            config.budgets.per_day_micros,
            config.budgets.assumed_agent_steps,
            spent,
        )

    calls = count_calls_until_day_cap(factory, agent_targets, steps) if agent_targets else 0
    capped = scenario_agent_micros(calls, steps)
    cancelled_each = scenario_agent_micros(1, 8)
    return "\n".join(
        [
            f"schedule: every 10 minutes ({RUNS_PER_DAY_EVERY_10_MIN} runs/day)",
            f"targets: {fetch_targets} Fetch URLs/run, {agent_targets} of them assumed to escalate",
            "observed 13 Nov 2026 Fetch: Newegg in-stock search returned 13 graphics cards, cheapest $6,709.99",
            "observed 13 Nov 2026 Agent: bestbuy-fe, nvidia-marketplace, bestbuy-astral, and bh-pny-5090 each ended "
            f"Cancelled: max_duration_seconds elapsed at 8 steps after about 119s ({format_micros(cancelled_each)} each) "
            "and returned no stock data",
            "observed 13 Nov 2026: mode=strict returned HTTP 403 (beta) on every Agent call, so prefer_strict_mode defaults off",
            "observed 13 Nov 2026 manual Best Buy FE run: 4 steps, about $0.0640, $1,999.99, Out of Stock",
            f"assumed Agent steps when a run actually finishes: {steps}",
            f"reservation before each Agent run: {config.budgets.assumed_agent_steps} steps "
            f"({format_micros(config.budgets.assumed_agent_steps * 16_000)})",
            f"agent max_duration_seconds: {config.agent.max_duration_seconds} "
            f"(poll deadline is the same value; prefer_strict_mode={str(config.agent.prefer_strict_mode).lower()})",
            f"Fetch URLs/day: {urls} (free allowance is 1,000/day, published price $0.0000)",
            f"Fetch cost/day inside the allowance: {format_micros(0)}",
            f"Agent cost per run if each escalation takes {steps} steps: {format_micros(per_run)}",
            f"Agent cost/day with no budget guard: {format_micros(per_day_uncapped)}",
            f"per-run budget: {format_micros(config.budgets.per_run_micros)}",
            f"per-day budget: {format_micros(config.budgets.per_day_micros)}",
            f"Agent calls before the day budget stops escalation: {calls}",
            f"Agent cost/day with the default guard, then Fetch only: {format_micros(capped)}",
            "Monitor, for comparison, is $0.005 per check and does not know the $2,500 rule.",
            f"Monitor cost/day for these URLs at the same cadence: {format_micros(fetch_targets * RUNS_PER_DAY_EVERY_10_MIN * 5_000)}",
        ]
    )


def _print_unsent(results: list[CheckResult]) -> None:
    for result in results:
        if result.alert_status in {"not_sent", "failed"} and result.alert_message:
            print("--- alert payload (not delivered) ---")
            print(f"Title: {result.alert_title}")
            print(f"Click: {result.alert_click}")
            print(result.alert_message)


