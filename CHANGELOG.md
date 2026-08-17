# Changelog

Who did what, when, and what it measured. Newest first.

Every entry says what it **measured**, not what it hoped. If a change made
things worse, that is recorded too — a negative result on a well-run measurement
removes a branch permanently and is worth more than a confident guess. See
[AGENTS.md](AGENTS.md) for the working agreement.

**Format:**

```
## YYYY-MM-DD — who — short title
Scope: files touched
What: what changed and why
Measured: baseline -> result, sample size, honest read on significance
Open: what is still unverified
```

---

## 2026-08-17 — Dominion — deployment-ready isolated candidate

**Scope:** complete stable Windows source snapshot imported onto `origin/main` in an isolated Linux worktree; reviewed PR #3 relay/server/tests; bridge route authentication; extension build marker; provenance manifest. The Windows live checkout, Chrome profile, `:8765`, and `:18901` were not modified or restarted.

**What:** preserved the concurrent dirty tree as a 191-file SHA-256 manifest, normalized text to LF, integrated loop-independent grid relay, pinned bridge state/result/command/player-model traffic to the extension's existing 256-bit token, retained public localhost-only redacted status, and bumped the unpacked extension to `0.4.2-deployment-ready`. Default remains manual, stopped, and `dry_run=true`; the control room still has no production submit hook.

**Measured:** 568/568 Python tests passed on Linux; 1/1 Node relay test passed; 8/8 extension JavaScript files passed syntax checks; Python compile, manifest JSON, secret-pattern scan, and `git diff --check` passed. Isolated cold start bound only `127.0.0.1:18765` and `:18999`; unauthenticated state returned 401, authenticated state returned 200, status retained authoritative NATS grid cells at 5/10/15 seconds while control was stopped, and bridge token/state/history files were mode 0600. No fee-adjusted profitable edge was established.

**Open:** native Windows Chrome must load the exact committed extension and return manifest `0.4.2` plus content marker `0.4.2-deployment-ready`; live activation remains separately approval-gated. The Windows checkout's POSIX `0600` assertion remains inapplicable on NTFS, so secret-bearing runtime state must stay in WSL/ext4.

---

## 2026-08-16 — selection desk — MEASURED: cell_edge wired to on_quote_label, tape empty until next start

**Scope:** copied `src/analytics/cell_edge.py` + `tests/test_cell_edge.py` from PR #10 (not rewritten). Wired `src/control/room.py` + `config/settings.py` (`CELL_EDGE_STORE` → `~/.euphoria/cell_edge.json`). Did not edit `gridboard.py`, `forecast.py`, `signal.py`. No third cloud agent. 8765 not restarted.

**What:** Adapter required. Hook rows are `(m, d, h, touched)`; `observe_many` tuples are `(m, touched, d, h)`. `_ingest_quote_labels` remaps. GridBoard already swallows a throwing consumer — verified, not weakened. `beats=True` cannot justify a bet.

**Measured:** tests/test_cell_edge.py 12 passed, all synthetic or wire. No live per-quote result. Historical 15k (d,h) rows cannot be rekeyed. Tape starts empty. Hours, not minutes. The running 8765 was started before this wire and will not accumulate until the next start. Clock starts then. Do not quote the open confirmation window.

**Open:** not a finding. Question still unanswered: does any cents-key beat its own quote on a large untruncated sample? Do not report a thin result.

## 2026-08-16 — horizon desk — PREDICTED: key 113 rate 0.86-0.88 (point 0.87), at or under BE 0.885

**Scope:** local CHANGELOG only. Prediction written before any quote-keyed tape. No code. Did not read live cell_edge counts. Did not edit `forecast.py`, `signal.py`, `calibration.py`, `horizon_error.py`. Not wired. 8765 not restarted. Confirmation book not quoted.

**What:** Claude unblocked the quote key. PendingCell.multiplier is on main. cell_edge accumulates from empty; the 15k (d,h) tape cannot be rekeyed. Two readings both fit the old pool ~0.897: (A) calibrated house, ~78% weight on 1.13, subset at BE 0.885; (B) flat ~3pp vig, same pool, zero edge at 1.13. This entry picks one before the first key-113 label lands.

**Predicted (not measured, tape empty today):**
- Pick: third, closer to B's conclusion than A's mechanism. I do not expect (A). Booking is two first-seen edges (~27.5s and ~42.5s), not 78% one quote, and 1.13 is not a stable property of (d=0,h=2). I do not expect (B)'s exact 3pp either: that residual was pool vs an unmeasured equal-weight five-quote mix.
- Mechanism I am betting: the house puts 1.13 on the harder ATM cells (longer first-seen T and/or price near the $0.50 band edge). Key 113 therefore comes in below the 0.897 pool, at or under BE.
- Point: 0.870. Range: 0.860-0.880. n>=200 at key 113 before this counts.
- Kill: key 113 hit rate >= 0.885 at n>=200. That single number makes the +1.63% cell real and this pick dead. A second kill on (A) specifically: if key 113 is the dominant (d=0,h=2) quote (weight >= 0.70), A's mix story was right and I was wrong on mechanism even if the rate lands at 0.885.
- Will not: treat n<200 as a verdict; move the 0.860-0.880 window after a thin print; rebase BE off 0.885; switch the claim to a fatter key (1.26, 1.40, 1.82) when 113 disappoints; quote the confirmation book before it closes.

**Measured:** none. Tape empty. No invented live per-quote rate.

**Open:** wait for cell_edge n>=200 at key 113. Hours, not minutes.

---

## 2026-08-16 — horizon desk — MEASURED: (d=0,h=2) vs 1.13 is a pooling comparison; cannot de-mix

**Scope:** local CHANGELOG only. Read `reachability.py`, `gridboard.py` settle, `test_quote_grid.py`, live reachability.json and `/think`. Did not edit `forecast.py`, `signal.py`, `calibration.py`, `horizon_error.py`. Not wired. 8765 not restarted.

**What:** Adversarial check of Claude pooling-artifact claim on the +1.63% EV cell (d=0, reachability slot 2, compared to 1.13x). PendingCell stores no multiplier. First-seen horizon_s is the key. Grade is 5s column overlap. Reachability observe is (distance, horizon_s, touched) only.

**Measured:**
- Live ledger (d=0): h0 6283/6386 = 0.98387; h1 13690/14393 = 0.95116; h2 13878/15464 = 0.89744; h3 1468/2060 = 0.71262. Monotone in first-seen T. Wilson ceiling on h2 is 0.902, needs_multiplier 1.1.
- Booking structure: calibration H_EDGES = (7.5, 12.5, 17.5, 27.5, 42.5, 1e9). One label per (cell, calibration band) at first sight. Reachability slot 2 (25-50s) therefore pools first-seen ~27.5s and ~42.5s, not a single quote. Condition (i) (nearly all labels were 1.13) is structurally false.
- Mix math, no per-quote rates (we do not have them). House-implied if calibrated: 1/1.04=0.9615, 1/1.06=0.9434, 1/1.07=0.9346, 1/1.09=0.9174, 1/1.13=0.8850. Equal-weight mean 0.9284. Observed pool 0.897 is 3.1pp below that mean. If house is calibrated, matching 0.897 with those five quotes needs ~78% weight on 1.13; then the 1.13 subset is at BE 0.885 and the pool-vs-1.13 +1.4% is the leftover easier cells. Uniform ~3pp vig also matches the pool (0.928-0.03=0.898) with zero 1.13 edge. Neither is measured.
- One surface snapshot (not the historical mix): ATM multipliers rose with forward, 1.07 at ~9s through 1.26-1.44 inside slot 2. A later pass had next-column ATM at 1.82 with price 0.03 from the top of [1884.5, 1885.0]. 1.13 is not a stable property of (d=0,h=2).
- Recent taken ATM taps (n=20, selected, not the label tape): 1.13 appeared once, in slot 0 (forward 1, 6.4s, win). The two slot-2 taps were 1.54 and 1.74. Do not read these as rates.
- d=0 h=1 vs 1.05: pool 0.95116 is BE at 1.05 (0.9524). Not a second +EV cell. Same pooling applies if someone compares 0.951 to the fatter quotes that show up in that slot.

**Open:** cannot tell until a quote key or first-seen-T split exists on the labels (selection desk claimed cell_edge.py for that). Did not prove Claude wrong. Comparing the pool to 1.13 overstates the 1.13 cell unless the 1.13 subset is measured above 0.885.

---

---

## 2026-08-16 — selection desk — MEASURED: could not break Claude's pooling claim

