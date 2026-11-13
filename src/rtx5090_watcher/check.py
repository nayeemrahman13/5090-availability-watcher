"""One pass over the targets.

Fetch is a batch: one POST, up to ten URLs, which is the Fetch API's unit.
Agent runs that the budget has already reserved start together. They are
async (/run-async returns a run_id, then we poll), so one pass waits out the
slowest run in a wave instead of the sum. Pay-as-you-go queues past the
account's concurrency. The guard does not depend on that queue: each run
holds an assumed-step reservation before it starts, and charge() replaces
that hold with the real num_of_steps. A target that does not fit is tried
again after the wave settles, which is the same admission rule as checking
the budget before every start.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

from rtx5090_watcher.decide import is_qualifying, needs_agent, should_alert
from rtx5090_watcher.goals import stock_goal
from rtx5090_watcher.models import (
    AgentOutcome,
    CheckResult,
    FetchPage,
    Observation,
    Target,
    TargetState,
    WatcherConfig,
)
from rtx5090_watcher.notify import Alert, NotifyError, build_alert
from rtx5090_watcher.parse import parse_agent_result, parse_fetch_text
from rtx5090_watcher.pricing import BudgetGuard, format_micros, format_price_cents
from rtx5090_watcher.state import StateStore, utc_day


@dataclass
class _Prepared:
    target: Target
    fetch_error: str | None
    latency_ms: int
    observation: Observation
    needs_agent: bool


def run_once(config: WatcherConfig, client, notifier, store: StateStore, now: datetime, only: list[str] | None = None) -> list[CheckResult]:
    targets = _select(config, only)
    fetch_urls = [target.url for target in targets if target.tier != "agent"]
    pages = client.fetch(fetch_urls) if fetch_urls else {}
    day = utc_day(now)
    guard = BudgetGuard(
        config.budgets.per_run_micros,
        config.budgets.per_day_micros,
        config.budgets.assumed_agent_steps,
        store.spent_today(day),
    )
    prepared = [_prepare(target, pages.get(target.url)) for target in targets]
    outcomes: dict[str, AgentOutcome] = {}
    costs: dict[str, int] = {}
    skipped: dict[str, str] = {}
    pending = [item for item in prepared if item.needs_agent]
    while pending:
        wave: list[_Prepared] = []
        rest: list[_Prepared] = []
        blocked = ""
        for index, item in enumerate(pending):
            ok, which = guard.allow()
            if not ok:
                blocked = which
                rest = list(pending[index:])
                break
            guard.hold()
            wave.append(item)
        if not wave:
            for item in pending:
                skipped[item.target.id] = blocked
            break
        wave_outcomes, errors = _run_wave(client, wave, config.agent)
        for item in wave:
            outcome = wave_outcomes.get(item.target.id)
            if outcome is None:
                guard.release_hold()
                continue
            costs[item.target.id] = guard.charge(outcome.steps)
            outcomes[item.target.id] = outcome
        _save_budget(store, guard, day)
        if errors:
            raise errors[0]
        pending = rest

    results = []
    for item in prepared:
        result = _finish(
            item,
            outcomes.get(item.target.id),
            costs.get(item.target.id, 0),
            skipped.get(item.target.id),
            config,
            guard,
            notifier,
            store,
            now,
        )
        _save_budget(store, guard, day)
        results.append(result)
    return results


def demo_transition(target: Target, store: StateStore, notifier, now: datetime) -> CheckResult:
    """Seed an out-of-stock reading, then apply one in-stock-under-threshold reading.

    This is the alert path and nothing else. It does not call TinyFish.
    """

    store.put(
        target.id,
        TargetState(
            qualifies=False,
            in_stock=False,
            price_cents=199_999,
            product=target.product_name,
            tier="demo-seed",
            detail="Forced out of stock so the next reading is a transition",
            checked_at=now.isoformat(),
            listing_url=target.url,
        ),
    )
    observation = Observation(
        determined=True,
        in_stock=True,
        price_cents=199_999,
        product="NVIDIA GeForce RTX 5090 Founders Edition",
        listing_url=target.url,
        detail="Forced in-stock reading for the alert demo",
        listings=1,
    )
    result = _record(target, observation, notifier, store, now, "demo", 0, None, None, None, False)
    store.save()
    return result


def _prepare(target: Target, page: FetchPage | None) -> _Prepared:
    if target.tier == "agent":
        observation = Observation(False, None, None, target.product_name, None, "Tier is agent; Fetch skipped", 0)
        return _Prepared(target, None, 0, observation, True)
    if page is None:
        page = FetchPage(target.url, None, "missing", None)
    latency = page.latency_ms or 0
    if page.error or not page.text:
        observation = Observation(False, None, None, target.product_name, None, f"Fetch {page.error}", 0)
    else:
        observation = parse_fetch_text(page.text, target)
    return _Prepared(target, page.error, latency, observation, needs_agent(target, page.error, observation))


def _run_wave(client, wave: list[_Prepared], settings) -> tuple[dict[str, AgentOutcome], list[Exception]]:
    if len(wave) == 1:
        item = wave[0]
        try:
            outcome = client.run_agent(item.target.url, stock_goal(item.target), settings)
        except Exception as exc:
            return {}, [exc]
        return {item.target.id: outcome}, []

    outcomes: dict[str, AgentOutcome] = {}
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=len(wave)) as pool:
        futures = {
            pool.submit(client.run_agent, item.target.url, stock_goal(item.target), settings): item.target.id
            for item in wave
        }
        for future in as_completed(futures):
            target_id = futures[future]
            try:
                outcomes[target_id] = future.result()
            except Exception as exc:
                errors.append(exc)
    return outcomes, errors


def _finish(item, outcome, cost, skipped, config, guard, notifier, store, now) -> CheckResult:
    target = item.target
    obs = item.observation
    latency = item.latency_ms
    steps = None
    escalated = False
    tier = "fetch"
    if outcome is not None:
        escalated = True
        tier = "agent"
        steps = outcome.steps if outcome.steps is not None else config.budgets.assumed_agent_steps
        latency += outcome.latency_ms
        obs = parse_agent_result(outcome.result, outcome.error, outcome.status)
        if outcome.strict_mode_fallback:
            obs = Observation(
                obs.determined,
                obs.in_stock,
                obs.price_cents,
                obs.product,
                obs.listing_url,
                f"{obs.detail}. strict mode is beta on this account; retried without mode",
                obs.listings,
            )
        if outcome.cancelled:
            obs = Observation(
                obs.determined,
                obs.in_stock,
                obs.price_cents,
                obs.product,
                obs.listing_url,
                f"{obs.detail}. Agent cancelled at max_duration_seconds",
                obs.listings,
            )
    elif skipped:
        reservation = format_micros(guard.reservation_micros())
        obs = Observation(
            obs.determined,
            obs.in_stock,
            obs.price_cents,
            obs.product,
            obs.listing_url,
            f"{obs.detail}. Agent skipped: {reservation} reservation would exceed the {skipped} budget",
            obs.listings,
        )
        tier = "fetch" if target.tier != "agent" else "skipped"
    return _record(target, obs, notifier, store, now, tier, cost, latency or None, steps, item.fetch_error, escalated)


def _save_budget(store: StateStore, guard: BudgetGuard, day: str) -> None:
    store.budget_day = day
    store.budget_micros = guard.today_micros
    store.save()


def _record(target, obs: Observation, notifier, store: StateStore, now: datetime, tier: str, cost: int, latency, steps, fetch_error, escalated: bool) -> CheckResult:
    qualifies = is_qualifying(obs, target.max_price_cents)
    previous = store.get(target.id)
    want_alert = should_alert(previous, qualifies)
    alert_status = "no"
    alert: Alert | None = None
    sent = False
    if want_alert:
        link = obs.listing_url or target.url
        product = obs.product or target.product_name
        alert = build_alert(target.retailer, product, format_price_cents(obs.price_cents), link)
        if not getattr(notifier, "topic", None):
            alert_status = "not_sent"
        else:
            try:
                notifier.send(alert)
                sent = True
                alert_status = "sent"
            except NotifyError:
                alert_status = "failed"
    stored_qualifies = qualifies and not (want_alert and not sent)
    store.put(
        target.id,
        TargetState(
            qualifies=stored_qualifies,
            in_stock=obs.in_stock,
            price_cents=obs.price_cents,
            product=obs.product,
            tier=tier,
            detail=obs.detail,
            checked_at=now.isoformat(),
            listing_url=obs.listing_url or target.url,
        ),
    )
    return CheckResult(
        target_id=target.id,
        retailer=target.retailer,
        url=target.url,
        tier=tier,
        escalated=escalated,
        fetch_error=fetch_error,
        in_stock=obs.in_stock,
        price_cents=obs.price_cents,
        product=obs.product,
        qualifies=stored_qualifies,
        alert_status=alert_status,
        latency_ms=latency,
        estimated_cost_micros=cost,
        steps=steps,
        detail=obs.detail,
        alert_title=None if alert is None else alert.title,
        alert_message=None if alert is None else alert.message,
        alert_click=None if alert is None else alert.click,
    )


def _select(config: WatcherConfig, only: list[str] | None) -> list[Target]:
    wanted = set(only or [])
    if wanted:
        unknown = wanted - {target.id for target in config.targets}
        if unknown:
            raise KeyError(f"Unknown target id(s): {', '.join(sorted(unknown))}")
    return [target for target in config.targets if target.enabled and (not wanted or target.id in wanted)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
