# Working agreement for agents on euphoria-bot

Read this before touching anything. It exists so you do not have to rediscover
what has already been learned here — most of it the expensive way.

Log your work in [CHANGELOG.md](CHANGELOG.md) when you finish. Date, who, what,
and the measured effect.

---

## 1. What this is

Euphoria Finance is a tap-trading grid game on MegaETH. The board is a grid of
price/time squares. You bet that price will **touch** a given square during its
~5-second window, at a multiplier the house quotes for that square. There is no
position, no exit, no stop. You are right or you are not.

The economics are one line:

```
EV = P(touch) x multiplier - 1        breakeven at P = 1/multiplier
```

Everything in this repo exists to estimate `P(touch)` better than the house
prices it. That is the whole game. A change that does not move that estimate,
or does not move what we do with it, is not an improvement.

**This bot advises. It does not place trades.** It runs in `manual` +
`dry_run`. Shadow trading is simulated against live tape. Do not add live order
placement, and do not remove the dry-run guard, without the owner saying so
explicitly in writing.

---

## 2. The one rule that matters

**Every bug that has cost real money in this project made the numbers look
BETTER, not worse.**

Not one of them crashed. Not one of them threw a traceback. They each quietly
manufactured profit that did not exist:

| What broke | How it looked |
|---|---|
| Duplicate settlements | one square paid ten times, +258 |
| Whole-column mis-timing | cells graded against the wrong window |
| Cold calibration buckets | +641% EV from three observations |
| A `p0` bucket conjuring 7% | +475% EV on a fat multiplier |
| Dead feed read as certainty | no ticks became "nothing will move" |
| Hurst extrapolated past its fit | long horizons off by 100x |

So: **be suspicious of good news.** If a change makes the numbers jump, your
first job is to prove it is not a bug. A result that looks too good has, in this
codebase, always been a bug. Every one of the above now has a test pinned to the
exact live case that produced it — do not delete those tests to make something
pass.

Corollary: when you fix one, write the test against the *real* numbers that
exposed it, and put the measurement in the docstring. The tests here read like a
casebook on purpose.

---

## 3. Layout

```
src/analytics/      the model. pure, no I/O, no network.
  forecast.py         diffusion fit, barrier/band touch probability,
                      displacement scaling, DiffusionSmoother
  calibration.py      Beta-posterior buckets, lower confidence bound, lift caps
  policy.py           CellScore, Bankroll, verdicts, Kelly sizing
  signal.py           ties it together -> Signal; the reachability gate
  gridboard.py        dense per-window grading (~50 free labels / 5s)
  regime.py           volatility regime from the HOUSE's number, not ours
  traversal.py        dwell / hazard / direction / hour-of-day
  pressure.py         CoilTracker - is a big move building
  pnl.py, tilt.py     attribution; behaviour (drawdown, pace)
  player.py           the human's own taps, for comparison

src/control/        the control room
  room.py             owns state, wiring, the think() loop
  server.py           HTTP endpoints (127.0.0.1:8765)
  static/index.html   the dashboard

extension/          Chrome MV3, MAIN world at document_start
  inject.js           WebSocket + fetch capture, quote grid parse, overlay
  content.js          the card
```

**Analytics stays pure.** No network calls, no file reads, no clock reads that
are not passed in as `now`. That is what makes it testable, and the tests are
the only thing standing between this project and the failure mode in section 2.

---

## 4. How to know if you improved anything

You do not get to assert an improvement. You measure it.

The measurement is **shadow trading per unit staked**, over a window long enough
to mean something, against the baseline recorded in the changelog.

Reference points:

- **−0.18 per unit** — picking cells at random. This is the bar. A ranking that
  scores worse than this is not merely unprofitable, it is anti-predictive.
- **0.0** — breakeven.

Recent history, so you know what movement looks like:

| state | trades | hit rate | per unit |
|---|---|---|---|
| sigma from our own vol regime | 431 | 12.5% | −0.707 |
| regime taken from the house | 452 | 25.0% | −0.323 |
| (extended sample) | 773 | 33.5% | −0.320 |
| sigma carried across passes | 453 | 48.3% | −0.271 |

How to run one:

```bash
python -m src.ui
```

Then `POST /start`, and sample `/shadow` and `/status` over time. There is a
sampler pattern in the scratchpad; write your own if you need different fields.

**Rules for measurement:**

1. **One change at a time.** Two changes in one window teaches you nothing.
2. **Do not change the model mid-window.** It invalidates the comparison.
3. **Short windows lie.** This book has swung from −0.65 to −0.27 inside a
   single run. A few hundred trades over a few minutes is noise. Say so when
   you report it rather than claiming the win.
4. **Report the baseline you beat**, not just your number.

---

## 5. What is currently known to be wrong

Live, measured, not speculation. Pick these up rather than inventing work.