**Scope:** read-only mix attack. `reachability.json`, `gridboard.py`, `reachability.py`, `extension/inject.js`, `tests/test_quote_grid.py`, `player.json`, `pnl.json`. Did not edit `cell_edge.py` (in flight on bc-f6017502). Did not edit `gridboard.py` / `reachability.py` / `forecast.py`. 8765 not touched.

**What:** Tried to prove the d=0 h=2 +EV cell is a real 1.13 edge, not a pooling artifact. Failed.

**Measured:**
- Ledger key is (d,h) only. PendingCell has no multiplier. settle feeds (distance, horizon_s, touched).
- Slot 2 (25–50s) covers ~5 ATM columns. Wire: 18×50 uint16 cents, ATM p=24, each t independent. Tests already quote ATM at 1.03 / 1.15 / 1.20.
- Live ledger later parse: d=0 h=2 = 13873/15448 = 0.8980 (file grew while read; money-man 13827/15374 = 0.8994 is an earlier snapshot). Same rate vs 1.04/1.06/1.07/1.09/1.13: −0.066 / −0.048 / −0.039 / −0.021 / **+0.015**. 1.13 is the first of those five that is not a veto.
- player.json at-price slot 2: 136 taps, 1.13 on **6/136**. Claude's set all present. Mode 1.19. Caveat: tapped cells, not the full strip. No full-grid dump on disk.

No per-quote historical tape. cell_edge cannot be run on the ~15k-row (d,h) bucket. No invented live per-quote rate.

**Open:** default to pooling artifact. Writer sketch (PendingCell.multiplier + settle row) stays in the module docstring when that sibling lands. Do not wire. Do not restart 8765.

---

## 2026-08-16 — selection desk — CLAIM: cell_edge.py + test_cell_edge.py

**Scope:** `src/analytics/cell_edge.py`, `tests/test_cell_edge.py` only. New sibling. Will not touch `forecast.py`, `signal.py`, `calibration.py`, `policy.py`, `reachability.py`, `selection.py`, `implied.py`, `gridboard.py`, `room.py`, or 8765.

**What:** Claiming before an adversarial quote-key edge test. Claude says the one +EV cell (d=0, h=2, 1.13x) is a pooling artifact. Default to that only if I cannot break it. Key = round(m*100) cents. Subtract-only: `beats_quote` is a measurement, not a tap. MIN_OBSERVATIONS=200. Cannot run on the 15,374-row tape (PendingCell does not store multiplier).

**Measured:** in progress. No invented live result. Hands off 8765.

**Open:** not wired. Writer sketch in the module docstring only.

## 2026-08-16 — horizon desk — MEASURED: 19x too-low sigma does not explain the short-T hole

**Scope:** local CHANGELOG only. Read `implied.py`. Did not edit `horizon_error.py`, `implied.py`, `forecast.py`, or `calibration.py`. Not wired. 8765 not restarted.

**What:** Premise change. Fitted σ 0.0105 vs house-implied ~0.199 collapsed the surface to p=1 at d=0 and p=0 at d=1. The 0.148× (h0) → 0.694× (h5) monotone over 9,818 p10 cells STANDS as a fact. Mechanism hunt restarts from the anchor. Question: does a 19x σ error produce that split at fixed predicted p=0.41?

**Measured:** no tagged post-anchor p10 slice exists. Live calibrator is mixed: labelled_total now 173,949 (was ~137,500). Current p10 still reads 0.148× / 0.720× (n=1,306 / 3,572). That is the old table plus new rows, not a post-anchor sample. Do not pretend otherwise. Do not invent 0.13/0.81.

Constant scale at fixed model p is T-flat (same 2Φ(-k · σ_m/σ_t) at every T). 19x too LOW predicts actual/pred = **2.35× at every horizon** (tiny barriers, true p≈0.97). Observed is 0.15× → 0.69×, both below 1. Wrong sign, no monotone. **Killed.**

The high-biased *anchor* is the other direction. A ~1.3× high σ is also T-flat and lands **0.69× at every T** — it can explain the long-end *level*, not the short-T hole. Same kill as uniform overstated σ.

H-floor stays dead (hole printed at live hurst 0.31–0.33).

Live smoother at read: σ=0.192 (raw tick 0.018, last_anchor 0.074, anchored=1628). Surface has a gradient again. Untagged increment since the last p10 read is not a post-anchor slice (h5 rows settling now were scored earlier).

No book quoted. All prior per-unit changelog numbers are unreliable on this premise, including 1365 / −0.2775 and −0.2712.

**Open:** need a tagged post-anchor p10 slice before anyone claims the hole vanished or survived. Composition (p10 = cliff cells under the bimodal surface) is a pre-anchor story only and is untested. Wilson rail + calibrator stay required while the high-biased anchor is on.

---

## 2026-08-16 — ou desk — MEASURED: re-run on house-anchored σ=0.199 (keep the rail)

**Scope:** analysis only. Read `implied.py`. Did not edit `ou.py`, `implied.py`, `signal.py`, `forecast.py`, or the rail. Not wired. 8765 not restarted.

**What:** Premise change. Fitted σ was 0.0105 vs house-implied ~0.199 (19x low). Prior BM-vs-OU used σ=0.272 matched to a 1-row 41% figure from the broken surface. Re-ran on σ=0.199. All prior per-unit changelog numbers are unreliable (broken surface + bankroll that went negative). No book result invented. 50-minute window is running; not quoted.

**Measured:** synthetic first-passage only, `one_sided_touch` at σ=0.199, row=$0.50.
- Broken σ=0.0105: every cell at T=5..50 and 1–4 rows is p=0. That is the 1.0 / 0.0 surface.
- Anchored T=5, 1-row: BM p=0.261 vs house 0.10–0.15 and ledger observed 0.154 / 1006. Anchor is high, as `implied.py` said.
- Anchored T=5, 2-row: BM p=0.025, 25x EV −0.38. BM already refuses. Spurious θ=0.12 → p=0.0057.
- Anchored T=30, 2-row: BM p=0.359, 25x EV +7.97. Spurious 45s-BM θ=0.12 → p=0.121, still +EV +2.01. θ=0.30 → p=0.0066, 25x EV −0.84. The rail's job is this column, and we cannot identify θ=0.30.
- Anchored T=30, 1-row: BM p=0.646; θ=0.12 → **0.701** (OU raises p). Near barrier + long T: reversion gives another chance. Good-news direction on the band that can make money.
- θ identification is unchanged (scale-invariant): 5s still unidentifiable; 45s still stamps θ≈0.12 on BM. Silent-BM cut still dead.

No shadow. No per-unit baseline cited (file is unreliable). Too small to claim a book move.

**Open:** keep the Wilson rail. PR #5 is still not a replacement. implied.py already says the rail is not optional while the high-biased anchor is on.

---

## 2026-08-16 — selection desk — NOTE: do not fit s on pre-anchor p_model

**Scope:** local CHANGELOG only. Read `src/analytics/implied.py`. Did not edit `rank_calibrator.py`, `test_selection_bias.py`, `implied.py`, `forecast.py`, `gridboard.py`, or `room.py`. Not wired. No writer. 8765 not restarted.

**What:** Premise change. Fitted sigma 0.0105 vs house ~0.199 (~19x low) collapsed the surface to p=1 at d=0 and p=0 at d=1. Every pre-anchor `p_model` is from that surface. Winner's-curse math is unchanged in principle. s = mean(p_model − 1{touched}) on rank-0 is meaningless on that tape.

**Measured:** no new s. n_windows still 0. PR #7 redo (head `647103bb`) needs no code change: `too_small` already sits the haircut, there is no tape to contaminate. All prior per-unit changelog numbers, and the 0.60 → 0.365 pin, are pre-anchor and not a baseline for this desk.

**Open:** writer stays parked until after this window. When it lands it belongs in `gridboard.settle` and must tag whether the window is post-anchor. Ingest must refuse untagged / pre-anchor rows so s cannot be fit on the broken surface. Do not invent s_hat. Do not wire.


## 2026-08-15 — selection desk — MEASURED: PR #7 redo gates too_small, still no tape

**Scope:** `src/analytics/rank_calibrator.py`, `tests/test_selection_bias.py` on https://github.com/seraphimarchangel147/euphoria-bot/pull/7 (head `647103bb`). Local CHANGELOG only. Not wired. No writer. `policy.py` / `calibration.py` / `forecast.py` / `signal.py` / `reachability.py` / `gridboard.py` / `room.py` not edited. 8765 not restarted.

