"""Explainable think signal: which nearby 5s square is likely to get touched.

Euphoria squares are price zones over a 5-second window. You win if price
touches the zone once; it does not need to stay there. Closer squares are
easier (lower multiplier); farther squares are harder.

Higher timeframes (1m / 5m / 1h / 4h / D / M) are bias only — they score or
gate a nearby tap (with-trend vs fading). They are never a reason to chase
far lottery cells.

This module only names a nearby square (or "no trade"). It never submits.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Sequence

from src.analytics.setups import (
    SetupMemory,
    choose_setup,
    detect_compression,
    detect_swing_1m,
    late_pink_sit,
    pair_lean,
    pink_metrics,
    window_id_for,
)
from src.analytics.timeframes import (
    TF_KEYS,
    TimeframeStack,
    alignment as tf_alignment,
    build_stack,
    empty_frames,
)

WINDOW_S = 5.0
SHORT_S = 1.2
MIN_TICKS = 3
DENSE_TICKS = 15
FLAT_THRESHOLD = 0.0003  # 3 bps — no real drift
# Default band width when the page grid is unknown (same 5 bps heuristic as the trader).
DEFAULT_CELL_BPS = 0.0005
# Need to project at least this fraction of one cell to call a nearby touch.
REACH_NEAREST = 0.4
MAX_NEAR_DISTANCE = 2
DEFAULT_SIZE = 0.10  # official default tap is small
FLIP_RATE_CHOP = 0.35
CONF_BAR = 0.4
FADING_BAR = 0.58
WITH_TREND_BOOST = 0.08
FADING_PENALTY = 0.12


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: float
    ts: float
    source: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())


@dataclass(frozen=True)
class Suggestion:
    asset: str
    side: str
    size: float
    cell: str
    distance: int
    label: str
    hint: str
    cell_x: int | None = None
    cell_y: int | None = None
    cell_height: float = 0.0
    looking_at: tuple = ()
    multiplier: float | None = None
    break_even_probability: float | None = None
    quoted_grid_ref_time: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Signal:
    bias: str  # up | down | flat
    confidence: float  # 0-1
    reason: str
    suggested: Suggestion | str  # Suggestion or "no trade"
    asset: str = "ETH"
    momentum_pct: float = 0.0
    hint: str = "no trade"
    looking_at: tuple = ()
    tf_stack: TimeframeStack | None = None
    alignment: str = "unknown"
    lesson: str = "waiting"
    why: str = "waiting on ticks"
    looking: str = "nearby above and below"
    setup: str = "none"
    sit_reason: str = ""
    wick_squares: float = 0.0
    compression_box: dict | None = None
    swing_1m: dict | None = None
    pink_age_s: float = 0.0
    range_shrinking: bool = False
    action: str = "sit"
    grid_context: dict | None = None

    def to_dict(self) -> dict:
        suggested: dict | str
        pick: dict | None
        if isinstance(self.suggested, Suggestion):
            suggested = self.suggested.to_dict()
            pick = {
                "side": self.suggested.side,
                "distance": self.suggested.distance,
                "cell": self.suggested.cell,
                "label": self.suggested.label,
                "hint": self.suggested.hint,
                "cell_x": self.suggested.cell_x,
                "cell_y": self.suggested.cell_y,
                "role": "sel",
                "multiplier": self.suggested.multiplier,
                "break_even_probability": self.suggested.break_even_probability,
                "quoted_grid_ref_time": self.suggested.quoted_grid_ref_time,
            }
        else:
            suggested = self.suggested
            pick = None
        candidates = [dict(t) if isinstance(t, dict) else t for t in self.looking_at]
        stack = self.tf_stack
        frames = stack.frames_dict() if stack else empty_frames()
        return {
            "bias": self.bias,
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested": suggested,
            "hint": self.hint,
            "looking_at": candidates,
            "candidates": candidates,
            "pick": pick,
            "timeframes": frames,
            "tf_lean": stack.lean if stack else "unknown",
            "tf_score": stack.score if stack else 0.0,
            "tf_line": stack.summary() if stack else " ".join(f"{k}·" for k in TF_KEYS),
            "alignment": self.alignment,
            "lesson": self.lesson,
            "why": self.why,
            "looking": self.looking,
            "setup": self.setup,
            "sit_reason": self.sit_reason,
            "wick_squares": self.wick_squares,
            "compression_box": self.compression_box,
            "swing_1m": self.swing_1m,
            "pink_age_s": self.pink_age_s,
            "range_shrinking": self.range_shrinking,
            "action": self.action,
            "asset": self.asset,
            "momentum_pct": self.momentum_pct,
            "grid_context": self.grid_context or {
                "authoritative": False,
                "reason": "quote grid unavailable",
                "cell_count": 0,
                "history": {"sample_count": 0},
            },
        }


def _waiting(
    reason: str,
    asset: str = "ETH",
    *,
    stack: TimeframeStack | None = None,
    looking: tuple = (),
    alignment: str = "unknown",
) -> Signal:
    return Signal(
        bias="flat",
        confidence=0.0,
        reason=reason,
        suggested="no trade",
        hint="no trade",
        asset=asset,
        looking_at=looking,
        tf_stack=stack,
        alignment=alignment,
        lesson="waiting",
        why="waiting on ticks",
        looking="nearby above and below",
    )


def _in_window(ticks: Sequence[Tick], symbol: str, now: float, window_s: float) -> list[Tick]:
    symbol = symbol.upper()
    out = [t for t in ticks if t.symbol == symbol and now - t.ts <= window_s and t.price > 0]
    out.sort(key=lambda t: t.ts)
    return out


def _momentum(ticks: Sequence[Tick]) -> float:
    first, last = ticks[0].price, ticks[-1].price
    if first <= 0:
        return 0.0
    return (last - first) / first


def _range_width(ticks: Sequence[Tick]) -> float:
    prices = [t.price for t in ticks]
    return max(prices) - min(prices)


def _sign_flips(ticks: Sequence[Tick]) -> tuple[int, int]:
    flips = 0
    moves = 0
    prev = 0
    for a, b in zip(ticks, ticks[1:]):
        delta = b.price - a.price
        if delta == 0:
            continue
        moves += 1
        sign = 1 if delta > 0 else -1
        if prev and sign != prev:
            flips += 1
        prev = sign
    return flips, moves


def _flip_rate(ticks: Sequence[Tick]) -> float:
    flips, moves = _sign_flips(ticks)
    if moves < 2:
        return 0.0
    return flips / moves


def _cell_height(last: float, cell_height: float | None, grid: dict[str, Any] | None) -> float:
    if cell_height is not None and cell_height > 0:
        return float(cell_height)
    if grid:
        raw = grid.get("cell_height") or grid.get("price_interval") or grid.get("priceInterval")
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            parsed = 0.0
        if parsed > 0:
            return parsed
    return max(last * DEFAULT_CELL_BPS, 1e-8)


def _grid_num(grid: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        raw = grid.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _current_indices(grid: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Current time/price cell from live page state (gridX/gridY) or origin aliases."""
    if not grid:
        return None, None
    gx = _grid_num(grid, "grid_x", "gridX")
    gy = _grid_num(grid, "grid_y", "gridY")
    now = _grid_num(grid, "now_ms", "now", "timestamp_ms")
    dur = _grid_num(grid, "square_duration", "squareDuration") or 5000.0
    price = _grid_num(grid, "price", "current_price", "currentPrice", "startPrice")
    dpl = _grid_num(
        grid, "dollars_per_line", "dollarsPerLine", "price_interval", "priceInterval", "cell_height"
    )
    if gx is None and now is not None and dur > 0:
        gx = now // dur
    if gy is None and price is not None and dpl and dpl > 0:
        gy = price // dpl
    if gx is None:
        gx = _grid_num(grid, "cell_x", "cellX", "origin_x")
    if gy is None:
        gy = _grid_num(grid, "cell_y", "cellY", "origin_y")
    if gx is None or gy is None:
        return None, None
    return int(gx), int(gy)


