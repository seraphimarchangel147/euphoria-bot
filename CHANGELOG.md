## 2026-08-16 — pnl desk — MEASURED: avg_multiplier headline reconciles
Scope: src/analytics/pnl.py, tests/test_pnl.py
What: Accounting fix only. Headline `avg_multiplier` is now `(1 + expectancy_per_unit) / hit_rate` (also exposed as `implied_multiplier`). Raw arithmetic mean of quoted multipliers is parked on `avg_multiplier_raw`. Honest twins added: `avg_multiplier_stake_weighted`, `median_multiplier`, `avg_win_multiplier`, `hit_rate_stake_weighted`, `paper_hit_rate_stake_weighted`, attribution `hit_rate_stake_weighted`. Expectancy in `_view()` stays per unit staked. Streaks and `real_summary.trading_change` unchanged. `p_lcb` / `ev_lcb` are still not averaged.
Measured: before, /pnl headline 33.4 vs back-solved 2.70; after, headline is the 2.70-class reconciled number and raw 33.4 cannot silently return. Sample = synthetic reproduction of the owner's /pnl book (live JSON was never pushed). Not a shadow-edge change. No shadow per-unit move claimed. Baseline −0.2712 is unrelated.
Open: none for this accounting lie

## 2026-08-16 — pnl desk — CLAIM: src/analytics/pnl.py + tests/test_pnl.py
Scope: src/analytics/pnl.py, tests/test_pnl.py
What: Claiming before the avg_multiplier heavy-tail fix. /pnl reports 33.4; back-solve from hit rate and expectancy is 2.70. Accounting only — not a shadow-edge change.
Measured: in progress
Open: headline must reconcile as (1+E)/hit_rate
