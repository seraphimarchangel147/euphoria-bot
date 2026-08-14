"""Explainable think signal: which nearby 5s square is likely to get touched.

Euphoria squares are price zones over a 5-second window. You win if price
touches the zone once; it does not need to stay there. Closer squares are
easier (lower multiplier); farther squares are harder.

This module only names a nearby square (or "no trade"). It never submits.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Sequence

WINDOW_S = 5.0
MIN_TICKS = 3
FLAT_THRESHOLD = 0.0003  # 3 bps — no real drift
# Default band width when the page grid is unknown (same 5 bps heuristic as the trader).
DEFAULT_CELL_BPS = 0.0005
# Need to project at least this fraction of one cell to call a nearby touch.
REACH_NEAREST = 0.4
MAX_NEAR_DISTANCE = 2
DEFAULT_SIZE = 0.10  # official default tap is small


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

    def to_dict(self) -> dict:
        suggested: dict | str
        if isinstance(self.suggested, Suggestion):
            suggested = self.suggested.to_dict()
        else:
            suggested = self.suggested
        return {
            "bias": self.bias,
            "confidence": self.confidence,
            "reason": self.reason,
            "suggested": suggested,
            "hint": self.hint,
            "asset": self.asset,
            "momentum_pct": self.momentum_pct,
        }


def _waiting(reason: str, asset: str = "ETH") -> Signal:
    return Signal(
        bias="flat",
        confidence=0.0,
        reason=reason,
        suggested="no trade",
        hint="no trade",
        asset=asset,
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


def _sign_flips(ticks: Sequence[Tick]) -> int:
    flips = 0
    prev = 0
    for a, b in zip(ticks, ticks[1:]):
        delta = b.price - a.price
        if delta == 0:
            continue
        sign = 1 if delta > 0 else -1
        if prev and sign != prev:
            flips += 1
        prev = sign
    return flips


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


def _map_cell(side: str, distance: int, grid: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Best-effort cellX/cellY if the page told us where the current square is."""
    if not grid:
        return None, None
    ox = grid.get("cell_x", grid.get("cellX", grid.get("origin_x")))
    oy = grid.get("cell_y", grid.get("cellY", grid.get("origin_y")))
    if ox is None or oy is None:
        return None, None
    try:
        cx = int(ox) + 1  # next 5s column
        cy = int(oy) + distance if side == "up" else int(oy) - distance
    except (TypeError, ValueError):
        return None, None
    return cx, cy


def _square_copy(side: str, distance: int) -> tuple[str, str, str]:
    if side == "up":
        if distance <= 1:
            return "nearest-up", "nearest square above", "nearest square above, ~5s, touch once"
        return "next-up", "next square above", "next square above, ~5s, touch once"
    if distance <= 1:
        return "nearest-down", "nearest square below", "nearest square below, ~5s, touch once"
    return "next-down", "next square below", "next square below, ~5s, touch once"


def compute_signal(
    ticks: Sequence[Tick] | "TickBuffer",
    *,
    now: float | None = None,
    window_s: float = WINDOW_S,
    size: float = DEFAULT_SIZE,
    cell_height: float | None = None,
    grid: dict[str, Any] | None = None,
) -> Signal:
    """Name the nearby square most likely to get touched in the next ~5s. No I/O."""
    now = time.time() if now is None else now
    if isinstance(ticks, TickBuffer):
        seq = ticks.snapshot(now=now)
    else:
        seq = list(ticks)

    eth = _in_window(seq, "ETH", now, window_s)
    if len(eth) < MIN_TICKS:
        return _waiting("waiting for ETH ticks")

    first, last = eth[0].price, eth[-1].price
    mom = _momentum(eth)
    mom_pct = mom * 100.0
    net = abs(last - first)
    rng = _range_width(eth)
    flips = _sign_flips(eth)
    height = _cell_height(last, cell_height, grid)
    reachable = net / height if height > 0 else 0.0

    btc = _in_window(seq, "BTC", now, window_s)
    btc_mom = _momentum(btc) if len(btc) >= 2 else None
    btc_confirms = (
        btc_mom is not None
        and abs(btc_mom) >= FLAT_THRESHOLD
        and ((btc_mom > 0 and mom > 0) or (btc_mom < 0 and mom < 0))
    )

    if flips >= 2 and rng > 2.0 * max(net, height * 0.25):
        return Signal(
            bias="flat",
            confidence=round(min(0.35, 0.12 + flips * 0.04), 3),
            reason="ETH tape is choppy over last 5s → no trade",
            suggested="no trade",
            hint="no trade",
            momentum_pct=round(mom_pct, 4),
        )

    if abs(mom) < FLAT_THRESHOLD or reachable < REACH_NEAREST:
        return Signal(
            bias="flat",
            confidence=round(min(0.3, 0.10 + (FLAT_THRESHOLD - min(abs(mom), FLAT_THRESHOLD)) * 200), 3),
            reason=f"ETH {mom_pct:+.2f}% over last 5s, quiet tape → no trade",
            suggested="no trade",
            hint="no trade",
            momentum_pct=round(mom_pct, 4),
        )

    bias = "up" if mom > 0 else "down"
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

    confidence = round(max(0.0, min(1.0, raw)), 3)
    if confidence < 0.4:
        return Signal(
            bias=bias,
            confidence=confidence,
            reason=f"ETH {mom_pct:+.2f}% over last 5s{extra} → weak, no trade",
            suggested="no trade",
            hint="no trade",
            momentum_pct=round(mom_pct, 4),
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
    )
    reason = f"ETH {mom_pct:+.2f}% over last 5s{extra} → {hint}"
    return Signal(
        bias=bias,
        confidence=confidence,
        reason=reason,
        suggested=suggested,
        hint=hint,
        momentum_pct=round(mom_pct, 4),
    )


class TickBuffer:
    """Bounded in-memory tick ring. Not a second price socket — just storage."""

    def __init__(self, maxlen: int = 800, max_age_s: float = 60.0) -> None:
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
