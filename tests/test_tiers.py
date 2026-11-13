import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from rtx5090_watcher.check import run_once
from rtx5090_watcher.decide import needs_agent
from rtx5090_watcher.goals import stock_goal
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
    assert "stop immediately" in goal
    assert "Do not navigate" in goal
    assert "Do not search" in goal
    assert "Do not open the cart" in goal
    assert "listing_url: null" in goal


def test_goal_reads_the_current_page_and_stops():
    product = stock_goal(make_target())
    search = stock_goal(make_target(id="nvidia-marketplace", page_kind="search", url="https://nvidia.example/5090"))
    assert "product page" in product
    assert "search or marketplace page" in search
    assert "cheapest RTX 5090 graphics card visible on this page" in search
    for goal in (product, search):
        assert "Do not navigate" in goal
        assert "Do not search" in goal
        assert "Do not open the cart" in goal
        assert "Stop as soon as those fields are filled" in goal


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


class _OverlapClient(FakeClient):
    """Holds each agent call briefly so overlapping starts are visible."""

    def __init__(self, pages, agents, dwell: float = 0.1) -> None:
        super().__init__(pages, agents)
        self.dwell = dwell
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    def run_agent(self, url, goal, settings):
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(self.dwell)
        finally:
            with self._lock:
                self._in_flight -= 1
        return super().run_agent(url, goal, settings)


def test_reserved_escalations_run_in_one_wave(tmp_path: Path):
    targets = [make_target(id=f"card-{index}", url=f"https://example.com/{index}") for index in range(4)]
    pages = {target.url: page(target.url, error="bot_blocked") for target in targets}
    agents = {target.url: agent_result(False, "$1,999.99", steps=4) for target in targets}
    client = _OverlapClient(pages, agents)
    # 4 reservations of $0.128 fit in the $0.60 per-run budget ($0.512).
    config = make_config(targets, per_run=600_000, per_day=2_000_000, assumed=8)
    path = tmp_path / "s.json"
    results = run_once(config, client, MemoryNotifier(), StateStore(path), NOW)
    assert client.max_in_flight == 4
    assert sorted(client.agent_urls) == sorted(target.url for target in targets)
    assert [result.escalated for result in results] == [True, True, True, True]
    assert [result.estimated_cost_micros for result in results] == [64_000, 64_000, 64_000, 64_000]
    assert StateStore(path).spent_today("2026-11-13") == 256_000


def test_day_budget_caps_the_wave_and_does_not_start_the_rest(tmp_path: Path):
    targets = [make_target(id=f"card-{index}", url=f"https://example.com/{index}") for index in range(4)]
    pages = {target.url: page(target.url, error="bot_blocked") for target in targets}
    agents = {target.url: agent_result(False, "$1,999.99", steps=8) for target in targets}
    client = _OverlapClient(pages, agents)
    # Two 8-step reservations are $0.256, which is the whole day budget.
    # Actual steps equal the reservation, so a later wave still does not fit.
    config = make_config(targets, per_run=2_000_000, per_day=256_000, assumed=8)
    results = run_once(config, client, MemoryNotifier(), StateStore(tmp_path / "s.json"), NOW)
    assert client.max_in_flight == 2
    assert len(client.agent_urls) == 2
    escalated = [result for result in results if result.escalated]
    skipped = [result for result in results if not result.escalated]
    assert len(escalated) == 2
    assert len(skipped) == 2
    assert all("per-day budget" in result.detail for result in skipped)
    assert sum(result.estimated_cost_micros for result in results) == 256_000


def test_second_wave_starts_only_after_the_first_charge_frees_budget(tmp_path: Path):
    first = make_target(id="bestbuy-fe", url="https://bestbuy.example/fe")
    second = make_target(id="nvidia-marketplace", url="https://nvidia.example/5090")
    pages = {
        first.url: page(first.url, error="bot_blocked"),
        second.url: page(second.url, error="bot_blocked"),
    }
    agents = {
        first.url: agent_result(False, "$1,999.99", steps=4),
        second.url: agent_result(False, "$2,199.99", steps=4),
    }
    client = _OverlapClient(pages, agents)
    # One reservation is $0.128. Two holds ($0.256) do not fit in $0.200.
    # After a 4-step charge ($0.064) the second reservation does: $0.192.
    config = make_config([first, second], per_run=200_000, per_day=2_000_000, assumed=8)
    results = run_once(config, client, MemoryNotifier(), StateStore(tmp_path / "s.json"), NOW)
    assert client.max_in_flight == 1
    assert client.agent_urls == [first.url, second.url]
    assert [result.escalated for result in results] == [True, True]
    assert [result.estimated_cost_micros for result in results] == [64_000, 64_000]
    assert results[1].price_cents == 219_999
