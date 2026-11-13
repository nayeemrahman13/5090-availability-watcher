from __future__ import annotations

from rtx5090_watcher.models import (
    AgentOutcome,
    AgentSettings,
    Budgets,
    FetchPage,
    Target,
    WatcherConfig,
)
from rtx5090_watcher.notify import Alert, NotifyError


def make_target(**overrides) -> Target:
    fields = dict(
        id="bestbuy-fe",
        retailer="Best Buy",
        product_name="NVIDIA GeForce RTX 5090 Founders Edition",
        url="https://www.bestbuy.com/product/fe",
        page_kind="product",
        tier="fetch-then-agent",
        max_price_cents=250_000,
    )
    fields.update(overrides)
    return Target(**fields)


def make_config(targets, per_run=600_000, per_day=2_000_000, assumed=8) -> WatcherConfig:
    return WatcherConfig(
        targets=tuple(targets),
        agent=AgentSettings("stealth", "US", "tinyfish", 75, True),
        budgets=Budgets(per_run, per_day, assumed),
    )


class FakeClient:
    def __init__(self, pages: dict[str, FetchPage], agents: dict[str, AgentOutcome] | None = None) -> None:
        self.pages = pages
        self.agents = agents or {}
        self.fetched: list[list[str]] = []
        self.agent_urls: list[str] = []
        self.goals: list[str] = []

    def fetch(self, urls: list[str]) -> dict[str, FetchPage]:
        self.fetched.append(list(urls))
        return {url: self.pages[url] for url in urls}

    def run_agent(self, url: str, goal: str, settings: AgentSettings) -> AgentOutcome:
        self.agent_urls.append(url)
        self.goals.append(goal)
        return self.agents[url]


class MemoryNotifier:
    def __init__(self, topic: str | None = "test-topic", fail: bool = False) -> None:
        self.topic = topic
        self.fail = fail
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> dict:
        if self.fail:
            raise NotifyError("down", alert)
        self.sent.append(alert)
        return {"id": "mem"}


def page(url: str, text: str | None = None, error: str | None = None, latency_ms: int = 120) -> FetchPage:
    return FetchPage(url, text, error, latency_ms)


def agent_result(in_stock: bool, price: str, steps: int = 4, product: str = "NVIDIA GeForce RTX 5090 Founders Edition") -> AgentOutcome:
    return AgentOutcome(
        status="COMPLETED",
        result={
            "product": product,
            "price": price,
            "button_state": "Add to Cart" if in_stock else "Out of Stock",
            "in_stock": in_stock,
            "listing_url": None,
        },
        error=None,
        steps=steps,
        latency_ms=4000,
        run_id="run_test",
    )
