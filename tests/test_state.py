from datetime import datetime, timezone
from pathlib import Path

from rtx5090_watcher.check import demo_transition, run_once
from rtx5090_watcher.decide import should_alert
from rtx5090_watcher.models import TargetState
from rtx5090_watcher.state import StateStore
from tests.support import FakeClient, MemoryNotifier, make_config, make_target, page

NOW = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
IN_STOCK = "NVIDIA GeForce RTX 5090 Founders Edition\n$1,999.99\nAdd to Cart\n"
OUT = "NVIDIA GeForce RTX 5090 Founders Edition\n$1,999.99\nOut of Stock\n"


def test_should_alert_only_on_the_rising_edge():
    assert should_alert(None, True) is True
    assert should_alert(None, False) is False
    assert should_alert(TargetState(False, False, None, None, None, None, None), True) is True
    assert should_alert(TargetState(True, True, 199_999, None, None, None, None), True) is False
    assert should_alert(TargetState(True, True, 199_999, None, None, None, None), False) is False


def test_transition_alerts_once_then_stays_quiet(tmp_path: Path):
    target = make_target()
    config = make_config([target])
    store = StateStore(tmp_path / "state.json")
    notifier = MemoryNotifier()
    url = target.url

    first = run_once(config, FakeClient({url: page(url, OUT)}), notifier, store, NOW)
    assert first[0].alert_status == "no"
    assert first[0].qualifies is False
    assert notifier.sent == []

    second = run_once(config, FakeClient({url: page(url, IN_STOCK)}), notifier, store, NOW)
    assert second[0].alert_status == "sent"
    assert second[0].qualifies is True
    assert len(notifier.sent) == 1
    assert "Best Buy" in notifier.sent[0].title
    assert "$1,999.99" in notifier.sent[0].message
    assert "Product:" in notifier.sent[0].message
    assert "Retailer: Best Buy" in notifier.sent[0].message
    assert url in notifier.sent[0].message

    third = run_once(config, FakeClient({url: page(url, IN_STOCK)}), notifier, store, NOW)
    assert third[0].alert_status == "no"
    assert len(notifier.sent) == 1


def test_failed_push_does_not_consume_the_transition(tmp_path: Path):
    target = make_target()
    config = make_config([target])
    store = StateStore(tmp_path / "state.json")
    url = target.url
    failing = MemoryNotifier(fail=True)
    failed = run_once(config, FakeClient({url: page(url, IN_STOCK)}), failing, store, NOW)
    assert failed[0].alert_status == "failed"
    assert failed[0].qualifies is False
    assert store.get(target.id).qualifies is False

    retry = MemoryNotifier()
    again = run_once(config, FakeClient({url: page(url, IN_STOCK)}), retry, store, NOW)
    assert again[0].alert_status == "sent"
    assert len(retry.sent) == 1


def test_over_threshold_in_stock_does_not_alert_and_a_later_drop_does(tmp_path: Path):
    target = make_target()
    config = make_config([target])
    store = StateStore(tmp_path / "state.json")
    notifier = MemoryNotifier()
    url = target.url
    expensive = "ASUS ROG Astral GeForce RTX 5090 Graphics Card\n$6,499.99\nAdd to Cart\n"
    cheap = "ASUS ROG Astral GeForce RTX 5090 Graphics Card\n$2,499.99\nAdd to Cart\n"

    high = run_once(config, FakeClient({url: page(url, expensive)}), notifier, store, NOW)
    assert high[0].in_stock is True
    assert high[0].qualifies is False
    assert notifier.sent == []

    drop = run_once(config, FakeClient({url: page(url, cheap)}), notifier, store, NOW)
    assert drop[0].alert_status == "sent"
    assert len(notifier.sent) == 1


def test_day_budget_resets_on_the_next_utc_day(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.charge("2026-11-13", 128_000)
    store.save()
    reloaded = StateStore(tmp_path / "state.json")
    assert reloaded.spent_today("2026-11-13") == 128_000
    assert reloaded.spent_today("2026-11-14") == 0


def test_demo_alert_sends_exactly_one_and_uses_the_same_transition(tmp_path: Path):
    target = make_target()
    store = StateStore(tmp_path / "demo.json")
    notifier = MemoryNotifier()
    result = demo_transition(target, store, notifier, NOW)
    assert result.alert_status == "sent"
    assert len(notifier.sent) == 1
    assert notifier.sent[0].click == target.url
    assert "RTX 5090 in stock at Best Buy for $1,999.99" == notifier.sent[0].title
    # Running the transition helper again is a deliberate re-seed. A normal
    # second observation of the same state does not alert.
    quiet = MemoryNotifier()
    from rtx5090_watcher.check import _record
    from rtx5090_watcher.models import Observation

    again = _record(
        target,
        Observation(True, True, 199_999, target.product_name, target.url, "still there", 1),
        quiet,
        store,
        NOW,
        "fetch",
        0,
        10,
        None,
        None,
        False,
    )
    assert again.alert_status == "no"
    assert quiet.sent == []


def test_missing_topic_leaves_the_transition_open(tmp_path: Path):
    target = make_target()
    config = make_config([target])
    store = StateStore(tmp_path / "state.json")
    silent = MemoryNotifier(topic=None)
    result = run_once(config, FakeClient({target.url: page(target.url, IN_STOCK)}), silent, store, NOW)
    assert result[0].alert_status == "not_sent"
    assert result[0].alert_message.startswith("Product:")
    assert store.get(target.id).qualifies is False
    assert silent.sent == []
