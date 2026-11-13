"""Push a stock alert to ntfy.sh.

No account. The topic in NTFY_TOPIC is the address. POST the body, put the
headline in the Title header and the product URL in Click.
https://docs.ntfy.sh/publish/
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Alert:
    title: str
    message: str
    click: str


class NotifyError(Exception):
    def __init__(self, message: str, alert: Alert) -> None:
        super().__init__(message)
        self.alert = alert


def build_alert(retailer: str, product: str, price: str, link: str) -> Alert:
    return Alert(
        title=f"RTX 5090 in stock at {retailer} for {price}",
        message="\n".join(
            [
                f"Product: {product}",
                f"Price: {price}",
                f"Retailer: {retailer}",
                f"Link: {link}",
            ]
        ),
        click=link,
    )


class NtfyNotifier:
    def __init__(self, topic: str | None, server: str | None = None, transport=None) -> None:
        self.topic = topic or None
        self.server = (server or "https://ntfy.sh").rstrip("/")
        self.transport = transport or _urllib_transport
        self.last_response: dict | None = None

    def send(self, alert: Alert) -> dict:
        if not self.topic:
            raise NotifyError("NTFY_TOPIC is not set", alert)
        url = f"{self.server}/{self.topic}"
        headers = {
            "Title": alert.title,
            "Click": alert.click,
            "Priority": "high",
            "Tags": "rotating_light,computer",
            "Content-Type": "text/plain; charset=utf-8",
        }
        try:
            status, body = self.transport("POST", url, headers, alert.message.encode("utf-8"), 20)
        except urllib.error.URLError as exc:
            raise NotifyError(f"ntfy unreachable: {exc.reason}", alert) from exc
        if status < 200 or status >= 300:
            raise NotifyError(f"ntfy HTTP {status}: {body[:300]}", alert)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body}
        self.last_response = parsed if isinstance(parsed, dict) else {"raw": body}
        return self.last_response


def notifier_from_env(topic: str | None = None) -> NtfyNotifier:
    chosen = topic if topic is not None else os.environ.get("NTFY_TOPIC", "").strip()
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    return NtfyNotifier(topic=chosen or None, server=server)


def _urllib_transport(method, url, headers, body, timeout) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", errors="replace")
