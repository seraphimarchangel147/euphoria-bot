"""Coil detection: is the tape winding up for a move?

Volatility clusters. Quiet does not stay quiet forever, and the transition is
not random -- it is preceded by measurable compression. Five independent tells,
each reported separately so the reading can be argued with rather than trusted:

* **Volatility compression.** Short-window sigma against a longer baseline. A
  tape moving at half its own recent rate is coiled, not calm.
* **Range contraction.** Recent price range against what the same span usually
  covers, scaled for time.
* **Dwell.** Price pinned in one row far longer than it normally holds. This is
  the operator's own observation -- rows that sit for many squares tend to go
  somewhere when they finally break.
* **Edge pressure.** Price leaning on a row boundary rather than resting mid
  row. Pressure against a level is different from indifference to it.
* **The house's own forecast.** Every quote frame carries the house's
  volatility estimate. When it marks that up while our realised tape is still
  quiet, a market maker with far more history is expecting expansion before we
  can see it.

Nothing here predicts direction. A coil says a move is coming, not which way --
direction is what the grid-walk break statistics are for.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Sequence

# Windows for the compression read, in seconds.
SHORT_WINDOW_S = 25.0
BASE_WINDOW_S = 300.0
MIN_BASE_PAIRS = 20

# Below this ratio of short-to-baseline volatility, the tape is compressed.
COMPRESSED_AT = 0.65
# Above this, it has already let go.
EXPANDING_AT = 1.60
HOUSE_KEEP = 240
HOUSE_MARKUP_AT = 1.20

STATES = ("expanding", "loaded", "coiling", "quiet", "unknown")


@dataclass
class Factor:
    name: str
    value: float
    weight: float
    score: float          # 0..1 contribution before weighting
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 4),
            "score": round(self.score, 4),
            "weight": self.weight,
            "detail": self.detail,
        }


@dataclass
class Coil:
    score: float                     # 0..1, how wound up
    state: str
    headline: str
    factors: list[Factor] = field(default_factory=list)
    sigma_short: float = 0.0
    sigma_base: float = 0.0
    ready: bool = False              # coiled AND showing a release tell
    confident: bool = False          # enough tape behind the reading

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "state": self.state,
            "headline": self.headline,
            "ready": self.ready,
            "confident": self.confident,
            "sigma_short": round(self.sigma_short, 6),
            "sigma_base": round(self.sigma_base, 6),
            "factors": [f.to_dict() for f in self.factors],
        }


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _range_over(ticks: Sequence[Any], now: float, window_s: float, symbol: str) -> tuple[float, int]:
    lo = hi = None
    n = 0
    for t in ticks:
        if getattr(t, "symbol", "").upper() != symbol or getattr(t, "price", 0) <= 0:
            continue
        if now - t.ts > window_s or t.ts > now:
            continue
        p = t.price
        lo = p if lo is None else min(lo, p)
        hi = p if hi is None else max(hi, p)
        n += 1
    if lo is None or hi is None:
        return 0.0, 0
    return hi - lo, n


class CoilTracker:
    """Tracks the house's published volatility so its changes can be read."""

    def __init__(self, keep: int = HOUSE_KEEP) -> None:
        self.house: deque[tuple[float, float]] = deque(maxlen=keep)

    def note_house_volatility(self, value: Any, *, now: float | None = None) -> None:
        try:
            vol = float(value)
        except (TypeError, ValueError):
            return
        if vol <= 0 or not math.isfinite(vol):
            return
        self.house.append((time.time() if now is None else now, vol))

    def house_ratio(self, *, now: float | None = None,
                    recent_s: float = 30.0, base_s: float = 300.0) -> tuple[float | None, int]:
        """House's current vol estimate against its own recent baseline."""
        if len(self.house) < 6:
            return None, len(self.house)
        now = time.time() if now is None else now
        recent = [v for ts, v in self.house if now - ts <= recent_s]
        base = [v for ts, v in self.house if now - ts <= base_s]
        if len(recent) < 2 or len(base) < 6:
            return None, len(self.house)
        mean_recent = sum(recent) / len(recent)
        mean_base = sum(base) / len(base)
        if mean_base <= 0:
            return None, len(self.house)
        return mean_recent / mean_base, len(self.house)

    # -- the read -----------------------------------------------------------
    def assess(
        self,
        ticks: Sequence[Any],
        *,
        now: float,
        diffusion: Any = None,
        dwell_columns: int = 0,
        typical_dwell: float | None = None,
        position_in_row: float | None = None,
        symbol: str = "ETH",
    ) -> Coil:
        from src.analytics.forecast import estimate_diffusion

        short = estimate_diffusion(ticks, now=now, window_s=SHORT_WINDOW_S, symbol=symbol)
        base = estimate_diffusion(ticks, now=now, window_s=BASE_WINDOW_S, symbol=symbol)
        factors: list[Factor] = []

        confident = base.ok and base.pairs >= MIN_BASE_PAIRS
        sigma_ratio = 1.0
        if confident and base.sigma > 0 and short.ok:
            sigma_ratio = short.sigma / base.sigma
            # 1.0 = normal, 0 = dead flat. Score rises as the tape compresses.
            compression = _clamp01((COMPRESSED_AT - sigma_ratio) / COMPRESSED_AT)
            factors.append(Factor(
                name="volatility compression", value=sigma_ratio, weight=0.35,
                score=compression,
                detail=f"moving at {sigma_ratio:.0%} of its {BASE_WINDOW_S / 60:.0f}min rate",
            ))

        # Range contraction, scaled so the comparison is fair across windows.
        r_short, n_short = _range_over(ticks, now, SHORT_WINDOW_S, symbol.upper())
        r_base, n_base = _range_over(ticks, now, BASE_WINDOW_S, symbol.upper())
        range_ratio = 1.0
        if n_short >= 4 and n_base >= 12 and r_base > 0:
            expected = r_base * math.sqrt(SHORT_WINDOW_S / BASE_WINDOW_S)
            if expected > 0:
                range_ratio = r_short / expected
                factors.append(Factor(
                    name="range contraction", value=range_ratio, weight=0.20,
                    score=_clamp01((1.0 - range_ratio) / 0.7),
                    detail=f"covering {range_ratio:.0%} of its usual span",
                ))

        # Dwell: pinned far longer than this row normally holds.
        if dwell_columns > 0 and typical_dwell and typical_dwell > 0:
            dwell_ratio = dwell_columns / typical_dwell
            factors.append(Factor(
                name="dwell", value=dwell_ratio, weight=0.25,
                score=_clamp01((dwell_ratio - 1.0) / 2.5),
                detail=f"held {dwell_columns} squares vs {typical_dwell:.1f} typical",
            ))

        # Leaning on a boundary rather than resting mid-row.
        if position_in_row is not None:
            press = _clamp01(abs(position_in_row - 0.5) * 2.0)
            edge = "floor" if position_in_row < 0.5 else "ceiling"
            factors.append(Factor(
                name="edge pressure", value=press, weight=0.10,
                score=press, detail=f"leaning on the {edge}",
            ))

        # The house's own forecast, moving before the tape does.
        ratio, samples = self.house_ratio(now=now)
        if ratio is not None:
            factors.append(Factor(
                name="house marking up", value=ratio, weight=0.10,
                score=_clamp01((ratio - 1.0) / 0.6),
                detail=(f"house vol {ratio:.0%} of its baseline"
                        + (" — it expects a move" if ratio >= HOUSE_MARKUP_AT else "")),
            ))

        total_w = sum(f.weight for f in factors)
        score = (sum(f.score * f.weight for f in factors) / total_w) if total_w > 0 else 0.0

        # Already released?
        expanding = confident and base.sigma > 0 and short.ok and sigma_ratio >= EXPANDING_AT
        if not confident:
            state = "unknown"
        elif expanding:
            state = "expanding"
        elif score >= 0.62:
            state = "loaded"
        elif score >= 0.35:
            state = "coiling"
        else:
            state = "quiet"

        house_up = ratio is not None and ratio >= HOUSE_MARKUP_AT
        ready = state == "loaded" and (house_up or score >= 0.75)

        if state == "expanding":
            headline = f"moving now — {sigma_ratio:.1f}x its baseline rate"
        elif state == "loaded":
            headline = "wound up — a move is overdue" + (" and the house agrees" if house_up else "")
        elif state == "coiling":
            headline = "tightening"
        elif state == "quiet":
            # Running hot but short of the expansion bar is not "no pressure".
            # Saying so would contradict the very factors printed beside it.
            headline = ("running above its baseline, not coiled" if sigma_ratio > 1.15
                        else "no pressure building")
        else:
            headline = "not enough tape to judge"

        return Coil(
            score=score, state=state, headline=headline, factors=factors,
            sigma_short=short.sigma if short.ok else 0.0,
            sigma_base=base.sigma if base.ok else 0.0,
            ready=ready, confident=confident,
        )