**What:** Redo of the sibling sketch. `too_small` (`n_windows < 50`) now sits `adjust_rank` / `apply_to_winner` (pass-through, `trusted=False`). `observe_ranked` does not increment windows. 146/400 is no longer allowed to print 0.365 as a result.

**Measured:** no tape. n_windows=0, s_hat=None, too_small, not measured. Baseline not beaten: 1365 / 12.2% / −0.2775 vs −0.2712 (indistinguishable). Drop −0.4948. Full windows only. Six sentence-style tests; one labelled SYNTHETIC Beta (p=0.40, 12/60). PR body claims 57 passed — not re-run here. Sample means nothing.

**Open:** still draft. Still a sibling sketch. Do not wire. Writer, if the owner wants it after this window, belongs in `gridboard.settle` (one ranked window per settle), not `room.think`.

---

## 2026-08-15 — horizon desk — MEASURED: live p=0.41 slice reprints the monotone, kills 0.21/0.75

**Scope:** local CHANGELOG only. Read `reachability.py` + live `/think` calibration reliability. Did not edit `horizon_error.py`, `forecast.py`, or `reachability.py`. Not wired. 8765 not restarted.

**What:** Tested the synthetic joint (tick-σ × T^0.05 vs saturating tape, 0.21× / 0.75×) against real observed rates. Reachability ledger is (distance, horizon) only and has no slot-0 (<10s) bucket at n≥60, so it cannot reprint a fixed-p slice. The live calibrator can: p10 is predicted 0.32–0.50 (mid 0.41), six horizon bands, `labelled_total` ≈ 137,500.

**Measured:** p10, vols pooled, n=9,818 labelled cells (not the selected ~180, not synthetic):

| horizon | n | actual | × of 0.41 |
| h0 <7.5s | 1,041 | 0.0605 | **0.148×** |
| h1 7.5–12.5 | 1,372 | 0.0729 | 0.178× |
| h2 12.5–17.5 | 1,484 | 0.0802 | 0.196× |
| h3 17.5–27.5 | 1,770 | 0.1169 | 0.285× |
| h4 27.5–42.5 | 2,140 | 0.1697 | 0.414× |
| h5 ≥42.5s | 2,011 | 0.2844 | **0.694×** |

Monotone reprints. Both ends stay below 1 (no good news). 0.21× / 0.75× does **not** reprint — short hole is deeper (0.15 vs 0.21), long is 0.69 not 0.75. Original 0.13× / 0.81× is still the signature; this is the same slice on a grown table, not a recovery of those two decimals.

Binomial SE: h0 ±0.007 on the rate (0.148× ±0.018); h5 ±0.010 (0.694× ±0.024). The gap is not noise. Reachability warm buckets (n≥60, no p-slice): d=1 rates 0.138 / 0.129 / 0.217 (h=10–25 / 25–50 / ≥50, n=145/140/60). Same direction at one row, too coarse to test 0.21/0.75.

No shadow claim. Full-window baseline to cite: 1365 / 12.2% / −0.2775 vs −0.2712, indistinguishable. Drop −0.4948. Current 57-trade shadow not quoted.

**Open:** not wired. Live smoother hurst was 0.33 (not on the 0.05 floor) while the hole still printed — H-floor stays dead. Tiny-p 16–20× under still a separate hole. Reachability slot 0 empty. Do not invent 0.13/0.81.

---

## 2026-08-15 — regime desk — MEASURED: garbage filter on rolling window

**Scope:** `src/analytics/rolling_regime.py` (NEW locally), `tests/test_rolling_regime.py` (NEW locally). Not wired. `regime.py` not edited. Not added to the GitHub PR. 8765 not restarted.

**What:** Same ingest as live VolRegime: drop None / non-float / <=0 / NaN / ±inf so a bad house-vol print cannot enter the 20-minute window. `now` still required. House source only.

**Measured:** synthetic only. tests/test_rolling_regime.py 5 passed, including `test_a_nan_inf_or_nonpositive_print_cannot_enter_the_window`: None, "x", 0, -1, 0.0, NaN, ±inf leave window_n=0; a later 0.05 is the only sample; subsequent NaN/inf/0 do not join it. Rising-input thirds unchanged (n=3600, window_n=1201). Too small. Not wired. Baseline not beaten: full window 1365 / 12.2% / −0.2775 vs −0.2712. Drop −0.4948.

**Open:** not wired; not measured on live tape. KEEP=4000 still clips the window above ~3.3 Hz. Claude to lift the two sibling files from the local tree onto current main. Do not restart 8765.

---

## 2026-08-15 — ou desk — MEASURED: fit_ou cannot feed a 5s cell (keep the rail)

**Scope:** analysis only. Read `reachability.py` + `tests/test_reachability.py`. Did not edit `ou.py`, `signal.py`, `forecast.py`, or the rail. Not wired. 8765 not restarted.

**What:** Claude asked whether PR #5 can replace the Wilson far-cell rail. The blocker is θ, not the PDE.

**Measured:** synthetic identification, 80 windows. Not a shadow move.
- 5s / 2 Hz (n=11): 80/80 thin. Cannot estimate θ on the cell horizon.
- 5s / 4 Hz (n=21): θ_hat median 0.94 on BM, 1.08 on true θ=0.30. Not identified.
- 45s / 2 Hz (n=91): BM → θ median 0.12; true θ=0.01 → 0.13 (cannot see the silent-BM cut); true θ=0.30 → 0.39. Theil-Sen is biased high.
- θT ≲ 0.05 silent BM (needs θ<0.01 at T=5) fired on 1/79 BM windows. Dead in practice.
- 2-row 5s cell, σ matched to live 1-row 41% (σ=0.272): BM p=0.100; 45s-BM fitted θ p_med=0.054. At 25x that is still +EV on 73% of windows (med EV +0.36). Wilson 0/269 upper=0.014, vetoes (25x EV −0.65).
- 1-row under the same spurious θ: 0.411 → 0.386. Mild. The rail's job is the 2-row zero-win book.

Baseline they did not beat: 1365 / 12.2% / −0.2775 vs −0.2712 (indistinguishable). Drop −0.4948. Full windows only. Too small to claim a shadow move.

**Open:** keep the empirical rail. PR #5 is a library, not a replacement, until θ is identified (bias-corrected or a much longer window). A biased-high θ that "helps" kill far cells is the v1-sigma-low shape.

---

## 2026-08-15 — regime desk — CLAIM: garbage filter on rolling_regime.py

**Scope:** `src/analytics/rolling_regime.py`, `tests/test_rolling_regime.py` only. Not folding into `regime.py`. Will not touch forecast/signal/smoother, policy, calibration, pnl, reachability, extension, or 8765.

**What:** Claiming before adding the live VolRegime ingest filter (drop None / <=0 / NaN / inf) so a bad print cannot enter the rolling window.

**Measured:** in progress. Labelling work stays synthetic / not wired / too small / baseline not beaten. Full-window baseline 1365 / 12.2% / −0.2775 vs −0.2712 (indistinguishable). Drop −0.4948.

**Open:** not wired. Filter must match live VolRegime before anyone hangs this on the room.


---

## 2026-08-15 — selection desk — CLAIM: rank_calibrator.py + test_selection_bias.py

**Scope:** `src/analytics/rank_calibrator.py`, `tests/test_selection_bias.py` only. Sibling redo of PR #7 from current main. Not folding into `policy.py` / `calibration.py`. Will not touch `forecast.py`, `signal.py`, DiffusionSmoother, `reachability.py`, `extension/`, or 8765.

**What:** Claiming before the redo. (1) s on real grading windows, not hardcoded hits=146/400. (2) `too_small` must gate `adjust_rank` / `apply_to_winner` (sit / pass-through), not merely widen the CI.

**Measured:** in progress. Baseline to beat is the full window 1365 / 12.2% / −0.2775 vs −0.2712 (indistinguishable). Drop −0.4948. Full windows only. Reliability pin 0.60 → 0.365 is diagnosis, not a result we get to reprint from synthetics.

**Open:** not wired. Label synthetic / too small / baseline not beaten until a real window count exists.


## 2026-08-15 — horizon desk — MEASURED: short-T overrate is tick-σ × T^0.05 vs saturating tape

