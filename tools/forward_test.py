"""Out-of-sample test of the favourite-longshot finding.

The finding: measured across the whole quote-keyed ledger, the house runs
NEGATIVE margin on modest quotes and takes 21-37% on longshots.

    1.06-1.10x   n=1983   EV +0.0491   z=+12.1
    1.11-1.20x   n=3997   EV +0.0785   z=+17.3
    1.21-1.50x   n=8877   EV +0.1063   z=+19.5
    2.01-3.00x  n=21816   EV -0.2092   z=-26.9
    5.01-10.0x  n=30653   EV -0.3527   z=-30.9

Large n, large z, and a well-precedented mechanism -- bookmakers price
favourites thin and longshots fat because punters want longshots, and this
game's players are chasing 100x squares.

**But it is in-sample.** It was measured on the same data that suggested it.
Every wrong call in this project's history had that shape: a real pattern in
data selected after the fact. So this tool freezes the ledger now and re-scores
later using ONLY cells graded after the freeze. Same computation, data the
hypothesis has never seen.

    python tools/forward_test.py snapshot     # freeze the baseline
    python tools/forward_test.py report       # score the increment only

Two arithmetic rules this enforces, both learned the hard way here:

* **EV per key, then stake-weighted.** Multiplying an aggregate rate by an
  aggregate multiplier is aggregation bias and inflates edge. On the same data
  it moved the 20x+ band from -0.099 to -0.236.
* **The standard error is on the RETURN, not the hit rate.** A 10x quote hit
  10% of the time has the same mean as an even-money coin, and nothing like the
  same variance.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

LEDGER = Path(os.path.expanduser("~/.euphoria/cell_edge.json"))
BASELINE = Path(os.path.expanduser("~/.euphoria/cell_edge.baseline.json"))

BANDS = (
    (101, 105), (106, 110), (111, 120), (121, 150), (151, 200),
    (201, 300), (301, 500), (501, 1000), (1001, 2000), (2001, 10000),
)
MIN_N = 200          # per band, on the increment alone


def load(path: Path) -> dict[int, tuple[float, float]]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[int, tuple[float, float]] = {}
    for c in raw.get("cells", []):
        try:
            out[int(c["key"])] = (float(c["hits"]), float(c["n"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def snapshot() -> None:
    if not LEDGER.exists():
        print("no ledger yet at", LEDGER)
        return
    data = json.loads(LEDGER.read_text())
    data["_frozen_at"] = time.time()
    BASELINE.write_text(json.dumps(data))
    cur = load(LEDGER)
    print(f"frozen {len(cur)} quote keys, {int(sum(n for _, n in cur.values()))} graded cells")
    print(f"baseline -> {BASELINE}")
    print("\nEverything graded from now on is out-of-sample. Run `report` later.")


def report() -> None:
    base, cur = load(BASELINE), load(LEDGER)
    if not base:
        print("no baseline; run `snapshot` first")
        return
    # Increment only: what the hypothesis has never seen.
    inc: dict[int, tuple[float, float]] = {}
    for key, (h, n) in cur.items():
        bh, bn = base.get(key, (0.0, 0.0))
        dh, dn = h - bh, n - bn
        if dn > 0 and dh >= 0:
            inc[key] = (dh, dn)
    total = int(sum(n for _, n in inc.values()))
    age = time.time() - json.loads(BASELINE.read_text()).get("_frozen_at", time.time())
    print(f"OUT-OF-SAMPLE: {total} cells graded since the freeze "
          f"({age / 3600:.1f}h ago)\n")
    if total < 1000:
        print("Too thin to say anything. Let it run.")
        return

    print(f"{'band':>14} {'n':>7} {'EV':>9} {'SE':>7} {'z':>7}   in-sample was")
    prior = {(106, 110): +0.0491, (111, 120): +0.0785, (121, 150): +0.1063,
             (201, 300): -0.2092, (301, 500): -0.2842, (501, 1000): -0.3527,
             (1001, 2000): -0.3661, (2001, 10000): -0.2363}
    for lo, hi in BANDS:
        ks = [k for k in inc if lo <= k <= hi]
        n = sum(inc[k][1] for k in ks)
        if n < MIN_N:
            continue
        ev = sum(inc[k][1] * ((inc[k][0] / inc[k][1]) * (k / 100.0) - 1.0)
                 for k in ks) / n
        var = sum(inc[k][1] * ((inc[k][0] / inc[k][1]) * (k / 100.0) ** 2
                               - ((inc[k][0] / inc[k][1]) * (k / 100.0)) ** 2)
                  for k in ks) / n
        se = math.sqrt(max(var, 0.0) / n)
        was = prior.get((lo, hi))
        print(f"{lo / 100:6.2f}-{hi / 100:6.2f} {int(n):7d} {ev:+9.4f} {se:7.4f} "
              f"{ev / se if se > 0 else 0:+7.1f}   "
              + (f"{was:+.4f}" if was is not None else "—"))

    print("\nThe finding survives only if the 1.06-1.50 bands are still positive")
    print("and 2.01+ still negative. A sign flip anywhere means it was in-sample")
    print("noise and should be dropped, not re-explained.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"snapshot": snapshot, "report": report}.get(cmd, report)()
