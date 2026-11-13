"""Last reading per target, plus the UTC-day Agent ledger.

An alert fires when `qualifies` flips from false to true. If the push fails,
`qualifies` stays false so the next run still sees the transition.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from rtx5090_watcher.models import TargetState


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.targets: dict[str, TargetState] = {}
        self.budget_day = ""
        self.budget_micros = 0
        if path.exists():
            self._load(json.loads(path.read_text(encoding="utf-8")))

    def get(self, target_id: str) -> TargetState | None:
        return self.targets.get(target_id)

    def put(self, target_id: str, state: TargetState) -> None:
        self.targets[target_id] = state

    def spent_today(self, day: str) -> int:
        self._roll(day)
        return self.budget_micros

    def charge(self, day: str, micros: int) -> None:
        self._roll(day)
        self.budget_micros += micros

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "budget": {"day": self.budget_day, "agent_micros": self.budget_micros},
            "targets": {key: asdict(value) for key, value in self.targets.items()},
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _roll(self, day: str) -> None:
        if self.budget_day != day:
            self.budget_day = day
            self.budget_micros = 0

    def _load(self, raw: dict) -> None:
        budget = raw.get("budget") or {}
        self.budget_day = str(budget.get("day") or "")
        self.budget_micros = int(budget.get("agent_micros") or 0)
        for key, value in (raw.get("targets") or {}).items():
            self.targets[key] = TargetState(
                qualifies=bool(value.get("qualifies")),
                in_stock=value.get("in_stock"),
                price_cents=value.get("price_cents"),
                product=value.get("product"),
                tier=value.get("tier"),
                detail=value.get("detail"),
                checked_at=value.get("checked_at"),
                listing_url=value.get("listing_url"),
            )


def utc_day(now: datetime) -> str:
    return now.date().isoformat()