**Scope:** `src/analytics/horizon_error.py` (NEW, PR #9), `tests/test_horizon_error.py` (NEW). Local CHANGELOG only. Not wired. `forecast.py`, `signal.py`, DiffusionSmoother, `policy.py`, `calibration.py` not edited. 8765 not restarted.

**What:** Reviewed https://github.com/seraphimarchangel147/euphoria-bot/pull/9. Analysis plus pin tests only. Live quote is p = 2Φ(-d / (σ_tick T^0.05)). At fixed predicted p=0.41 that map is almost time-flat, so the same p names almost the same barrier at 5s and 40s. A saturating tape still accumulates then flats, so short T is far more overrated. Fat tick-σ (16Hz dp^2/dt) puts both ends below 1.

**Measured:** signature is the 111,381-cell reliability table (all labelled cells, not the selected ~180): at predicted 0.41, shortest realises 0.13× (actual ≈ 0.053), longest 0.81× (actual ≈ 0.332). Confirmation is synthetic. Joint helper (r=4, θ=0.03, T=5/40) lands **0.21× / 0.75×** — sign and a factor-of-two hole at short T, not a three-decimal recovery of 0.13 / 0.81. Do not dress that up as the table.

Killed at fixed p=0.41:
- H-floor vs house 0.09: 1.07× / 1.16× (wrong monotone + good news). Vs H=0.03 (below floor): 0.96× / 0.92×, still the wrong monotone.
- Missing OU, σ matched, θ=0.03: 1.63× / 1.95× (wrong monotone + good news). Classic BM-quoted p (d ∝ √T) vs OU overrates the *long* end.
- Uniform overstated σ, same H: 0.24× / 0.24× at r=2 (T-flat). Fat σ sets the level, not the split.

4 tests passed. No shadow window. Baseline they did not beat: 453 / 48.3% / −0.2712 per unit. Too small to claim a shadow move.

**Open:** not wired. Tiny-p 16–20× under is a separate hole (fat tick-σ makes tiny-p *more* overrated). Stand-in buckets are 5s/40s; live calibrator edges are 7.5/12.5/17.5/27.5/42.5. _classic_ou_touch_p is a heuristic aimed at money-man 2.45, not real OU first-passage; the matched-σ kill uses ou_rms.

---

## 2026-08-15 — selection desk — HOLD: PR #7 rank calibrator is a sibling sketch

**Scope:** local CHANGELOG only. Reviewed https://github.com/seraphimarchangel147/euphoria-bot/pull/7. Did not edit `src/analytics/rank_calibrator.py`, `tests/test_selection_bias.py`, `policy.py`, or `calibration.py`. Not wired. No fix agent launched.

**What:** Empirical winner's-curse sibling landed as two new GitHub files only. `s = mean(p_model − 1{touched})` on rank-0, then mean across windows. Bootstrap resamples those per-window s values (n_boot=400), not a moving-block bootstrap. Closed-form μ_N is comparison-only; live path is a Beta haircut on rank bands. Cold rank-0 (n < 20 cells) returns incoming p unchanged.

**Measured:** no tape sample. Live n_cells = none. Live n_windows = none. The 0.60 → 0.365 pin is hardcoded `hits = 146` of 400 synthetic observes; the PR then reports p_cal ≈ 0.370 as if measured. Four tests, all constructed. 60-window "fat" bootstrap is barely ≥ 50 and invented — too small, and short windows lie. `too_small` (n_windows < 50) only widens the CI to include 0; it does not gate `adjust_rank` / `apply_to_winner`, which trust at 20 cells. Those two facts (146/400 invention, too_small not gating the haircut) are why this stays a sketch.

Baseline they did not beat: 453 / 48.3% / −0.2712 per unit. Reliability pin 0.60 → 0.365 is still diagnosis, not a shipped correction. No −0.27 → +0.40 flip, which is the one honest sentence.

**Open:** HOLD. Do not wire. Do not ship until (1) s is measured on real windows, not 146/400, and (2) `too_small` gates the correction, not just the CI. 8765 not restarted.

---

## 2026-08-16 — regime desk — MEASURED: rolling-window terciles (synthetic only)

**Scope:** `src/analytics/rolling_regime.py` (NEW, PR #8), `tests/test_rolling_regime.py` (NEW). Not wired. Not added to the GitHub changelog. `regime.py` not edited.

**What:** Sibling of live VolRegime. Terciles over a 20-minute (1200s) time window of house vol, not the full KEEP=4000 retained history. `now` required, no wall-clock. Source stays house.

**Measured:** strictly rising 1 Hz house vol, n=3600, window_n=1201. Whole-history occupancy on the recent window: LOW 0.000 / MED 0.001 / HIGH 0.999. Rolling occupancy: 0.333 / 0.334 / 0.333 (400/401/400). Four tests passed. Latest-print bucket() on the same monotone rise is still HIGH (newest is the window max) — occupancy destaturates, the current print does not. That is what terciles mean, not a leftover bug. Synthetic only. Too small to move the book.

**Open:** not wired; not measured on live tape. Missing garbage filter (None/<=0/nan/inf) — looser ingest than live VolRegime; must go in before anyone hangs this on the room. KEEP=4000 clips the 20-minute window if observe rate exceeds ~3.3 Hz. Do not restart 8765.

---

## 2026-08-16 — ou desk — MEASURED: OU first-passage (synthetic only)

**Scope:** `src/analytics/ou.py` (NEW), `tests/test_ou.py` (NEW). Not wired. PR #5.

**What:** Numerically safe OU first-passage (CN absorbing PDE in dimensionless s=θt, y=(x−μ)√(2θ)/σ) plus robust 45s fit (median μ, MAD σ, Theil–Sen θ). Failed fit is NaN, not σ=0.

**Measured:** unit tests only — 9 passed, synthetic. Independent re-run (corrected): at BM p=0.25, θT=1.5, CN **2.08** (p_ou=0.1203). Fine Euler+BB and exact-OU+BB (12k-20k paths) **2.08-2.10**. Homemade CN matches the module to 1e-4. Coarse 80-step Euler, no interpolation: **2.49**. Money-man's 2.45 is missed crossings. An earlier 2.19 / CN-5pct-high reading was a thin MC and is withdrawn. Far barrier 4 σ_stat at θT=20: OU 0.00986 vs BM 0.53 (MFPT branch; MC 0.00913). Sample is synthetic and too small to claim a shadow move.

**Baseline cited:** 453 trades / 48.3% / −0.2712 per unit. Live shadow unverified. Do not restart 8765.

**Open:** θT ≲ 0.05 silently uses BM (5s cell, θ<0.01). Far-barrier CN/MFPT cut at τ=8 jumps ~20% relative; MFPT is the high side vs MC. fit_ou stamps mean θ≈0.12 on a 45s BM window (Theil-Sen finite-sample reversion). CN clip is not material (~5e-5). Owner still has to integrate; do not treat this as a book move.

---

## 2026-08-16 — pnl desk — MEASURED: folded PR #6 field names into pnl.py
Scope: src/analytics/pnl.py, tests/test_pnl.py
What: Same scheme as HonestPaperStats. Headline implied_multiplier (2.70); avg_multiplier is the same number so /pnl consumers do not keep the tail mean. avg_multiplier_raw is 33.4. Added hit_rate_staked, stake_weighted_multiplier, win_multiplier_mean. median_multiplier is diagnostic only.
Measured: PR #6 loser-cluster book (10 wins @ 2.70 + 17 losses @ ~51.46, n=27, E=0) raw 33.4, implied 2.70, median 51.46. tests/test_pnl.py 31 passed. Not a shadow move. 8765 not restarted.
Open: live /pnl still shows old numbers until 8765 restarts.

---

## 2026-08-16 — pnl desk — MEASURED: avg_multiplier no longer reports the 33.4 tail mean
Scope: src/analytics/pnl.py, tests/test_pnl.py
What: Headline avg_multiplier is now (1+E)/hit_rate. Raw tail mean moved to avg_multiplier_raw. Added stake-weighted / median / win-mean twins, and stake-weighted hit-rate twins on paper_summary, session, and attribution.
Measured: before /pnl avg_multiplier 33.4 vs back-solved 2.70; after, headline reconciles as (1+E)/hit_rate. Sample = synthetic reproduction of the owner cited book (27 wins @ 2.70 + 73 heavy-tail losers; n=100), not the live /pnl ledger. tests/test_pnl.py 30 passed. Not a shadow per-unit move. Baseline -0.2712 is unrelated.
Open: live /pnl will keep showing 33.4 until 8765 is restarted (we did not restart it). session_start_ts of 0.0 is treated as unset (or now) — pre-existing, not changed.

---

## 2026-08-16 — pnl desk — CLAIM: src/analytics/pnl.py + tests/test_pnl.py
Scope: src/analytics/pnl.py, tests/test_pnl.py
What: Claiming before the avg_multiplier heavy-tail fix on the local tree. /pnl reports 33.4; back-solve (1+E)/hit_rate is 2.70. Will not touch forecast/signal/smoother, policy, calibration, regime, extension, or 8765.
Measured: in progress
Open: headline must reconcile as (1+E)/hit_rate; audit every other summary mean in pnl.py

---

## 2026-08-16 - Claude - FOUND: a candidate edge, and three reasons we were blind to it

**Scope:** analysis on the new quote-keyed ledger. No behaviour changed yet.

**Measured, keyed at the house's own granularity for the first time:**

| quote band | n | rate | EV at worst quote in band |
|---|---|---|---|
| **1.01-1.04x** | **195** | **1.0000** (zero misses) | **+0.0100 to +0.0400** |
| 1.11-1.20x | 495 | 0.9111 | +0.0113 |
| 1.21-1.50x | 851 | 0.7450 | -0.0985 |
| 1.51-2.00x | 1041 | 0.4697 | -0.2907 |
| 2.01-3.00x | 1732 | 0.3089 | -0.3791 |

195 of 195 at 1.01-1.04x with **no misses**. Wilson lower bound 0.9807, so on a
pessimistic reading EV is still **+0.0101 at 1.03x and +0.0199 at 1.04x**. At
1.01x the lower bound is -0.0095, so the edge starts around 1.02x.

The mechanism is unglamorous and plausible: these are cells whose band already
contains the price, so the bet is "does a quiet market stay in its row for five
seconds". On this tape it does, about 100% of the time, and the house prices it
at 0.96-0.99.

**Three independent reasons this was invisible, all of them ours:**

1. **Pooling hid it.** The old attribution reported the "1-1.5x" bucket at 126
   trades / 87.3% / **+0.001 per unit** -- indistinguishable from zero. Split at
   the house's granularity, 1.01-1.04x is +0.010 and 1.21-1.50x is -0.107.
   Averaged they cancel. I spent this session arguing pooling can *manufacture*
   edge; it hid one just as effectively and I never checked that direction.
2. **The reachability rail vetoes it.** The ledger pools (distance, horizon)
   across all multipliers: d=0 slot 2 has pooled rate 0.8746, ceiling 0.8795, so
   it demands 1.14x and refuses a 1.03x quote. The 1.03x subset resolves at
   1.0000 over 69 samples. My own safety rail is too coarse in exactly the way I
   criticised the +1.85% finding for being.
3. **The policy would never pick them.** Ranking by EV means a fat multiplier on
   an overstated far cell always outbids +3% on a near-certainty. The book has
   been systematically avoiding the one place edge appears to exist -- which is
   also why 64% hit at an effective 1.40x kept losing the vig.

**What this does NOT establish.** 195 observations from one unusually quiet
stretch -- price moved $0.14 in three minutes during sampling. The edge is
almost certainly regime-dependent: in a volatile regime at-the-money cells leave
their band and the rate falls below the 0.97 breakeven, inverting the sign. It
is also 1-4% gross, thin enough that gas, latency, minimum stake or slippage
could consume all of it. Nobody should size a real bet on this yet.

**Open, in order:** (1) run the quote-keyed ledger to n>2000 in the low band;
(2) split by volatility regime and find where it inverts; (3) key the
reachability rail by quote so it stops vetoing its own best cells; (4) only then
consider a ranking that prefers proven cheap certainty over unproven fat
multipliers.


## 2026-08-16 — Claude — REPLICATION FAILED, and it invalidates my own significance test

**Scope:** measurement. `gridboard.py` (quote-keyed labels), `tests/
test_quote_labels.py` (+7), `test_closes.py` (+13). Suite 535 with the desks'
`cell_edge.py` landed.

**Measured: −0.1066 did not replicate.** Same configuration, same code,
different tape:

| anchor-off window | trades | per unit |
|---|---|---|
| 1 | 772 | **−0.1066** |
| 2 | 986 | **−0.4600** |

They differ by **0.353**, which the within-window SE (±0.05) calls a 5-sigma
event — on *identical configuration*. That is not possible if the SE is right,
so the SE is wrong.

**Why, and it undoes a claim I made today.** Per-trade SE assumes independent
trades. Every cell in a window rides one price path, so trades are strongly
correlated and the effective sample size is a small multiple of the number of
*windows*, not the number of trades. I wrote that caveat down twice and then
kept quoting the naive number anyway.

Re-scoring the anchor A/B honestly, using between-window SD (~0.250) estimated
from these two same-config runs:

| | |
|---|---|
| anchor ON (1 window) | −0.7358 |
| anchor OFF (2 windows) | mean **−0.2833** |
| gap | 0.4525 ≈ **1.8 sigma**, not the 9.3 I reported |

The anchor is still probably worse, and leaving it default-off is still the
right call on the evidence. But "9.3 sigma, decisive" was wrong, and any future
comparison needs **multiple windows per arm** — a single window per arm cannot
resolve a difference smaller than about 0.5 per unit.

**Also landed:** quote-keyed labels. `PendingCell` now carries the multiplier
and `settle` emits `(multiplier, distance, horizon_s, touched)` through an
`on_quote_label` hook, which is the wall both Garage desks stopped at. A
consumer that throws cannot stop the grading — the calibrator and rail ride the
same pass. Card now shows the last 9 column closes by row (up / down / held).

**Open:** every per-unit comparison in this file rests on one window per arm and
is therefore weaker than written. The next question is not "does this change
help" but "can this harness resolve any change at all", and the answer is: not
below ~0.5 per unit without several windows per arm.

---

## 2026-08-16 — Claude — MEASURED: the anchor is harmful. Default OFF. My diagnosis was wrong.

**Scope:** `config/settings.py` (`USE_HOUSE_ANCHOR`, default off),
`src/analytics/signal.py` (switch), `tests/test_implied.py` (+1). Suite 486.

**Matched 50-minute windows, byte-identical code, one setting apart:**

| | trades | hit | per unit |
|---|---|---|---|
| anchor **ON** | 1218 | 15.5% | **−0.7358** |
| anchor **OFF** | 772 | **64.0%** | **−0.1066** |

A gap of **+0.629 per unit at 9.3 sigma**, and −0.1066 is the first result this
project has produced that **beats picking at random** (−0.18). Median sigma ON
was 0.1344 against 0.0401 OFF — the anchor ran ~3.4x hot, which made far cells
look reachable, and the book took them. Implied effective multiplier fell from
1.70x to 1.40x: with the anchor off the system bets near-certainties at thin
odds and loses roughly the vig, which is a coherent way to be wrong. With it on
it bought lottery tickets.

**The diagnosis behind it was wrong, and the error is worth naming.** I measured
sigma at 0.0105 against a house-implied 0.199, called it a systematic 19x bias,
and rebuilt the volatility path around it. That reading came from **a single
snapshot**. The tick fit's median across a full window is 0.0401, so 0.0105 was
a transient low and I generalised a systematic bias from one sample. The
supporting evidence — bimodal surface, house and ledger agreeing against us —
was real but was also collected at that same moment, so it corroborated the
snapshot rather than the claim.

The honest reading now: the house's implied sigma sits ~3x above our tick fit
because a quoted multiplier embeds the house's margin, exactly as
`implied.py`'s own docstring says (`1/m` is above true p, so the sigma
reproducing it is above true sigma). I wrote that warning down, shipped the
thing anyway, and it cost 0.63 per unit.

**What survives:** the switch, the untruncated harness, the persisted rail, the
`no-funds` verdict, and `implied.py` itself as a diagnostic — the gap between
tick fit and house anchor is a real signal, it just must not drive sigma.

**Open:** −0.1066 over 772 trades is one window and needs repeating. The
selection-bias correction is now the highest-value item; nothing shipped this
session addressed whether the ranking beats the house's pricing.

---

## 2026-08-16 — Claude — MEASURED: −0.7358. The first honest window, and it is bad.

**Scope:** measurement + QA. `policy.py` (`no-funds` verdict),
`extension/content.js` (board line). Suite 485.

**Measured, 50 minutes, untruncated:** **1218 trades / 15.5% / −0.7358 per
unit.** Random is −0.18. This is far worse than anything previously recorded
here, and it is the first number in this file that should be believed.

**Every earlier figure was optimistic, and the mechanism is systematic.** A book
that stops trading at ruin stops *exactly when it is losing fastest*, so
truncation censors the worst stretch of every run and biases per-unit upward.
All three prior numbers were truncated:

| | reported | ended by |
|---|---|---|
| baseline | −0.3201 / 773 | ruin |
| median-sigma | −0.2775 / 1365 | ruin |
| rail run 1 | −0.2004 / 1265 | ruin |
| **this window** | **−0.7358 / 1218** | **the clock** |

The apparent improvement from −0.32 to −0.20 across this session was, at least
in part, books going broke sooner and therefore censoring more of their own bad
tape. I reported those as progress. They were not.

**The anchor is still not fit for purpose.** The gates helped and did not
fix it: over the window the anchor ranged **995x** (0.0019 to 1.9024) with a
step median of 1.55, p90 12.8 and max 70.8. 17% of samples sit outside any
plausible 0.01–0.50 band; 8 samples exceed 1.5 against a search bound of 2.0 —
the boundary gate is set at 0.99 x SIGMA_HI = 1.98 and simply does not catch
1.90. The carried sigma looks stable (4.1x range, step median 1.10) only because
the EWMA is heavily damping garbage. A smooth average of nonsense is still
nonsense, which is the same mistake as stabilising the 19x-low tick fit, made
one level up.

**Overlay QA, same session.** The card reported `NO TRADE - 0 edge` while the
API carried a cell at `ev_lcb +0.062` with Kelly 0.23 and `stake 0.0` — the
paper roll was empty, so every stake sized to zero and fell through to `thin`.
"0 edge" reads as *the market has nothing*; the truth was *we are broke*. Added
a `no-funds` verdict and surfaced it. Also folded `untouched` into the card's
"out of reach" count: the reachability rail was vetoing 72 of 120 cells and none
of it appeared on the overlay at all.

**Open:** the anchor needs either a hard plausibility band or to be abandoned.
Next measurement should be the same untruncated harness with the anchor OFF, to
separate "the anchor is bad" from "untruncated measurement is just honest".
Until that runs, −0.7358 is the system's real number and the anchor's
contribution to it is unknown.

---

## 2026-08-16 — Claude — Three fixes the first anchored window exposed

**Scope:** `implied.py` (residual + boundary gates), `reachability.py`
(persistence), `room.py` (reachability store, shadow notional),
`config/settings.py` (`REACHABILITY_STORE`), tests +6. Suite 482.

The first anchored window died in 20 minutes — 583 trades, 6.7% hit, `pnl`
exactly −100.0 — and diagnosing it turned up three separate problems.

**1. The anchor was reporting its own search bound as a measurement.** Live
anchor values of exactly `2.00000` — `SIGMA_HI` to five decimals. Because touch
probability peaks and falls, a grid our model cannot reproduce has no interior
minimum, so the scan slides to the end of its range and the argmin is just the
least-bad miss. Measured over nine minutes: anchor **range 54.5x, median step
3.61x, p90 25.2x** — worse than the estimator it replaced. Now gated on both
`|residual| > 0.05` and proximity to either bound; either way the answer is "no
anchor this pass" and the caller falls back to the tick fit. After the gate:
carried 0.126 against anchor 0.158, a 1.26x gap where it had been 5.3x, with 64
of 67 passes anchored and 3 correctly falling back.

**2. Two correct changes combined into a worse one.** The anchor is biased high
so it makes far cells look reachable; the reachability ledger is its only
backstop and correctly refuses to have an opinion under 60 observations — but
started from zero on every restart. Every restart therefore opened a window
where the model was at its most eager and its guard was silent. The ledger now
persists to `~/.euphoria/reachability.json`; corrupt files and impossible rows
(more hits than trials) are dropped rather than trusted. It now boots with 21
buckets armed and vetoing.

**3. Ruin was truncating the measurement, not the market.** The shadow book
measures edge per unit staked, which is defined regardless of how much money is
left, but it was sized like a real 100 roll — so windows ended at ruin and then
reported the floor as a result. Given a 1,000,000 notional it now ends when the
clock does. This is what invalidated the previous three windows.

**Measured:** no book result. Clean window running.

---

## 2026-08-15 — Claude — Sigma anchored to the house's quote grid

**Scope:** `src/analytics/implied.py` (NEW), `tests/test_implied.py` (NEW, 18),
`forecast.py` (smoother takes an `anchor`, `Diffusion.anchored`), `signal.py`
(`_anchor_forecasts`, solve before pricing the surface). Suite 476.

**What:** Rather than invert a textbook barrier formula, this solves for the
sigma at which **our own** `band_touch_prob` reproduces the house's quoted
prices. The anchor therefore lands in our model's units and any quirk of our
formula cancels instead of being smuggled in. The smoother still carries and
smooths it; it is now carrying the right number.

**Bisection would have been wrong, and the tests caught it.** Touch probability
is **not monotonic in sigma**: for a narrow band in a future window it rises,
peaks, and falls again, because at enormous volatility the price is spread so
wide by the time the column opens that it is unlikely to be anywhere near a
half-dollar band. Measured on one cell, p goes 0.00 → 0.396 as sigma runs 1e-5
→ 1.0, then *decreases* to 0.376 by sigma 20. The solve minimises |median
error| over a log scan plus golden-section refine, and the range is capped below
the turn. Pinned in `test_touch_probability_is_not_monotonic_in_sigma`.

**A claim I had backwards, corrected in the module docstring.** I first wrote
that the anchor is biased *low* and therefore safe. It is the opposite. The
house profits when `p_true * m < 1`, so `1/m` sits **above** the true
probability by roughly their margin, and the sigma reproducing it sits above
true sigma. The anchor overstates how far price can travel and makes far cells
look more reachable — the exact failure this project keeps paying for. Vastly
better than a fit 19x low, but **not conservative**, and it is only safe while
the reachability ledger (which vetoes on measured touch rates, not on any model)
and the calibrator's shrink toward observed frequency are both in place.

**Measured, live at restart:** the surface has a gradient again. Before: every
cell exactly 0.0 or exactly 1.0. Now **44 of 96 cells strictly between**, and
the at-price column decays 0.888 → 0.524 across horizons, tracking the house's
0.971 → 0.690 in shape and sitting below it as the margin implies. Tappable
cells went from **0 to 18**. Our tick fit was running 3.0x below the anchor at
that moment (0.0171 vs 0.0513) — the same direction as the 19x gap, different
size, which is itself worth watching now that the ratio is logged.

**No book result yet.** Window running.

---

## 2026-08-15 — Claude — ROOT CAUSE: sigma is ~19x too low, and the house tells us so

**Scope:** diagnosis, no code changed.

**What:** Chasing the `p_cal` 0.992 anomaly found the thing underneath most of
this session's work.

The probability surface is **bimodal**. `p_model` is exactly `1.00000` at
distance 0 and exactly `0.00000` at distance 1, with nothing in between, at
every horizon from 12s to 77s. There is no gradient at all. The house, on the
same grid at the same instant, prices a smooth curve: at-price decays 0.98 →
0.69 across the horizons, and distance-1 runs 0.10–0.23.

Cause is not the barrier formula. I suspected `t_start` was being ignored —
scoring forward cells from now instead of over their own window — and checked:
`band_touch_prob` does proper Gauss quadrature over the column-open
distribution. That hypothesis was wrong.

The cause is that **sigma is grossly underestimated**. At `sigma = 0.0105`, price
is expected to move about two cents in seventy seconds, so an at-price band is a
lock and a one-row band is thirteen-plus standard deviations away and underflows
to zero. Both poles of the bimodal surface are the same error.

**Three-way confirmation, and one leg of it is model-free:**

| source | P(touch) for distance 1 |
|---|---|
| house implied (1/multiplier) | 0.10 – 0.15 |
| **reachability ledger, observed** | **0.154** (155/1006) |
| our model | **0.0000** |

The rail's 0.154 is a measured frequency and depends on no formula of mine. It
agrees with the house and not with us.

Inverting the whole quoted grid for the sigma that would reproduce the house's
prices gives a median of **0.199 against our 0.0105 — about 19x too low** (n=79
cells). That multiple is approximate: it uses a single-barrier normal
approximation and our own hurst. The direction and order of magnitude are not
approximate.

**Why this reframes the session.** Sigma has been the target all along, but the
work went into *stability* — the smoother, the median target, the rail exponent.
Stability was real and measurable and it was also the wrong axis. A number that
is 19x low does not become useful by being smoothly 19x low. Stabilising a
biased estimator just yields a stable wrong answer, which is what the flat
shadow results have been saying.

**The fix follows from the same idea that fixed the regime label.** Volatility
regime was corrected by taking the house's published number instead of our own
fitted sigma. Do it again one level down: the house publishes an implied
probability for **every cell, every five seconds**, produced by a model with far
more history than ours, and it is precisely the number that prices the bet we
are making. Invert the quote grid for implied sigma and anchor to it, rather
than fitting from our own ticks and hoping.

**Open:** implement the anchor. Keep our fitted sigma as a cross-check and log
the ratio — a large persistent gap either way is then a signal rather than an
invisible bias. Re-run baseline and rail windows afterwards; every per-unit
figure in this file predates this and was measured on a broken surface.

---

## 2026-08-15 — Claude — REPLICATION FAILED, and the fault was in my harness

**Scope:** `src/analytics/policy.py` (Bankroll floors at ruin, `ruined` flag),
`tests/test_bankroll_persistence.py` (+3). Suite 458.

**What:** The second rail window did not replicate −0.2004, and it did not
refute it either — it never ran. It booked **154 trades in 50 minutes against
1265 in the first**, and read on the dashboard as a quiet market. It was a
bankrupt one.

`Bankroll.balance` was allowed to go negative. `score_cell` sizes stakes from
`balance`, so once the shadow book went under, `max(0.0, kelly * 0.25 *
negative)` returned zero for every cell, `stake < min_stake` flipped each one
from `tap` to `thin`, and trading stopped silently. Shadow balance at the end of
the window: **−222.51**. The verdict histogram showed `tap: 0` with cells
carrying `ev_lcb` above +11 sitting in `thin`, which is what finally gave it
away.

**This contaminates earlier windows too, including ones I have already quoted.**
The −0.2775 baseline run ended at pnl −247 on a 100 start, so it was ruined for
part of its window as well. Every measurement in this changelog that ran past
roughly −100 pnl has been silently truncated at the point of ruin. The numbers
are not fabricated, but they cover fewer trades than their sample counts imply
and they are not comparable to each other. Treat cross-window comparisons in the
entries above as weaker than they read.

**Fixed:** balance floors at 0, and `ruined` is exposed so this announces itself
instead of looking like a market with nothing to bet on.

**Measured:** the incremental second window was −0.2473 over 154 trades, SE
±0.118. That is consistent with anything between −0.48 and −0.01 and means
nothing. **The rail's −0.2004 is neither confirmed nor refuted.**

**A second thing worth chasing,** spotted in the same verdict dump: cells at
distance 1 / horizon 7s showing `p_cal` 0.992 against a 13.0x quote — an
apparent +1185% EV. The rail's own observed rate for that exact (distance,
horizon) bucket is 0.154 over 1006 samples. The house does not misprice by
1200%. That is the section-2 signature and it should be treated as a bug until
proven otherwise.

