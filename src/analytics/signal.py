"""Explainable v1 think signal: 5-second ETH momentum / breakout.

Consumes a short tick buffer (extension WS/DOM ticks, or Redstone fallback).
Never submits a trade. Callers decide what to do with the Signal.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

WINDOW_S = 5.0
MIN_TICKS = 3
# Moves smaller than this are noise, not a tap.
FLAT_THRESHOLD = 0.0003  # 3 bps
# |momentum| at which confidence saturates.
FULL_MOVE = 0.002  # 20 bps
QUIET_VOL = 0.0004
DEFAULT_SIZE = 1.0


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
            "asset": self.asset,
            "momentum_pct": self.momentum_pct,
        }


def _waiting(reason: str, asset: str = "ETH") -> Signal:
    return Signal(
        bias="flat",
        confidence=0.0,
        reason=reason,
        suggested="no trade",
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


def _range_vol(ticks: Sequence[Tick]) -> float:
    prices = [t.price for t in ticks]
    mid = (max(prices) + min(prices)) / 2.0
    if mid <= 0:
        return 0.0
    return (max(prices) - min(prices)) / mid


def compute_signal(
    ticks: Sequence[Tick] | "TickBuffer",
    *,
    now: float | None = None,
    window_s: float = WINDOW_S,
    size: float = DEFAULT_SIZE,
) -> Signal:
    """5s ETH momentum with optional BTC confirmation. No I/O."""
    now = time.time() if now is None else now
    if isinstance(ticks, TickBuffer):
        seq = ticks.snapshot(now=now)
    else:
        seq = list(ticks)

    eth = _in_window(seq, "ETH", now, window_s)
    if len(eth) < MIN_TICKS:
        return _waiting("waiting for ETH ticks")

    mom = _momentum(eth)
    vol = _range_vol(eth)
    abs_mom = abs(mom)
    mom_pct = mom * 100.0

    btc = _in_window(seq, "BTC", now, window_s)
    btc_mom = _momentum(btc) if len(btc) >= 2 else None
    btc_confirms = (
        btc_mom is not None
        and abs(btc_mom) >= FLAT_THRESHOLD
        and ((btc_mom > 0 and mom > 0) or (btc_mom < 0 and mom < 0))
    )

    if abs_mom < FLAT_THRESHOLD:
        conf = round(min(0.35, 0.10 + (FLAT_THRESHOLD - abs_mom) * 200), 3)
        return Signal(
            bias="flat",
            confidence=conf,
            reason=f"ETH {mom_pct:+.2f}% over last 5s, below threshold → no trade",
            suggested="no trade",
            momentum_pct=round(mom_pct, 4),
        )

    bias = "up" if mom > 0 else "down"
    raw = min(1.0, abs_mom / FULL_MOVE)
    if vol > max(abs_mom * 2.0, QUIET_VOL * 3):
        raw *= 0.6
        vol_word = "choppy"
    elif vol <= QUIET_VOL:
        raw = min(1.0, raw * 1.1)
        vol_word = "vol quiet"
    else:
        vol_word = "vol normal"

    extra = ""
    if btc_confirms:
        raw = min(1.0, raw + 0.15)
        extra = ", BTC confirming"

    confidence = round(max(0.0, min(1.0, raw)), 3)
    action = "tap up" if bias == "up" else "tap down"
    reason = f"ETH {mom_pct:+.2f}% over last 5s, {vol_word}{extra} → {action}"

    suggested: Suggestion | str
    if confidence >= 0.4:
        suggested = Suggestion(asset="ETH", side=bias, size=size, cell=f"ETH-{bias}")
    else:
        suggested = "no trade"
        reason = f"ETH {mom_pct:+.2f}% over last 5s, {vol_word}{extra} → weak, no trade"

    return Signal(
        bias=bias,
        confidence=confidence,
        reason=reason,
        suggested=suggested,
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
