from pathlib import Path

from rtx5090_watcher.cli import render_estimate
from rtx5090_watcher.config import load_config
from rtx5090_watcher.pricing import RUNS_PER_DAY_EVERY_10_MIN, count_calls_until_day_cap, scenario_agent_micros
from rtx5090_watcher.pricing import BudgetGuard


def test_shipped_targets_match_the_pages_checked_on_nov_13():
    config = load_config(Path("targets.json"))
    by_id = {target.id: target for target in config.targets}
    assert set(by_id) == {
        "bestbuy-fe",
        "nvidia-marketplace",
        "bestbuy-astral",
        "bh-pny-5090",
        "newegg-instock",
    }
    assert by_id["bestbuy-fe"].url.endswith("/J3GWYHGPCP")
    assert "N=4131" in by_id["newegg-instock"].url
    assert by_id["newegg-instock"].in_stock_filter is True
    assert by_id["nvidia-marketplace"].url.startswith("https://marketplace.nvidia.com/")
    assert "1874647-REG" in by_id["bh-pny-5090"].url
    assert by_id["bestbuy-astral"].url.endswith("/JJGGLHJVSV")
    assert all(target.max_price_cents == 250_000 for target in config.targets)
    assert all(target.tier == "fetch-then-agent" for target in config.targets)
    assert config.agent.browser_profile == "stealth"
    assert config.agent.proxy_country == "US"
    assert config.budgets.per_run_micros == 600_000
    assert config.budgets.per_day_micros == 2_000_000
    assert config.budgets.assumed_agent_steps == 8


def test_default_budgets_cover_one_pass_and_cap_the_day():
    def factory(spent: int) -> BudgetGuard:
        return BudgetGuard(600_000, 2_000_000, assumed_steps=8, already_spent_today=spent)

    # 4 escalations, 4 actual steps (the Best Buy reading). Reservation is 8.
    calls = count_calls_until_day_cap(factory, targets_per_run=4, actual_steps=4)
    assert calls == 30
    assert scenario_agent_micros(calls, 4) == 30 * 4 * 16_000
    uncapped = 4 * 4 * RUNS_PER_DAY_EVERY_10_MIN * 16_000
    assert uncapped == 36_864_000  # $36.8640
    assert fetch_headroom() == 280


def fetch_headroom() -> int:
    return 1_000 - 5 * RUNS_PER_DAY_EVERY_10_MIN


def test_estimate_text_quotes_the_same_numbers():
    text = render_estimate(load_config(Path("targets.json")), agent_targets=4, steps=4)
    assert "720" in text
    assert "$36.8640" in text
    assert "Agent calls before the day budget stops escalation: 30" in text
    assert "$1.9200" in text  # 30 * 4 * $0.016
