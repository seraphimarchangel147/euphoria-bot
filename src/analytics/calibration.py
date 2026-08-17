"""Learned calibration: turn a modelled probability into an evidence-weighted one.

Why this can learn fast
-----------------------
Every closed 5s column labels *every* cell we scored, not just the one we would
have tapped -- the tape either entered that band or it did not. That is ~50
labelled outcomes per five seconds instead of one, which is the difference
between a model that learns in an afternoon and one that never leaves the
noise floor. Nothing has to be staked to collect them.

How it learns
-------------
Predictions are bucketed by (probability band, horizon band, volatility
regime). Each bucket holds a Beta posterior seeded from the model's own
probability, so a cold bucket returns the model unchanged and a warm one
overrides it with what actually happened.

Decisions use the *lower confidence bound*, never the posterior mean. An
unproven bucket therefore cannot talk the policy into a bet: thin evidence
widens the interval, the LCB drops, and the edge disappears. That asymmetry is
deliberate -- it is what keeps a learning system from paying tuition it cannot
afford.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Probability bands. Fine near the bottom because that is where nearly every
# real cell lives, and where a small absolute error flips an EV sign.
# Probability bands. The bottom of this range needs far finer resolution than
# intuition suggests: on a 900-cell grid most cells sit near zero, and a single
# "below 1%" bucket averages a cell at one-in-a-million together with one at
# one-in-a-hundred. The observed rate is then dominated by the easy members and
# handed back to the impossible ones -- which, against a 100x quote, reads as a
# 475% edge. That exact failure was observed live.
P_EDGES = (0.0002, 0.001, 0.004, 0.01, 0.02, 0.04, 0.07, 0.12, 0.20,
           0.32, 0.50, 0.70, 0.88, 1.01)
# A calibrator may correct a probability. It may not conjure one. However warm
# a bucket is, it cannot lift a cell more than this above what the model said.
MAX_LIFT_FACTOR = 4.0
MAX_LIFT_ABSOLUTE = 0.01
# Horizon bands in seconds (column close).
H_EDGES = (7.5, 12.5, 17.5, 27.5, 42.5, 1e9)
VOL_BUCKETS = ("LOW", "MED", "HIGH")

PRIOR_WEIGHT = 8.0      # pseudo-observations backing the model's own estimate
LCB_Z = 1.6449          # one-sided 95%
MAX_BUCKET_WEIGHT = 4000.0   # decay cap, so old regimes cannot fossilise a bucket


def p_bucket(p: float) -> int:
    for i, edge in enumerate(P_EDGES):
        if p < edge:
            return i
    return len(P_EDGES) - 1


def horizon_bucket(horizon_s: float) -> int:
    for i, edge in enumerate(H_EDGES):
        if horizon_s < edge:
            return i
    return len(H_EDGES) - 1


def bucket_key(p: float, horizon_s: float, vol: str) -> str:
    vol = vol if vol in VOL_BUCKETS else "MED"
    return f"p{p_bucket(p)}|h{horizon_bucket(horizon_s)}|{vol}"


@dataclass
class Calibrated:
    """What the calibrator returns for one prediction."""

    p_model: float
    p_cal: float
    p_lcb: float
    n: int
    key: str
    trusted: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "p_model": round(self.p_model, 6),
            "p_cal": round(self.p_cal, 6),
            "p_lcb": round(self.p_lcb, 6),
            "n": self.n,
            "key": self.key,
            "trusted": self.trusted,
        }


class Calibrator:
    """Beta-posterior calibration buckets with a conservative lower bound."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        prior_weight: float = PRIOR_WEIGHT,
        trust_n: int = 30,
    ) -> None:
        self.path = Path(path) if path else None
        self.prior_weight = float(prior_weight)
        self.trust_n = int(trust_n)
        # key -> [hits, total]  (observed only; the prior is applied at read time)
        self.buckets: dict[str, list[float]] = {}
        self.brier_sum = 0.0
        self.brier_n = 0
        self.observed = 0
        self.updated_at = 0.0
        if self.path:
            self.load()

    # -- read ---------------------------------------------------------------
    def adjust(self, p_model: float, horizon_s: float, vol: str) -> Calibrated:
        p_model = min(1.0, max(0.0, float(p_model)))
        key = bucket_key(p_model, horizon_s, vol)
        hits, total = self.buckets.get(key, (0.0, 0.0))
        w = self.prior_weight
        alpha = w * p_model + hits
        beta = w * (1.0 - p_model) + (total - hits)
        denom = alpha + beta
        if denom <= 0:
            return Calibrated(p_model, p_model, 0.0, 0, key, False)
        mean = alpha / denom
        var = (alpha * beta) / (denom * denom * (denom + 1.0))
        lcb = max(0.0, min(1.0, mean - LCB_Z * math.sqrt(max(var, 0.0))))
        # Bound how far the bucket may move this cell. A bucket describes a
        # band; a cell at the very bottom of that band must not inherit the
        # average of everything above it, because on a fat multiplier that
        # difference is the entire apparent edge.
        ceiling = p_model * MAX_LIFT_FACTOR + MAX_LIFT_ABSOLUTE
        mean = min(mean, ceiling)
        lcb = min(lcb, ceiling)
        n = int(total)
        return Calibrated(
            p_model=p_model,
            p_cal=mean,
            p_lcb=lcb,
            n=n,
            key=key,
            trusted=n >= self.trust_n,
        )

    # -- write --------------------------------------------------------------
    def observe(self, p_model: float, horizon_s: float, vol: str, touched: bool) -> None:
        p_model = min(1.0, max(0.0, float(p_model)))
        key = bucket_key(p_model, horizon_s, vol)
        row = self.buckets.setdefault(key, [0.0, 0.0])
        row[0] += 1.0 if touched else 0.0
        row[1] += 1.0
        if row[1] > MAX_BUCKET_WEIGHT:          # exponential forgetting of old regimes
            row[0] *= 0.5
            row[1] *= 0.5
        y = 1.0 if touched else 0.0
        self.brier_sum += (p_model - y) ** 2
        self.brier_n += 1
        self.observed += 1
        self.updated_at = time.time()

    def observe_many(self, rows: Iterable[tuple[float, float, str, bool]]) -> int:
        n = 0
        for p, horizon, vol, touched in rows:
            self.observe(p, horizon, vol, touched)
            n += 1
        return n

    # -- reporting ----------------------------------------------------------
    @property
    def brier(self) -> float | None:
        return round(self.brier_sum / self.brier_n, 6) if self.brier_n else None

    def reliability(self, *, min_n: int = 10) -> list[dict[str, Any]]:
        """Predicted-vs-actual table, the honest read on whether it is learning.

        Ordered by probability band numerically, not by string. Sorting the raw
        keys put "p10" between "p1" and "p2", so a truncated view showed only
        p0/p1/p10 and hid every mid-range bucket -- which is exactly where the
        decisions are made.
        """
        def _order(item: tuple[str, list[float]]) -> tuple:
            key = item[0]
            parts = key.split("|")
            try:
                return (int(parts[0][1:]), int(parts[1][1:]), parts[2])
            except (IndexError, ValueError):
                return (99, 99, key)

        out: list[dict[str, Any]] = []
        for key, (hits, total) in sorted(self.buckets.items(), key=_order):
            if total < min_n:
                continue
            idx = int(key.split("|")[0][1:])
            lo = 0.0 if idx == 0 else P_EDGES[idx - 1]
            hi = P_EDGES[min(idx, len(P_EDGES) - 1)]
            out.append({
                "key": key,
                "predicted_lo": round(lo, 4),
                "predicted_hi": round(hi, 4),
                "predicted_mid": round((lo + hi) / 2.0, 4),
                "actual": round(hits / total, 4),
                "n": int(total),
            })
        return out

    def stats(self) -> dict[str, Any]:
        table = self.reliability()
        total_n = int(sum(t for _h, t in self.buckets.values()))
        return {
            "observations": self.observed,
            "labelled_total": total_n,
            "buckets": len(self.buckets),
            "warm_buckets": sum(1 for _h, t in self.buckets.values() if t >= self.trust_n),
            "brier": self.brier,
            "reliability": table,
            "updated_at": self.updated_at,
        }

    # -- persistence --------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "prior_weight": self.prior_weight,
            "trust_n": self.trust_n,
            "brier_sum": self.brier_sum,
            "brier_n": self.brier_n,
            "observed": self.observed,
            "updated_at": self.updated_at,
            "buckets": {k: [v[0], v[1]] for k, v in self.buckets.items()},
        }

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        except OSError:
            pass    # the learned table is a cache, never fatal

    def load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        raw = data.get("buckets")
        if isinstance(raw, dict):
            for key, val in raw.items():
                if isinstance(val, (list, tuple)) and len(val) == 2:
                    try:
                        self.buckets[str(key)] = [float(val[0]), float(val[1])]
                    except (TypeError, ValueError):
                        continue
        self.brier_sum = float(data.get("brier_sum") or 0.0)
        self.brier_n = int(data.get("brier_n") or 0)
        self.observed = int(data.get("observed") or 0)
        self.updated_at = float(data.get("updated_at") or 0.0)
