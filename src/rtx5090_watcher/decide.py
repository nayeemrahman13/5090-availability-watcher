"""The two decisions that are not TinyFish's problem.

1. Does this reading mean "in stock at or under the max price"?
2. Did Fetch fail in a way where the Agent is the next cheapest layer that can work?
"""

from __future__ import annotations

from rtx5090_watcher.models import Observation, Target, TargetState

# Per-URL Fetch errors that mean "the reader could not see the page".
# Documented on https://docs.tinyfish.ai/fetch-api/reference
ESCALATE_ERRORS = frozenset(
    {
        "target_unreachable",
        "bot_blocked",
        "timeout",
        "empty_content",
        "login_required",
        "proxy_error",
        "target_http_error",
        "content_too_large",
        "conditional_unsupported",
    }
)

# The page is gone, or the URL itself was rejected. A browser agent will not fix that.
NO_ESCALATE_ERRORS = frozenset(
    {
        "page_not_found",
        "invalid_url",
        "invalid_redirect_url",
        "selector_not_matched",
        "selector_unsupported",
    }
)


def is_qualifying(obs: Observation, max_price_cents: int) -> bool:
    return bool(
        obs.determined
        and obs.in_stock is True
        and obs.price_cents is not None
        and obs.price_cents <= max_price_cents
    )


def should_alert(previous: TargetState | None, qualifies_now: bool) -> bool:
    """Alert on the transition into in-stock-under-threshold, including the first sighting."""

    was = False if previous is None else previous.qualifies
    return qualifies_now and not was


def needs_agent(target: Target, fetch_error: str | None, obs: Observation) -> bool:
    if target.tier == "fetch":
        return False
    if target.tier == "agent":
        return True
    if fetch_error in NO_ESCALATE_ERRORS:
        return False
    if fetch_error in ESCALATE_ERRORS or fetch_error:
        return True
    if not obs.determined:
        return True
    if obs.in_stock is True and obs.price_cents is None:
        return True
    return False
