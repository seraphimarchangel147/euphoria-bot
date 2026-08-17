"""Calibration buckets, EV/Kelly policy, and the dense grading board. No network."""
from src.analytics.calibration import Calibrator, bucket_key, horizon_bucket
from src.analytics.forecast import CellForecast, Diffusion
from src.analytics.gridboard import GridBoard
from src.analytics.policy import Bankroll, Policy, kelly_fraction, surface_summary
from src.analytics.signal import Tick


def _train(calib, p, n=600, *, rate=None):
    """Feed a bucket `n` outcomes at a real touch rate.

    Defaults to a perfectly calibrated bucket (`rate = p`), which is the honest
    setup for an EV test: training every outcome as a hit would teach the model
    the cell always touches and turn any multiplier into free money.
    """
    rate = p if rate is None else rate
    hits = round(n * rate)
    for i in range(n):
        calib.observe(p, 5.0, "MED", touched=i < hits)
    return calib


def _cell(p, mult, *, cx=10, cy=100, t_start=0.0, t_end=5.0, forward=1, row=1):
    return CellForecast(
        cell_x=cx, cell_y=cy, forward=forward, row_offset=row,
        side="up" if row > 0 else "down", distance=abs(row),
        lo=cy * 0.5, hi=cy * 0.5 + 0.5, t_start=t_start, t_end=t_end,
        p_touch=p, edge_cells=0.5, sd_cells=1.0,
        multiplier=mult, breakeven=round(1 / mult, 6) if mult else None,
    )


# --- calibration ----------------------------------------------------------
def test_a_cold_bucket_returns_the_model_untouched():
    c = Calibrator()
    out = c.adjust(0.30, 5.0, "MED")
    assert abs(out.p_cal - 0.30) < 1e-9
    assert out.n == 0
    assert not out.trusted
    assert out.p_lcb < out.p_cal          # no evidence -> wide interval


def test_evidence_moves_the_posterior_toward_what_happened():
    c = Calibrator()
    for _ in range(400):
        c.observe(0.30, 5.0, "MED", touched=True)
    out = c.adjust(0.30, 5.0, "MED")
    assert out.p_cal > 0.9
    assert out.trusted
    assert out.n == 400


def test_the_lower_bound_tightens_as_evidence_accumulates():
    thin, thick = Calibrator(), Calibrator()
    for _ in range(10):
        thin.observe(0.50, 5.0, "MED", touched=True)
    for _ in range(1000):
        thick.observe(0.50, 5.0, "MED", touched=True)
    assert thick.adjust(0.50, 5.0, "MED").p_lcb > thin.adjust(0.50, 5.0, "MED").p_lcb


def test_buckets_separate_probability_horizon_and_volatility():
    assert bucket_key(0.05, 5.0, "LOW") != bucket_key(0.05, 5.0, "HIGH")
    assert bucket_key(0.05, 5.0, "LOW") != bucket_key(0.05, 30.0, "LOW")
    assert bucket_key(0.05, 5.0, "LOW") != bucket_key(0.60, 5.0, "LOW")
    assert horizon_bucket(5.0) < horizon_bucket(40.0)


def test_brier_score_rewards_the_better_forecaster():
    good, bad = Calibrator(), Calibrator()
    for _ in range(200):
        good.observe(0.95, 5.0, "MED", touched=True)
        bad.observe(0.05, 5.0, "MED", touched=True)
    assert good.brier < bad.brier


def test_calibration_survives_a_save_and_reload(tmp_path):
    path = tmp_path / "calib.json"
    c = Calibrator(path)
    for _ in range(50):
        c.observe(0.20, 5.0, "MED", touched=True)
    c.save()
    again = Calibrator(path)
    assert again.adjust(0.20, 5.0, "MED").n == 50
    assert again.observed == 50


# --- policy ---------------------------------------------------------------
def test_kelly_is_zero_without_an_edge_and_positive_with_one():
    assert kelly_fraction(0.20, 5.0) == 0.0            # exactly breakeven
    assert kelly_fraction(0.10, 5.0) == 0.0            # negative
    assert kelly_fraction(0.40, 5.0) > 0.0


def test_a_fat_multiplier_on_a_reachable_cell_is_tappable():
    c = _train(Calibrator(), 0.55)                      # calibrated and trusted
    p = Policy()
    score = p.score_cell(_cell(0.55, 6.0), c, vol_bucket="MED", bankroll=Bankroll())
    assert score.verdict == "tap"
    assert score.ev_lcb > p.min_edge
    assert score.stake > 0


