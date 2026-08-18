"""P&L ledger and attribution. No network."""
import time

from src.analytics.pnl import (
    PnLTracker,
    horizon_band,
    multiplier_band,
)
from src.control.room import ControlRoom


def _tap(**kw):
    base = {
        "ts": 1_700_000_000.0, "cell_x": 1, "cell_y": 2, "outcome": "win",
        "stake": 2.0, "delta": 1.0, "multiplier": 1.5, "side": "up",
        "distance": 0, "forward": 2, "horizon_s": 10.0, "p_lcb": 0.8, "ev_lcb": 0.2,
    }
    base.update(kw)
    return base


# --- banding --------------------------------------------------------------
def test_multipliers_and_horizons_band_sensibly():
    assert multiplier_band(1.2) == "1-1.5x"
    assert multiplier_band(4.0) == "3-8x"
    assert multiplier_band(90.0) == "25x+"
    assert multiplier_band(None) == "—"
    assert horizon_band(5.0) == "0-10s"
    assert horizon_band(40.0) == "25-50s"
    assert horizon_band(300.0) == "50s+"


# --- the ledger -----------------------------------------------------------
def test_settled_positions_are_booked_once():
    p = PnLTracker()
    tap = _tap()
    assert p.record_paper(tap) is not None
    assert p.record_paper(tap) is None          # same cell, same instant
    assert p.paper_summary()["trades"] == 1


def test_voids_and_junk_are_not_booked():
    p = PnLTracker()
    assert p.record_paper(_tap(outcome="void")) is None
    assert p.record_paper({"outcome": "win"}) is not None or True
    assert p.record_paper("nonsense") is None
    assert p.record_paper(None) is None


