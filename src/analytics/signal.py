"""Explainable think signal: which square on the grid is worth a tap.

Euphoria squares are price zones over a 5-second window. You win if price
touches the zone once; it does not need to stay there. Every cell carries a
quoted multiplier, so the only question that decides anything is

    P(touch) * multiplier > 1

Two layers live here:

* **The surface.** When the page hands us quoted multipliers, every cell on the
  grid -- any column forward, any row above, below or diagonal -- is priced
  against its own multiplier and ranked by expected value on the lower bound of
  a calibrated probability. That is the decision engine.
* **The legacy read.** Momentum, higher-timeframe bias and the named setups
  (stall / compression / sweep / late pink) still run. Without a quoted surface
  they name a nearby square as before; with one they become context and veto.

Both paths now pass through the same reachability gate: a square is never named
unless the fitted diffusion says the tape can actually reach it inside the
window. Skipping that gate is what made the setup path name squares three
standard deviations away.

This module never submits.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence

from config import settings
from src.analytics.implied import implied_sigma
from src.analytics.forecast import (
    Diffusion,
    band_touch_prob,
    estimate_diffusion,
    forecast_grid,
    neighbourhood,
)
from src.analytics.policy import surface_summary

from src.analytics.setups import (
    SETUP_PAYLOAD_KEYS,
    SetupMemory,
    attach_setup_clocks,
    build_setup_payload,
    choose_setup,
    detect_compression,
    detect_swing_1m,
    empty_setup_payload,
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

# A square is never named below this modelled touch chance, whichever path
# proposed it. The setup path used to skip this entirely, which is how
# compression breaks ended up naming squares the tape could not reach.
MIN_TAP_PROB = 0.25
# Grid the surface covers when the page has not sent quoted cells.
SURFACE_FORWARD = 6
SURFACE_RADIUS = 4


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
    setup_metrics: dict = field(default_factory=empty_setup_payload)
    # -- surface layer (empty on the legacy path) ---------------------------
    surface: tuple = ()
    surface_stats: dict | None = None
    diffusion: dict | None = None
    plan: dict | None = None
    reach_prob: float | None = None
    vol_bucket: str = "MED"

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
            }
        else:
            suggested = self.suggested
            pick = None
        candidates = [dict(t) if isinstance(t, dict) else t for t in self.looking_at]
        stack = self.tf_stack
        frames = stack.frames_dict() if stack else empty_frames()
        body = {
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
            "surface": [dict(c) if isinstance(c, dict) else c for c in self.surface],
            "surface_stats": self.surface_stats,
            "diffusion": self.diffusion,
            "plan": self.plan,
            "reach_prob": self.reach_prob,
            "vol_bucket": self.vol_bucket,
        }
        metrics = self.setup_metrics or empty_setup_payload()
        blank = empty_setup_payload()
        for key in SETUP_PAYLOAD_KEYS:
            body[key] = metrics[key] if key in metrics else blank[key]
        return body


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
        raw = (
            grid.get("cell_height") or grid.get("dollars_per_line")
            or grid.get("dollarsPerLine") or grid.get("price_interval")
            or grid.get("priceInterval")
        )
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


def _looking_at(grid: dict[str, Any] | None) -> tuple:
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
    setup_metrics: dict | None = None,
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
        "setup_metrics": setup_metrics if setup_metrics is not None else empty_setup_payload(),
    }


def _clock_metrics(
    metrics: dict[str, Any],
    *,
    pink,
    memory: SetupMemory | None,
    now: float,
    has_pick: bool,
) -> dict[str, Any]:
    if has_pick and memory is not None:
        memory.mark_blue(now)
    blue: float | None
    if not has_pick:
        blue = None
    elif memory is not None:
        blue = memory.blue_age(now)
        if blue is None:
            blue = 0.0
    else:
        blue = 0.0
    return attach_setup_clocks(
        metrics,
        pink_age_s=pink.age_s,
        blue_age_s=blue,
        range_shrinking=pink.shrinking,
    )


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
    )


GRID_STALE_S = 3.0


def _column_geometry(grid: dict[str, Any] | None, now: float) -> tuple[int, float, float]:
    """(current column index, seconds already elapsed in it, column length).

    The page's `now_ms` is a server-clock reading taken when the snapshot was
    built, not now. Advancing it by the snapshot's own age removes a bias that
    is always in the same direction -- the graded window otherwise slides late
    by exactly the transport delay, never early, so it never averages out.
    A snapshot older than GRID_STALE_S means the tab has stalled; fall back to
    the local clock rather than replaying a frozen instant forever.
    """
    column_s = WINDOW_S
    if grid:
        raw = _grid_num(grid, "square_duration", "squareDuration")
        if raw and raw > 0:
            column_s = raw / 1000.0 if raw > 20 else raw
    if column_s <= 0:
        column_s = WINDOW_S
    now_ms = _grid_num(grid, "now_ms", "now", "timestamp_ms") if grid else None
    if now_ms is not None:
        received = _grid_num(grid, "_received_local") if grid else None
        if received is not None:
            age = now - received
            if age > GRID_STALE_S or age < -1.0:
                now_ms = None                   # stalled tab: do not trust it
            else:
                now_ms += max(0.0, age) * 1000.0
    if now_ms is not None:
        period_ms = column_s * 1000.0
        return int(now_ms // period_ms), (now_ms % period_ms) / 1000.0, column_s
    return int(now // column_s), now % column_s, column_s


def reach_prob(
    side: str,
    distance: int,
    price: float,
    height: float,
    diffusion: Diffusion,
    *,
    column_s: float = WINDOW_S,
) -> float:
    """Modelled chance of touching that square inside the next window.

    Evaluated from here over the next ``column_s`` seconds -- the same "~5s,
    touch once" question the copy has always claimed to answer.
    """
    if height <= 0 or price <= 0:
        return 0.0
    row = math.floor(price / height)
    target = row + distance if side == "up" else row - distance
    lo = target * height
    return band_touch_prob(lo - price, lo + height - price, 0.0, column_s, diffusion)


def _surface_cells(grid: dict[str, Any] | None) -> tuple[list[dict[str, Any]] | None, bool]:
    """Quoted cells from the page, if the extension found the quote feed."""
    if isinstance(grid, dict):
        raw = grid.get("cells")
        if isinstance(raw, list) and raw:
            return raw, True
    return None, False


def _anchor_forecasts(*, now: float, price: float, height: float,
                      grid: dict[str, Any] | None, diffusion: Diffusion):
    """The quoted grid as forecasts, purely so the anchor can be solved for.

    Deliberately separate from `_build_surface`: the anchor has to be settled
    before the surface is priced, or the surface would be built on the sigma the
    anchor is about to replace.
    """
    cur_x, offset, column_s = _column_geometry(grid, now)
    cells, quoted = _surface_cells(grid)
    if not cells or not quoted:
        return ()
    return forecast_grid(
        cells, price=price, dpl=height, column_s=column_s,
        current_x=cur_x, now_offset_s=offset, diffusion=diffusion,
    )


def _build_surface(
    *,
    now: float,
    price: float,
    height: float,
    grid: dict[str, Any] | None,
    diffusion: Diffusion,
    calibrator: Any,
    policy: Any,
    bankroll: Any,
    vol_bucket: str,
    reachability: Any = None,
    limit: int = 96,
) -> dict[str, Any]:
    """Price every cell on the grid. Returns rows, stats, and the chosen plan."""
    blank = {"rows": (), "stats": None, "plan": None, "scored": (), "quoted": False}
    if price <= 0 or height <= 0:
        return blank
    cur_x, offset, column_s = _column_geometry(grid, now)
    cells, quoted = _surface_cells(grid)
    if cells is None:
        cells = neighbourhood(
            cur_x, math.floor(price / height),
            forward=SURFACE_FORWARD, radius=SURFACE_RADIUS,
        )
    forecasts = forecast_grid(
        cells,
        price=price,
        dpl=height,
        column_s=column_s,
        current_x=cur_x,
        now_offset_s=offset,
        diffusion=diffusion,
    )
    if not forecasts:
        return blank
    if policy is None or calibrator is None:
        rows = sorted(
            (dict(f.to_dict(), verdict="unscored") for f in forecasts),
            key=lambda r: r["p_touch"],
            reverse=True,
        )
        return {"rows": tuple(rows[:limit]), "stats": None, "plan": None,
                "scored": (), "quoted": quoted}
    scored = policy.score(
        forecasts, calibrator, vol_bucket=vol_bucket, bankroll=bankroll,
        diffusion_ok=diffusion.ok, reachability=reachability,
    )
    chosen = policy.choose(scored)
    return {
        "rows": tuple(s.to_dict() for s in scored[:limit]),
        "stats": surface_summary(scored),
        "plan": chosen,
        "scored": tuple(scored),
        "quoted": quoted,
    }


def _plan_signal(
    plan: Any,
    *,
    stack: TimeframeStack,
    looking: tuple,
    surface: dict[str, Any],
    diffusion: Diffusion,
    vol_bucket: str,
    mom_pct: float,
    extra_fields: dict[str, Any],
    height: float,
) -> Signal:
    """Build the Signal for a positive-edge cell chosen off the surface."""
    where = f"+{plan.forward}col {plan.row_offset:+d}row"
    hint = (
        f"{plan.side} {plan.distance} row{'s' if plan.distance != 1 else ''} "
        f"{plan.forward} column{'s' if plan.forward != 1 else ''} out "
        f"(~{plan.horizon_s:.0f}s), touch once"
    )
    reason = (
        f"EV+{plan.ev_lcb:.2f} on {where}: p={plan.p_cal:.3f} "
        f"(lcb {plan.p_lcb:.3f}) vs breakeven {plan.breakeven:.3f} at {plan.multiplier:g}x "
        f"→ stake {plan.stake:g}"
    )
    suggested = Suggestion(
        asset="ETH",
        side=plan.side if plan.side in ("up", "down") else "up",
        size=plan.stake,
        cell=where,
        distance=plan.distance,
        label=f"{plan.side} {plan.distance} · {plan.forward} out",
        hint=hint,
        cell_x=plan.cell_x,
        cell_y=plan.cell_y,
        cell_height=round(height, 8),
        looking_at=looking,
    )
    # extra_fields already carries the surface keys; drop them so they are not
    # passed twice alongside the explicit arguments below.
    fields = {
        k: v for k, v in extra_fields.items()
        if k not in ("surface", "surface_stats", "diffusion", "vol_bucket", "reach_prob")
    }
    fields["action"] = "tap"
    fields["sit_reason"] = ""
    return Signal(
        bias=plan.side if plan.side in ("up", "down") else "flat",
        confidence=round(min(1.0, max(0.0, plan.p_lcb)), 3),
        reason=reason,
        suggested=suggested,
        hint=hint,
        looking_at=looking,
        momentum_pct=round(mom_pct, 4),
        tf_stack=stack,
        alignment=tf_alignment(plan.side, stack.lean),
        lesson="edge",
        why=f"quoted {plan.multiplier:g}x pays above our touch odds",
        looking=suggested.label,
        surface=surface["rows"],
        surface_stats=surface["stats"],
        diffusion=diffusion.to_dict(),
        plan=plan.to_dict(),
        reach_prob=round(plan.p_cal, 6),
        vol_bucket=vol_bucket,
        **fields,
    )


def compute_signal(
    ticks: Sequence[Tick] | "TickBuffer",
    *,
    now: float | None = None,
    window_s: float = WINDOW_S,
    size: float = DEFAULT_SIZE,
    cell_height: float | None = None,
    grid: dict[str, Any] | None = None,
    ohlc: dict[str, Any] | None = None,
    memory: SetupMemory | None = None,
    calibrator: Any = None,
    policy: Any = None,
    bankroll: Any = None,
    vol_bucket_override: str | None = None,
    smoother: Any = None,
    reachability: Any = None,
) -> Signal:
    """Price the grid, and name a square only if it is both reachable and paid.

    With ``calibrator``/``policy`` supplied and quoted multipliers on the grid,
    the surface decides. Without them this behaves as it always did, except that
    no square is named unless the diffusion fit says it is reachable.
    """
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
    metrics = build_setup_payload(
        ticks_5s=eth,
        ticks_all=seq,
        height=height,
        stack=stack,
        now=now,
        ohlc=ohlc,
        pink_age_s=pink.age_s,
        blue_age_s=None,
        range_shrinking=pink.shrinking,
    )
    extra_fields = _setup_fields(
        wick_squares=0.0,
        compression_box=box,
        swing_1m=swing,
        pink_age_s=pink.age_s,
        range_shrinking=pink.shrinking,
        action="sit",
        setup_metrics=_clock_metrics(metrics, pink=pink, memory=memory, now=now, has_pick=False),
    )

    # -- surface layer ------------------------------------------------------
    diff = estimate_diffusion(seq, now=now)
    # Each pass refits sigma from scratch over 45 seconds of tape, so it swung
    # 16-fold across a 50-minute stretch in which price moved six rows. Ranking
    # cells by probability and taking the maximum then selects whichever square
    # is riding the largest upward estimation error, which is how the ranking
    # scored worse than random. Carry the estimate across passes.
    if smoother is not None:
        # Anchor to the house's own grid when it is quoting. Our tick fit read
        # 0.0105 against a house-implied 0.199 -- 19x apart -- and the observed
        # touch rate agreed with the house. Smoothing a number that wrong just
        # carries it steadily.
        anchor = None
        if settings.USE_HOUSE_ANCHOR and grid and grid.get("cells"):
            try:
                probe = _anchor_forecasts(now=now, price=last, height=height, grid=grid,
                                          diffusion=diff)
                if probe:
                    anchor = implied_sigma(probe, diff, price=last).get("sigma")
            except Exception:
                anchor = None
        diff = smoother.update(diff, now=now, anchor=anchor)
    # The regime label must not come from the same sigma that drives the
    # prediction, or conditioning on it selects our own estimation error and
    # the bucket is miscalibrated by construction. An independent label -- the
    # house's own volatility -- is used when one is available.
    vol_bucket = vol_bucket_override or diff.vol_bucket(height)
    _, _, column_s = _column_geometry(grid, now)
    surface = _build_surface(
        now=now, price=last, height=height, grid=grid, diffusion=diff,
        calibrator=calibrator, policy=policy, bankroll=bankroll, vol_bucket=vol_bucket,
        reachability=reachability,
    )
    extra_fields["surface"] = surface["rows"]
    extra_fields["surface_stats"] = surface["stats"]
    extra_fields["diffusion"] = diff.to_dict()
    extra_fields["vol_bucket"] = vol_bucket

    plan = surface["plan"]
    if plan is not None:
        return _plan_signal(
            plan, stack=stack, looking=looking, surface=surface, diffusion=diff,
            vol_bucket=vol_bucket, mom_pct=mom_pct, extra_fields=extra_fields,
            height=height,
        )

    def _unreachable(side: str, distance: int = 1) -> tuple[bool, float]:
        """Same gate for every path: can the tape actually get there?

        A failed diffusion fit means *unknown*, not *unreachable* -- with too
        few ticks to measure volatility the gate has no basis to veto, so it
        stands down rather than blocking everything.
        """
        p = reach_prob(side, distance, last, height, diff, column_s=column_s)
        if not diff.ok:
            return False, p
        return p < MIN_TAP_PROB, p

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
        extra_fields["setup_metrics"] = _clock_metrics(
            metrics, pink=pink, memory=memory, now=now, has_pick=not named.sit
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
        # The gate the setup path used to skip entirely.
        side = named.side or "up"
        blocked, p_reach = _unreachable(side)
        extra_fields["reach_prob"] = round(p_reach, 6)
        if blocked:
            why = (
                f"{named.name} — square is {p_reach:.0%} to be touched in {column_s:.0f}s "
                f"({diff.sd_over(column_s) / height:.2f} cells of range), sitting"
            )
            extra_fields["action"] = "sit"
            extra_fields["sit_reason"] = why
            return Signal(
                bias="flat",
                confidence=round(p_reach, 3),
                reason=why,
                suggested="no trade",
                hint="no trade",
                looking_at=looking,
                momentum_pct=round(mom_pct, 4),
                tf_stack=stack,
                alignment=tf_alignment(side, stack.lean),
                lesson="out-of-reach",
                why=why,
                looking="nearby above and below",
                **extra_fields,
            )
        suggested = _pick_nearby(side, size=size, height=height, grid=grid, looking=looking)
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
                setup_metrics=_clock_metrics(metrics, pink=pink, memory=memory, now=now, has_pick=False),
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

    blocked, p_reach = _unreachable(bias, distance)
    extra_fields["reach_prob"] = round(p_reach, 6)
    if blocked:
        why = (
            f"{p_reach:.0%} to touch the {label} in {column_s:.0f}s — the tape only "
            f"carries {diff.sd_over(column_s) / height:.2f} cells of range, sitting"
        )
        return Signal(
            bias=bias,
            confidence=round(p_reach, 3),
            reason=f"ETH {mom_pct:+.2f}% over last 5s{extra}{tf_note} → {why}",
            suggested="no trade",
            hint="no trade",
            looking_at=looking,
            momentum_pct=round(mom_pct, 4),
            tf_stack=stack,
            alignment=how,
            lesson="out-of-reach",
            why=why,
            looking="nearby above and below",
            **extra_fields,
        )

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
    extra_fields["setup_metrics"] = _clock_metrics(
        metrics, pink=pink, memory=memory, now=now, has_pick=True
    )
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

    def history(self, symbol: str, window_s: float = 60.0, now: float | None = None, max_points: int = 180) -> list[dict]:
        now = time.time() if now is None else now
        symbol = symbol.upper()
        cut = now - window_s
        pts = [{"t": t.ts, "p": t.price} for t in self._ticks if t.symbol == symbol and t.ts >= cut]
        if len(pts) <= max_points:
            return pts
        last = len(pts) - 1
        lo_i = min(range(len(pts)), key=lambda i: pts[i]["p"])
        hi_i = max(range(len(pts)), key=lambda i: pts[i]["p"])
        must = {0, last, lo_i, hi_i}
        keep = set(must)
        for i in range(max_points):
            keep.add(int(round(i * last / (max_points - 1))))
        if len(keep) > max_points:
            extras = sorted(keep - must)
            keep = must | set(extras[: max(0, max_points - len(must))])
        return [pts[i] for i in sorted(keep)]

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
        for sym, q in out.items():
            q["history"] = self.history(sym, 60.0)
        return dict(sorted(out.items()))