**The ranking is fine. The calibration is not.** Over 111,381 labelled cells,
Spearman(predicted, actual) = **+0.996**. The model orders cells almost
perfectly. But:

- it is calibrated at both extremes and **~3x optimistic through p = 0.10–0.45**
- that band is exactly where a tappable bet lives (a 3–6x multiplier breaks even
  at p = 0.17–0.33), so every cell that looks like a good tap is overrated
  threefold
- the error is **ordered by horizon**: at predicted 0.41, the shortest horizon
  bucket realises 0.13x of prediction, the longest 0.81x

**The mechanism is max-of-N selection bias.** We take the argmax over ~180
quoted cells. For per-cell error `s`, the selected cell is inflated by `s·μ_N`
where `μ_N = √(2 log N) − (log log N + log 4π) / (2√(2 log N))`. At N=200 and
s=0.08–0.12 that is **+0.21 to +0.31 absolute** — enough to turn a true 0.35
into a displayed 0.60. Measured table says predicted 0.60 → actual 0.365. Two
independent routes, same number.

This is why per-bucket calibration does not rescue it: the calibrator is right
*on average*, but the cell we pick is an extreme order statistic, not an average
draw.

**Important caveat before you "fix" this with the closed form:** our ~180 cells
all ride **one price path**. Their errors are tape-shared, effective N is far
below 180, and μ_N therefore *overstates* the bias. Measure it empirically —
block bootstrap over windows, or calibrate on the selected cells specifically,
which captures the winner's curse without modelling it.

**The displacement-scaling exponent is a fudge.** We use `spread = sigma *
T^hurst` with hurst clamped to [0.05, 0.50]. Live it pins to the 0.05 floor:
the estimator wants to go *lower than the model can express*. The house's own
quotes imply about 0.09. Real tape on a 5s grid is far more mean-reverting than
Brownian, and `T^hurst` is a hack, not a process. An Ornstein–Uhlenbeck
first-passage treatment is the principled replacement.

**Smaller, open:**

- Real taps still do not resolve to cells — the order payload has never been
  observed on the wire. Capture is armed and waiting on a trade.
- `avg_multiplier` in `/pnl` reports 33.4; back-solving from hit rate and
  expectancy gives 2.70. It is a mean over a heavy tail and is misleading.
- Regime labelling can saturate on `HIGH` when house volatility trends, because
  the terciles are taken over the whole retained history.
- The token at `USDM_ADDRESS` reports `symbol() = "EV"` and `decimals() = 24`.
  Unexplained.
- One pre-existing test failure on Windows: `chmod 0600` cannot be asserted.
  Not yours, do not "fix" it by weakening the assertion.

---

## 6. Working alongside other agents

Several agents work this repo at once. The rules are about not destroying each
other's measurements, which is easier to do than you would think.

**Claim your files.** Put your name and scope in the changelog entry *before*
you start, not after. If your change touches a file someone else has claimed,
talk to them first.

Rough ownership boundaries that tend not to collide:

| area | files |
|---|---|
| probability model | `forecast.py`, `signal.py` |
| calibration & selection | `calibration.py`, `policy.py` |
| grading & labels | `gridboard.py`, `traversal.py` |
| accounting | `pnl.py`, `tilt.py`, `player.py` |
| capture / extension | `extension/*` |
| dashboard | `src/control/static/index.html` |

`room.py` is the junction and everyone ends up in it. Keep edits there small and
mechanical — wiring only, no logic.

**Do not restart the control room without saying so.** Someone is probably
mid-measurement. Restarting resets the shadow book and destroys their window.

**Never edit and measure at the same time on the same axis.**

**Hand back what you measured, not what you hoped.** If it got worse, say it got
worse and by how much. A negative result on a well-run measurement is worth more
here than a confident guess, because it removes a branch permanently. Nobody is
graded on the direction of the number.

---

## 7. Testing

```bash
python -m pytest -q
```

Currently 417 tests, one known Windows failure (section 5).

Conventions worth matching:

- Tests are named as sentences about behaviour, not about functions.
  `test_a_failed_fit_carries_rather_than_reading_as_stillness`, not
  `test_update_2`.
- The docstring says **what went wrong in the real world** and gives the real
  numbers. That context is why the test exists and is the first thing a future
  reader needs.
- State is isolated structurally in `conftest.py` — it discovers `*_STORE`
  settings by reflection so a new store cannot silently start writing to the
  user's real `~/.euphoria` during a test run. That happened. Do not replace it
  with a hardcoded list.
- No network in tests. Ever.

---

## 8. Reporting back

When you finish a piece of work, say:

1. what you changed, and where
2. **the measurement** — baseline, result, sample size, and whether the sample
   is big enough to mean anything
3. what you found that you were not looking for
4. what is still broken or unverified

Then add the changelog entry.

Plain prose. No status-report padding. If something is uncertain, mark it
uncertain — a confident wrong number costs more here than an honest gap, since
the next agent will build on whatever you hand them.
