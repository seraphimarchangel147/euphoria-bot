"""Paper/real PnL book for /pnl.

Accounting only. This module does not change shadow edge, policy, or
signals. Baseline −0.2712 is a separate shadow-edge number and is not
used here.

Live /pnl (owner book, 2026-08-16) reported avg_multiplier 33.4. That
was the arithmetic mean of *quoted* multipliers on every settled paper
trade, including 80x lottery tickets that lost. One tail print dominates.
Back-solving the same book from the per-unit identity

    expectancy_per_unit = hit_rate * m_win - 1
    => m_win = (1 + expectancy_per_unit) / hit_rate

gives 2.70. That is the typical winning multiplier that actually
reconciles with the cash. Shape check: E = -0.271, hit_rate ≈ 0.27
→ (1 - 0.271) / 0.27 ≈ 2.70. If a headline m looks like 12x while
expectancy is still negative, the formula is wrong.

This checkout does not have the owner's live /pnl JSON. Tests use a
synthetic reproduction of that 33.4-vs-2.70 book.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round_or_none(value: float | None, ndigits: int) -> float | None:
    return None if value is None else round(value, ndigits)


def _stake_weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    weight = sum(stake for stake, _ in pairs)
    if weight <= 0:
        return None
    return sum(stake * value for stake, value in pairs) / weight


def _implied_win_multiplier(hit_rate: float | None, expectancy_per_unit: float | None) -> float | None:
    """m_win that satisfies E = hit_rate * m_win - 1 when losses pay 0."""
    if hit_rate is None or hit_rate <= 0 or expectancy_per_unit is None:
        return None
    return (1.0 + expectancy_per_unit) / hit_rate


@dataclass
class Trade:
    kind: str
    outcome: str
    stake: float
    delta: float
    multiplier: float | None = None
    tag: str | None = None
    ts: float | None = None
    p_lcb: float | None = None
    ev_lcb: float | None = None


def _settled(rows: Iterable[Trade], kind: str | None = None) -> list[Trade]:
    out = [t for t in rows if t.outcome in ("win", "loss")]
    if kind is not None:
        out = [t for t in out if t.kind == kind]
    return out


def _view(rows: Iterable[Trade], *, min_n: int = 1) -> dict[str, Any]:
    """Slice stats. hit_rate is wins/n (count). expectancy is pnl/staked.

    p_lcb / ev_lcb may be stored on trades. They are not averaged here —
    a mean of those lower bounds would manufacture a prettier number than
    the per-unit cash.
    """
    rows = list(rows)
    if len(rows) < min_n:
        return {
            "n": len(rows),
            "wins": 0,
            "staked": 0.0,
            "pnl": 0.0,
            "hit_rate": None,
            "hit_rate_stake_weighted": None,
            "expectancy": None,
        }
    wins = sum(1 for t in rows if t.outcome == "win")
    staked = sum(max(0.0, t.stake) for t in rows)
    pnl = sum(t.delta for t in rows)
    wins_staked = sum(max(0.0, t.stake) for t in rows if t.outcome == "win")
    hit_rate = wins / len(rows) if rows else None
    hit_rate_stake_weighted = (wins_staked / staked) if staked > 0 else None
    expectancy = (pnl / staked) if staked > 0 else None
    return {
        "n": len(rows),
        "wins": wins,
        "staked": staked,
        "pnl": pnl,
        "hit_rate": hit_rate,
        "hit_rate_stake_weighted": hit_rate_stake_weighted,
        "expectancy": expectancy,
    }


class PnLTracker:
    """In-memory (optionally persisted) paper/real trade book."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.trades: list[Trade] = []
        if self.path is not None and self.path.exists():
            self._load()

    def record_paper(
        self,
        *,
        stake: float,
        outcome: str,
        multiplier: float | None = None,
        delta: float | None = None,
        tag: str | None = None,
        ts: float | None = None,
        p_lcb: float | None = None,
        ev_lcb: float | None = None,
    ) -> Trade:
        """Record a settled paper trade. Losses pay 0 (delta = -stake)."""
        if outcome not in ("win", "loss"):
            raise ValueError("paper outcome must be 'win' or 'loss'")
        if delta is None:
            if outcome == "loss":
                delta = -max(0.0, float(stake))
            elif multiplier is not None:
                delta = max(0.0, float(stake)) * (float(multiplier) - 1.0)
            else:
                raise ValueError("winning paper trade needs multiplier or delta")
        trade = Trade(
            kind="paper",
            outcome=outcome,
            stake=float(stake),
            delta=float(delta),
            multiplier=float(multiplier) if multiplier is not None else None,
            tag=tag,
            ts=ts,
            p_lcb=p_lcb,
            ev_lcb=ev_lcb,
        )
        self.trades.append(trade)
        self._persist()
        return trade

    def record_real(
        self,
        *,
        stake: float,
        delta: float,
        outcome: str = "loss",
        multiplier: float | None = None,
        tag: str | None = None,
        ts: float | None = None,
    ) -> Trade:
        if outcome not in ("win", "loss"):
            outcome = "win" if delta > 0 else "loss"
        trade = Trade(
            kind="real",
            outcome=outcome,
            stake=float(stake),
            delta=float(delta),
            multiplier=float(multiplier) if multiplier is not None else None,
            tag=tag,
            ts=ts,
        )
        self.trades.append(trade)
        self._persist()
        return trade

    def paper_summary(self) -> dict[str, Any]:
        """Settled paper book.

        Headline ``avg_multiplier`` is the reconciled win multiplier
        ``(1 + expectancy_per_unit) / hit_rate``, not the arithmetic mean
        of quoted multipliers. Live /pnl showed 33.4 for that raw mean;
        the same book back-solves to 2.70. The 33.4 figure is parked on
        ``avg_multiplier_raw`` so it cannot silently become the headline
        again. ``implied_multiplier`` is the same reconciled number under
        an explicit name.

        Honest twins (not headlines): stake-weighted mean of quoted
        multipliers, median of quoted multipliers, mean of *winning*
        multipliers. Headline ``hit_rate`` stays wins/n so it remains the
        rate used in the 2.70 back-solve; ``hit_rate_stake_weighted`` is
        the cash-weighted twin.
        """
        rows = _settled(self.trades, kind="paper")
        wins = sum(1 for t in rows if t.outcome == "win")
        staked = sum(max(0.0, t.stake) for t in rows)
        pnl = sum(t.delta for t in rows)
        wins_staked = sum(max(0.0, t.stake) for t in rows if t.outcome == "win")

        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for trade in rows:
            equity += trade.delta
            if equity > peak:
                peak = equity
            drawdown = equity - peak
            if drawdown < max_drawdown:
                max_drawdown = drawdown

        quoted = [(t, t.multiplier) for t in rows if t.multiplier]
        avg_mult = [m for _, m in quoted]
        win_mult = [t.multiplier for t in rows if t.outcome == "win" and t.multiplier]
        stake_mult = [(max(0.0, t.stake), m) for t, m in quoted if t.stake > 0]

        hit_rate = wins / len(rows) if rows else None
        hit_rate_stake_weighted = (wins_staked / staked) if staked > 0 else None
        expectancy_per_unit = (pnl / staked) if staked > 0 else None
        reconciled = _implied_win_multiplier(hit_rate, expectancy_per_unit)

        return {
            "n": len(rows),
            "wins": wins,
            "staked": staked,
            "pnl": pnl,
            "hit_rate": hit_rate,
            "hit_rate_stake_weighted": hit_rate_stake_weighted,
            "expectancy_per_unit": expectancy_per_unit,
            "avg_multiplier": _round_or_none(reconciled, 3),
            "implied_multiplier": _round_or_none(reconciled, 3),
            "avg_multiplier_raw": _round_or_none(_mean(avg_mult), 3),
            "avg_multiplier_stake_weighted": _round_or_none(_stake_weighted_mean(stake_mult), 3),
            "median_multiplier": _round_or_none(statistics.median(avg_mult) if avg_mult else None, 3),
            "avg_win_multiplier": _round_or_none(_mean(win_mult), 3),
            "max_drawdown": max_drawdown,
        }

    def session(self) -> dict[str, Any]:
        rows = _settled(self.trades, kind="paper")
        wins = sum(1 for t in rows if t.outcome == "win")
        staked = sum(max(0.0, t.stake) for t in rows)
        wins_staked = sum(max(0.0, t.stake) for t in rows if t.outcome == "win")
        paper_pnl = sum(t.delta for t in rows)
        return {
            "paper_n": len(rows),
            "paper_pnl": paper_pnl,
            "paper_staked": staked,
            "paper_hit_rate": round(wins / len(rows), 4) if rows else None,
            "paper_hit_rate_stake_weighted": round(wins_staked / staked, 4) if staked > 0 else None,
            # No external wallet mark on this checkout; recorded reals fully explain themselves.
            "unexplained": 0.0,
        }

    def streaks(self) -> dict[str, Any]:
        rows = _settled(self.trades, kind="paper")
        current = 0
        current_kind: str | None = None
        max_win = 0
        max_loss = 0
        run = 0
        run_kind: str | None = None
        for trade in rows:
            if run_kind == trade.outcome:
                run += 1
            else:
                run_kind = trade.outcome
                run = 1
            if trade.outcome == "win":
                max_win = max(max_win, run)
            else:
                max_loss = max(max_loss, run)
            current_kind = trade.outcome
            current = run
        return {
            "current": current,
            "current_kind": current_kind,
            "max_win": max_win,
            "max_loss": max_loss,
        }

    def real_summary(self) -> dict[str, Any]:
        rows = [t for t in self.trades if t.kind == "real"]
        return {
            "n": len(rows),
            "trading_change": sum(t.delta for t in rows),
        }

    def attribution(self, key: str = "tag", *, min_n: int = 1) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[Trade]] = {}
        for trade in _settled(self.trades, kind="paper"):
            label = getattr(trade, key, None) or "unknown"
            groups.setdefault(str(label), []).append(trade)
        return {label: _view(bucket, min_n=min_n) for label, bucket in groups.items()}

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(trade) for trade in self.trades]
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        allowed = {f.name for f in fields(Trade)}
        self.trades = [Trade(**{k: v for k, v in row.items() if k in allowed}) for row in raw]