**Open:** re-run both baseline and rail windows now that ruin is floored, from
equal starting bankrolls. Until then no per-unit comparison in this file is
trustworthy.

---

## 2026-08-15 — Claude — MEASURED: the rail, −0.2004. Best yet, not yet proven.

**Scope:** measurement only.

**Measured, full 50-minute window:** **1265 trades / 22.9% / −0.2004 per unit**,
against a −0.2775 baseline. Best result this project has recorded, and the first
time the book has reached parity with picking at random (−0.18) rather than
sitting below it. The rail fired on 272,857 of 760,379 checks (36%) across 27
warm buckets.

**What this does not establish.** The difference is +0.0771. Per-trade sd is
~1.47 at the implied 3.5x effective multiplier, so the standard error over 1265
trades is ~0.041 and the gap is about **1.3 sigma** — and that arithmetic
assumes trades are independent, which is false, since every cell in a window
rides one price path. The true interval is wider than 1.3 sigma. This is
suggestive and it is pointed the right way. It is not proof.

The within-window swing says the same thing louder: at 35 minutes this book was
at **−0.0033 per unit over 836 trades**, essentially breakeven, and it gave back
0.20 over the final quarter. Three windows in a row now have swung far enough
mid-run to reverse any conclusion drawn early. Nothing under a full window gets
quoted, and one full window is evidence, not a verdict. This needs repeating
before anyone believes the number — including me.

