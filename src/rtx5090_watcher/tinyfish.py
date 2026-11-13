"""TinyFish REST client. Two endpoints, used in that order on purpose.

Fetch  POST https://api.fetch.tinyfish.ai
       Markdown for a URL. Per-URL failures come back as HTTP 200 plus errors[].
       Docs: https://docs.tinyfish.ai/fetch-api/reference

Agent  POST https://agent.tinyfish.ai/v1/automation/run-async
       Then GET  https://agent.tinyfish.ai/v1/runs/{id}
       Cancel POST https://agent.tinyfish.ai/v1/runs/{id}/cancel
       Docs: https://docs.tinyfish.ai/agent/api/agent

Auth for both is the X-API-Key header (https://docs.tinyfish.ai/authentication).

/run is synchronous and cannot be cancelled, so a budget guard could not stop
it. /run-async returns a run_id immediately. We poll until the run finishes
or max_duration_seconds elapses, then cancel. That poll clock is the same
number we send in agent_config, and it starts when the run_id arrives. A
GET cannot be given a timeout past the time still left, so a slow poll
cannot drag the cancel out past the cap. On 13 Nov 2026 the cap was 75s and
the client waited 75+15s, then checked only after a GET with a 30s timeout.
The four escalations came back "Cancelled: max_duration_seconds elapsed" at
8 steps after about 119s, with no stock fields.

`agent_config.mode = strict` and `max_steps` are beta and return 403 on
accounts that have not joined. This account got that 403 on every call, so
prefer_strict_mode defaults off and we do not spend the round trip. When the
option is on and the request is forbidden, we retry the same goal once
without mode. We never send max_steps. max_duration_seconds is the cap we
rely on.

Stealth plus a US proxy is the combination that got a Best Buy reading on
13 Nov 2026, after Fetch returned target_unreachable for that host.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Callable

from rtx5090_watcher.models import AgentOutcome, AgentSettings, FetchPage

FETCH_URL = "https://api.fetch.tinyfish.ai"
RUN_ASYNC_URL = "https://agent.tinyfish.ai/v1/automation/run-async"
RUNS_URL = "https://agent.tinyfish.ai/v1/runs"

# output_schema is a JSON Schema subset. additionalProperties, oneOf, and
# type arrays are rejected. Nullable fields use `nullable: true`.
# https://docs.tinyfish.ai/agent/api/agent/structured-output
STOCK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "nullable": True},
        "price": {"type": "string", "nullable": True},
        "button_state": {"type": "string", "nullable": True},
        "in_stock": {"type": "boolean"},
        "listing_url": {"type": "string", "nullable": True},
    },
    "required": ["product", "price", "button_state", "in_stock", "listing_url"],
    "propertyOrdering": ["product", "price", "button_state", "in_stock", "listing_url"],
}

_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


class TinyFishError(Exception):
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"TinyFish HTTP {status}: {body[:400]}")


class TinyFishClient:
    def __init__(self, api_key: str, transport: Callable | None = None) -> None:
        if not api_key:
            raise TinyFishError(401, "TINYFISH_API_KEY is empty")
        self.api_key = api_key
        self.transport = transport or UrllibTransport()

    def fetch(self, urls: list[str]) -> dict[str, FetchPage]:
        if not urls:
            return {}
        started = time.monotonic()
        status, parsed, text = self._json(
            "POST",
            FETCH_URL,
            {
                "urls": urls,
                "format": "markdown",
                "links": False,
                # ttl 0 asks for a live fetch. A cached page would lie about stock.
                "ttl": 0,
                "purpose": "Read RTX 5090 price and stock. Read only.",
                "per_url_timeout_ms": 45_000,
            },
            timeout=150,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if status != 200 or not isinstance(parsed, dict):
            raise TinyFishError(status, text)
        pages: dict[str, FetchPage] = {}
        for item in parsed.get("results") or []:
            latency = item.get("latency_ms")
            pages[item["url"]] = FetchPage(
                url=item["url"],
                text=item.get("text") if isinstance(item.get("text"), str) else None,
                error=None,
                latency_ms=int(latency) if isinstance(latency, (int, float)) else elapsed_ms,
            )
        for item in parsed.get("errors") or []:
            pages[item["url"]] = FetchPage(
                url=item["url"],
                text=None,
                error=str(item.get("error") or "unknown"),
                latency_ms=elapsed_ms,
            )
        for url in urls:
            pages.setdefault(url, FetchPage(url, None, "missing", elapsed_ms))
        return pages

    def run_agent(
        self,
        url: str,
        goal: str,
        settings: AgentSettings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        poll_interval: float = 2.0,
    ) -> AgentOutcome:
        started = monotonic()
        body = _agent_body(url, goal, settings, strict=settings.prefer_strict_mode)
        status, parsed, text = self._json("POST", RUN_ASYNC_URL, body, timeout=60)
        fallback = False
        if status == 403 and settings.prefer_strict_mode:
            # Beta gate: mode=strict is rejected with 403 on accounts that
            # have not enrolled. The goal and schema stay the same.
            body = _agent_body(url, goal, settings, strict=False)
            status, parsed, text = self._json("POST", RUN_ASYNC_URL, body, timeout=60)
            fallback = True
        if status != 200 or not isinstance(parsed, dict):
            raise TinyFishError(status, text)
        if parsed.get("error"):
            message = _error_message(parsed.get("error"))
            return AgentOutcome("FAILED", None, message, 0, _elapsed(monotonic, started), strict_mode_fallback=fallback)
        run_id = str(parsed["run_id"])
        # The server's cap starts once the run exists. The 403 retry, if we
        # made one, is not part of that clock.
        budget_started = monotonic()
        limit = settings.max_duration_seconds
        while True:
            elapsed = monotonic() - budget_started
            if elapsed >= limit:
                return self._stop_for_duration(run_id, fallback, started, monotonic)
            remaining = limit - elapsed
            run = self._get_run(run_id, timeout=min(30, remaining))
            if run.get("status") in _TERMINAL:
                return _outcome(run, fallback, _elapsed(monotonic, started))
            elapsed = monotonic() - budget_started
            if elapsed >= limit:
                return self._stop_for_duration(run_id, fallback, started, monotonic)
            nap = min(poll_interval, limit - elapsed)
            if nap > 0:
                sleep(nap)

    def _stop_for_duration(self, run_id: str, fallback: bool, started: float, monotonic: Callable[[], float]) -> AgentOutcome:
        self._cancel(run_id)
        final = self._get_run(run_id)
        outcome = _outcome(final, fallback, _elapsed(monotonic, started))
        status = outcome.status if outcome.status in _TERMINAL else "CANCELLED"
        error = outcome.error or "Cancelled: max_duration_seconds elapsed"
        return replace(outcome, cancelled=True, status=status, error=error)

    def _get_run(self, run_id: str, timeout: float = 30) -> dict:
        status, parsed, text = self._json(
            "GET",
            f"{RUNS_URL}/{run_id}?screenshots=none&html=none",
            None,
            timeout=timeout,
        )
        if status != 200 or not isinstance(parsed, dict):
            raise TinyFishError(status, text)
        return parsed

    def _cancel(self, run_id: str) -> None:
        self._json("POST", f"{RUNS_URL}/{run_id}/cancel", {}, timeout=30)

    def _json(self, method: str, url: str, body: dict | None, timeout: float):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"X-API-Key": self.api_key, "Accept": "application/json", "User-Agent": "rtx5090-watcher/1.0"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self.transport(method, url, headers, payload, timeout)
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        return status, parsed, text


def _agent_body(url: str, goal: str, settings: AgentSettings, strict: bool) -> dict:
    agent_config: dict = {"max_duration_seconds": settings.max_duration_seconds}
    if strict:
        agent_config["mode"] = "strict"
    return {
        "url": url,
        "goal": goal,
        "output_schema": STOCK_OUTPUT_SCHEMA,
        "browser_profile": settings.browser_profile,
        "proxy_config": {
            "enabled": True,
            "type": settings.proxy_type,
            "country_code": settings.proxy_country,
        },
        "agent_config": agent_config,
    }


def _outcome(parsed: dict, fallback: bool, latency_ms: int) -> AgentOutcome:
    result = parsed.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = None
    if not isinstance(result, dict):
        result = None
    steps = parsed.get("num_of_steps")
    return AgentOutcome(
        status=str(parsed.get("status") or "FAILED"),
        result=result,
        error=_error_message(parsed.get("error")),
        steps=steps if isinstance(steps, int) else None,
        latency_ms=latency_ms,
        run_id=parsed.get("run_id"),
        strict_mode_fallback=fallback,
        cancelled=parsed.get("status") == "CANCELLED",
    )


def _error_message(error: object) -> str | None:
    if error is None:
        return None
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or error)
    return str(error)


def _elapsed(monotonic: Callable[[], float], started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


class UrllibTransport:
    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()