def test_a_house_edge_cell_is_refused_however_reachable():
    """p=0.45 on a 2x is a 10% loss per unit. Reachability is not an edge."""
    c = _train(Calibrator(), 0.45)
    score = Policy().score_cell(_cell(0.45, 2.0), c, vol_bucket="MED", bankroll=Bankroll())
    assert score.verdict == "negative"
    assert score.ev_lcb < 0
    assert score.stake == 0


def test_an_unproven_bucket_cannot_talk_the_policy_into_a_bet():
    """Same numbers, no evidence: the lower bound kills the edge."""
    score = Policy().score_cell(_cell(0.55, 2.2), Calibrator(), vol_bucket="MED",
                                bankroll=Bankroll())
    assert score.verdict != "tap"
    assert score.stake == 0


def test_a_lottery_cell_is_refused_however_juicy_the_multiplier():
    score = Policy().score_cell(_cell(0.0002, 5000.0), Calibrator(), vol_bucket="MED",
                                bankroll=Bankroll())
    assert score.verdict == "unreachable"
    assert score.stake == 0


def test_unquoted_cells_are_ranked_but_never_staked():
    scores = Policy().score(
        [_cell(0.8, None, cy=100), _cell(0.2, None, cy=104, row=4)],
        Calibrator(), vol_bucket="MED", bankroll=Bankroll(),
    )
    assert all(s.verdict == "unquoted" and s.stake == 0 for s in scores)
    assert Policy.choose(scores) is None
    assert Policy.best_unquoted(scores).p_cal > 0.5


def test_ranking_puts_the_best_lower_bound_edge_first():
    c = _train(_train(Calibrator(), 0.55), 0.30)
    scores = Policy().score(
        [_cell(0.30, 2.0, cy=100), _cell(0.55, 6.0, cy=102, row=2)],
        c, vol_bucket="MED", bankroll=Bankroll(),
    )
    assert scores[0].cell_y == 102
    summary = surface_summary(scores)
    assert summary["cells"] == 2 and summary["quoted"] == 2
    assert summary["tappable"] >= 1


def test_stake_is_capped_by_the_bankroll_not_just_by_kelly():
    c = _train(Calibrator(), 0.90, 1200)
    roll = Bankroll(start=20.0, balance=20.0, peak=20.0)
    score = Policy(max_stake=1000.0).score_cell(_cell(0.90, 8.0), c, vol_bucket="MED",
                                                 bankroll=roll)
    assert score.verdict == "tap"
    assert score.stake <= 20.0 * 0.05 + 1e-9


# --- bankroll -------------------------------------------------------------
def test_bankroll_books_wins_losses_and_drawdown():
    roll = Bankroll(start=100.0, balance=100.0, peak=100.0)
    roll.settle(10.0, 3.0, won=True)
    assert roll.balance == 120.0 and roll.wins == 1
    roll.settle(10.0, 3.0, won=False)
    assert roll.balance == 110.0 and roll.losses == 1
    assert roll.peak == 120.0
    assert roll.max_drawdown == 10.0
    assert roll.pnl == 10.0
    assert roll.hit_rate == 0.5


def test_syncing_a_live_balance_rebases_the_curve_once():
    roll = Bankroll()
    roll.sync_live(42.0)
    assert roll.live_synced and roll.start == 42.0 and roll.balance == 42.0
    roll.sync_live(50.0)
    assert roll.start == 42.0 and roll.balance == 50.0


# --- dense grading board --------------------------------------------------
def _board():
    c = Calibrator()
    return GridBoard(calibrator=c, bankroll=Bankroll()), c


def test_the_board_grades_every_cell_not_just_the_pick():
    board, calib = _board()
    now = 1000.0
    scores = Policy().score(
        [_cell(0.5, 2.0, cy=6000, t_start=0.0, t_end=5.0),
         _cell(0.5, 2.0, cy=6004, row=4, t_start=0.0, t_end=5.0)],
        calib, vol_bucket="MED", bankroll=board.bankroll,
    )
    assert board.record(scores, now=now, vol="MED") == 2
    # Tape stays inside row 6000 for the whole window.
    ticks = [Tick("ETH", 3000.1, now + i * 0.5, source="test") for i in range(10)]
    out = board.settle(ticks, now=now + 6.0)
    assert out["graded"] == 2
    assert out["touched"] == 1                 # 6000 touched, 6004 not
    assert calib.observed == 2


def test_a_window_with_no_tape_is_discarded_not_scored_as_a_miss():
    """A dropped price feed must not teach the model that the grid is unreachable."""
    board, calib = _board()
    now = 1000.0
    scores = Policy().score([_cell(0.5, 2.0, cy=6000)], calib, vol_bucket="MED",
                            bankroll=board.bankroll)
    board.record(scores, now=now, vol="MED")
    out = board.settle([], now=now + 6.0)      # feed outage
    assert out["graded"] == 0
    assert out["dropped"] == 1
    assert calib.observed == 0
    assert board.dropped_windows == 1


