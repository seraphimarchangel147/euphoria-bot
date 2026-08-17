"""What has actually been touched, by distance and horizon.

This is an interim safety rail, and it is worth being clear about why it exists
rather than dressing it up as the real fix.

The model's probabilities are ~3x optimistic through p = 0.10-0.45, and the
error grows with distance. Measured over a live book: cells one row away or
closer returned **+0.2937 per unit over 305 trades**; cells two rows away or
further returned **-1.0000 over 269 trades, with zero wins**. Not one. Same
shape by multiplier -- 25x and above was zero wins in 216 trades.

Zero wins in 269 attempts is not a probability being overestimated by a factor
of three. It is an impossible bet being taken repeatedly. On that tape a row was
$0.50 and price moved $0.80 in thirty minutes, so two rows is roughly 3.8 sigma
inside a five-second window. The existing ``MIN_TAP_PROB`` gate does not stop
these, because the model sincerely believes a 3.8-sigma move is more than 25%
likely -- the gate consults the very estimate that is wrong.

So this module does not consult the model at all. It only remembers what the
tape has actually done at each distance and horizon, and refuses to stake where
the evidence cannot support the bet.

The rule: take the **upper** confidence bound on the observed touch rate -- the
most generous reading the evidence permits -- and veto only if even that is not
enough to break even against the offered multiplier. Being wrong in the
optimistic direction and still losing is a solid reason not to bet.

Two properties follow, and both matter more than the veto itself:

* **It self-heals.** These are not hardcoded distances. If the market gets
  volatile and far cells start being touched, the bound rises and the veto
  lifts on its own. A constant would have to be found and changed by hand, and
  would be wrong at every other volatility.
* **It cannot manufacture edge.** The bound is only ever used to *refuse* a
  bet, never to justify one. Everything in this codebase that has cost money
  did so by making the numbers look better (see AGENTS.md), so a component that
  can only subtract is a component that cannot join that list.

Replace this with a corrected reachability estimate -- the OU first-passage
work is the principled version. Until then, this stops the bleeding.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# 95% one-sided. With zero hits this reproduces the "rule of three": the upper
# bound on a rate never observed in n trials is about 3/n.
Z = 1.96

# Below this we have no opinion and stand aside from vetoing. A handful of
# observations cannot rule anything out, and a guard that fires on thin
# evidence is just a hardcoded cap wearing a confidence interval.
MIN_OBSERVATIONS = 60

# Horizon buckets in seconds. Reachability is a different question at 5s than
# at 50s, and the live error is monotone in exactly this axis.
HORIZON_EDGES = (10.0, 25.0, 50.0)


def horizon_slot(horizon_s: float) -> int:
    for i, edge in enumerate(HORIZON_EDGES):
        if horizon_s < edge:
            return i
    return len(HORIZON_EDGES)


def wilson_upper(hits: float, n: float, z: float = Z) -> float:
    """Upper confidence bound on a binomial rate.

    Wilson rather than the normal approximation because the interesting case
    here is hits = 0, where the normal interval collapses to zero width and
    would wave through every far cell as a certainty in the wrong direction.
    """
    if n <= 0:
        return 1.0
    p = max(0.0, min(1.0, hits / n))
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(max(p * (1.0 - p) / n + z2 / (4.0 * n * n), 0.0)) / denom
    return max(0.0, min(1.0, centre + half))


@dataclass
class ReachabilityLedger:
    """Observed touch rates keyed on (distance in rows, horizon slot)."""

    counts: dict[tuple[int, int], list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0.0])
    )
    vetoes: int = 0
    checks: int = 0
    # Finer evidence, when it exists. This bucket pools every multiplier at a
    # given (distance, horizon), and the house does not: it quoted 1.04, 1.06,
    # 1.07, 1.09 and 1.13 inside one of these, because it rates them
    # differently. Measured live, d=0 slot 2 pools to 0.8746 and therefore
    # demands 1.14x -- while the 1.03x subset alone resolved 131/131. So this
    # rail was refusing the best-evidenced cells on the board, which is the
    # same coarseness error it was built to catch, pointed the other way.
    #
    # Read-only, and never required: when the quote key is cold the pooled
    # bucket still decides.
    quotes: Any = None
    quote_deferrals: int = 0

    def observe(self, distance: int, horizon_s: float, touched: bool) -> None:
        try:
            d = int(abs(distance))
            h = horizon_slot(float(horizon_s))
        except (TypeError, ValueError):
            return
        cell = self.counts[(d, h)]
        cell[0] += 1.0 if touched else 0.0
        cell[1] += 1.0

    def observe_many(self, rows: Any) -> int:
        n = 0
        for row in rows or ():
            try:
                distance, horizon_s, touched = row
            except (TypeError, ValueError):
                continue
            self.observe(distance, horizon_s, touched)
            n += 1
        return n

    def evidence(self, distance: int, horizon_s: float) -> tuple[float, float]:
        try:
            key = (int(abs(distance)), horizon_slot(float(horizon_s)))
        except (TypeError, ValueError):
            return (0.0, 0.0)
        hits, n = self.counts.get(key, (0.0, 0.0))
        return (hits, n)

    def ceiling(self, distance: int, horizon_s: float,
                multiplier: float | None = None) -> float | None:
        """Most generous touch rate the evidence permits. None = no opinion.

        Prefers the rate measured at this exact quote when one is warm, and
        falls back to the pooled bucket otherwise. Finer evidence about the
        same question beats coarser evidence, in both directions -- it can
        clear a cell the pool would refuse, and refuse one the pool would
        clear.
        """
        if multiplier and self.quotes is not None:
            try:
                hits, n, _rate = self.quotes.evidence(multiplier)
            except Exception:
                hits, n = 0, 0
            if n >= getattr(self.quotes, "min_observations", 200):
                self.quote_deferrals += 1
                return wilson_upper(hits, n)
        hits, n = self.evidence(distance, horizon_s)
        if n < MIN_OBSERVATIONS:
            return None
        return wilson_upper(hits, n)

    def veto(self, distance: int, horizon_s: float, multiplier: float | None) -> bool:
        """True when even the optimistic bound cannot break even on this quote.

        Deliberately one-directional: this answers "is there any way this bet
        makes sense", never "is this bet good". A False here is not an
        endorsement, it is only the absence of a refusal.
        """
        self.checks += 1
        if not multiplier or multiplier <= 1.0:
            return False
        ceil = self.ceiling(distance, horizon_s, multiplier)
        if ceil is None:
            return False
        if ceil * multiplier - 1.0 < 0.0:
            self.vetoes += 1
            return True
        return False

    def stats(self) -> dict[str, Any]:
        rows = []
        for (d, h), (hits, n) in sorted(self.counts.items()):
            if n < MIN_OBSERVATIONS:
                continue
            ceil = wilson_upper(hits, n)
            rows.append({
                "distance": d,
                "horizon_slot": h,
                "n": int(n),
                "touched": int(hits),
                "rate": round(hits / n, 5) if n else None,
                "ceiling": round(ceil, 5),
                # The multiplier at which this bucket could still be worth a
                # bet. Reads as "nothing under 71x here" on a dead far cell.
                "needs_multiplier": round(1.0 / ceil, 1) if ceil > 0 else None,
            })
        return {
            "buckets": rows,
            "tracked": len(self.counts),
            "checks": self.checks,
            "vetoes": self.vetoes,
            "min_observations": MIN_OBSERVATIONS,
            "quote_keyed": self.quotes is not None,
            "quote_deferrals": self.quote_deferrals,
        }

    # -- persistence --------------------------------------------------------
    # This has to survive a restart, and the reason is specific. The house
    # anchor is biased HIGH (see implied.py), so it makes far cells look more
    # reachable than they are, and this ledger is one of only two things
    # standing in front of that. It also refuses to have an opinion under
    # MIN_OBSERVATIONS -- correctly. Put those together on a cold start and
    # there is a window, every restart, where the model is at its most eager
    # and its guard is silent. Measured: a restarted room went to ruin in 20
    # minutes over 583 trades at a 6.7% hit rate. Evidence about how far the
    # tape reaches does not expire when the process does.
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "counts": [[d, h, hits, n] for (d, h), (hits, n) in self.counts.items()],
        }

    @classmethod
    def from_json(cls, data: Any) -> "ReachabilityLedger":
        led = cls()
        rows = (data or {}).get("counts") if isinstance(data, dict) else None
        for row in rows or ():
            try:
                d, h, hits, n = row
                d, h = int(d), int(h)
                hits, n = float(hits), float(n)
            except (TypeError, ValueError):
                continue
            if n <= 0 or hits < 0 or hits > n:
                continue
            led.counts[(d, h)] = [hits, n]
        return led
