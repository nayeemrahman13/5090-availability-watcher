from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    id: str
    retailer: str
    product_name: str
    url: str
    page_kind: str
    tier: str
    max_price_cents: int
    in_stock_filter: bool = False
    enabled: bool = True
    notes: str = ""


@dataclass(frozen=True)
class AgentSettings:
    browser_profile: str
    proxy_country: str
    proxy_type: str
    max_duration_seconds: int
    prefer_strict_mode: bool


@dataclass(frozen=True)
class Budgets:
    per_run_micros: int
    per_day_micros: int
    assumed_agent_steps: int


@dataclass(frozen=True)
class WatcherConfig:
    targets: tuple[Target, ...]
    agent: AgentSettings
    budgets: Budgets

    def by_id(self, target_id: str) -> Target:
        for target in self.targets:
            if target.id == target_id:
                return target
        known = ", ".join(target.id for target in self.targets)
        raise KeyError(f"Unknown target {target_id!r}. Known ids: {known}")


@dataclass(frozen=True)
class Observation:
    """One reading of a target, whichever TinyFish layer produced it."""

    determined: bool
    in_stock: bool | None
    price_cents: int | None
    product: str | None
    listing_url: str | None
    detail: str
    listings: int = 0


@dataclass(frozen=True)
class FetchPage:
    url: str
    text: str | None
    error: str | None
    latency_ms: int | None


@dataclass(frozen=True)
class AgentOutcome:
    status: str
    result: dict | None
    error: str | None
    steps: int | None
    latency_ms: int
    run_id: str | None = None
    strict_mode_fallback: bool = False
    cancelled: bool = False


@dataclass
class TargetState:
    qualifies: bool
    in_stock: bool | None
    price_cents: int | None
    product: str | None
    tier: str | None
    detail: str | None
    checked_at: str | None
    listing_url: str | None = None


@dataclass
class CheckResult:
    target_id: str
    retailer: str
    url: str
    tier: str
    escalated: bool
    fetch_error: str | None
    in_stock: bool | None
    price_cents: int | None
    product: str | None
    qualifies: bool
    alert_status: str
    latency_ms: int | None
    estimated_cost_micros: int
    steps: int | None
    detail: str
    alert_title: str | None = None
    alert_message: str | None = None
    alert_click: str | None = None
