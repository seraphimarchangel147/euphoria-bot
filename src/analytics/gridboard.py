"""Dense per-window scoreboard: record the whole surface, grade every cell.

The old GradeBook graded one named square per window. The grid offers far more:
once a column closes, every cell we scored has a known answer, and none of them
cost anything to observe. GridBoard books all of them, feeds the calibrator, and
keeps the running hit-rate / Brier / bankroll numbers the dashboard shows.

Two rules keep the training signal honest:

* **One label per (cell, horizon band).** A cell six columns out is a different
  prediction problem from the same cell one column out, so each horizon band
  gets its own first-seen snapshot. Re-recording a cell as its column
  approaches would otherwise flood the easy horizons and teach nothing.
* **A window with no tape is discarded, not scored as a miss.** When the page
  feed drops out there are no ticks, every band looks untouched, and a naive
  scorer would teach the model that the grid is unreachable. Windows below
  ``MIN_TICKS_TO_GRADE`` are dropped with a counter instead.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from src.analytics.calibration import Calibrator, horizon_bucket
from src.analytics.policy import Bankroll, CellScore

MIN_TICKS_TO_GRADE = 2
WINDOW_KEEP = 240
TAP_KEEP = 200
MAX_PENDING = 4000
MAX_BOOKED = 20_000
# How far a cell's absolute start may sit from where its column index says it
# should, before we call the two axes out of step. Transport jitter lives well
# inside this; a whole-column error is 5s and cannot hide in it.
ALIGN_TOLERANCE_S = 0.25
# ~90 seconds of scored cells at the loop rate, enough to match any tap.
SCORE_TAPE_KEEP = 6000


@dataclass
class PendingCell:
    cell_x: int
    cell_y: int
    lo: float
    hi: float
    start_ts: float
    end_ts: float
    p_model: float
    p_cal: float
    horizon_s: float
    vol: str
    forward: int
    row_offset: int
    distance: int = 0
    # The quote this cell carried when we first saw it. Without it a settled
    # label cannot be keyed by price, and the only question that matters --
    # does any cell beat its OWN quote -- is unanswerable. Two desks tried to
    # test it and both stopped here.
    multiplier: float | None = None


@dataclass
class OpenTap:
    cell_x: int
    cell_y: int
    lo: float
    hi: float
    start_ts: float
    end_ts: float
    stake: float
    multiplier: float
    p_lcb: float
    ev_lcb: float
    side: str
    forward: int
    placed_at: float
    distance: int = 0


@dataclass
class GridBoard:
    """Records forecasts, grades them, and feeds the learner."""

    calibrator: Calibrator | None = None
    reachability: Any = None
    bankroll: Bankroll | None = None
    pending: dict[tuple[int, int, int], PendingCell] = field(default_factory=dict)
    open_taps: list[OpenTap] = field(default_factory=list)
    windows: deque = field(default_factory=lambda: deque(maxlen=WINDOW_KEEP))
    taps: deque = field(default_factory=lambda: deque(maxlen=TAP_KEEP))
    graded_cells: int = 0
    touched_cells: int = 0
    dropped_windows: int = 0
    dropped_cells: int = 0
    misaligned_cells: int = 0
    by_horizon: dict[int, list[float]] = field(default_factory=dict)
    # Every square ever staked, so a settled one can never be re-entered.
    _booked: set = field(default_factory=set)
    # Called with each settlement as it happens, so P&L never has to poll a
    # bounded list and hope nothing rolled off it.
    on_settle: Any = None
    # Called with (multiplier, distance, horizon_s, touched) rows as columns
    # settle. Deliberately a hook rather than a ledger: the quote-keyed edge
    # test is owned elsewhere, and this file only has to make the data exist.
    on_quote_label: Any = None
    _settle_seq: int = 0
    # What the surface said, moment by moment. A real tap arrives after the
    # fact, and a cell's probability changes on every pass as its horizon
    # shrinks -- so the only way to know what we believed *when the human
    # tapped* is to have written it down at the time.
    score_tape: deque = field(default_factory=lambda: deque(maxlen=SCORE_TAPE_KEEP))

    # -- record -------------------------------------------------------------
    def record(
        self,
        scores: Sequence[CellScore],
        *,
        now: float,
        vol: str,
        column_s: float = 5.0,
        server_offset_s: float = 0.0,
    ) -> int:
        """Snapshot each (cell, horizon band) the first time we see it.

        Also checks the one invariant that ties the two axes together: the
        absolute start we are about to grade must land in the column the cell
        names. Every timing bug this system has had would trip it.
        """
        added = 0
        for s in scores:
            if s.t_end <= 0:
                continue
            self.score_tape.append({
                "ts": now, "cell_x": s.cell_x, "cell_y": s.cell_y,
                "p_model": s.p_model, "p_cal": s.p_cal, "p_lcb": s.p_lcb,
                "ev_lcb": s.ev_lcb, "verdict": s.verdict, "vol": vol,
                "lo": s.lo, "hi": s.hi, "end_ts": now + s.t_end,
                "horizon_s": s.horizon_s, "multiplier": s.multiplier,
                "distance": s.distance, "side": s.side,
            })
            key = (s.cell_x, s.cell_y, horizon_bucket(s.horizon_s))
            if key in self.pending:
                continue
            if column_s > 0:
                # Where the cell's own column index says it starts, relative to
                # now. Compared in seconds rather than by flooring to a column,
                # because an exact boundary lands a floor either side of it.
                expected = s.cell_x * column_s - (now + server_offset_s)
                if abs(expected - s.t_start) > ALIGN_TOLERANCE_S:
                    self.misaligned_cells += 1
            self.pending[key] = PendingCell(
                cell_x=s.cell_x,
                cell_y=s.cell_y,
                lo=s.lo,
                hi=s.hi,
                start_ts=now + s.t_start,
                end_ts=now + s.t_end,
                p_model=s.p_model,
                p_cal=s.p_cal,
                horizon_s=s.horizon_s,
                vol=vol,
                forward=s.forward,
                row_offset=s.row_offset,
                distance=s.distance,
                multiplier=s.multiplier,
            )
            added += 1
        if len(self.pending) > MAX_PENDING:      # runaway guard
            oldest = sorted(self.pending.items(), key=lambda kv: kv[1].end_ts)
            for key, _ in oldest[: len(self.pending) - MAX_PENDING]:
                self.pending.pop(key, None)
        return added

    def register_tap(self, score: CellScore, *, now: float) -> OpenTap | None:
        """Book a chosen cell as a paper position awaiting its column close.

        A cell may be booked at most ONCE, ever. Deduping against `open_taps`
        alone was not enough: once a position settled it left that list, the
        think loop re-proposed the same best-EV cell on its next pass, and the
        same square was booked and paid again and again. One cell paid out ten
        times in a single run and produced almost all of a 30x "profit" that
        never existed. A settled square is finished; it cannot be re-entered.
        """
        if not score.tappable or score.stake <= 0 or not score.multiplier:
            return None
        if score.t_end <= 0:                    # that column has already closed
            return None
        key = (score.cell_x, score.cell_y)
        if key in self._booked:
            return None
        if any(t.cell_x == score.cell_x and t.cell_y == score.cell_y for t in self.open_taps):
            return None
        self._booked.add(key)
        if len(self._booked) > MAX_BOOKED:
            keep = {(t.cell_x, t.cell_y) for t in self.open_taps}
            self._booked = keep | set(list(self._booked)[-MAX_BOOKED // 2:])
        tap = OpenTap(
            cell_x=score.cell_x,
            cell_y=score.cell_y,
            lo=score.lo,
            hi=score.hi,
            start_ts=now + score.t_start,
            end_ts=now + score.t_end,
            stake=score.stake,
            multiplier=float(score.multiplier),
            p_lcb=score.p_lcb,
            ev_lcb=score.ev_lcb or 0.0,
            side=score.side,
            forward=score.forward,
            placed_at=now,
            distance=score.distance,
        )
        self.open_taps.append(tap)
        return tap

    # -- grade --------------------------------------------------------------
    @staticmethod
    def _window_ticks(ticks: Sequence[Any], start_ts: float, end_ts: float, symbol: str) -> list[float]:
        symbol = symbol.upper()
        return [
            t.price for t in ticks
            if getattr(t, "symbol", "").upper() == symbol
            and getattr(t, "price", 0) > 0
            and start_ts <= t.ts < end_ts
        ]

    def settle(
        self,
        ticks: Sequence[Any],
        *,
        now: float,
        symbol: str = "ETH",
    ) -> dict[str, Any]:
        """Grade every pending cell whose column has closed. Feeds the learner."""
        due = [(k, c) for k, c in self.pending.items() if c.end_ts <= now]
        if not due and not self._due_taps(now):
            return {"graded": 0, "touched": 0, "dropped": 0, "taps": 0}

        # Group by column so one tick scan serves every cell in that window.
        columns: dict[tuple[float, float], list[tuple[Any, PendingCell]]] = {}
        for key, cell in due:
            columns.setdefault((cell.start_ts, cell.end_ts), []).append((key, cell))

        graded = touched = dropped = 0
        learn_rows: list[tuple[float, float, str, bool]] = []
        reach_rows: list[tuple[int, float, bool]] = []
        quote_rows: list[tuple[float, int, float, bool, str]] = []
        for (start_ts, end_ts), rows in columns.items():
            prices = self._window_ticks(ticks, start_ts, end_ts, symbol)
            if len(prices) < MIN_TICKS_TO_GRADE:
                for key, _cell in rows:
                    self.pending.pop(key, None)
                dropped += len(rows)
                self.dropped_cells += len(rows)
                self.dropped_windows += 1
                continue
            lo_px, hi_px = min(prices), max(prices)
            hits = 0
            for key, cell in rows:
                self.pending.pop(key, None)
                # A band is touched iff the window's price range overlaps it.
                was = (lo_px < cell.hi) and (hi_px >= cell.lo)
                learn_rows.append((cell.p_model, cell.horizon_s, cell.vol, was))
                # Same free label, asked a different question: not "was our
                # probability right" but "does the tape ever reach this far".
                reach_rows.append((cell.distance, cell.horizon_s, was))
                # Same free label a third time, now carrying the price it was
                # offered at. Pooling by (distance, horizon) compares a bucket
                # average against whichever quote in it the house rates least
                # likely, which manufactures an edge by construction. Keyed by
                # quote, that comparison becomes honest.
                if cell.multiplier:
                    # Carries the volatility regime the cell was scored in.
                    # The 1.02-1.04x band measured 546/552 -- but entirely on
                    # tape where price moved $0.14 in three minutes. That edge
                    # is "a quiet market stays in its row for five seconds",
                    # which is a statement ABOUT the regime, so a number
                    # averaged across regimes cannot test it.
                    quote_rows.append((cell.multiplier, cell.distance,
                                       cell.horizon_s, was, cell.vol))
                bucket = self.by_horizon.setdefault(horizon_bucket(cell.horizon_s), [0.0, 0.0])
                bucket[0] += 1.0 if was else 0.0
                bucket[1] += 1.0
                graded += 1
                if was:
                    hits += 1
                    touched += 1
            self.windows.append({
                "start_ts": start_ts,
                "end_ts": end_ts,
                "cells": len(rows),
                "touched": hits,
                "lo": round(lo_px, 6),
                "hi": round(hi_px, 6),
                # Where the column actually finished. The high/low say how far
                # price reached; the close says where it settled, which is what
                # the next column opens from.
                "open": round(prices[0], 6),
                "close": round(prices[-1], 6),
                "ticks": len(prices),
            })

        if learn_rows and self.calibrator is not None:
            self.calibrator.observe_many(learn_rows)
        if reach_rows and self.reachability is not None:
            self.reachability.observe_many(reach_rows)
        if quote_rows and self.on_quote_label is not None:
            try:
                self.on_quote_label(quote_rows)
            except Exception:      # a consumer must never stop the grading
                pass
        self.graded_cells += graded
        self.touched_cells += touched

        settled_taps = self._settle_taps(ticks, now=now, symbol=symbol)
        return {"graded": graded, "touched": touched, "dropped": dropped, "taps": settled_taps}

    def score_at(self, cell_x: int, cell_y: int, ts: float,
                 *, tolerance_s: float = 5.0) -> dict[str, Any] | None:
        """What we said about that cell, closest to that instant."""
        best = None
        best_gap = tolerance_s
        for row in self.score_tape:
            if row["cell_x"] != cell_x or row["cell_y"] != cell_y:
                continue
            gap = abs(row["ts"] - ts)
            if gap <= best_gap:
                best, best_gap = row, gap
        return best

    def _due_taps(self, now: float) -> list[OpenTap]:
        return [t for t in self.open_taps if t.end_ts <= now]

    def _book(self, record: dict[str, Any]) -> dict[str, Any]:
        """Stamp a settlement and hand it straight to any listener.

        `self.taps` is a bounded ring kept for display. Booking P&L by
        re-scanning it means a burst of settlements can roll off the end before
        anyone looks, and the money silently never existed. The sequence number
        makes each settlement identifiable exactly once, and the callback books
        it the moment it happens rather than whenever something asks.
        """
        self._settle_seq += 1
        record["seq"] = self._settle_seq
        self.taps.append(record)
        if self.on_settle is not None:
            try:
                self.on_settle(record)
            except Exception:
                pass    # a listener must never break settlement
        return record

    def _settle_taps(self, ticks: Sequence[Any], *, now: float, symbol: str) -> int:
        due = self._due_taps(now)
        if not due:
            return 0
        n = 0
        for tap in due:
            self.open_taps.remove(tap)
            prices = self._window_ticks(ticks, tap.start_ts, tap.end_ts, symbol)
            if len(prices) < MIN_TICKS_TO_GRADE:
                self._book({
                    "ts": now, "cell_x": tap.cell_x, "cell_y": tap.cell_y,
                    "outcome": "void", "stake": tap.stake, "delta": 0.0,
                    "multiplier": tap.multiplier, "p_lcb": tap.p_lcb, "side": tap.side,
                    "distance": abs(tap.cell_y - tap.cell_y), "forward": tap.forward,
                    "horizon_s": round(tap.end_ts - tap.placed_at, 3),
                })
                continue
            won = (min(prices) < tap.hi) and (max(prices) >= tap.lo)
            delta = 0.0
            if self.bankroll is not None:
                delta = self.bankroll.settle(tap.stake, tap.multiplier, won, now=now)
            self._book({
                "ts": now,
                "cell_x": tap.cell_x,
                "cell_y": tap.cell_y,
                "outcome": "win" if won else "loss",
                "stake": round(tap.stake, 4),
                "delta": round(delta, 4),
                "multiplier": tap.multiplier,
                "p_lcb": tap.p_lcb,
                "ev_lcb": round(tap.ev_lcb, 4),
                "side": tap.side,
                "distance": tap.distance,
                "forward": tap.forward,
                "horizon_s": round(tap.end_ts - tap.placed_at, 3),
                "placed_at": tap.placed_at,
            })
            n += 1
        return n

    # -- reporting ----------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        horizons = {}
        for idx, (hits, total) in sorted(self.by_horizon.items()):
            if total <= 0:
                continue
            horizons[str(idx)] = {
                "n": int(total),
                "touch_rate": round(hits / total, 4),
            }
        recent = list(self.windows)[-30:]
        return {
            "graded_cells": self.graded_cells,
            "touched_cells": self.touched_cells,
            "touch_rate": round(self.touched_cells / self.graded_cells, 4) if self.graded_cells else None,
            "pending": len(self.pending),
            "open_taps": len(self.open_taps),
            "dropped_windows": self.dropped_windows,
            "dropped_cells": self.dropped_cells,
            "misaligned_cells": self.misaligned_cells,
            "booked_squares": len(self._booked),
            "by_horizon": horizons,
            "recent_windows": recent,
            "recent_taps": list(self.taps)[-20:],
        }
