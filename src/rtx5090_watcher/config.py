"""Load targets.json. Budgets can be overridden from the environment."""

from __future__ import annotations

import json
import os
from pathlib import Path

from rtx5090_watcher.models import AgentSettings, Budgets, Target, WatcherConfig
from rtx5090_watcher.pricing import usd_to_micros


def dollars_to_cents(amount: float) -> int:
    return int(round(float(amount) * 100))


TIERS = {"fetch", "agent", "fetch-then-agent"}
PAGE_KINDS = {"product", "search"}


def load_config(path: Path) -> WatcherConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    agent_raw = raw["agent"]
    budget_raw = raw["budgets"]

    per_run = float(os.environ.get("AGENT_BUDGET_PER_RUN_USD", budget_raw["agent_per_run_usd"]))
    per_day = float(os.environ.get("AGENT_BUDGET_PER_DAY_USD", budget_raw["agent_per_day_usd"]))
    budgets = Budgets(
        per_run_micros=usd_to_micros(per_run),
        per_day_micros=usd_to_micros(per_day),
        assumed_agent_steps=int(budget_raw["assumed_agent_steps"]),
    )
    agent = AgentSettings(
        browser_profile=str(agent_raw.get("browser_profile", "stealth")),
        proxy_country=str(agent_raw.get("proxy_country", "US")),
        proxy_type=str(agent_raw.get("proxy_type", "tinyfish")),
        max_duration_seconds=int(agent_raw["max_duration_seconds"]),
        prefer_strict_mode=bool(agent_raw.get("prefer_strict_mode", True)),
    )

    seen: set[str] = set()
    targets: list[Target] = []
    for entry in raw["targets"]:
        target_id = str(entry["id"])
        if target_id in seen:
            raise ValueError(f"Duplicate target id {target_id}")
        seen.add(target_id)
        tier = str(entry.get("tier", defaults.get("tier", "fetch-then-agent")))
        page_kind = str(entry["page_kind"])
        if tier not in TIERS:
            raise ValueError(f"{target_id}: tier must be one of {sorted(TIERS)}")
        if page_kind not in PAGE_KINDS:
            raise ValueError(f"{target_id}: page_kind must be one of {sorted(PAGE_KINDS)}")
        max_price = entry.get("max_price_usd", defaults.get("max_price_usd", 2500))
        targets.append(
            Target(
                id=target_id,
                retailer=str(entry["retailer"]),
                product_name=str(entry["product_name"]),
                url=str(entry["url"]),
                page_kind=page_kind,
                tier=tier,
                max_price_cents=dollars_to_cents(float(max_price)),
                in_stock_filter=bool(entry.get("in_stock_filter", False)),
                enabled=bool(entry.get("enabled", True)),
                notes=str(entry.get("notes", "")),
            )
        )
    if not targets:
        raise ValueError("targets.json has no targets")
    return WatcherConfig(targets=tuple(targets), agent=agent, budgets=budgets)
