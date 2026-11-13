import json

from rtx5090_watcher.models import AgentSettings
from rtx5090_watcher.tinyfish import FETCH_URL, RUN_ASYNC_URL, STOCK_OUTPUT_SCHEMA, TinyFishClient


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        status, payload = self.responses.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, raw


class Clock:
    def __init__(self, times):
        self.times = list(times)
        self.index = 0

    def __call__(self):
        value = self.times[min(self.index, len(self.times) - 1)]
        self.index += 1
        return value


def _body(call) -> dict:
    return json.loads(call[3].decode())


def test_schema_is_the_documented_subset():
    assert STOCK_OUTPUT_SCHEMA["type"] == "object"
    assert "additionalProperties" not in STOCK_OUTPUT_SCHEMA
    assert "oneOf" not in json.dumps(STOCK_OUTPUT_SCHEMA)
    assert STOCK_OUTPUT_SCHEMA["properties"]["price"]["nullable"] is True
    assert "in_stock" in STOCK_OUTPUT_SCHEMA["required"]


def test_fetch_posts_the_documented_body():
    script = Scripted([(200, {"results": [{"url": "https://example.com/a", "text": "hi", "latency_ms": 12}], "errors": []})])
    client = TinyFishClient("test-key", script)
    pages = client.fetch(["https://example.com/a"])
    method, url, headers, _body_bytes, timeout = script.calls[0]
    assert method == "POST"
    assert url == FETCH_URL
    assert headers["X-API-Key"] == "test-key"
    assert timeout == 150
    body = _body(script.calls[0])
    assert body["urls"] == ["https://example.com/a"]
    assert body["format"] == "markdown"
    assert body["ttl"] == 0
    assert body["links"] is False
    assert pages["https://example.com/a"].text == "hi"
    assert pages["https://example.com/a"].latency_ms == 12


def test_fetch_keeps_per_url_errors_on_http_200():
    script = Scripted(
        [
            (
                200,
                {
                    "results": [],
                    "errors": [{"url": "https://www.bestbuy.com/fe", "error": "target_unreachable"}],
                },
            )
        ]
    )
    pages = TinyFishClient("test-key", script).fetch(["https://www.bestbuy.com/fe"])
    assert pages["https://www.bestbuy.com/fe"].error == "target_unreachable"


def test_agent_request_is_stealth_us_proxy_schema_and_no_max_steps():
    script = Scripted(
        [
            (403, {"error": {"code": "FORBIDDEN", "message": "mode is in beta"}}),
            (200, {"run_id": "run_123", "error": None}),
            (
                200,
                {
                    "run_id": "run_123",
                    "status": "COMPLETED",
                    "num_of_steps": 4,
                    "result": {
                        "product": "NVIDIA GeForce RTX 5090 Founders Edition",
                        "price": "$1,999.99",
                        "button_state": "Out of Stock",
                        "in_stock": False,
                        "listing_url": None,
                    },
                    "error": None,
                },
            ),
        ]
    )
    settings = AgentSettings("stealth", "US", "tinyfish", 75, True)
    outcome = TinyFishClient("test-key", script).run_agent(
        "https://www.bestbuy.com/fe",
        "Read only. Do not click Add to Cart.",
        settings,
        sleep=lambda _seconds: None,
        monotonic=Clock([0, 1]).__call__,
        poll_interval=0,
    )
    assert outcome.status == "COMPLETED"
    assert outcome.steps == 4
    assert outcome.strict_mode_fallback is True
    assert outcome.result["in_stock"] is False
    first = _body(script.calls[0])
    second = _body(script.calls[1])
    assert script.calls[0][1] == RUN_ASYNC_URL
    assert script.calls[0][2]["X-API-Key"] == "test-key"
    assert first["browser_profile"] == "stealth"
    assert first["proxy_config"] == {"enabled": True, "type": "tinyfish", "country_code": "US"}
    assert first["output_schema"]["required"] == STOCK_OUTPUT_SCHEMA["required"]
    assert first["agent_config"] == {"max_duration_seconds": 75, "mode": "strict"}
    assert "max_steps" not in first["agent_config"]
    assert "use_vault" not in first
    assert "use_profile" not in first
    assert "mode" not in second["agent_config"]
    assert second["goal"].startswith("Read only")
    assert script.calls[2][0] == "GET"
    assert script.calls[2][1].endswith("/v1/runs/run_123?screenshots=none&html=none")


def test_agent_is_cancelled_when_max_duration_passes():
    script = Scripted(
        [
            (200, {"run_id": "run_9", "error": None}),
            (200, {"run_id": "run_9", "status": "RUNNING", "num_of_steps": 2, "result": None, "error": None}),
            (200, {"status": "CANCELLED"}),
            (200, {"run_id": "run_9", "status": "CANCELLED", "num_of_steps": 2, "result": None, "error": None}),
        ]
    )
    settings = AgentSettings("stealth", "US", "tinyfish", 75, False)
    outcome = TinyFishClient("test-key", script).run_agent(
        "https://example.com",
        "read",
        settings,
        sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("should cancel before sleeping")),
        monotonic=Clock([0, 1_000]).__call__,
        poll_interval=0,
    )
    assert outcome.cancelled is True
    assert outcome.status == "CANCELLED"
    assert outcome.steps == 2
    assert script.calls[2][0] == "POST"
    assert script.calls[2][1].endswith("/v1/runs/run_9/cancel")
    assert "mode" not in _body(script.calls[0])["agent_config"]
