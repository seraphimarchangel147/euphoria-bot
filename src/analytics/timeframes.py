"""Multi-timeframe trend context. Bias only — never a reason to chase far squares.

Timeframes: 1m, 5m, 1h, 4h, D, M. Bars come from public OHLC when available,
or from accumulated ticks for the short windows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Protocol, Sequence


class _Priced(Protocol):
    symbol: str
    price: float
    ts: float

TF_KEYS = ("1m", "5m", "1h", "4h", "D", "M")
TF_PERIOD_S = {
    "1m": 60,
    "5m": 300,
    "1h": 3600,
    "4h": 14400,
    "D": 86400,
    "M": 2592000,
}
TF_WEIGHTS = {"1m": 1.0, "5m": 1.2, "1h": 1.5, "4h": 1.5, "D": 1.8, "M": 1.2}
# Move smaller than this (fraction) is flat on that TF.
TF_FLAT = {"1m": 0.0004, "5m": 0.0006, "1h": 0.001, "4h": 0.0015, "D": 0.003, "M": 0.01}


@dataclass
class Bar:
    ts: float  # bar open unix seconds
    open: float
    high: float
    low: float
    close: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TfTrend:
    key: str
    lean: str  # up | down | flat | unknown
    change_pct: float
    source: str  # ticks | ohlc | unknown
    bars: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TimeframeStack:
    symbol: str
    frames: tuple[TfTrend, ...]
    lean: str  # up | down | mixed | unknown
    score: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "lean": self.lean,
            "score": self.score,
            "line": self.summary(),
            "frames": self.frames_dict(),
        }

    def frame(self, key: str) -> TfTrend | None:
        for item in self.frames:
            if item.key == key:
                return item
        return None

    def frames_dict(self) -> dict[str, dict]:
        return {
            f.key: {
                "lean": f.lean,
                "change_pct": f.change_pct,
                "source": f.source,
                "bars": f.bars,
            }
            for f in self.frames
        }

    def summary(self) -> str:
        mark = {"up": "↑", "down": "↓", "flat": "→", "unknown": "·"}
        return " ".join(f"{f.key}{mark.get(f.lean, '·')}" for f in self.frames)


def empty_frames() -> dict[str, dict]:
    return {key: {"lean": "unknown", "change_pct": 0.0, "source": "unknown", "bars": 0} for key in TF_KEYS}


def ticks_to_bars(ticks: Sequence[_Priced], period_s: float, *, now: float | None = None) -> list[Bar]:
    """Bucket ticks into OHLC bars. No I/O."""
    period = float(period_s)
    if period <= 0:
        return []
    buckets: dict[int, Bar] = {}
    for tick in ticks:
        if tick.price <= 0:
            continue
        if now is not None and tick.ts > now:
            continue
        key = int(tick.ts // period) * int(period)
        bar = buckets.get(key)
        if bar is None:
            buckets[key] = Bar(ts=float(key), open=tick.price, high=tick.price, low=tick.price, close=tick.price)
            continue
        bar.high = max(bar.high, tick.price)
        bar.low = min(bar.low, tick.price)
        bar.close = tick.price
    return [buckets[k] for k in sorted(buckets)]


def classify_bars(bars: Sequence[Bar], *, key: str, source: str) -> TfTrend:
    """Lean from first-open → last-close. One bar uses its own open/close."""
    if not bars:
        return TfTrend(key=key, lean="unknown", change_pct=0.0, source="unknown", bars=0)
    first, last = bars[0], bars[-1]
    if first.open <= 0:
        return TfTrend(key=key, lean="unknown", change_pct=0.0, source=source, bars=len(bars))
    change = (last.close - first.open) / first.open
    floor = TF_FLAT.get(key, 0.001)
    if abs(change) < floor:
        lean = "flat"
    else:
        lean = "up" if change > 0 else "down"
    return TfTrend(
        key=key,
        lean=lean,
        change_pct=round(change * 100.0, 4),
        source=source,
        bars=len(bars),
    )


def _bars_for(
    key: str,
    ticks: Sequence[_Priced],
    ohlc: dict[str, Sequence[Bar]] | None,
    now: float | None,
) -> tuple[list[Bar], str]:
    canned = (ohlc or {}).get(key)
    if canned:
        return list(canned), "ohlc"
    period = TF_PERIOD_S[key]
    # Ticks only cover short windows reliably.
    if period > 300 and not canned:
        built = ticks_to_bars(ticks, period, now=now)
        if len(built) >= 2:
            return built, "ticks"
        return [], "unknown"
    built = ticks_to_bars(ticks, period, now=now)
    if built:
        return built, "ticks"
    return [], "unknown"


def build_stack(
    ticks: Sequence[_Priced] | Iterable[_Priced],
    *,
    symbol: str = "ETH",
    now: float | None = None,
    ohlc: dict[str, Sequence[Bar]] | None = None,
) -> TimeframeStack:
    """Classify 1m / 5m / 1h / 4h / D / M. Prefer supplied OHLC, else ticks."""
    symbol = symbol.upper()
    seq = [t for t in ticks if t.symbol == symbol]
    frames: list[TfTrend] = []
    for key in TF_KEYS:
        bars, source = _bars_for(key, seq, ohlc, now)
        if not bars:
            frames.append(TfTrend(key=key, lean="unknown", change_pct=0.0, source="unknown", bars=0))
        else:
            frames.append(classify_bars(bars, key=key, source=source))
    score = 0.0
    known = 0
    for frame in frames:
        if frame.lean == "unknown":
            continue
        known += 1
        if frame.lean == "up":
            score += TF_WEIGHTS[frame.key]
        elif frame.lean == "down":
            score -= TF_WEIGHTS[frame.key]
    if known == 0:
        lean = "unknown"
    elif score >= 2.0:
        lean = "up"
    elif score <= -2.0:
        lean = "down"
    else:
        lean = "mixed"
    return TimeframeStack(symbol=symbol, frames=tuple(frames), lean=lean, score=round(score, 3))


def alignment(tap_side: str, tf_lean: str) -> str:
    """How a 5s nearby tap sits against the higher-TF stack."""
    if tap_side not in ("up", "down") or tf_lean in ("unknown", "mixed", "flat"):
        return "mixed"
    if tap_side == tf_lean:
        return "with-trend"
    return "fading"
