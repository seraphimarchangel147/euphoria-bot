"""Grade a named nearby square after its 5s window.

Euphoria wins on a single touch. This module only teaches — it never submits.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

from src.analytics.signal import Signal, Suggestion, Tick, WINDOW_S

GRADE_KEEP = 20


@dataclass(frozen=True)
class Call:
    window_id: int
    start_ts: float
    end_ts: float
    named: bool
    side: str | None = None
    distance: int | None = None
    cell_x: int | None = None
    cell_y: int | None = None
    lo: float | None = None
    hi: float | None = None
    label: str = ""
    lesson: str = "waiting"
    why: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Grade:
    window_id: int
    outcome: str  # hit | miss | stood-out | unknown
    touched: bool | None
    ticks: int
    side: str | None
    label: str
    lo: float | None
    hi: float | None
    line: str
    lesson: str = ""
    why: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def next_window(now: float, grid: dict[str, Any] | None = None, window_s: float = WINDOW_S) -> tuple[int, float, float]:
    """Upcoming 5s column: (window_id, start_ts, end_ts)."""
    dur_ms = float(window_s) * 1000.0
    if grid:
        raw_dur = grid.get("square_duration") or grid.get("squareDuration")
        if raw_dur:
            try:
                dur_ms = float(raw_dur)
            except (TypeError, ValueError):
                pass
        now_ms = grid.get("now_ms") or grid.get("now") or grid.get("timestamp_ms")
        gx = grid.get("grid_x") or grid.get("gridX")
        try:
            if gx is None and now_ms is not None and dur_ms > 0:
                gx = int(float(now_ms) // dur_ms)
            else:
                gx = int(gx) if gx is not None else None
        except (TypeError, ValueError):
            gx = None
        if gx is not None and dur_ms > 0:
            page_now = float(now_ms) if now_ms is not None else float(gx) * dur_ms
            next_gx = gx + 1
            start = now + (next_gx * dur_ms - page_now) / 1000.0
            end = start + dur_ms / 1000.0
            return next_gx, start, end
    dur = dur_ms / 1000.0 if dur_ms > 0 else window_s
    current = int(now // dur)
    start = (current + 1) * dur
    return current + 1, start, start + dur


def price_band(
    price: float,
    height: float,
    side: str,
    distance: int = 1,
    cell_y: int | None = None,
) -> tuple[float, float] | None:
    """Inclusive-low / exclusive-high band matching gridY = floor(price / height)."""
    if height <= 0:
        return None
    if cell_y is not None:
        lo = float(cell_y) * height
        return lo, lo + height
    if price <= 0 or side not in ("up", "down"):
        return None
    row = int(price // height)
    target = row + distance if side == "up" else row - distance
    lo = target * height
    return lo, lo + height


def price_touched(
    ticks: Sequence[Tick] | Iterable[Tick],
    lo: float,
    hi: float,
    start_ts: float,
    end_ts: float,
    *,
    symbol: str = "ETH",
) -> tuple[bool, int]:
    """True if any tick in [start, end) tags [lo, hi). Returns (touched, ticks_in_window)."""
    symbol = symbol.upper()
    n = 0
    hit = False
    for tick in ticks:
        if tick.symbol != symbol or tick.price <= 0:
            continue
        if tick.ts < start_ts or tick.ts >= end_ts:
            continue
        n += 1
        if lo <= tick.price < hi:
            hit = True
    return hit, n


def _height_from(sig: Signal, grid: dict[str, Any] | None) -> float:
    if isinstance(sig.suggested, Suggestion) and sig.suggested.cell_height > 0:
        return float(sig.suggested.cell_height)
    if grid:
        for key in ("cell_height", "dollars_per_line", "dollarsPerLine", "price_interval", "priceInterval"):
            raw = grid.get(key)
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val > 0:
                return val
    return 0.0


def call_from_signal(
    sig: Signal,
    window_id: int,
    start_ts: float,
    end_ts: float,
    *,
    last_price: float | None = None,
    grid: dict[str, Any] | None = None,
) -> Call:
    why = sig.why or sig.reason
    lesson = sig.lesson or "waiting"
    if not isinstance(sig.suggested, Suggestion):
        return Call(
            window_id=window_id,
            start_ts=start_ts,
            end_ts=end_ts,
            named=False,
            label="no trade",
            lesson=lesson,
            why=why,
        )
    sug = sig.suggested
    height = _height_from(sig, grid)
    band = price_band(last_price or 0.0, height, sug.side, sug.distance or 1, sug.cell_y)
    lo, hi = band if band else (None, None)
    return Call(
        window_id=window_id,
        start_ts=start_ts,
        end_ts=end_ts,
        named=True,
        side=sug.side,
        distance=sug.distance,
        cell_x=sug.cell_x,
        cell_y=sug.cell_y,
        lo=lo,
        hi=hi,
        label=sug.label or sig.looking or "nearby square",
        lesson=lesson,
        why=why,
    )


def grade_call(call: Call, ticks: Sequence[Tick] | Iterable[Tick]) -> Grade:
    """Score one closed window. Named squares are hit/miss; no-trade is stood-out."""
    if not call.named:
        return Grade(
            window_id=call.window_id,
            outcome="stood-out",
            touched=None,
            ticks=0,
            side=call.side,
            label=call.label,
            lo=call.lo,
            hi=call.hi,
            line=f"stood aside — {call.lesson}" if call.lesson and call.lesson != "waiting" else "stood aside",
            lesson=call.lesson,
            why=call.why,
        )
    if call.lo is None or call.hi is None or call.hi <= call.lo:
        return Grade(
            window_id=call.window_id,
            outcome="unknown",
            touched=None,
            ticks=0,
            side=call.side,
            label=call.label,
            lo=call.lo,
            hi=call.hi,
            line="no band to grade",
            lesson=call.lesson,
            why=call.why,
        )
    touched, n = price_touched(ticks, call.lo, call.hi, call.start_ts, call.end_ts)
    if n == 0:
        return Grade(
            window_id=call.window_id,
            outcome="unknown",
            touched=None,
            ticks=0,
            side=call.side,
            label=call.label,
            lo=call.lo,
            hi=call.hi,
            line="no ticks to grade",
            lesson=call.lesson,
            why=call.why,
        )
    outcome = "hit" if touched else "miss"
    verb = "hit" if touched else "missed"
    return Grade(
        window_id=call.window_id,
        outcome=outcome,
        touched=touched,
        ticks=n,
        side=call.side,
        label=call.label,
        lo=call.lo,
        hi=call.hi,
        line=f"{verb} {call.label}",
        lesson=call.lesson,
        why=call.why,
    )


def empty_grade() -> dict:
    return {
        "last": None,
        "hits": 0,
        "misses": 0,
        "n": 0,
        "hit_rate": None,
        "line": "",
        "recent": [],
    }


class GradeBook:
    """Lock the upcoming 5s call, then grade it once the window closes."""

    def __init__(self, keep: int = GRADE_KEEP) -> None:
        self.keep = keep
        self.upcoming: Call | None = None
        self.live: Call | None = None
        self.grades: deque[Grade] = deque(maxlen=keep)
        self._seen: set[int] = set()

    def update(
        self,
        sig: Signal,
        ticks: Sequence[Tick] | Iterable[Tick],
        *,
        now: float,
        grid: dict[str, Any] | None = None,
        last_price: float | None = None,
    ) -> dict:
        wid, start, end = next_window(now, grid)

        if self.live is not None and now >= self.live.end_ts:
            self._settle(self.live, ticks)
            self.live = None
        if self.upcoming is not None and now >= self.upcoming.end_ts:
            self._settle(self.upcoming, ticks)
            self.upcoming = None

        if self.upcoming is not None and self.upcoming.window_id != wid:
            if self.live is not None and self.live.window_id != self.upcoming.window_id:
                self._settle(self.live, ticks)
            self.live = self.upcoming
            self.upcoming = None
            if self.live is not None and now >= self.live.end_ts:
                self._settle(self.live, ticks)
                self.live = None

        if now < end:
            self.upcoming = call_from_signal(
                sig, wid, start, end, last_price=last_price, grid=grid
            )
        return self.to_dict()

    def _settle(self, call: Call, ticks: Sequence[Tick] | Iterable[Tick]) -> None:
        if call.window_id in self._seen:
            return
        self._seen.add(call.window_id)
        self.grades.append(grade_call(call, ticks))
        if len(self._seen) > self.keep * 4:
            keep = {g.window_id for g in self.grades}
            self._seen = keep

    def to_dict(self) -> dict:
        named = [g for g in self.grades if g.outcome in ("hit", "miss")]
        hits = sum(1 for g in named if g.outcome == "hit")
        last = self.grades[-1] if self.grades else None
        if last and named:
            line = f"last: {last.line} · {hits}/{len(named)} tagged"
        elif last:
            line = f"last: {last.line}"
        else:
            line = ""
        return {
            "last": last.to_dict() if last else None,
            "hits": hits,
            "misses": len(named) - hits,
            "n": len(named),
            "hit_rate": round(hits / len(named), 3) if named else None,
            "line": line,
            "recent": [g.to_dict() for g in list(self.grades)[-12:]],
        }
