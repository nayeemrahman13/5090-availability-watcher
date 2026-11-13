"""Published TinyFish prices, in millionths of a dollar so budgets do not drift.

Checked against https://www.tinyfish.ai/pricing on 13 Nov 2026:

  Search   $0.00 per request   (30/min, 500/hour)
  Fetch    $0.00 per URL       (150/min, 1,000/day)
  Agent    $0.016 per step
  Browser  $0.002 per minute
  Monitor  $0.005 per check

The Fetch reference also says a 402 once the 1,000 URL/day allowance is gone.
The pricing FAQ says Fetch keeps working at a $0 wallet balance. Either way
the default schedule stays under 1,000 Fetch URLs a day, so a Fetch call is $0.
Agent steps are the only spend this process is built to make.
"""

from __future__ import annotations

AGENT_STEP_MICROS = 16_000  # $0.016
FETCH_URL_MICROS = 0
RUNS_PER_DAY_EVERY_10_MIN = 144

# A card price outside this band is an accessory, a "you save" line, or noise.
MIN_CARD_CENTS = 50_000  # $500
MAX_CARD_CENTS = 2_000_000  # $20,000


def usd_to_micros(amount: float) -> int:
    return int(round(amount * 1_000_000))


def format_micros(micros: int) -> str:
    sign = "-" if micros < 0 else ""
    return f"{sign}${abs(int(micros)) / 1_000_000:.4f}"


def format_price_cents(cents: int | None) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def agent_cost_micros(steps: int) -> int:
    if steps < 0:
        raise ValueError("steps cannot be negative")
    return steps * AGENT_STEP_MICROS


class BudgetGuard:
    """Per-run and per-day caps on Agent spend.

    Before a run starts we hold `assumed_steps` (the run might cost that
    much). Concurrent runs each hold that reservation before any of them
    starts, so a wave cannot be admitted for more than the caps allow. When
    a run finishes, charge() drops one hold and adds the real `num_of_steps`.
    The number stored for the day is the charge, not the hold. A caller that
    never holds (the daily estimate) is unchanged: charge() just adds the
    actual cost. Fetch is not charged: the published price inside the daily
    allowance is $0.
    """

    def __init__(
        self,
        per_run_micros: int,
        per_day_micros: int,
        assumed_steps: int,
        already_spent_today: int = 0,
    ) -> None:
        if assumed_steps < 1:
            raise ValueError("assumed_steps must be at least 1")
        self.per_run_micros = per_run_micros
        self.per_day_micros = per_day_micros
        self.assumed_steps = assumed_steps
        self.today_micros = already_spent_today
        self.run_micros = 0
        self.held_micros = 0

    def reservation_micros(self) -> int:
        return agent_cost_micros(self.assumed_steps)

    def allow(self) -> tuple[bool, str]:
        reservation = self.reservation_micros()
        if self.run_micros + self.held_micros + reservation > self.per_run_micros:
            return False, "per-run"
        if self.today_micros + self.held_micros + reservation > self.per_day_micros:
            return False, "per-day"
        return True, ""

    def hold(self) -> int:
        """Call only after allow() returned True. Holds one pessimistic reservation."""

        micros = self.reservation_micros()
        self.held_micros += micros
        return micros

    def release_hold(self) -> None:
        """Drop one hold when a run produced no step count to charge."""

        self.held_micros -= min(self.held_micros, self.reservation_micros())

    def charge(self, steps: int | None) -> int:
        used = self.assumed_steps if steps is None else steps
        micros = agent_cost_micros(used)
        self.held_micros -= min(self.held_micros, self.reservation_micros())
        self.run_micros += micros
        self.today_micros += micros
        return micros


def fetch_urls_per_day(targets: int, runs_per_day: int = RUNS_PER_DAY_EVERY_10_MIN) -> int:
    return targets * runs_per_day


def scenario_agent_micros(calls: int, steps_each: int) -> int:
    return calls * agent_cost_micros(steps_each)


def count_calls_until_day_cap(
    guard_factory,
    targets_per_run: int,
    actual_steps: int,
) -> int:
    """How many Agent calls fit in a UTC day under both budgets.

    `guard_factory` is called once per scheduled run and must return a fresh
    BudgetGuard that already knows today's spend. The checker uses the same
    rule, so this count is the one the README quotes.
    """

    spent_today = 0
    calls = 0
    for _ in range(runs_safety_limit(targets_per_run)):
        guard = guard_factory(spent_today)
        ran = 0
        for _target in range(targets_per_run):
            ok, _which = guard.allow()
            if not ok:
                break
            guard.charge(actual_steps)
            ran += 1
            calls += 1
        spent_today = guard.today_micros
        if ran == 0:
            break
    return calls


def runs_safety_limit(targets_per_run: int) -> int:
    return RUNS_PER_DAY_EVERY_10_MIN + 5
