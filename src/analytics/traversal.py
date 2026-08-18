"""How price walks the grid: dwell time per row, and which way it leaves.

The diffusion fit is memoryless. It says the chance of leaving a row is the same
whether price arrived this second or has been pinned there for a minute, and it
says a 3am grid behaves like a 3pm grid. Neither is true, and both are visible
in the tape:

* **Dwell.** Price occupies a row for some number of 5-second columns and then
  breaks. If the chance of breaking rises with how long it has already held,
  that is a hazard rate a memoryless model cannot express -- and it is exactly
  the "after so many squares it moves" pattern.
* **Direction.** When a row does break, it breaks up or down, and that split
  need not be even. Position within the row matters too: price sitting against
  the top edge is a different proposition from price sitting mid-row.
* **Hour.** Overnight the grid slows: fewer rows crossed per minute, longer
  dwells. A single volatility number averaged across the day describes neither
  the quiet hours nor the busy ones.

This module only measures. It never submits, and it never invents a number it
has not seen -- every estimate carries the sample count that produced it.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.analytics.forecast import SETTLEMENT_SOURCES

# Dwell is bucketed because raw column counts go sparse fast. Fine at the short
# end, where most visits live and where the decision usually is.
DWELL_BUCKETS: tuple[tuple[int, int | None], ...] = (
    (1, 1), (2, 2), (3, 3), (4, 5), (6, 8), (9, 12), (13, None),
)
HOUR_BLOCK_H = 4          # local-time blocks; "nighttime" is a local idea
PRIOR_WEIGHT = 6.0        # pseudo-counts behind a cold hazard estimate
LCB_Z = 1.6449
VISIT_KEEP = 400
MIN_SAMPLES_TRUSTED = 25
MAX_BUCKET_WEIGHT = 3000.0
# Price must move this far past a row boundary before the row counts as
# changed. Without it, price resting on a boundary flickers between two rows on
# cent-level noise and every visit reads as one square long -- which is exactly
# the signature the dwell model is trying to measure, manufactured out of jitter.
HYSTERESIS = 0.15


def dwell_bucket(columns: int) -> str:
    for lo, hi in DWELL_BUCKETS:
        if columns >= lo and (hi is None or columns <= hi):
            return f"{lo}+" if hi is None else (f"{lo}" if lo == hi else f"{lo}-{hi}")
    return "1"


def hour_bucket(ts: float) -> str:
    """Local-time block. The observation being modelled is a human one."""
    hour = time.localtime(ts).tm_hour
    lo = (hour // HOUR_BLOCK_H) * HOUR_BLOCK_H
    return f"{lo:02d}-{lo + HOUR_BLOCK_H:02d}"


def _beta_bounds(hits: float, total: float, prior_p: float,
                 prior_w: float = PRIOR_WEIGHT) -> tuple[float, float]:
    """(posterior mean, lower confidence bound) for a rate."""
    alpha = prior_w * prior_p + hits
    beta = prior_w * (1.0 - prior_p) + max(0.0, total - hits)
    denom = alpha + beta
    if denom <= 0:
        return prior_p, 0.0
    mean = alpha / denom
    var = (alpha * beta) / (denom * denom * (denom + 1.0))
    lcb = max(0.0, min(1.0, mean - LCB_Z * math.sqrt(max(var, 0.0))))
    return mean, lcb


@dataclass
class Visit:
    """One occupancy of a price row, open until price leaves it."""

    row: int
    start_ts: float
    start_column: int
    last_column: int
    hour: str
    entered_from: str = "unknown"      # up | down | unknown

    @property
    def columns_held(self) -> int:
        return max(1, self.last_column - self.start_column + 1)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "columns_held": self.columns_held}


@dataclass
class Traversal:
    """Learned grid-walking behaviour, persisted between runs."""

    # dwell bucket -> [exits at this dwell, visits that reached it]
    hazard: dict[str, list[float]] = field(default_factory=dict)
    # dwell bucket -> [upward exits, total exits]
    direction: dict[str, list[float]] = field(default_factory=dict)
    # hour block -> [rows crossed, seconds observed]
    activity: dict[str, list[float]] = field(default_factory=dict)
    # hour block -> [total columns dwelled, visits closed]
    dwell_by_hour: dict[str, list[float]] = field(default_factory=dict)
    visits: list[dict[str, Any]] = field(default_factory=list)

    open_visit: Visit | None = None
    _last_ts: float = 0.0
    path: Path | None = None

    # -- ingestion ----------------------------------------------------------
    def observe(
        self,
        ticks: Sequence[Any],
        *,
        dpl: float,
        column_of,
        symbol: str = "ETH",
        sources: frozenset[str] | None = SETTLEMENT_SOURCES,
    ) -> int:
        """Feed ticks in time order; returns how many row changes were seen.

        ``column_of(ts)`` maps a tick timestamp to its grid column, so dwell is
        counted in the same 5-second squares the board is drawn in.

        Only the settlement tape is admitted. Interleaving two feeds that differ
        by a few cents makes price appear to cross a boundary every time the
        source alternates, and the oracle's timestamps are its publish times --
        so mixed feeds corrupt both the dwell counts and the hour they are
        filed under.
        """
        if dpl <= 0:
            return 0
        symbol = symbol.upper()
        moves = 0
        for tick in ticks:
            if getattr(tick, "symbol", "").upper() != symbol:
                continue
            if sources is not None and getattr(tick, "source", "") not in sources:
                continue
            price = getattr(tick, "price", 0.0)
            ts = getattr(tick, "ts", 0.0)
            if price <= 0 or ts <= self._last_ts:
                continue
            self._last_ts = ts
            row = math.floor(price / dpl)
            column = int(column_of(ts))
            if self.open_visit is None:
                self.open_visit = Visit(row=row, start_ts=ts, start_column=column,
                                        last_column=column, hour=hour_bucket(ts))
                continue
            if row == self.open_visit.row:
                if column > self.open_visit.last_column:
                    self.open_visit.last_column = column
                continue
            # Confirm the break: price must clear the boundary it just crossed
            # by a margin, not merely touch the far side of it.
            held = self.open_visit.row
            if row > held:
                if price < (held + 1) * dpl + HYSTERESIS * dpl:
                    continue
            else:
                if price > held * dpl - HYSTERESIS * dpl:
                    continue
            direction = "up" if row > self.open_visit.row else "down"
            crossed = abs(row - self.open_visit.row)
            self._close(self.open_visit, direction, ts, crossed)
            self.open_visit = Visit(row=row, start_ts=ts, start_column=column,
                                    last_column=column, hour=hour_bucket(ts),
                                    entered_from=direction)
            moves += 1
        return moves

    def _close(self, visit: Visit, direction: str, ts: float, rows_crossed: int) -> None:
        held = visit.columns_held
        # Every column the row survived is a trial it did not break on; the
        # final one is the trial it did. That is what makes this a hazard rate
        # rather than a plain frequency.
        for k in range(1, held + 1):
            key = dwell_bucket(k)
            row = self.hazard.setdefault(key, [0.0, 0.0])
            row[1] += 1.0
            if k == held:
                row[0] += 1.0
            if row[1] > MAX_BUCKET_WEIGHT:
                row[0] *= 0.5
                row[1] *= 0.5

        key = dwell_bucket(held)
        dirs = self.direction.setdefault(key, [0.0, 0.0])
        dirs[1] += 1.0
        if direction == "up":
            dirs[0] += 1.0

        self._attribute_time(visit.start_ts, ts, rows_crossed)

        dw = self.dwell_by_hour.setdefault(visit.hour, [0.0, 0.0])
        dw[0] += held
        dw[1] += 1.0

        self.visits.append({
            "row": visit.row, "held": held, "exit": direction,
            "hour": visit.hour, "ts": ts, "rows_crossed": rows_crossed,
        })
        if len(self.visits) > VISIT_KEEP:
            self.visits = self.visits[-VISIT_KEEP:]

    def _attribute_time(self, start_ts: float, end_ts: float, rows_crossed: int) -> None:
        """Split a visit's duration across the hour blocks it actually spanned.

        A row that holds from 1am to 4am belongs to three blocks, not to the one
        it happened to start in. Filing it all under the start hour is what
        makes a quiet overnight stretch look like a burst of activity at 1am --
        the opposite of the pattern being measured.
        """
        span = max(0.0, end_ts - start_ts)
        block = HOUR_BLOCK_H * 3600.0
        if span <= 0:
            act = self.activity.setdefault(hour_bucket(start_ts), [0.0, 0.0])
            act[0] += float(rows_crossed)
            return
        cursor = start_ts
        guard = 0
        while cursor < end_ts and guard < 512:
            guard += 1
            bucket = hour_bucket(cursor)
            # Seconds remaining in this local block, found without timezone maths.
            tm = time.localtime(cursor)
            into = (tm.tm_hour % HOUR_BLOCK_H) * 3600.0 + tm.tm_min * 60.0 + tm.tm_sec
            edge = cursor + max(1.0, block - into)
            chunk_end = min(edge, end_ts)
            seconds = max(0.0, chunk_end - cursor)
            act = self.activity.setdefault(bucket, [0.0, 0.0])
            act[1] += seconds
            # The crossing itself happened at the end, so credit it there.
            if chunk_end >= end_ts:
                act[0] += float(rows_crossed)
            cursor = chunk_end

    # -- estimates ----------------------------------------------------------
    def break_chance(self, dwell_columns: int) -> dict[str, Any]:
        """P(this row breaks in the next column), given it has held this long."""
        key = dwell_bucket(max(1, dwell_columns))
        exits, seen = self.hazard.get(key, (0.0, 0.0))
        base = self.base_hazard()
        mean, lcb = _beta_bounds(exits, seen, base)
        return {
            "dwell_columns": max(1, dwell_columns),
            "bucket": key,
            "p_break": round(mean, 5),
            "p_break_lcb": round(lcb, 5),
            "n": int(seen),
            "trusted": seen >= MIN_SAMPLES_TRUSTED,
        }

    def base_hazard(self) -> float:
        exits = sum(v[0] for v in self.hazard.values())
        seen = sum(v[1] for v in self.hazard.values())
        return (exits / seen) if seen > 0 else 0.5

    def break_direction(self, dwell_columns: int) -> dict[str, Any]:
        """When it breaks, which way. Not a forecast of the tape, a split."""
        key = dwell_bucket(max(1, dwell_columns))
        ups, total = self.direction.get(key, (0.0, 0.0))
        all_up = sum(v[0] for v in self.direction.values())
        all_total = sum(v[1] for v in self.direction.values())
        prior = (all_up / all_total) if all_total > 0 else 0.5
        mean, _lcb = _beta_bounds(ups, total, prior)
        return {
            "p_up": round(mean, 5),
            "p_down": round(1.0 - mean, 5),
            "n": int(total),
            "trusted": total >= MIN_SAMPLES_TRUSTED,
        }

    def hazard_curve(self) -> list[dict[str, Any]]:
        """Break chance against how long the row has already held."""
        out = []
        for lo, hi in DWELL_BUCKETS:
            key = f"{lo}+" if hi is None else (f"{lo}" if lo == hi else f"{lo}-{hi}")
            exits, seen = self.hazard.get(key, (0.0, 0.0))
            if seen <= 0:
                continue
            ups, total = self.direction.get(key, (0.0, 0.0))
            out.append({
                "bucket": key,
                "columns": lo,
                "p_break": round(exits / seen, 4),
                "n": int(seen),
                "p_up": round(ups / total, 4) if total > 0 else None,
                "exits": int(total),
            })
        return out

    def by_hour(self) -> list[dict[str, Any]]:
        """Rows crossed per minute, and mean dwell, by local-time block."""
        out = []
        for hour in sorted(set(self.activity) | set(self.dwell_by_hour)):
            rows, seconds = self.activity.get(hour, (0.0, 0.0))
            held, visits = self.dwell_by_hour.get(hour, (0.0, 0.0))
            out.append({
                "hour": hour,
                "rows_per_min": round(rows / (seconds / 60.0), 3) if seconds > 0 else None,
                "mean_dwell_columns": round(held / visits, 2) if visits > 0 else None,
                "visits": int(visits),
                "minutes": round(seconds / 60.0, 1),
            })
        return out

    def quietest_hour(self) -> str | None:
        rows = [r for r in self.by_hour() if r["rows_per_min"] is not None and r["visits"] >= 5]
        return min(rows, key=lambda r: r["rows_per_min"])["hour"] if rows else None

    def busiest_hour(self) -> str | None:
        rows = [r for r in self.by_hour() if r["rows_per_min"] is not None and r["visits"] >= 5]
        return max(rows, key=lambda r: r["rows_per_min"])["hour"] if rows else None

    def current(self, now: float, column_of, price: float, dpl: float) -> dict[str, Any]:
        """Live read: how long this row has held, and what usually follows."""
        visit = self.open_visit
        if visit is None or dpl <= 0:
            return {
                "in_row": None, "dwell_columns": 0, "dwell_s": 0.0,
                "position_in_row": None, "hour": hour_bucket(now),
                "break": self.break_chance(1), "direction": self.break_direction(1),
                "visits_recorded": len(self.visits),
            }
        column = int(column_of(now))
        held = max(1, column - visit.start_column + 1)
        lo = visit.row * dpl
        position = (price - lo) / dpl if price > 0 else None
        return {
            "in_row": visit.row,
            "dwell_columns": held,
            "dwell_s": round(max(0.0, now - visit.start_ts), 2),
            # 0 = sitting on the floor of the row, 1 = against the ceiling.
            "position_in_row": round(position, 4) if position is not None else None,
            "entered_from": visit.entered_from,
            "hour": visit.hour,
            "break": self.break_chance(held),
            "direction": self.break_direction(held),
            "visits_recorded": len(self.visits),
        }

    def stats(self, *, now: float | None = None, column_of=None,
              price: float = 0.0, dpl: float = 0.0) -> dict[str, Any]:
        now = time.time() if now is None else now
        body: dict[str, Any] = {
            "hazard_curve": self.hazard_curve(),
            "by_hour": self.by_hour(),
            "quietest_hour": self.quietest_hour(),
            "busiest_hour": self.busiest_hour(),
            "base_break_chance": round(self.base_hazard(), 4),
            "visits": len(self.visits),
            "recent": self.visits[-20:],
        }
        if column_of is not None:
            body["current"] = self.current(now, column_of, price, dpl)
        return body

    # -- persistence --------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "hazard": {k: list(v) for k, v in self.hazard.items()},
            "direction": {k: list(v) for k, v in self.direction.items()},
            "activity": {k: list(v) for k, v in self.activity.items()},
            "dwell_by_hour": {k: list(v) for k, v in self.dwell_by_hour.items()},
            "visits": self.visits[-VISIT_KEEP:],
        }

    @classmethod
    def from_json(cls, data: Any) -> "Traversal":
        out = cls()
        if not isinstance(data, dict):
            return out
        for name in ("hazard", "direction", "activity", "dwell_by_hour"):
            raw = data.get(name)
            if not isinstance(raw, dict):
                continue
            target = getattr(out, name)
            for key, val in raw.items():
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    try:
                        target[str(key)] = [float(val[0]), float(val[1])]
                    except (TypeError, ValueError):
                        continue
        visits = data.get("visits")
        if isinstance(visits, list):
            out.visits = [v for v in visits if isinstance(v, dict)][-VISIT_KEEP:]
        return out

    def save(self) -> None:
        if not self.path:
            return
        try:
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path | None) -> "Traversal":
        if not path or not Path(path).is_file():
            out = cls()
            out.path = Path(path) if path else None
            return out
        try:
            out = cls.from_json(json.loads(Path(path).read_text()))
        except (OSError, ValueError):
            out = cls()
        out.path = Path(path)
        return out