**Open:** repeat the window. A second independent run near −0.20 would make this
real; a run back at −0.28 would mean the first was tape.

---

## 2026-08-15 — Claude — Interim rail: the tape decides what is reachable

**Scope:** `src/analytics/reachability.py` (NEW), `tests/test_reachability.py`
(NEW, 26 tests), `policy.py` (veto + `untouched` verdict), `gridboard.py`
(feeds the ledger, `PendingCell.distance`), `signal.py` + `room.py` (wiring),
suite now 450.

**What:** `MIN_TAP_PROB` did not stop a single one of the 269 losing far-cell
bets because it consults **our own estimate**, and our own estimate believed a
3.8-sigma move inside five seconds was better than one in four. So this guard
does not ask the model anything. It remembers what the tape actually did at each
(distance, horizon), takes the **upper** Wilson bound on that touch rate — the
most generous reading the evidence allows — and vetoes only when even that
cannot break even against the offered multiplier. Being optimistic and still
losing is a solid reason not to bet.

Wilson rather than the normal approximation specifically because the interesting
case is zero hits, where a normal interval collapses to zero width and would
wave every dead cell through as a certainty in the wrong direction. At 269 empty
trials the bound is ~1.4% (the rule of three), so a 25x quote is refused and
anything above ~71x is still allowed — which is the honest reading of that
evidence, not a distance ban.