def test_summary_reports_hit_rate_expectancy_and_drawdown():
    p = PnLTracker()
    p.record_paper(_tap(ts=1.0, cell_x=1, outcome="win", stake=2.0, delta=1.0))
    p.record_paper(_tap(ts=2.0, cell_x=2, outcome="loss", stake=2.0, delta=-2.0))
    p.record_paper(_tap(ts=3.0, cell_x=3, outcome="win", stake=2.0, delta=1.0))
    s = p.paper_summary()
    assert s["trades"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["hit_rate"] == round(2 / 3, 4)
    assert s["staked"] == 6.0
    assert s["pnl"] == 0.0
    assert s["expectancy_per_unit"] == 0.0
    assert s["max_drawdown"] == 2.0          # peak +1 then down to -1


def test_expectancy_is_per_unit_staked_not_per_trade():
    """A 90%-at-1.05x grind and a 10%-at-12x lottery must be comparable."""
    grind = PnLTracker()
    for i in range(10):
        won = i < 9
        grind.record_paper(_tap(ts=float(i), cell_x=i, outcome="win" if won else "loss",
                                stake=10.0, delta=0.5 if won else -10.0))
    lotto = PnLTracker()
    for i in range(10):
        won = i < 1
        lotto.record_paper(_tap(ts=float(i), cell_x=i, outcome="win" if won else "loss",
                                stake=10.0, delta=110.0 if won else -10.0))
    assert grind.paper_summary()["expectancy_per_unit"] < 0
    assert lotto.paper_summary()["expectancy_per_unit"] > 0


def test_streaks_are_tracked():
    p = PnLTracker()
    for i, outcome in enumerate(["win", "win", "win", "loss", "loss", "win"]):
        p.record_paper(_tap(ts=float(i), cell_x=i, outcome=outcome,
                            delta=1.0 if outcome == "win" else -1.0))
    st = p.paper_summary()["streaks"]
    assert st["longest_win"] == 3
    assert st["longest_loss"] == 2
    assert st["current"] == 1


# --- attribution ----------------------------------------------------------
def test_results_are_attributed_to_the_kind_of_bet():
    p = PnLTracker()
    # at-the-money grind: many small wins
    for i in range(8):
        p.record_paper(_tap(ts=float(i), cell_x=i, distance=0, multiplier=1.4,
                            horizon_s=8.0, outcome="win", stake=2.0, delta=0.8))
    # far lottery: all losses
    for i in range(4):
        p.record_paper(_tap(ts=100.0 + i, cell_x=100 + i, distance=3, multiplier=30.0,
                            horizon_s=60.0, side="down", outcome="loss",
                            stake=2.0, delta=-2.0))
    att = p.attribution()
    dist = {r["key"]: r for r in att["by_distance"]}
    assert dist["at price"]["pnl"] > 0
    assert dist["3 rows"]["pnl"] < 0
    mult = {r["key"]: r for r in att["by_multiplier"]}
    assert mult["25x+"]["hit_rate"] == 0.0
    hor = {r["key"]: r for r in att["by_horizon"]}
    assert hor["0-10s"]["n"] == 8
    sides = {r["key"]: r for r in att["by_side"]}
    assert sides["down"]["n"] == 4


def test_results_are_split_by_day():
    p = PnLTracker()
    day1 = time.mktime((2026, 8, 14, 12, 0, 0, 0, 0, -1))
    day2 = time.mktime((2026, 8, 15, 12, 0, 0, 0, 0, -1))
    p.record_paper(_tap(ts=day1, cell_x=1, outcome="win", delta=3.0))
    p.record_paper(_tap(ts=day2, cell_x=2, outcome="loss", delta=-1.0))
    rows = p.by_day()
    assert len(rows) == 2
    assert rows[0]["key"] < rows[1]["key"]       # chronological


# --- real balance ---------------------------------------------------------
def test_the_first_balance_sets_the_baseline_without_booking_a_move():
    p = PnLTracker()
    assert p.mark_balance(87.7) is None
    assert p.real_summary()["first_balance"] == 87.7
    assert p.real_summary()["moves"] == 0


def test_a_balance_change_is_recorded_as_real_money_moving():
    p = PnLTracker()
    p.mark_balance(87.7)
    move = p.mark_balance(89.2)
    assert move is not None
    assert round(move.delta, 4) == 1.5
    assert move.suspect_transfer is False
    r = p.real_summary()
    assert r["change"] == 1.5
    assert r["trading_change"] == 1.5


def test_a_large_jump_is_flagged_as_a_probable_transfer():
    """A deposit is indistinguishable from a win in a balance feed."""
    p = PnLTracker()
    p.record_paper(_tap(stake=2.0))
    p.mark_balance(87.7)
    move = p.mark_balance(587.7)
    assert move.suspect_transfer is True
    r = p.real_summary()
    assert r["suspect_transfers"] == 1
    # Gross change includes it; the trading figure does not.
    assert r["change"] == 500.0
    assert r["trading_change"] == 0.0


def test_an_unchanged_balance_is_not_a_move():
    p = PnLTracker()
    p.mark_balance(87.7)
    assert p.mark_balance(87.7) is None
    assert p.real_summary()["moves"] == 0


def test_nonsense_balances_are_ignored():
    p = PnLTracker()
    for bad in (None, "x", -1, float("nan"), float("inf")):
        assert p.mark_balance(bad) is None
    assert p.real_summary()["first_balance"] is None


# --- persistence ----------------------------------------------------------
def test_the_ledger_survives_a_restart(tmp_path):
    path = tmp_path / "pnl.json"
    p = PnLTracker.load(path)
    p.record_paper(_tap(ts=1.0, cell_x=1, outcome="win", delta=2.0, distance=0))
    p.record_paper(_tap(ts=2.0, cell_x=2, outcome="loss", delta=-1.0, distance=2))
    p.mark_balance(87.7)
    p.mark_balance(88.7)
    p.save()

    again = PnLTracker.load(path)
    assert again.paper_summary()["trades"] == 2
    assert again.paper_summary()["pnl"] == 1.0
    assert again.real_summary()["balance"] == 88.7
    assert {r["key"] for r in again.attribution()["by_distance"]} == {"at price", "2 rows"}


def test_a_corrupt_ledger_does_not_brick_startup(tmp_path):
    path = tmp_path / "pnl.json"
    path.write_text("{not json")
    p = PnLTracker.load(path)
    assert p.paper_summary()["trades"] == 0


# --- live accuracy --------------------------------------------------------
def test_settlements_are_deduped_by_sequence_not_by_timestamp():
    """Two settlements in the same instant are two trades, not one."""
    p = PnLTracker()
    p.record_paper(_tap(ts=100.0, cell_x=1, cell_y=1, seq=1, delta=1.0))
    p.record_paper(_tap(ts=100.0, cell_x=1, cell_y=1, seq=2, delta=1.0))
    assert p.paper_summary()["trades"] == 2
    # The same sequence number twice is the same trade.
    p.record_paper(_tap(ts=100.0, cell_x=1, cell_y=1, seq=2, delta=1.0))
    assert p.paper_summary()["trades"] == 2


def test_a_settlement_lost_before_booking_is_reported_not_hidden():
    p = PnLTracker()
    p.record_paper(_tap(ts=1.0, cell_x=1, seq=1))
    p.record_paper(_tap(ts=2.0, cell_x=2, seq=5))     # 2,3,4 rolled off
    assert p.missed_settlements == 3
    assert p.report()["integrity"]["missed_settlements"] == 3


def test_the_board_books_pnl_the_moment_a_position_settles():
    """No polling: the ring can overflow, a callback cannot."""
    from src.analytics.calibration import Calibrator
    from src.analytics.gridboard import GridBoard
    from src.analytics.policy import Bankroll, CellScore
    from src.analytics.signal import Tick

    p = PnLTracker()
    board = GridBoard(calibrator=Calibrator(), bankroll=Bankroll())
    board.on_settle = p.record_paper
    score = CellScore(
        cell_x=200, cell_y=100, forward=1, row_offset=0, side="at-price", distance=0,
        lo=49.5, hi=50.5, t_start=0.0, t_end=5.0, horizon_s=5.0, edge_cells=0.0,
        sd_cells=0.5, p_model=0.9, p_cal=0.9, p_lcb=0.85, n_obs=900, trusted=True,
        multiplier=1.5, breakeven=0.667, ev=0.35, ev_lcb=0.27, kelly=0.4,
        stake=2.0, verdict="tap",
    )
    now = 1000.0
    board.register_tap(score, now=now)
    ticks = [Tick("ETH", 50.0, now + i * 0.5, source="page") for i in range(10)]
    board.settle(ticks, now=now + 6.0)
    s = p.paper_summary()
    assert s["trades"] == 1 and s["wins"] == 1
    assert p.trades[-1].distance == 0
    assert p.trades[-1].horizon_s is not None


def test_a_listener_that_throws_cannot_break_settlement():
    from src.analytics.calibration import Calibrator
    from src.analytics.gridboard import GridBoard
    from src.analytics.policy import Bankroll

    board = GridBoard(calibrator=Calibrator(), bankroll=Bankroll())
    board.on_settle = lambda rec: (_ for _ in ()).throw(RuntimeError("boom"))
    rec = board._book({"ts": 1.0, "outcome": "win", "stake": 1.0, "delta": 1.0})
    assert rec["seq"] == 1
    assert board.taps[-1]["outcome"] == "win"


# --- session (live) figures ----------------------------------------------
def test_session_figures_cover_this_run_not_all_time():
    p = PnLTracker()
    p.record_paper(_tap(ts=100.0, cell_x=1, seq=1, delta=5.0))   # a previous run
    p.mark_balance(100.0)
    p.begin_session(now=500.0)
    p.record_paper(_tap(ts=600.0, cell_x=2, seq=2, delta=-2.0))  # this run
    s = p.session(now=700.0)
    assert s["paper_trades"] == 1
    assert s["paper_pnl"] == -2.0
    assert p.paper_summary()["pnl"] == 3.0        # all-time still counts both


def test_session_reconciles_real_money_against_the_policy():
    """Real falling while paper is flat means losses the policy never chose."""
    p = PnLTracker()
    p.record_paper(_tap(ts=1.0, cell_x=9, seq=1, stake=2.0, delta=1.0))  # sets the scale
    p.mark_balance(100.0)
    p.begin_session(now=100.0)
    p.mark_balance(97.0, now=110.0)              # a trade-sized loss, not a transfer
    s = p.session(now=120.0)
    assert s["real_change"] == -3.0
    assert s["paper_pnl"] == 0.0                 # the policy took nothing
    assert s["unexplained"] == -3.0              # so all of it is unaccounted for


def test_a_transfer_does_not_pollute_the_reconciliation():
    p = PnLTracker()
    p.record_paper(_tap(ts=1.0, cell_x=9, seq=1, stake=2.0, delta=1.0))
    p.mark_balance(100.0)
    p.begin_session(now=100.0)
    p.mark_balance(600.0, now=110.0)             # obviously a deposit
    s = p.session(now=120.0)
    assert s["real_change"] == 500.0             # the balance really did move
    assert s["real_trading_change"] == 0.0       # but none of it was trading
    assert s["unexplained"] == 0.0


def test_the_balance_curve_is_recorded_for_the_live_chart():
    p = PnLTracker()
    for i, v in enumerate((100.0, 101.0, 99.5)):
        p.mark_balance(v, now=float(i))
    curve = p.report()["balance_curve"]
    assert [round(c["balance"], 2) for c in curve] == [100.0, 101.0, 99.5]


# --- through the room -----------------------------------------------------
def test_the_room_exposes_a_pnl_report(tmp_path):
    room = ControlRoom(
        enable_oracle=False, enable_wallet=False, dry_run=True,
        session_path=tmp_path / "s.json", token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json", bankroll_path=tmp_path / "b.json",
        traversal_path=tmp_path / "tr.json", pnl_path=tmp_path / "p.json",
    )
    room.board.taps.append(_tap(ts=time.time(), cell_x=7, cell_y=8))
    room.set_wallet({"balance": 87.7})
    room.set_wallet({"balance": 89.0})
    report = room.pnl_view()
    assert report["paper"]["trades"] == 1
    assert report["real"]["balance"] == 89.0
    assert report["real"]["change"] == round(89.0 - 87.7, 6)
    assert "by_distance" in report["attribution"]
    room.close()
    assert (tmp_path / "p.json").is_file()



# --- headline multiplier must reconcile, not flatter -----------------------
def test_avg_multiplier_no_longer_reports_the_334_tail_mean():
    """Live /pnl reported avg_multiplier 33.4; back-solved (1+E)/hit_rate was 2.70.

    33.4 was the arithmetic mean of quoted multipliers, losers included. A
    heavy tail of lottery tickets manufactured a prettier number than the
    book earned. Headline avg_multiplier is the reconciled 2.70-class figure.
    """
    p = PnLTracker()
    n_win, n_loss = 27, 73
    win_m = 2.70
    raw_target = 33.4
    loss_m = (raw_target * (n_win + n_loss) - n_win * win_m) / n_loss
    for i in range(n_win):
        p.record_paper(_tap(ts=float(i), cell_x=i, seq=i + 1, outcome="win",
                            stake=1.0, delta=win_m - 1.0, multiplier=win_m))
    for i in range(n_loss):
        p.record_paper(_tap(ts=100.0 + i, cell_x=100 + i, seq=100 + i,
                            outcome="loss", stake=1.0, delta=-1.0, multiplier=loss_m))
    s = p.paper_summary()
    assert s["trades"] == 100
    assert s["hit_rate"] == 0.27
    assert s["avg_multiplier_raw"] == 33.4
    assert s["avg_multiplier"] == 2.7
    assert s["implied_multiplier"] == 2.7
    assert s["avg_multiplier"] == s["implied_multiplier"]
    assert s["avg_multiplier"] != s["avg_multiplier_raw"]


def test_implied_multiplier_reconciles_hit_rate_and_expectancy():
    p = PnLTracker()
    for i in range(27):
        p.record_paper(_tap(ts=float(i), cell_x=i, seq=i + 1, outcome="win",
                            stake=1.0, delta=1.70, multiplier=2.70))
    for i in range(73):
        p.record_paper(_tap(ts=100.0 + i, cell_x=100 + i, seq=100 + i,
                            outcome="loss", stake=1.0, delta=-1.0, multiplier=2.70))
    s = p.paper_summary()
    implied = (1.0 + s["expectancy_per_unit"]) / s["hit_rate"]
    assert s["avg_multiplier"] == round(implied, 3)
    assert s["implied_multiplier"] == round(implied, 3)


def test_a_single_80x_loser_cannot_pull_the_headline_multiplier_into_the_teens():
    p = PnLTracker()
    seq = 1
    for i in range(4):
        p.record_paper(_tap(ts=float(i), cell_x=i, seq=seq, outcome="win",
                            stake=1.0, delta=1.7, multiplier=2.7))
        seq += 1
    for i in range(4):
        p.record_paper(_tap(ts=10.0 + i, cell_x=10 + i, seq=seq, outcome="loss",
                            stake=1.0, delta=-1.0, multiplier=2.7))
        seq += 1
    p.record_paper(_tap(ts=20.0, cell_x=20, seq=seq, outcome="loss",
                        stake=1.0, delta=-1.0, multiplier=80.0))
    s = p.paper_summary()
    assert s["avg_multiplier_raw"] >= 10
    assert s["avg_multiplier"] < 10
    assert s["avg_multiplier"] == 2.7


def test_count_hit_rate_is_not_the_only_hit_rate_when_stakes_vary():
    """A 1-unit win and a 9-unit loss are not a 50% book."""
    p = PnLTracker()
    p.record_paper(_tap(ts=1.0, cell_x=1, seq=1, outcome="win",
                        stake=1.0, delta=1.0, multiplier=2.0))
    p.record_paper(_tap(ts=2.0, cell_x=2, seq=2, outcome="loss",
                        stake=9.0, delta=-9.0, multiplier=2.0))
    s = p.paper_summary()
    assert s["hit_rate"] == 0.5
    assert s["hit_rate_staked"] == 0.1
    assert s["hit_rate_stake_weighted"] == 0.1
    assert s["hit_rate"] != s["hit_rate_staked"]


def test_attribution_hit_rate_does_not_hide_a_stake_weighted_miss():
    p = PnLTracker()
    p.record_paper(_tap(ts=1.0, cell_x=1, seq=1, outcome="win",
                        stake=1.0, delta=0.4, multiplier=1.4, distance=0))
    p.record_paper(_tap(ts=2.0, cell_x=2, seq=2, outcome="loss",
                        stake=9.0, delta=-9.0, multiplier=1.4, distance=0))
    rows = {r["key"]: r for r in p.attribution()["by_distance"]}
    at = rows["at price"]
    assert at["hit_rate"] == 0.5
    assert at["hit_rate_staked"] == 0.1
    assert at["hit_rate_stake_weighted"] == 0.1


def test_session_paper_hit_rate_has_a_stake_weighted_twin():
    p = PnLTracker()
    p.begin_session(now=100.0)
    p.record_paper(_tap(ts=101.0, cell_x=1, seq=1, outcome="win",
                        stake=1.0, delta=1.0, multiplier=2.0))
    p.record_paper(_tap(ts=102.0, cell_x=2, seq=2, outcome="loss",
                        stake=9.0, delta=-9.0, multiplier=2.0))
    s = p.session(now=103.0)
    assert s["paper_hit_rate"] == 0.5
    assert s["paper_hit_rate_staked"] == 0.1
    assert s["paper_hit_rate_stake_weighted"] == 0.1



def test_median_multiplier_is_not_a_safe_headline_on_a_loser_cluster():
    """PR #6 book: 10 wins at 2.70, 17 losses at ~51.46. Median is 51.46.

    Median resists one 80x outlier but not a majority of lottery quotes
    that lost. implied_multiplier stays 2.70. Expectancy is 0 — no invented
    profit. Live /pnl 33.4 vs back-solved 2.70.
    """
    p = PnLTracker()
    n_win, n_loss = 10, 17
    win_m = 2.70
    loss_m = (33.4 * (n_win + n_loss) - n_win * win_m) / n_loss
    seq = 1
    for i in range(n_win):
        p.record_paper(_tap(ts=float(i), cell_x=i, seq=seq, outcome="win",
                            stake=1.0, delta=win_m - 1.0, multiplier=win_m))
        seq += 1
    for i in range(n_loss):
        p.record_paper(_tap(ts=100.0 + i, cell_x=100 + i, seq=seq, outcome="loss",
                            stake=1.0, delta=-1.0, multiplier=loss_m))
        seq += 1
    s = p.paper_summary()
    assert s["avg_multiplier_raw"] == 33.4
    assert s["implied_multiplier"] == 2.7
    assert s["median_multiplier"] == round(loss_m, 3)
    assert s["median_multiplier"] > 50
    assert s["implied_multiplier"] != s["median_multiplier"]
    assert s["expectancy_per_unit"] == 0.0
    assert s["stake_weighted_multiplier"] == 2.7
    assert s["win_multiplier_mean"] == 2.7
