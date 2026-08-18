"""Turn a calibrated probability surface into a decision and a stake.

Euphoria quotes a multiplier ``m`` per cell. A winning tap returns ``m*stake``,
a loser returns nothing. So the edge on a cell is

    EV = p*m - 1          (per unit staked)

and the house is beatable only on cells where our ``p`` beats the quoted
breakeven ``1/m``. Everything here follows from that one line.

Three guards, in order:

1. **Rank on the lower bound, not the mean.** ``ev_lcb`` uses the calibrator's
   ``p_lcb``. A cell only ranks if the edge survives the uncertainty in our own
   probability estimate.
2. **Fractional Kelly.** ``f* = (p*m - 1) / (m - 1)`` is the growth-optimal
   fraction; we stake a quarter of it. Full Kelly on a mis-estimated ``p`` is
   how bankrolls die even when the edge is real.
3. **No trade is the default.** If nothing clears the bar the correct output is
   silence, and on a house-edge game that is most of the time. A policy that
   always finds something to tap is a policy that has stopped measuring.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

from src.analytics.calibration import Calibrator
from src.analytics.forecast import CellForecast, Diffusion

# Decision bars.
MIN_EDGE = 0.05          # need +5% EV on the lower bound before tapping
MIN_P_LCB = 0.02         # ignore lottery cells however juicy the multiplier
KELLY_FRACTION = 0.25
MIN_STAKE = 0.10
DEFAULT_BANKROLL = 100.0
MAX_BANKROLL_FRACTION = 0.05   # never risk more than 5% of the roll on one tap
EQUITY_KEEP = 600


@dataclass(frozen=True)
class CellScore:
    """A forecast cell priced against its quoted multiplier."""

    cell_x: int
    cell_y: int
    forward: int
    row_offset: int
    side: str
    distance: int
    lo: float
    hi: float
    t_start: float
    t_end: float
    horizon_s: float
    edge_cells: float
    sd_cells: float
    p_model: float
    p_cal: float
    p_lcb: float
    n_obs: int
    trusted: bool
    multiplier: float | None
    breakeven: float | None
    ev: float | None            # on the calibrated mean
    ev_lcb: float | None        # on the lower bound -- what we rank by
    kelly: float
    stake: float
    verdict: str                # tap | thin | negative | unquoted | unreachable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def tappable(self) -> bool:
        return self.verdict == "tap"


@dataclass
class Bankroll:
    """Paper bankroll. Mirrors real USDM only once the API hands us a balance."""

    start: float = DEFAULT_BANKROLL
    balance: float = DEFAULT_BANKROLL
    staked: float = 0.0
    returned: float = 0.0
    wins: int = 0
    losses: int = 0
    peak: float = DEFAULT_BANKROLL
    max_drawdown: float = 0.0
    live_synced: bool = False
    equity: deque = field(default_factory=lambda: deque(maxlen=EQUITY_KEEP))

    def __post_init__(self) -> None:
        if not self.equity:
            self.equity.append({"ts": time.time(), "balance": self.balance})

    @property
    def trades(self) -> int:
        return self.wins + self.losses

    @property
    def pnl(self) -> float:
        return self.balance - self.start

    @property
    def roi(self) -> float | None:
        return (self.pnl / self.start) if self.start > 0 else None

    @property
    def hit_rate(self) -> float | None:
        return (self.wins / self.trades) if self.trades else None

    @property
    def ruined(self) -> bool:
        """Out of money. Says so instead of looking like a quiet market."""
        return self.balance < MIN_STAKE

    def settle(self, stake: float, multiplier: float, won: bool, *, now: float | None = None) -> float:
        """Book one resolved tap. Returns the change in balance."""
        now = time.time() if now is None else now
        payout = stake * multiplier if won else 0.0
        delta = payout - stake
        # Ruin is a floor, not a number that keeps going. A negative balance is
        # not a state you can be in -- you cannot stake what you do not have --
        # and letting it go negative did real damage to the measurements rather
        # than to the book: `score_cell` sizes from `balance`, so once this went
        # under, every stake computed to zero, every tappable cell silently
        # became "thin", and the shadow book stopped trading. A 50-minute
        # replication window booked 154 trades instead of 1265 and read as a
        # quiet market. It was a bankrupt one.
        self.balance = max(0.0, self.balance + delta)
        self.staked += stake
        self.returned += payout
        if won:
            self.wins += 1
        else:
            self.losses += 1
        self.peak = max(self.peak, self.balance)
        self.max_drawdown = max(self.max_drawdown, self.peak - self.balance)
        self.equity.append({"ts": now, "balance": round(self.balance, 6)})
        return delta

    def sync_live(self, balance: float) -> None:
        """Adopt a real balance from the API once one is available."""
        if balance is None or balance <= 0:
            return
        self.balance = float(balance)
        if not self.live_synced:
            self.start = float(balance)
            self.peak = float(balance)
            self.live_synced = True
        self.peak = max(self.peak, self.balance)
        self.equity.append({"ts": time.time(), "balance": round(self.balance, 6)})

    # -- persistence --------------------------------------------------------
    # The equity curve is the only evidence that any of this works. Keeping it
    # in memory means every restart erases the track record, which is exactly
    # the record you need to read before risking real money.
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "start": self.start,
            "balance": self.balance,
            "staked": self.staked,
            "returned": self.returned,
            "wins": self.wins,
            "losses": self.losses,
            "peak": self.peak,
            "max_drawdown": self.max_drawdown,
            "live_synced": self.live_synced,
            "equity": list(self.equity)[-EQUITY_KEEP:],
        }

    @classmethod
    def from_json(cls, data: Any, *, default_start: float = DEFAULT_BANKROLL) -> "Bankroll":
        if not isinstance(data, dict):
            return cls(start=default_start, balance=default_start, peak=default_start)

        def _num(key: str, fallback: float) -> float:
            try:
                val = float(data.get(key))
            except (TypeError, ValueError):
                return fallback
            return val if val == val else fallback      # reject NaN

        start = _num("start", default_start)
        balance = _num("balance", start)
        roll = cls(
            start=start,
            balance=balance,
            staked=max(0.0, _num("staked", 0.0)),
            returned=max(0.0, _num("returned", 0.0)),
            wins=max(0, int(data.get("wins") or 0)),
            losses=max(0, int(data.get("losses") or 0)),
            peak=max(_num("peak", balance), balance),
            max_drawdown=max(0.0, _num("max_drawdown", 0.0)),
            live_synced=bool(data.get("live_synced")),
        )
        curve = data.get("equity")
        if isinstance(curve, list):
            roll.equity.clear()
            for point in curve[-EQUITY_KEEP:]:
                if isinstance(point, dict) and "balance" in point:
                    roll.equity.append(point)
            if not roll.equity:
                roll.equity.append({"ts": time.time(), "balance": roll.balance})
        return roll

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 6),
            "balance": round(self.balance, 6),
            "pnl": round(self.pnl, 6),
            "roi": round(self.roi, 6) if self.roi is not None else None,
            "staked": round(self.staked, 6),
            "returned": round(self.returned, 6),
            "wins": self.wins,
            "losses": self.losses,
            "trades": self.trades,
            "ruined": self.ruined,
            "hit_rate": round(self.hit_rate, 4) if self.hit_rate is not None else None,
            "peak": round(self.peak, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "live_synced": self.live_synced,
            "equity": list(self.equity)[-180:],
        }


def kelly_fraction(p: float, multiplier: float) -> float:
    """Growth-optimal fraction for a bet paying ``multiplier`` on a win."""
    if multiplier <= 1.0 or p <= 0.0:
        return 0.0
    f = (p * multiplier - 1.0) / (multiplier - 1.0)
    return max(0.0, min(1.0, f))


class Policy:
    def __init__(
        self,
        *,
        min_edge: float = MIN_EDGE,
        min_p_lcb: float = MIN_P_LCB,
        kelly_fraction_used: float = KELLY_FRACTION,
        max_stake: float = 10.0,
        min_stake: float = MIN_STAKE,
        max_bankroll_fraction: float = MAX_BANKROLL_FRACTION,
    ) -> None:
        self.min_edge = float(min_edge)
        self.min_p_lcb = float(min_p_lcb)
        self.kelly_fraction_used = float(kelly_fraction_used)
        self.max_stake = float(max_stake)
        self.min_stake = float(min_stake)
        self.max_bankroll_fraction = float(max_bankroll_fraction)

    def score_cell(
        self,
        cell: CellForecast,
        calibrator: Calibrator,
        *,
        vol_bucket: str,
        bankroll: Bankroll | None = None,
        diffusion_ok: bool = True,
        reachability: Any = None,
    ) -> CellScore:
        horizon = max(cell.t_end, 0.0)
        cal = calibrator.adjust(cell.p_touch, horizon, vol_bucket)
        mult = cell.multiplier
        ev = ev_lcb = None
        kelly = stake = 0.0
        verdict = "unquoted"

        # A failed volatility fit is the most dangerous state this system can be
        # in, because it fails toward confidence rather than caution: sigma
        # collapses to zero, the model concludes price cannot move, and every
        # at-the-money cell scores p = 1.0 with a fat apparent edge. Observed
        # live -- a feed dropout produced ten "tappable" cells at ev_lcb 0.51.
        # No fit, no bet.
        if not diffusion_ok:
            return CellScore(
                cell_x=cell.cell_x, cell_y=cell.cell_y, forward=cell.forward,
                row_offset=cell.row_offset, side=cell.side, distance=cell.distance,
                lo=cell.lo, hi=cell.hi, t_start=round(cell.t_start, 4),
                t_end=round(cell.t_end, 4), horizon_s=round(horizon, 4),
                edge_cells=cell.edge_cells, sd_cells=cell.sd_cells,
                p_model=round(cal.p_model, 6), p_cal=round(cal.p_cal, 6),
                p_lcb=round(cal.p_lcb, 6), n_obs=cal.n, trusted=False,
                multiplier=mult, breakeven=cell.breakeven,
                ev=None, ev_lcb=None, kelly=0.0, stake=0.0, verdict="no-fit",
            )

        if mult and mult > 1.0:
            ev = cal.p_cal * mult - 1.0
            ev_lcb = cal.p_lcb * mult - 1.0
            # Checked before the model gets a say, because the model is the
            # thing that is wrong here. Cells two rows out returned -1.0000
            # over 269 trades with zero wins, and `min_p_lcb` waved every one
            # of them through -- it consults our own estimate, and our own
            # estimate believed a 3.8-sigma move was better than one in four.
            # This asks the tape instead.
            if reachability is not None and reachability.veto(
                cell.distance, horizon, mult
            ):
                verdict = "untouched"
            elif cal.p_lcb < self.min_p_lcb:
                verdict = "unreachable"
            elif not cal.trusted:
                # The lower bound alone does not survive contact with a fat
                # multiplier. A cold bucket seeded at p=0.5 still yields a
                # lower bound near 0.23, and against a 40x quote that reads as
                # +800% EV -- observed live, seven "tappable" cells with zero
                # warm buckets behind them. Evidence first, then edge.
                verdict = "unproven"
            elif ev_lcb < self.min_edge:
                verdict = "negative" if ev_lcb <= 0 else "thin"
            else:
                verdict = "tap"
                kelly = kelly_fraction(cal.p_lcb, mult)
                roll = bankroll.balance if bankroll else DEFAULT_BANKROLL
                raw = kelly * self.kelly_fraction_used * roll
                capped = min(raw, self.max_stake, roll * self.max_bankroll_fraction)
                stake = round(max(0.0, capped), 4)
                if stake < self.min_stake:
                    # Say which of the two it is. A cell with +6.2% edge on the
                    # lower bound and a Kelly fraction of 0.23 was being sized
                    # to zero and shown on the overlay as "NO TRADE - 0 edge",
                    # because the roll was empty. That reads as "the market has
                    # nothing", which is a different fact and the wrong one to
                    # act on. Out of money is not out of edge.
                    verdict = "no-funds" if (bankroll and bankroll.ruined) else "thin"
                    stake = 0.0

        return CellScore(
            cell_x=cell.cell_x,
            cell_y=cell.cell_y,
            forward=cell.forward,
            row_offset=cell.row_offset,
            side=cell.side,
            distance=cell.distance,
            lo=cell.lo,
            hi=cell.hi,
            t_start=round(cell.t_start, 4),
            t_end=round(cell.t_end, 4),
            horizon_s=round(horizon, 4),
            edge_cells=cell.edge_cells,
            sd_cells=cell.sd_cells,
            p_model=round(cal.p_model, 6),
            p_cal=round(cal.p_cal, 6),
            p_lcb=round(cal.p_lcb, 6),
            n_obs=cal.n,
            trusted=cal.trusted,
            multiplier=mult,
            breakeven=cell.breakeven,
            ev=round(ev, 6) if ev is not None else None,
            ev_lcb=round(ev_lcb, 6) if ev_lcb is not None else None,
            kelly=round(kelly, 6),
            stake=stake,
            verdict=verdict,
        )

    def score(
        self,
        cells: Iterable[CellForecast],
        calibrator: Calibrator,
        *,
        vol_bucket: str,
        bankroll: Bankroll | None = None,
        diffusion_ok: bool = True,
        reachability: Any = None,
    ) -> list[CellScore]:
        scored = [
            self.score_cell(c, calibrator, vol_bucket=vol_bucket, bankroll=bankroll,
                            diffusion_ok=diffusion_ok, reachability=reachability)
            for c in cells
        ]
        scored.sort(
            key=lambda s: (
                s.ev_lcb if s.ev_lcb is not None else -99.0,
                s.p_cal,
                -s.horizon_s,
            ),
            reverse=True,
        )
        return scored

    @staticmethod
    def choose(scored: Sequence[CellScore]) -> CellScore | None:
        for s in scored:
            if s.tappable and s.stake > 0:
                return s
        return None

    @staticmethod
    def best_unquoted(scored: Sequence[CellScore]) -> CellScore | None:
        """Highest-probability cell when the page gave us no multipliers.

        Advisory only -- without a multiplier there is no EV, so this can be
        shown but must never be staked.
        """
        ranked = [s for s in scored if s.multiplier is None]
        if not ranked:
            return None
        return max(ranked, key=lambda s: (s.p_cal, -s.horizon_s))


def surface_summary(scored: Sequence[CellScore]) -> dict[str, Any]:
    """Compact stats for the dashboard header."""
    quoted = [s for s in scored if s.multiplier]
    taps = [s for s in scored if s.tappable]
    best = max((s.ev_lcb for s in quoted if s.ev_lcb is not None), default=None)
    return {
        "cells": len(scored),
        "quoted": len(quoted),
        "tappable": len(taps),
        "best_ev_lcb": round(best, 6) if best is not None else None,
        "reachable": sum(1 for s in scored if s.p_cal >= 0.05),
        "verdicts": {
            v: sum(1 for s in scored if s.verdict == v)
            for v in ("tap", "thin", "unproven", "negative", "unreachable",
                      "untouched", "no-funds", "unquoted", "no-fit")
        },
    }