Two properties that matter more than the veto:

* **It self-heals.** No hardcoded distances. If the market wakes up and far
  cells start being touched, the bound rises and the veto lifts on its own. A
  constant would need a human and would be wrong at every other volatility.
* **It can only subtract.** The bound is only ever used to refuse a bet, never
  to justify one. Everything that has cost money here did so by making the
  numbers look better; a component that can only say no cannot join that list.

Below 60 observations in a bucket it has no opinion and stands aside — a guard
that fires on thin evidence is a hardcoded cap wearing a confidence interval.

**Measured:** suite 450, one known Windows failure. Live at restart: 4,280
checks, 0 vetoes, 0 buckets tracked — correct, the ledger starts empty and must
earn its opinions from the grading stream. Shadow window running; **no book
result yet, do not quote one.**

**A wrong assumption caught while writing the tests,** kept as
`test_a_thin_multiplier_is_refused_even_on_a_reachable_cell`: one row out is the
most profitable distance in the book, so "never veto it" looks obviously right.
It is not. The observed rate there is 60/146 = 0.411, upper bound ~0.49, and a
1.5x quote needs 0.667. Refusing it is correct — distance is not what makes a
bet good, price is. The +0.483 that distance earned came from fatter quotes.

**Open:** interim only. The principled replacement is a corrected reachability
estimate — PR #5's OU first-passage. Do not let this calcify into the answer.

---

## 2026-08-15 — Claude — MEASURED: median-target sigma, and a mid-window call I got wrong

**Scope:** measurement only, no code changed

**What:** QA of the v2 (median-target) smoother against the v1 baseline.

**Measured, full 50-minute window:** baseline 453 trades / 48.3% / −0.2712 per
unit → **1365 trades / 12.2% / −0.2775**. Statistically indistinguishable on a
3x larger sample. v2 is not an improvement in the book, and it is not a
regression either.

On the objective it was actually built for, v2 wins cleanly:

| consecutive-sample ratio | raw | v1 carried | v2 carried |
|---|---|---|---|
| median | 1.184 | 1.114 | **1.070** |
| p90 | 1.840 | 1.178 | **1.192** |
| max | 3.495 | 1.183 | 1.466 |
| share > 1.5x | 20.6% | 0.0% | **0.0%** |
| full range | 22.9x | 73x (worse than raw) | **10.6x** |

v1 damped the step but made the *range* worse than the raw fit (73x vs 14x)
because of the cold-start ramp. v2 fixes that: range is now better than raw.

**A call I got wrong, recorded because it matters.** At the 30-minute mark this
book read −0.4948 over 822 trades and I wrote it up as "worse, decisively,"
citing four stretches of 49–67 consecutive losses as structural rather than
variance. The last 20 minutes recovered it to −0.2775. The consecutive-loss runs
were real but they were *distance-driven* (see below), not evidence that v2
regressed. Two mid-window readings in this same run — +0.208 at 4 minutes,
−0.4948 at 30 — both failed to survive. This book swings hard enough that
nothing under a full window should be quoted, including by me.

**The decisive finding — the loss is entirely distance.** Three orthogonal cuts
of the same book all say it:

| by distance | n | wins | per unit |
|---|---|---|---|
| at price | 159 | 127 | **+0.151** |
| 1 row | 146 | 60 | **+0.483** |
| 2 rows | 80 | **0** | −1.000 |
| 3 rows | 128 | **0** | −1.000 |
| 4 rows | 58 | **0** | −1.000 |

| by horizon | n | per unit | | by multiplier | n | per unit |
|---|---|---|---|---|---|---|
| 0–10s | 62 | **+1.061** | | 3–8x | 72 | **+1.204** |
| 10–25s | 105 | +0.146 | | 1–1.5x | 126 | +0.001 |
| 25–50s | 125 | −0.249 | | 1.5–3x | 113 | −0.145 |
| 50s+ | 282 | −0.845 | | 25x+ | 216 | **−1.000** |

Split at two rows: **≤1 row = +0.2937 per unit over 305 trades. ≥2 rows =
−1.0000 over 269 trades, zero wins.** Same for 25x+ multipliers: zero wins in
216 trades.

Zero wins in 269 is not an overestimated probability, it is an impossible bet
being taken repeatedly. On this tape a row is $0.50 and price moved $0.8 in 30
minutes, so two rows is ~3.8 sigma inside a 5-second window — genuinely
unreachable. The `MIN_TAP_PROB` reachability gate does not stop these because
the model sincerely believes a 3.8-sigma move is >25% likely. That is the
overconfidence, measured directly.

**There is a profitable strategy already inside this bot** — near cells, short
horizons, moderate multipliers — being swamped by far-cell bets that never win.

**Open:** the fix is not a hardcoded distance cap; it is making far cells score
correctly near-zero. A distance/multiplier guard is a defensible interim safety
rail. Caveat honestly: subsetting a book post-hoc is exactly how people fool
themselves, and this needs to hold up on a forward window before it is believed
— though the mechanism is sound and 0/269 and 0/216 are not marginal calls.

---

## 2026-08-15 — Claude — Agent working agreement + changelog

**Scope:** `AGENTS.md`, `CHANGELOG.md` (new)

**What:** Wrote the working agreement so agents joining this repo do not have to
rediscover the architecture, the measurement discipline, or the six bugs that
each manufactured profit rather than crashing. Started this changelog.

**Measured:** n/a — documentation.

---

## 2026-08-15 — Claude — Socket capture survives reconnects

**Scope:** `extension/inject.js`, `tests/test_grid_supply.py`

**What:** The multiplier grid died 25 minutes into a measurement while prices
kept flowing, freezing the trade count and blinding the second half of the
window. That is the shape of a socket reconnecting through a path that escaped
the constructor wrap — a wrap on `window.WebSocket` only sees sockets built
through it afterwards. Now hooks the prototype as well (`addEventListener` and
the `onmessage` setter), both routed through one decoder with a per-socket
guard, since settlement frames are read on that path and a duplicated settlement
once paid a single square ten times.

**Measured:** grid feed recovered to 181 of 181 cells quoted. Durability across
a reconnect not yet observed — needs a long run to confirm.

**Open:** confirm the prototype hook actually catches a reconnect in the wild.

---

## 2026-08-15 — Claude — Sigma carried across passes (v2: median target)

**Scope:** `src/analytics/forecast.py`, `src/analytics/signal.py`,
`src/control/room.py`, `src/control/static/index.html`,
`tests/test_smoothing.py` (new, 20 tests)

**What:** Sigma was refit from scratch every pass with no memory of the previous
one. Over a 50-minute observation it swung 16.6x while price moved six rows —
2% of a row per sample. The market was not doing that; the estimator was. Since
the policy ranks cells and takes the maximum, an unstable sigma means it
reliably selects whichever square rides the largest upward estimation error.
Taking a max over noisy estimates selects the noise.

Added `DiffusionSmoother`: EWMA in log space (sigma's error is multiplicative),
30s half-life, weighted by wall-clock so it does not depend on how often
`think()` happens to run. A failed fit now carries the last value instead of
reading as "the market stopped moving".

**v1 shipped a defect, caught in measurement and fixed:** capping each step
against the carried value compounds, because the cap binds every pass. Climbing
out of a dead patch to a 40x higher sigma took six minutes, and throughout that
ramp the model believed the market was up to 15x calmer than it was and scored
every cell unreachable. Replaced with a **median over the last 60s of raw fits**
— one 6x print cannot move a median, while a genuine regime shift moves it in
full once most of the window agrees. Convergence is now bounded by the half-life.

**Hurst rails are asymmetric.** v1 held on either clamp on the theory that a
pinned estimator measured nothing. The observation log says otherwise: 72 of 197
samples sat at the high rail against 12 at the low one, and the two mean
opposite things. A fit at 0.50 wanted to claim a full random walk and was cut
off — believing it inflates how reachable distant cells look. A fit at 0.05
wanted to go *lower* — tape reverting harder than the model can express — and
holding a higher value overstates reachability the same way. Rule is now: a
saturated fit may lower the exponent, never lift it.

**Measured** (v1, on identical tape, raw fit vs carried):

| consecutive-sample ratio | raw | carried |
|---|---|---|
| median | 1.161 | 1.114 |
| p90 | 1.793 | 1.178 |
| max | 3.910 | 1.183 |
| share > 1.5x | 19.7% | 0.0% |

Shadow: baseline 773 trades / 33.5% / **−0.3201** per unit → 453 trades / 48.3%
/ **−0.2712**. Better on both axes but still short of random (−0.18), as
expected — this was the precondition for fixing calibration, not the fix.

**Open:** v2 (median target) measurement was still running at time of writing.
Early reading was positive but far inside noise; do not quote it.

---

## 2026-08-15 — Claude — Diagnosis: the ranking is fine, the calibration is not

**Scope:** analysis only, no code changed

**What:** Computed the reliability table over 111,381 labelled cells.
Spearman(predicted, actual) = **+0.996** — the ordering is essentially perfect,
which rules out the "model is anti-predictive, invert it" branch entirely.

The error is miscalibration with a specific shape: calibrated at both extremes,
~3x optimistic through p = 0.10–0.45. That band is exactly where a tappable bet
lives (3–6x multiplier breaks even at p = 0.17–0.33), so every cell that looks
like a good tap is overrated threefold. The error is further **ordered by
horizon** — at predicted 0.41 the shortest bucket realises 0.13x of prediction,
the longest 0.81x.

Mechanism identified as **max-of-N selection bias** (derivation delegated to
Grok/Garage, cross-checked against our own table): the argmax over ~180 cells is
inflated by `s·μ_N`, about +0.21 to +0.31 absolute at N=200 with s = 0.08–0.12.
Enough to turn a true 0.35 into a displayed 0.60. Our table: predicted 0.60 →
actual 0.365. Two independent routes, same number.

Caveat that must survive into the fix: the ~180 cells ride **one price path**,
so errors are tape-shared, effective N is far below 180, and the closed form
overstates the bias. Measure it empirically.

**Measured:** n/a — diagnosis. Numbers above are the measurement.

**Open:** the correction itself is unimplemented. Next upgrade.

---

## Earlier work (before this changelog existed)

Reconstructed from the record, undated. All by Claude.

- **Six profit-manufacturing bugs** found and each pinned with a test on the
  live case: duplicate settlements (one square paid 10x, +258); whole-column
  mis-timing; cold buckets at +641% EV; a `p0` bucket conjuring 7% into +475%
  EV; a dead feed read as certainty; hurst extrapolated past its fitted range
  (long horizons off by 100x). See AGENTS.md §2 — none of them crashed.
- **Reachability gate** (`MIN_TAP_PROB`) applied on *all* paths; the setup path
  had bypassed it. Two tests had encoded the bug and were corrected.
- **Volatility regime taken from the house's own number** instead of our fitted
  sigma. Conditioning calibration on a label derived from our own sigma selected
  our own estimation error, making buckets miscalibrated by construction —
  `p9|MED` predicted 0.26 and delivered 0.066. Shadow: −0.707 → −0.323 per unit.
- **Grid emit moved out of `draw()`**, which returns early when the canvas is
  missing — a rendering problem was silently cutting off every multiplier while
  the bot looked healthy.
- **Test isolation made structural** in `conftest.py` after tests wrote to the
  real `~/.euphoria` state twice.
- Built out: whole-grid surface scoring, dense per-window grading, traversal /
  coil / P&L / tilt / player-comparison analytics, shadow trading, the dashboard
  panels, live wallet reads.
