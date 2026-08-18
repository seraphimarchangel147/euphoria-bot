"""Named A/B tells for the live indicator. Detect only — never submit.

Setups: stall, compression, sweep, late_pink. Each either names a nearby
square (one cell) or sits. Higher TFs gate; far lottery cells are never named.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from src.analytics.timeframes import TimeframeStack, ticks_to_bars

SETUP_NAMES = ("stall", "compression", "sweep", "late_pink", "none")

# Locked /think setup payload. Do not rename.
SETUP_PAYLOAD_KEYS = (
    "stall",
    "stall_squares",
    "new_extreme",
    "stall_broke_against_htf",
    "compression",
    "range_squares",
    "box_lo",
    "box_hi",
    "first_close_outside",
    "break_side",
    "wick_and_fail",
    "sweep_1m",
    "swing_1m_lo",
    "swing_1m_hi",
    "wick_squares_past",
    "reclaim",
    "pink_age_s",
    "blue_age_s",
    "range_shrinking",
)


@dataclass
class SetupHit:
    name: str
    sit: bool
    reason: str
    side: str | None = None
    wick_squares: float = 0.0
    box: dict[str, float] | None = None
    swing: dict[str, float] | None = None


@dataclass
class PinkState:
    age_s: float = 0.0
    shrinking: bool = False
    allow_late_blue: bool = False


@dataclass
class SetupMemory:
    """Per-window pink clock so late-sit does not need a human mark."""

    window_id: int | None = None
    pink_since: float | None = None
    first_htf_lean: str | None = None
    had_blue: bool = False
    blue_since: float | None = None
    range_peak: float = 0.0
    last_range: float = 0.0
    shrunk: bool = False
    expanded_after_shrink: bool = False
    history: list[tuple[float, float]] = field(default_factory=list)

    def reset(self, window_id: int, now: float, range_sq: float, htf: str | None) -> None:
        self.window_id = window_id
        self.pink_since = now
        self.first_htf_lean = htf if htf in ("up", "down") else None
        self.had_blue = False
        self.blue_since = None
        self.range_peak = range_sq
        self.last_range = range_sq
        self.shrunk = False
        self.expanded_after_shrink = False
        self.history = [(now, range_sq)]

    def update(
        self,
        *,
        now: float,
        window_id: int,
        range_sq: float,
        htf: str | None,
        has_candidates: bool,
    ) -> None:
        if self.window_id != window_id:
            if has_candidates:
                self.reset(window_id, now, range_sq, htf)
            else:
                self.window_id = window_id
                self.pink_since = None
                self.first_htf_lean = None
                self.had_blue = False
                self.blue_since = None
                self.range_peak = range_sq
                self.last_range = range_sq
                self.shrunk = False
                self.expanded_after_shrink = False
                self.history = []
            return
        if self.pink_since is None and has_candidates:
            self.pink_since = now
            if self.first_htf_lean is None and htf in ("up", "down"):
                self.first_htf_lean = htf
        if range_sq + 0.05 < self.range_peak:
            self.shrunk = True
            self.expanded_after_shrink = False
        elif self.shrunk and range_sq > self.last_range + 0.08:
            self.expanded_after_shrink = True
        self.range_peak = max(self.range_peak, range_sq)
        self.last_range = range_sq
        self.history.append((now, range_sq))
        if len(self.history) > 40:
            self.history = self.history[-40:]

    def mark_blue(self, now: float | None = None) -> None:
        if self.blue_since is None:
            self.blue_since = float(now) if now is not None else None
        self.had_blue = True

    def age(self, now: float) -> float:
        if self.pink_since is None:
            return 0.0
        return max(0.0, now - self.pink_since)

    def blue_age(self, now: float) -> float | None:
        if self.blue_since is None:
            return None
        return round(max(0.0, now - self.blue_since), 3)


def pair_lean(stack: TimeframeStack | None, *keys: str) -> str | None:
    """Agreeing directional lean on every named TF, or None."""
    if stack is None:
        return None
    leans: list[str] = []
    for key in keys:
        frame = stack.frame(key)
        if frame is None or frame.lean not in ("up", "down"):
            return None
        leans.append(frame.lean)
    if not leans or any(item != leans[0] for item in leans):
        return None
    return leans[0]


def window_id_for(now: float, grid: dict[str, Any] | None = None) -> int:
    if grid:
        raw_now = grid.get("now_ms") or grid.get("now") or grid.get("timestamp_ms")
        raw_dur = grid.get("square_duration") or grid.get("squareDuration") or 5000
        try:
            if raw_now is not None and float(raw_dur) > 0:
                return int(float(raw_now) // float(raw_dur))
        except (TypeError, ValueError):
            pass
    return int(now // 5.0)


def _sq(delta: float, height: float) -> float:
    if height <= 0:
        return 0.0
    return abs(delta) / height


def detect_stall(ticks: Sequence[Any], height: float) -> dict[str, Any] | None:
    """5s pull of 1–2 squares, then overlap — not a new extreme."""
    if len(ticks) < 4 or height <= 0:
        return None
    open_px = ticks[0].price
    last = ticks[-1].price
    lo_i = min(range(len(ticks)), key=lambda i: ticks[i].price)
    hi_i = max(range(len(ticks)), key=lambda i: ticks[i].price)
    lo, hi = ticks[lo_i].price, ticks[hi_i].price
    down = (open_px - lo) / height
    up = (hi - open_px) / height

    def _came_back(ext_i: int, ext_px: float, pull_side: str) -> bool:
        if ext_i >= len(ticks) - 1:
            return False
        after = ticks[ext_i + 1 :]
        if pull_side == "down" and any(t.price < ext_px - 1e-12 for t in after):
            return False
        if pull_side == "up" and any(t.price > ext_px + 1e-12 for t in after):
            return False
        if pull_side == "down" and last <= ext_px + 0.2 * height:
            return False
        if pull_side == "up" and last >= ext_px - 0.2 * height:
            return False
        if last > hi + 0.25 * height or last < lo - 0.25 * height:
            return False
        return True

    if 1.0 - 1e-6 <= down <= 2.0 + 1e-6 and down >= up - 1e-9:
        if _came_back(lo_i, lo, "down"):
            return {"pull": "down", "squares": round(down, 3)}
    if 1.0 - 1e-6 <= up <= 2.0 + 1e-6 and up >= down - 1e-9:
        if _came_back(hi_i, hi, "up"):
            return {"pull": "up", "squares": round(up, 3)}
    return None


def stall_tell(ticks: Sequence[Any], height: float, stack: TimeframeStack | None) -> SetupHit | None:
    found = detect_stall(ticks, height)
    if not found:
        return None
    htf = pair_lean(stack, "1h", "4h")
    pull = found["pull"]
    wick = float(found["squares"])
    if htf is None:
        return SetupHit(
            name="stall",
            sit=True,
            reason="stall — 1–2 square pull overlapped, no 1h+4h lean, sitting",
            wick_squares=wick,
        )
    # Nearest square back in HTF direction (fade the pull when HTF disagrees with it).
    return SetupHit(
        name="stall",
        sit=False,
        side=htf,
        reason=f"stall — pull {pull}, fade back with 1h+4h {htf}",
        wick_squares=wick,
    )


def detect_compression(
    ticks: Sequence[Any],
    height: float,
    *,
    now: float,
) -> dict[str, Any] | None:
    """2–3 overlapping 5s bars with range ≤2 squares."""
    if height <= 0 or len(ticks) < 6:
        return None
    bars = ticks_to_bars(ticks, 5.0, now=now)
    if len(bars) < 2:
        return None

    def _ok(chunk: list) -> bool:
        if len(chunk) < 2 or len(chunk) > 3:
            return False
        for bar in chunk:
            if _sq(bar.high - bar.low, height) > 2.0 + 1e-6:
                return False
        for a, b in zip(chunk, chunk[1:]):
            if b.low > a.high + 1e-12 or b.high < a.low - 1e-12:
                return False
        return True

    def _pack(chunk: list, close: float) -> dict[str, Any]:
        box_lo = min(b.low for b in chunk)
        box_hi = max(b.high for b in chunk)
        return {
            "low": box_lo,
            "high": box_hi,
            "close": close,
            "bars": len(chunk),
            "inside": box_lo - 1e-12 <= close <= box_hi + 1e-12,
        }

    # A close outside an already-formed box is the break — do not grow the box.
    for n in (3, 2):
        if len(bars) >= n + 1 and _ok(bars[-(n + 1) : -1]):
            packed = _pack(bars[-(n + 1) : -1], bars[-1].close)
            if not packed["inside"]:
                return packed
    for n in (3, 2):
        if len(bars) >= n and _ok(bars[-n:]):
            return _pack(bars[-n:], bars[-1].close)
    return None


def compression_tell(ticks: Sequence[Any], height: float, *, now: float) -> SetupHit | None:
    found = detect_compression(ticks, height, now=now)
    if not found:
        return None
    box = {"low": found["low"], "high": found["high"]}
    if found["inside"]:
        return SetupHit(
            name="compression",
            sit=True,
            reason="compression — 2–3 overlapping 5s inside the box, sitting",
            box=box,
        )
    side = "up" if found["close"] > found["high"] else "down"
    return SetupHit(
        name="compression",
        sit=False,
        side=side,
        reason=f"compression — first close outside the box, one square {side}",
        box=box,
    )


def detect_swing_1m(ticks: Sequence[Any], *, now: float, ohlc: dict | None = None) -> dict[str, float] | None:
    bars = []
    canned = (ohlc or {}).get("1m")
    if canned:
        bars = list(canned)
    else:
        bars = ticks_to_bars(ticks, 60.0, now=now)
    if len(bars) < 3:
        return None
    last = bars[-1]
    completed = bars[:-1] if now - last.ts < 60 else bars
    if len(completed) < 3:
        completed = bars
    if len(completed) < 3:
        return None
    swing_high = None
    swing_low = None
    for i in range(1, len(completed) - 1):
        mid = completed[i]
        if mid.high >= completed[i - 1].high and mid.high >= completed[i + 1].high:
            swing_high = mid.high if swing_high is None else max(swing_high, mid.high)
        if mid.low <= completed[i - 1].low and mid.low <= completed[i + 1].low:
            swing_low = mid.low if swing_low is None else min(swing_low, mid.low)
    if swing_high is None:
        swing_high = max(b.high for b in completed[:-1])
    if swing_low is None:
        swing_low = min(b.low for b in completed[:-1])
    return {"high": float(swing_high), "low": float(swing_low)}


def detect_sweep(
    ticks_5s: Sequence[Any],
    height: float,
    swing: dict[str, float] | None,
) -> dict[str, Any] | None:
    """Wick exactly ~1 square past the 1m swing, then reclaim. Skip 2+ squares."""
    if not swing or height <= 0 or len(ticks_5s) < 3:
        return None
    hi = max(t.price for t in ticks_5s)
    lo = min(t.price for t in ticks_5s)
    last = ticks_5s[-1].price
    up = (hi - swing["high"]) / height
    down = (swing["low"] - lo) / height
    if 0.75 <= up < 2.0 and last < swing["high"] - 1e-12:
        return {"side": "down", "wick": round(up, 3), "which": "high"}
    if 0.75 <= down < 2.0 and last > swing["low"] + 1e-12:
        return {"side": "up", "wick": round(down, 3), "which": "low"}
    return None


def sweep_tell(
    ticks_5s: Sequence[Any],
    ticks_all: Sequence[Any],
    height: float,
    stack: TimeframeStack | None,
    *,
    now: float,
    ohlc: dict | None = None,
) -> SetupHit | None:
    htf = pair_lean(stack, "4h", "D")
    if htf is None:
        return None
    swing = detect_swing_1m(ticks_all, now=now, ohlc=ohlc)
    if not swing:
        return None
    found = detect_sweep(ticks_5s, height, swing)
    if not found:
        return None
    return SetupHit(
        name="sweep",
        sit=False,
        side=found["side"],
        reason=f"sweep — 1-square wick past 1m {found['which']}, first square back inside",
        wick_squares=float(found["wick"]),
        swing=swing,
    )


def pink_metrics(
    ticks_5s: Sequence[Any],
    height: float,
    *,
    now: float,
    memory: SetupMemory | None,
    window_id: int,
    htf: str | None,
    has_candidates: bool,
) -> PinkState:
    prices = [t.price for t in ticks_5s if t.price > 0]
    range_now = (max(prices) - min(prices)) / height if prices and height > 0 else 0.0
    prior = [t for t in ticks_5s if t.ts <= now - 1.0]
    if len(prior) >= 2 and height > 0:
        range_before = (max(t.price for t in prior) - min(t.price for t in prior)) / height
    else:
        range_before = range_now
    shrinking = range_now <= range_before + 0.12

    if memory is not None:
        memory.update(
            now=now,
            window_id=window_id,
            range_sq=range_now,
            htf=htf,
            has_candidates=has_candidates,
        )
        age = memory.age(now)
        allow_late = bool(memory.expanded_after_shrink and memory.first_htf_lean in (None, htf))
        shrinking = shrinking or (memory.shrunk and not memory.expanded_after_shrink)
        return PinkState(age_s=round(age, 3), shrinking=shrinking, allow_late_blue=allow_late)

    if ticks_5s:
        first = ticks_5s[min(2, len(ticks_5s) - 1)].ts
        age = max(0.0, now - first)
    else:
        age = 0.0
    allow_late = (not shrinking) and age > 2.0
    return PinkState(age_s=round(age, 3), shrinking=shrinking, allow_late_blue=allow_late)


def late_pink_sit(pink: PinkState, *, would_pick: bool) -> SetupHit | None:
    """Pink >2s, no blue yet, range shrinking → sit. Early blue (≤1s) is allowed."""
    if not would_pick:
        return None
    if pink.age_s <= 1.0:
        return None
    if pink.age_s > 2.0 and pink.shrinking and not pink.allow_late_blue:
        return SetupHit(
            name="late_pink",
            sit=True,
            reason="late pink — candidates >2s, range shrinking, sitting",
        )
    if pink.age_s > 2.0 and not pink.allow_late_blue and pink.shrinking:
        return SetupHit(
            name="late_pink",
            sit=True,
            reason="late pink — candidates >2s, range shrinking, sitting",
        )
    return None


def missing_setup_keys(body: dict[str, Any] | None) -> list[str]:
    if not body:
        return list(SETUP_PAYLOAD_KEYS)
    return [key for key in SETUP_PAYLOAD_KEYS if key not in body]


def empty_setup_payload() -> dict[str, Any]:
    return {
        "stall": False,
        "stall_squares": 1,
        "new_extreme": False,
        "stall_broke_against_htf": False,
        "compression": False,
        "range_squares": 0.0,
        "box_lo": 0.0,
        "box_hi": 0.0,
        "first_close_outside": False,
        "break_side": None,
        "wick_and_fail": False,
        "sweep_1m": False,
        "swing_1m_lo": 0.0,
        "swing_1m_hi": 0.0,
        "wick_squares_past": 0,
        "reclaim": False,
        "pink_age_s": 0.0,
        "blue_age_s": None,
        "range_shrinking": False,
    }


def attach_setup_clocks(
    payload: dict[str, Any],
    *,
    pink_age_s: float,
    blue_age_s: float | None,
    range_shrinking: bool,
) -> dict[str, Any]:
    out = dict(payload)
    out["pink_age_s"] = round(float(pink_age_s), 3)
    out["blue_age_s"] = None if blue_age_s is None else round(float(blue_age_s), 3)
    out["range_shrinking"] = bool(range_shrinking)
    return out


def _clamp_stall_squares(raw: float) -> int:
    return 2 if raw >= 1.5 else 1


def _wick_and_fail(ticks: Sequence[Any], box_lo: float, box_hi: float, close: float) -> bool:
    if not ticks:
        return False
    hi = max(t.price for t in ticks)
    lo = min(t.price for t in ticks)
    poked = hi > box_hi + 1e-12 or lo < box_lo - 1e-12
    closed_in = box_lo - 1e-12 <= close <= box_hi + 1e-12
    return bool(poked and closed_in)


def _new_extreme(ticks: Sequence[Any], height: float, stall_held: bool) -> bool:
    if stall_held or len(ticks) < 2 or height <= 0:
        return False
    open_px = ticks[0].price
    last = ticks[-1].price
    lo = min(t.price for t in ticks)
    hi = max(t.price for t in ticks)
    up = (hi - open_px) / height
    down = (open_px - lo) / height
    at_high = last >= hi - 1e-12 and up >= 1.0 - 1e-6
    at_low = last <= lo + 1e-12 and down >= 1.0 - 1e-6
    return bool(at_high or at_low)


def _stall_broke_against_htf(
    ticks: Sequence[Any],
    height: float,
    htf: str | None,
    stall_held: bool,
) -> bool:
    if stall_held or htf not in ("up", "down") or len(ticks) < 3 or height <= 0:
        return False
    open_px = ticks[0].price
    last = ticks[-1].price
    lo = min(t.price for t in ticks)
    hi = max(t.price for t in ticks)
    down = (open_px - lo) / height
    up = (hi - open_px) / height
    if htf == "up" and down >= 1.0 - 1e-6 and last <= lo + 0.25 * height:
        return True
    if htf == "down" and up >= 1.0 - 1e-6 and last >= hi - 0.25 * height:
        return True
    return False


def build_setup_payload(
    *,
    ticks_5s: Sequence[Any],
    ticks_all: Sequence[Any],
    height: float,
    stack: TimeframeStack | None,
    now: float,
    ohlc: dict | None = None,
    pink_age_s: float = 0.0,
    blue_age_s: float | None = None,
    range_shrinking: bool = False,
) -> dict[str, Any]:
    """Exact /think setup keys. Always present; booleans stay False when quiet."""
    out = empty_setup_payload()
    stall_found = detect_stall(ticks_5s, height) if ticks_5s else None
    stall_held = stall_found is not None
    out["stall"] = stall_held
    if stall_found:
        out["stall_squares"] = _clamp_stall_squares(float(stall_found["squares"]))
    elif ticks_5s and height > 0:
        open_px = ticks_5s[0].price
        lo = min(t.price for t in ticks_5s)
        hi = max(t.price for t in ticks_5s)
        pull = max((open_px - lo) / height, (hi - open_px) / height)
        if pull >= 1.0 - 1e-6:
            out["stall_squares"] = _clamp_stall_squares(pull)
    htf = pair_lean(stack, "1h", "4h")
    out["new_extreme"] = _new_extreme(ticks_5s, height, stall_held)
    out["stall_broke_against_htf"] = _stall_broke_against_htf(ticks_5s, height, htf, stall_held)

    comp = detect_compression(ticks_all, height, now=now) if ticks_all else None
    if ticks_5s:
        win_lo = min(t.price for t in ticks_5s)
        win_hi = max(t.price for t in ticks_5s)
        out["box_lo"] = round(win_lo, 8)
        out["box_hi"] = round(win_hi, 8)
        out["range_squares"] = round((win_hi - win_lo) / height, 3) if height > 0 else 0.0
    if comp:
        out["compression"] = True
        out["box_lo"] = round(float(comp["low"]), 8)
        out["box_hi"] = round(float(comp["high"]), 8)
        span = float(comp["high"]) - float(comp["low"])
        out["range_squares"] = round(span / height, 3) if height > 0 else 0.0
        out["first_close_outside"] = not bool(comp["inside"])
        if comp["inside"]:
            out["break_side"] = None
        else:
            out["break_side"] = "up" if comp["close"] > comp["high"] else "down"
        out["wick_and_fail"] = _wick_and_fail(ticks_5s, comp["low"], comp["high"], comp["close"])

    swing = detect_swing_1m(ticks_all, now=now, ohlc=ohlc)
    if swing:
        out["swing_1m_lo"] = round(float(swing["low"]), 8)
        out["swing_1m_hi"] = round(float(swing["high"]), 8)
        found = detect_sweep(ticks_5s, height, swing) if ticks_5s else None
        if found:
            out["sweep_1m"] = True
            out["wick_squares_past"] = max(1, int(round(float(found["wick"]))))
            out["reclaim"] = True
        elif ticks_5s and height > 0:
            hi = max(t.price for t in ticks_5s)
            lo = min(t.price for t in ticks_5s)
            last = ticks_5s[-1].price
            up = (hi - swing["high"]) / height
            down = (swing["low"] - lo) / height
            if up >= 0.75:
                out["wick_squares_past"] = int(round(up))
                out["reclaim"] = last < swing["high"] - 1e-12
            elif down >= 0.75:
                out["wick_squares_past"] = int(round(down))
                out["reclaim"] = last > swing["low"] + 1e-12

    return attach_setup_clocks(
        out,
        pink_age_s=pink_age_s,
        blue_age_s=blue_age_s,
        range_shrinking=range_shrinking,
    )


def choose_setup(
    *,
    ticks_5s: Sequence[Any],
    ticks_all: Sequence[Any],
    height: float,
    stack: TimeframeStack | None,
    now: float,
    ohlc: dict | None = None,
) -> SetupHit | None:
    """First matching named tell. Sweep, then stall, then compression."""
    sweep = sweep_tell(ticks_5s, ticks_all, height, stack, now=now, ohlc=ohlc)
    if sweep:
        return sweep
    stall = stall_tell(ticks_5s, height, stack)
    if stall:
        return stall
    comp = compression_tell(ticks_all, height, now=now)
    if comp:
        return comp
    return None