def _map_cell(side: str, distance: int, grid: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Next 5s column, one (or two) cells above/below the current price row."""
    gx, gy = _current_indices(grid)
    if gx is None or gy is None:
        return None, None
    cx = gx + 1
    cy = gy + distance if side == "up" else gy - distance
    return cx, cy


def _quoted_cells(grid: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    if not grid or grid.get("authoritative") is not True or not isinstance(grid.get("cells"), list):
        return ()
    out = []
    for raw in grid["cells"][:64]:
        if not isinstance(raw, dict):
            continue
        side = raw.get("side")
        try:
            multiplier = float(raw.get("multiplier"))
            forward = int(raw.get("forward"))
            distance = int(raw.get("distance"))
            cell_x = int(raw.get("cell_x"))
            cell_y = int(raw.get("cell_y"))
        except (TypeError, ValueError):
            continue
        if side not in ("up", "down") or multiplier <= 0 or forward not in (1, 2, 3):
            continue
        if distance not in (1, 2):
            continue
        try:
            break_even = float(raw.get("break_even_probability"))
        except (TypeError, ValueError):
            break_even = 1.0 / multiplier
        out.append({
            "side": side,
            "distance": distance,
            "cell_x": cell_x,
            "cell_y": cell_y,
            "forward": forward,
            "forward_s": float(raw.get("forward_s") or forward * 5),
            "multiplier": multiplier,
            "break_even_probability": round(break_even, 6),
            "role": "look",
        })
    return tuple(sorted(out, key=lambda item: (item["forward"], item["distance"], item["side"])))


def _quoted_cell(
    grid: dict[str, Any] | None, side: str, distance: int = 1, forward: int = 1
) -> dict[str, Any] | None:
    return next(
        (
            cell for cell in _quoted_cells(grid)
            if cell["side"] == side and cell["distance"] == distance and cell["forward"] == forward
        ),
        None,
    )


def _grid_context_summary(grid: dict[str, Any] | None) -> dict[str, Any]:
    cells = _quoted_cells(grid)
    history = grid.get("history") if isinstance(grid, dict) and isinstance(grid.get("history"), dict) else {}
    safe_history = {
        "window_s": history.get("window_s", 0),
        "sample_count": history.get("sample_count", 0),
        "change_bps": history.get("change_bps", 0),
        "range_bps": history.get("range_bps", 0),
        "samples": list(history.get("samples") or [])[-120:],
    }
    return {
        "authoritative": bool(cells) and bool(grid and grid.get("authoritative") is True),
        "reason": grid.get("reason") if isinstance(grid, dict) else "quote grid unavailable",
        "multiplier_source": grid.get("multiplier_source") if isinstance(grid, dict) else None,
        "quoted_grid_ref_time": grid.get("quoted_grid_ref_time") if isinstance(grid, dict) else None,
        "forward_columns": grid.get("forward_columns", 0) if isinstance(grid, dict) else 0,
        "cell_count": len(cells),
        "history": safe_history,
    }


def _looking_at(grid: dict[str, Any] | None) -> tuple:
    quoted = _quoted_cells(grid)
    if quoted:
        return quoted
    tiles = []
    for side, distance in (("up", 1), ("down", 1)):
        cx, cy = _map_cell(side, distance, grid)
        tiles.append({
            "side": side,
            "distance": distance,
            "cell_x": cx,
            "cell_y": cy,
            "role": "look",
        })
    return tuple(tiles)


def _square_copy(side: str, distance: int) -> tuple[str, str, str]:
    if side == "up":
        if distance <= 1:
            return "nearest-up", "nearest square above", "nearest square above, ~5s, touch once"
        return "next-up", "next square above", "next square above, ~5s, touch once"
    if distance <= 1:
        return "nearest-down", "nearest square below", "nearest square below, ~5s, touch once"
    return "next-down", "next square below", "next square below, ~5s, touch once"


def _page_dense(ticks: Sequence[Tick]) -> bool:
    if len(ticks) < DENSE_TICKS:
        return False
    return any(t.source in ("page", "extension", "ws") for t in ticks) or len(ticks) >= DENSE_TICKS


def _setup_fields(
    *,
    setup: str = "none",
    sit_reason: str = "",
    wick_squares: float = 0.0,
    compression_box: dict | None = None,
    swing_1m: dict | None = None,
    pink_age_s: float = 0.0,
    range_shrinking: bool = False,
    action: str = "sit",
) -> dict[str, Any]:
    return {
        "setup": setup,
        "sit_reason": sit_reason,
        "wick_squares": round(float(wick_squares), 3),
        "compression_box": compression_box,
        "swing_1m": swing_1m,
        "pink_age_s": round(float(pink_age_s), 3),
        "range_shrinking": bool(range_shrinking),
        "action": action,
    }


def _pick_nearby(
    side: str,
    *,
    size: float,
    height: float,
    grid: dict[str, Any] | None,
    looking: tuple,
) -> Suggestion:
    cell, label, hint = _square_copy(side, 1)
    cell_x, cell_y = _map_cell(side, 1, grid)
    quoted = _quoted_cell(grid, side, 1, 1)
    return Suggestion(
        asset="ETH",
        side=side,
        size=size,
        cell=cell,
        distance=1,
        label=label,
        hint=hint,
        cell_x=cell_x,
        cell_y=cell_y,
        cell_height=round(height, 8),
        looking_at=looking,
        multiplier=quoted.get("multiplier") if quoted else None,
        break_even_probability=quoted.get("break_even_probability") if quoted else None,
        quoted_grid_ref_time=grid.get("quoted_grid_ref_time") if quoted and grid else None,
    )


def _compute_signal(
    ticks: Sequence[Tick] | "TickBuffer",
    *,
    now: float | None = None,
    window_s: float = WINDOW_S,
    size: float = DEFAULT_SIZE,
    cell_height: float | None = None,
    grid: dict[str, Any] | None = None,
    ohlc: dict[str, Any] | None = None,
    memory: SetupMemory | None = None,
) -> Signal:
    """Name the nearby square most likely to get touched in the next ~5s. No I/O."""
    now = time.time() if now is None else now
    if isinstance(ticks, TickBuffer):
        seq = ticks.snapshot(now=now)
    else:
        seq = list(ticks)

    stack = build_stack(seq, symbol="ETH", now=now, ohlc=ohlc)
    looking = _looking_at(grid)

    eth = _in_window(seq, "ETH", now, window_s)
    if len(eth) < MIN_TICKS:
        return _waiting("waiting for ETH ticks", stack=stack, looking=looking)

    first, last = eth[0].price, eth[-1].price
    mom = _momentum(eth)
    mom_pct = mom * 100.0
    net = abs(last - first)
    rng = _range_width(eth)
    flips, _moves = _sign_flips(eth)
    flip_rate = _flip_rate(eth)
    height = _cell_height(last, cell_height, grid)
    reachable = net / height if height > 0 else 0.0
    wid = window_id_for(now, grid)
    htf_now = stack.lean if stack.lean in ("up", "down") else pair_lean(stack, "1h", "4h")
    pink = pink_metrics(
        eth,
        height,
        now=now,
        memory=memory,
        window_id=wid,
        htf=htf_now,
        has_candidates=True,
    )
    box_raw = detect_compression(seq, height, now=now)
    box = {"low": box_raw["low"], "high": box_raw["high"]} if box_raw else None
    swing = detect_swing_1m(seq, now=now, ohlc=ohlc)
    extra_fields = _setup_fields(
        wick_squares=0.0,
        compression_box=box,
        swing_1m=swing,
        pink_age_s=pink.age_s,
        range_shrinking=pink.shrinking,
        action="sit",
    )

    named = choose_setup(
        ticks_5s=eth,
        ticks_all=seq,
        height=height,
        stack=stack,
        now=now,
        ohlc=ohlc,
    )
    if named:
        extra_fields.update(
            _setup_fields(
                setup=named.name,
                sit_reason=named.reason if named.sit else "",
                wick_squares=named.wick_squares,
                compression_box=named.box or box,
                swing_1m=named.swing or swing,
                pink_age_s=pink.age_s,
                range_shrinking=pink.shrinking,
                action="sit" if named.sit else "tap",
            )
        )
        if named.sit:
            return Signal(
                bias="flat",
                confidence=0.35,
                reason=named.reason,
                suggested="no trade",
                hint="no trade",
                looking_at=looking,
                momentum_pct=round(mom_pct, 4),
                tf_stack=stack,
                alignment=tf_alignment(named.side or "flat", stack.lean) if named.side else "unknown",
                lesson=named.name,
                why=named.reason,
                looking="nearby above and below",
                **extra_fields,
            )
        suggested = _pick_nearby(named.side or "up", size=size, height=height, grid=grid, looking=looking)
        if memory is not None:
            memory.mark_blue()
        extra_fields["action"] = "tap"
        extra_fields["sit_reason"] = ""
        return Signal(
            bias=named.side or "up",
            confidence=0.62,
            reason=named.reason + f" → {suggested.hint}",
            suggested=suggested,
            hint=suggested.hint,
            looking_at=looking,
            momentum_pct=round(mom_pct, 4),
            tf_stack=stack,
            alignment=tf_alignment(named.side or "up", stack.lean),
            lesson=named.name,
            why=named.reason,
            looking=suggested.label,
            **extra_fields,
        )

    btc = _in_window(seq, "BTC", now, window_s)
    btc_mom = _momentum(btc) if len(btc) >= 2 else None
    btc_confirms = (
        btc_mom is not None
        and abs(btc_mom) >= FLAT_THRESHOLD
        and ((btc_mom > 0 and mom > 0) or (btc_mom < 0 and mom < 0))
    )

    if flip_rate > FLIP_RATE_CHOP and rng > 2.0 * max(net, height * 0.25):
        return Signal(
            bias="flat",
            confidence=round(min(0.35, 0.12 + flips * 0.04), 3),
            reason="ETH tape is choppy over last 5s → no trade",
            suggested="no trade",
            hint="no trade",
            looking_at=looking,
            momentum_pct=round(mom_pct, 4),
            tf_stack=stack,
            alignment="unknown",
            lesson="chop",
            why="chop — tape is whipping, standing aside",
            looking="nearby above and below",
            **extra_fields,
        )

    if abs(mom) < FLAT_THRESHOLD or reachable < REACH_NEAREST:
        return Signal(
            bias="flat",
            confidence=round(min(0.3, 0.10 + (FLAT_THRESHOLD - min(abs(mom), FLAT_THRESHOLD)) * 200), 3),
            reason=f"ETH {mom_pct:+.2f}% over last 5s, quiet tape → no trade",
            suggested="no trade",
            hint="no trade",
            looking_at=looking,
            momentum_pct=round(mom_pct, 4),
            tf_stack=stack,
            alignment="unknown",
            lesson="quiet",
            why="quiet — not enough drift to tag a nearby square",
            looking="nearby above and below",
            **extra_fields,
        )

    bias = "up" if mom > 0 else "down"
    how = tf_alignment(bias, stack.lean)

    # Dense / ~10Hz tape: if the last second disagrees, the 5s drift already faded.
    if _page_dense(eth):
        short = _in_window(eth, "ETH", now, SHORT_S)
        if len(short) >= 4:
            short_mom = _momentum(short)
            if abs(short_mom) >= FLAT_THRESHOLD and (short_mom > 0) != (mom > 0):
                return Signal(
                    bias="flat",
                    confidence=round(min(0.35, 0.18 + abs(short_mom) * 40), 3),
                    reason="ETH 5s drift faded on the recent tape → no trade",
                    suggested="no trade",
                    hint="no trade",
                    looking_at=looking,
                    momentum_pct=round(mom_pct, 4),
                    tf_stack=stack,
                    alignment=how,
                    lesson="faded",
                    why="recent tape faded — standing aside",
                    looking="nearby above and below",
                    **extra_fields,
                )

    # Touch-once: the nearest square on the drift side is the most likely hit.
    # Far cells are never named, even on a large print.
    distance = min(1, MAX_NEAR_DISTANCE)

    cell, label, hint = _square_copy(bias, distance)
    cell_x, cell_y = _map_cell(bias, distance, grid)

    raw = min(1.0, abs(mom) / 0.002)
    if rng > abs(last - first) * 1.8:
        raw *= 0.75
    extra = ""
    if btc_confirms:
        raw = min(1.0, raw + 0.12)
        extra = ", BTC agreeing"
    if _page_dense(eth):
        extra += ", page tape"

    tf_note = ""
    if how == "with-trend":
        raw = min(1.0, raw + WITH_TREND_BOOST)
        tf_note = f", with-trend vs {stack.lean}"
    elif how == "fading":
        raw = max(0.0, raw - FADING_PENALTY)
        tf_note = f", fading vs higher-TF {stack.lean}"

    confidence = round(max(0.0, min(1.0, raw)), 3)
    bar = FADING_BAR if how == "fading" else CONF_BAR
    if confidence < bar:
        why = "fading vs higher TF, no trade" if how == "fading" else "weak, no trade"
        lesson = "fade" if how == "fading" else "weak"
        teach = (
            "fade — 5s tape is against the higher-TF lean, standing aside"
            if how == "fading"
            else "weak 5s drift — not a tap"
        )
        return Signal(
            bias=bias,
            confidence=confidence,
            reason=f"ETH {mom_pct:+.2f}% over last 5s{extra}{tf_note} → {why}",
            suggested="no trade",
            hint="no trade",
            looking_at=looking,
            momentum_pct=round(mom_pct, 4),
            tf_stack=stack,
            alignment=how,
            lesson=lesson,
            why=teach,
            looking="nearby above and below",
            **extra_fields,
        )

    late = late_pink_sit(pink, would_pick=True)
    if late:
        extra_fields.update(
            _setup_fields(
                setup="late_pink",
                sit_reason=late.reason,
                wick_squares=extra_fields["wick_squares"],
                compression_box=box,
                swing_1m=swing,
                pink_age_s=pink.age_s,
                range_shrinking=pink.shrinking,
                action="sit",
            )
        )
        return Signal(
            bias=bias,
            confidence=confidence,
            reason=late.reason,
            suggested="no trade",
            hint="no trade",
            looking_at=looking,
            momentum_pct=round(mom_pct, 4),
            tf_stack=stack,
            alignment=how,
            lesson="late_pink",
            why=late.reason,
            looking="nearby above and below",
            **extra_fields,
        )

    quoted = _quoted_cell(grid, bias, distance, 1)
    suggested = Suggestion(
        asset="ETH",
        side=bias,
        size=size,
        cell=cell,
        distance=distance,
        label=label,
        hint=hint,
        cell_x=cell_x,
        cell_y=cell_y,
        cell_height=round(height, 8),
        looking_at=looking,
        multiplier=quoted.get("multiplier") if quoted else None,
        break_even_probability=quoted.get("break_even_probability") if quoted else None,
        quoted_grid_ref_time=grid.get("quoted_grid_ref_time") if quoted and grid else None,
    )
    reason = f"ETH {mom_pct:+.2f}% over last 5s{extra}{tf_note} → {hint}"
    if how == "with-trend":
        lesson, teach = "with-trend", "with-trend nearby tap — higher TFs agree"
    elif how == "fading":
        lesson, teach = "fade", "fade — 5s tape is against the higher-TF lean, still the nearby square"
    else:
        lesson, teach = "mixed", "nearby tap — higher TFs are mixed"
    extra_fields["action"] = "tap"
    extra_fields["setup"] = "none"
    extra_fields["sit_reason"] = ""
    if memory is not None:
        memory.mark_blue()
    return Signal(
        bias=bias,
        confidence=confidence,
        reason=reason,
        suggested=suggested,
        hint=hint,
        looking_at=looking,
        momentum_pct=round(mom_pct, 4),
        tf_stack=stack,
        alignment=how,
        lesson=lesson,
        why=teach,
        looking=label,
        **extra_fields,
    )


class TickBuffer:
    """Bounded in-memory tick ring. Not a second price socket — just storage.

    Sized for a ~10Hz page feed over ~20 minutes so 1m / 5m bars can be
    accumulated when public OHLC is missing.
    """

    def __init__(self, maxlen: int = 12_000, max_age_s: float = 1200.0) -> None:
        self._ticks: deque[Tick] = deque(maxlen=maxlen)
        self.max_age_s = max_age_s

    def __len__(self) -> int:
        return len(self._ticks)

    def push(
        self,
        symbol: str,
        price: float,
        ts: float | None = None,
        source: str = "unknown",
    ) -> Tick | None:
        try:
            px = float(price)
        except (TypeError, ValueError):
            return None
        if px <= 0:
            return None
        tick = Tick(symbol=symbol, price=px, ts=float(ts if ts is not None else time.time()), source=source)
        self._ticks.append(tick)
        return tick

    def extend(self, ticks: Iterable[Tick | dict]) -> int:
        n = 0
        for item in ticks:
            if isinstance(item, Tick):
                if item.price > 0:
                    self._ticks.append(item)
                    n += 1
                continue
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol") or item.get("asset")
            price = item.get("price") or item.get("value")
            if not symbol or price is None:
                continue
            ts = item.get("ts") or item.get("timestamp") or item.get("time")
            if ts is not None and float(ts) > 1e12:
                ts = float(ts) / 1000.0
            if self.push(str(symbol), price, ts, source=str(item.get("source") or "unknown")):
                n += 1
        return n

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        while self._ticks and now - self._ticks[0].ts > self.max_age_s:
            self._ticks.popleft()

    def snapshot(self, now: float | None = None) -> list[Tick]:
        self.prune(now)
        return list(self._ticks)

    def latest(self, symbol: str) -> Tick | None:
        symbol = symbol.upper()
        for tick in reversed(self._ticks):
            if tick.symbol == symbol:
                return tick
        return None

    def latest_quotes(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for tick in reversed(self._ticks):
            if tick.symbol in out:
                continue
            out[tick.symbol] = {
                "price": tick.price,
                "ts": tick.ts,
                "source": tick.source,
            }
            if len(out) >= 8:
                break
        return dict(sorted(out.items()))


def compute_signal(
    ticks: Sequence[Tick] | TickBuffer,
    *,
    now: float | None = None,
    window_s: float = WINDOW_S,
    size: float = DEFAULT_SIZE,
    cell_height: float | None = None,
    grid: dict[str, Any] | None = None,
    ohlc: dict[str, Any] | None = None,
    memory: SetupMemory | None = None,
) -> Signal:
    """Compute the tape signal and attach bounded quote-grid/history evidence."""
    signal = _compute_signal(
        ticks,
        now=now,
        window_s=window_s,
        size=size,
        cell_height=cell_height,
        grid=grid,
        ohlc=ohlc,
        memory=memory,
    )
    return replace(signal, grid_context=_grid_context_summary(grid))