def test_each_horizon_band_gets_its_own_label():
    board, calib = _board()
    now = 1000.0
    near = Policy().score([_cell(0.5, 2.0, cy=6000, t_start=0.0, t_end=5.0)],
                          calib, vol_bucket="MED", bankroll=board.bankroll)
    far = Policy().score([_cell(0.2, 9.0, cy=6000, t_start=25.0, t_end=30.0, forward=6)],
                         calib, vol_bucket="MED", bankroll=board.bankroll)
    assert board.record(near, now=now, vol="MED") == 1
    assert board.record(far, now=now, vol="MED") == 1        # same cell, new horizon
    assert board.record(near, now=now, vol="MED") == 0       # already booked


def test_a_settled_tap_moves_the_bankroll():
    board, calib = _board()
    _train(calib, 0.90, 1200)
    now = 1000.0
    scores = Policy().score([_cell(0.90, 4.0, cy=6000)], calib, vol_bucket="MED",
                            bankroll=board.bankroll)
    tap = board.register_tap(scores[0], now=now)
    assert tap is not None
    start = board.bankroll.balance
    ticks = [Tick("ETH", 3000.1, now + i * 0.5, source="test") for i in range(10)]
    board.settle(ticks, now=now + 6.0)
    assert board.bankroll.balance > start          # touched a 4x
    assert board.bankroll.wins == 1
    assert board.stats()["recent_taps"][-1]["outcome"] == "win"


def test_board_stats_expose_touch_rate_and_pending_depth():
    board, calib = _board()
    scores = Policy().score([_cell(0.5, 2.0, cy=6000)], calib, vol_bucket="MED",
                            bankroll=board.bankroll)
    board.record(scores, now=1000.0, vol="MED")
    stats = board.stats()
    assert stats["pending"] == 1
    assert stats["graded_cells"] == 0
    assert stats["touch_rate"] is None


# --- out of money is not out of edge --------------------------------------
def _funded_cell(mult=1.27):
    from src.analytics.forecast import CellForecast
    return CellForecast(
        cell_x=3, cell_y=0, forward=3, row_offset=0, side="at-price", distance=0,
        lo=99.9, hi=100.4, t_start=23.0, t_end=28.0, p_touch=0.85,
        edge_cells=0.0, sd_cells=1.0, multiplier=mult, breakeven=1.0 / mult,
    )


def test_an_empty_roll_reports_no_funds_not_thin():
    """QA'd on the live overlay: a cell carrying +6.2% EV on the lower bound
    with a Kelly fraction of 0.23 was staked at zero and shown to the user as
    "NO TRADE - 0 edge". The roll was empty. That reads as "the market has
    nothing", which is a different fact and the wrong one to act on."""
    from src.analytics.calibration import Calibrator
    from src.analytics.policy import Bankroll, Policy

    cal = Calibrator()
    cal.observe_many([(0.85, 28.0, "MED", i % 10 != 0) for i in range(600)])
    broke = Bankroll(start=100.0, balance=0.0)
    assert broke.ruined is True
    scored = Policy().score_cell(_funded_cell(), cal, vol_bucket="MED", bankroll=broke)
    assert scored.verdict == "no-funds"
    assert scored.ev_lcb > 0, "the edge was real; only the money was missing"
    assert scored.stake == 0.0


def test_a_funded_roll_still_taps_the_same_cell():
    from src.analytics.calibration import Calibrator
    from src.analytics.policy import Bankroll, Policy

    cal = Calibrator()
    cal.observe_many([(0.85, 28.0, "MED", i % 10 != 0) for i in range(600)])
    rich = Policy().score_cell(_funded_cell(), cal, vol_bucket="MED",
                               bankroll=Bankroll(start=100.0, balance=100.0))
    assert rich.verdict == "tap"
    assert rich.stake > 0


def test_a_genuinely_tiny_edge_is_still_thin_not_no_funds():
    """The two must not blur: a funded roll declining a marginal cell is a
    different statement from a broke one declining a good cell."""
    from src.analytics.calibration import Calibrator
    from src.analytics.policy import Bankroll, Policy

    cal = Calibrator()
    cal.observe_many([(0.85, 28.0, "MED", i % 10 != 0) for i in range(600)])
    out = Policy(min_stake=1e9).score_cell(
        _funded_cell(), cal, vol_bucket="MED",
        bankroll=Bankroll(start=100.0, balance=100.0))
    assert out.verdict == "thin"
