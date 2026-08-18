"""Tilt guard: is the account being traded faster than it is being thought about?

This measures behaviour, not markets. Three things move together when trading
stops being decisions and starts being reactions:

* **Drawdown from the session peak.** Not from where you started -- from the
  best the account reached today. That is the number the mind is actually
  chasing.
* **Trade rate.** The house publishes a lifetime `settledTrades` counter, so
  trades per minute is directly observable rather than inferred.
* **Loss velocity.** Balance lost per minute over a recent window. Slow decline
  and a cliff are different situations and should not read the same.

The guard states a fact and a threshold. It does not lecture, and it cannot
stop anyone -- it can only make the pattern visible while it is happening,
which is the one moment it is hardest to see.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

WINDOW_S = 600.0           # ten minutes of recent behaviour
KEEP = 600

# A busy-but-deliberate pace, per minute. Above this is reacting, not deciding.
FAST_RATE = 4.0
HOT_RATE = 8.0
# Drawdown from the session peak, as a fraction.
DRAWDOWN_WARN = 0.15
DRAWDOWN_BAD = 0.30
# Balance lost per minute, as a fraction of the peak.
BLEED_WARN = 0.02
BLEED_BAD = 0.05


@dataclass
class TiltGuard:
    balances: deque = field(default_factory=lambda: deque(maxlen=KEEP))
    counts: deque = field(default_factory=lambda: deque(maxlen=KEEP))
    peak: float = 0.0
    session_start_balance: float | None = None
    session_start_ts: float = 0.0

    def begin_session(self, *, now: float | None = None) -> None:
        self.session_start_ts = time.time() if now is None else now
        self.session_start_balance = None
        self.peak = 0.0

    # -- ingestion ----------------------------------------------------------
    def note_balance(self, balance: Any, *, now: float | None = None) -> None:
        try:
            value = float(balance)
        except (TypeError, ValueError):
            return
        if value < 0 or value != value:
            return
        now = time.time() if now is None else now
        if self.session_start_balance is None:
            self.session_start_balance = value
        self.peak = max(self.peak, value)
        self.balances.append((now, value))

    def note_trade_count(self, settled: Any, *, now: float | None = None) -> None:
        """The house's lifetime settled-trade counter."""
        try:
            count = int(settled)
        except (TypeError, ValueError):
            return
        if count <= 0:
            return
        now = time.time() if now is None else now
        if self.counts and count < self.counts[-1][1]:
            return                          # counter went backwards; ignore
        self.counts.append((now, count))

    # -- reading ------------------------------------------------------------
    def _recent(self, seq: deque, now: float, window_s: float) -> list:
        return [row for row in seq if now - row[0] <= window_s]

    def trades_in_window(self, *, now: float, window_s: float = WINDOW_S) -> tuple[int, float]:
        rows = self._recent(self.counts, now, window_s)
        if len(rows) < 2:
            return 0, 0.0
        span = rows[-1][0] - rows[0][0]
        traded = rows[-1][1] - rows[0][1]
        if span <= 0:
            return int(max(0, traded)), 0.0
        return int(max(0, traded)), max(0.0, traded) / (span / 60.0)

    def bleed(self, *, now: float, window_s: float = WINDOW_S) -> tuple[float, float]:
        """(balance change, change per minute) over the recent window."""
        rows = self._recent(self.balances, now, window_s)
        if len(rows) < 2:
            return 0.0, 0.0
        span = rows[-1][0] - rows[0][0]
        change = rows[-1][1] - rows[0][1]
        if span <= 0:
            return change, 0.0
        return change, change / (span / 60.0)

    def assess(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        balance = self.balances[-1][1] if self.balances else None
        peak = max(self.peak, balance or 0.0)
        drawdown = (peak - balance) if balance is not None else 0.0
        dd_frac = (drawdown / peak) if peak > 0 else 0.0
        traded, rate = self.trades_in_window(now=now)
        change, per_min = self.bleed(now=now)
        bleed_frac = (-per_min / peak) if (peak > 0 and per_min < 0) else 0.0

        reasons: list[str] = []
        score = 0
        if dd_frac >= DRAWDOWN_BAD:
            reasons.append(f"down {dd_frac:.0%} from today's high")
            score += 2
        elif dd_frac >= DRAWDOWN_WARN:
            reasons.append(f"down {dd_frac:.0%} from today's high")
            score += 1
        if rate >= HOT_RATE:
            reasons.append(f"{rate:.0f} trades a minute")
            score += 2
        elif rate >= FAST_RATE:
            reasons.append(f"{rate:.0f} trades a minute")
            score += 1
        if bleed_frac >= BLEED_BAD:
            reasons.append(f"losing {bleed_frac:.0%} of the account per minute")
            score += 2
        elif bleed_frac >= BLEED_WARN:
            reasons.append(f"losing {bleed_frac:.0%} of the account per minute")
            score += 1

        if score >= 4:
            severity = "tilted"
        elif score >= 2:
            severity = "elevated"
        else:
            severity = "calm"

        if severity == "tilted":
            headline = "trading fast into a drawdown"
        elif severity == "elevated":
            headline = "pace is picking up"
        elif balance is not None:
            headline = "steady"
        else:
            headline = "no balance seen yet"

        # What one more max-size loss would cost, as a share of what is left.
        risk_of_ruin = None
        if balance and balance > 0:
            risk_of_ruin = round(min(1.0, 10.0 / balance), 4)

        return {
            "flagged": severity != "calm",
            "severity": severity,
            "headline": headline,
            "reasons": reasons,
            "balance": round(balance, 6) if balance is not None else None,
            "peak": round(peak, 6) if peak else None,
            "drawdown": round(drawdown, 6),
            "drawdown_pct": round(dd_frac, 4),
            "trades_10m": traded,
            "trade_rate_per_min": round(rate, 2),
            "change_10m": round(change, 6),
            "change_per_min": round(per_min, 6),
            "session_change": round(balance - self.session_start_balance, 6)
                if (balance is not None and self.session_start_balance is not None) else None,
            "max_stake_share": risk_of_ruin,
            "samples": {"balances": len(self.balances), "counts": len(self.counts)},
        }
