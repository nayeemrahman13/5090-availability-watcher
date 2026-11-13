from datetime import datetime, timezone
from pathlib import Path

from rtx5090_watcher.check import run_once
from rtx5090_watcher.decide import needs_agent
from rtx5090_watcher.models import Observation
from rtx5090_watcher.state import StateStore
from tests.support import FakeClient, MemoryNotifier, agent_result, make_config, make_target, page

NOW = datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc)
READABLE = "NVIDIA GeForce RTX 5090 Founders Edition\n$1,999.99\nOut of Stock\n"
UNREADABLE = "NVIDIA GeForce RTX 5090 Founders Edition\nSpecifications\n"


def test_needs_agent_matrix():
    fetch_then = make_target()
    fetch_only = make_target(tier="fetch")
    agent_only = make_target(tier="agent")
    determined = Observation(True, False, 199_999, "card", None, "out", 1)
    unknown = Observation(False, None, None, None, None, "no button", 0)

    assert needs_agent(fetch_then, None, determined) is False
    assert needs_agent(fetch_then, "bot_blocked", determined) is True
    assert needs_agent(fetch_then, "target_unreachable", determined) is True
    assert needs_agent(fetch_then, "page_not_found", determined) is False
    assert needs_agent(fetch_then, "invalid_url", unknown) is False
    assert needs_agent(fetch_then, None, unknown) is True
    assert needs_agent(fetch_only, "bot_blocked", unknown) is False
    assert needs_agent(agent_only, None, determined) is True


def test_readable_out_of_stock_page_does_not_call_the_agent(tmp_path: Path):
    target = make_target()
    client = FakeClient({target.url: page(target.url, READABLE, latency_ms=80)})
    results = run_once(make_config([target]), client, MemoryNotifier(), StateStore(tmp_path / "s.json"), NOW)
    assert client.fetched == [[target.url]]
    assert client.agent_urls == []
    assert results[0].tier == "fetch"
    assert results[0].escalated is False
    assert results[0].in_stock is False
    assert results[0].price_cents == 199_999
    assert results[0].estimated_cost_micros == 0
    assert results[0].latency_ms == 80


def test_bot_block_escalates_and_the_goal_is_read_only(tmp_path: Path):
    target = make_target()
    client = FakeClient(
        {target.url: page(target.url, error="bot_blocked", latency_ms=40)},
        {target.url: agent_result(False, "$1,999.99", steps=4)},
    )
    results = run_once(make_config([target]), client, MemoryNotifier(), StateStore(tmp_path / "s.json"), NOW)
    assert results[0].tier == "agent"
    assert results[0].escalated is True
    assert results[0].fetch_error == "bot_blocked"
    assert results[0].in_stock is False
    assert results[0].price_cents == 199_999
    assert results[0].steps == 4
    assert results[0].estimated_cost_micros == 4 * 16_000
    goal = client.goals[0]
    assert "Do not click Add to Cart" in goal
    assert "Sign in" in goal
    assert "Do not type into any form" in goal


def test_undetermined_fetch_text_escalates(tmp_path: Path):
    target = make_target()
    client = FakeClient(
        {target.url: page(target.url, UNREADABLE)},
        {target.url: agent_result(True, "$1,999.99", steps=4)},
    )
    notifier = MemoryNotifier()
    results = run_once(make_config([target]), client, notifier, StateStore(tmp_path / "s.json"), NOW)
    assert client.agent_urls == [target.url]
    assert results[0].in_stock is True
    assert results[0].alert_status == "sent"
    assert len(notifier.sent) == 1


def test_fetch_only_tier_does_not_escalate_when_blocked(tmp_path: Path):
    target = make_target(id="newegg-instock", tier="fetch", page_kind="search", in_stock_filter=True)
    client = FakeClient({target.url: page(target.url, error="target_unreachable")})
    results = run_once(make_config([target]), client, MemoryNotifier(), StateStore(tmp_path / "s.json"), NOW)
    assert client.agent_urls == []
    assert results[0].tier == "fetch"
    assert results[0].escalated is False
    assert results[0].in_stock is None


def test_one_fetch_batch_and_agent_only_for_the_blocked_url(tmp_path: Path):
    good = make_target(id="newegg-instock", url="https://newegg.example/5090", page_kind="search", in_stock_filter=True)
    bad = make_target(id="bestbuy-fe", url="https://bestbuy.example/fe")
    gone = make_target(id="bh-pny-5090", url="https://bh.example/5090", retailer="B&H")
    search = "MSI Ventus GeForce RTX 5090 32GB Graphics Card\n\n$6,999.99\n"
    client = FakeClient(
        {
            good.url: page(good.url, search, latency_ms=200),
            bad.url: page(bad.url, error="target_unreachable", latency_ms=50),
            gone.url: page(gone.url, error="page_not_found", latency_ms=30),
        },
        {bad.url: agent_result(False, "$1,999.99", steps=4)},
    )
    results = run_once(
        make_config([good, bad, gone]),
        client,
        MemoryNotifier(),
        StateStore(tmp_path / "s.json"),
        NOW,
    )
    assert client.fetched == [[good.url, bad.url, gone.url]]
    assert client.agent_urls == [bad.url]
    by_id = {result.target_id: result for result in results}
    assert by_id["newegg-instock"].tier == "fetch"
    assert by_id["newegg-instock"].in_stock is True
    assert by_id["newegg-instock"].price_cents == 699_999
    assert by_id["bestbuy-fe"].tier == "agent"
    assert by_id["bh-pny-5090"].tier == "fetch"
    assert by_id["bh-pny-5090"].escalated is False


def test_per_run_budget_skips_the_second_agent(tmp_path: Path):
    first = make_target(id="bestbuy-fe", url="https://bestbuy.example/fe")
    second = make_target(id="nvidia-marketplace", url="https://nvidia.example/5090", retailer="NVIDIA Marketplace")
    client = FakeClient(
        {
            first.url: page(first.url, error="bot_blocked"),
            second.url: page(second.url, error="bot_blocked"),
        },
        {
            first.url: agent_result(False, "$1,999.99", steps=4),
            second.url: agent_result(False, "$1,999.99", steps=4),
        },
    )
    # One 8-step reservation is $0.128. After a 4-step charge ($0.064) the
    # next reservation no longer fits in a $0.128 run budget.
    config = make_config([first, second], per_run=128_000, per_day=2_000_000, assumed=8)
    results = run_once(config, client, MemoryNotifier(), StateStore(tmp_path / "s.json"), NOW)
    assert client.agent_urls == [first.url]
    assert results[1].tier == "fetch"
    assert results[1].escalated is False
    assert "per-run budget" in results[1].detail


def test_per_day_budget_persists_and_blocks_the_next_run(tmp_path: Path):
    target = make_target()
    path = tmp_path / "s.json"
    client = FakeClient(
        {target.url: page(target.url, error="bot_blocked")},
        {target.url: agent_result(False, "$1,999.99", steps=8)},
    )
    config = make_config([target], per_run=2_000_000, per_day=128_000, assumed=8)
    first = run_once(config, client, MemoryNotifier(), StateStore(path), NOW)
    assert first[0].escalated is True
    assert StateStore(path).spent_today("2026-11-13") == 128_000

    later = FakeClient(
        {target.url: page(target.url, error="bot_blocked")},
        {target.url: agent_result(True, "$1,999.99", steps=4)},
    )
    second = run_once(config, later, MemoryNotifier(), StateStore(path), NOW)
    assert later.agent_urls == []
    assert second[0].escalated is False
    assert "per-day budget" in second[0].detail
