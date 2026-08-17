"""The tape decides what is reachable, not the model. No network.

Measured over a live book: cells one row away or closer returned +0.2937 per
unit over 305 trades. Cells two rows away or further returned -1.0000 over 269
trades with **zero wins** -- not one. By multiplier the same shape: 25x and
above was zero wins in 216 trades.

Zero wins in 269 attempts is not a probability overestimated threefold, it is an
impossible bet taken repeatedly. `MIN_TAP_PROB` did not stop any of them because
it consults our own estimate, and our own estimate believed a 3.8-sigma move
inside five seconds was better than one in four.

So this guard never asks the model anything. It remembers what the tape did.
"""
import pytest

from src.analytics.reachability import (
    MIN_OBSERVATIONS,
    ReachabilityLedger,
    horizon_slot,
    wilson_upper,
)


def _feed(ledger, distance, horizon_s, *, n, hits=0):
    for i in range(n):
        ledger.observe(distance, horizon_s, i < hits)
    return ledger


# --- the bound ------------------------------------------------------------
def test_a_rate_never_observed_is_not_treated_as_zero():
    """The whole point. A normal interval collapses to zero width at hits=0
    and would wave every dead cell through as a certainty -- in the wrong
    direction."""
    up = wilson_upper(0, 269)
    assert up > 0.0
    # The rule of three: roughly 3/n for an unobserved event.
    assert 0.005 < up < 0.02


def test_the_bound_tightens_as_evidence_accumulates():
    assert wilson_upper(0, 60) > wilson_upper(0, 269) > wilson_upper(0, 2000)


def test_the_bound_sits_above_the_observed_rate():
    for hits, n in ((0, 100), (5, 100), (50, 100), (99, 100), (100, 100)):
        assert wilson_upper(hits, n) >= hits / n - 1e-12


def test_the_bound_stays_a_probability():
    for hits, n in ((0, 1), (1, 1), (0, 0), (7, 7), (3, 1000)):
        assert 0.0 <= wilson_upper(hits, n) <= 1.0


# --- it refuses to have an opinion without evidence -----------------------
def test_no_opinion_until_there_is_enough_evidence():
    """A guard that fires on thin evidence is a hardcoded cap wearing a
    confidence interval."""
    led = ReachabilityLedger()
    _feed(led, 3, 30.0, n=MIN_OBSERVATIONS - 1)
    assert led.ceiling(3, 30.0) is None
    assert led.veto(3, 30.0, 50.0) is False


def test_an_opinion_appears_once_the_evidence_does():
    led = _feed(ReachabilityLedger(), 3, 30.0, n=MIN_OBSERVATIONS)
    assert led.ceiling(3, 30.0) is not None


def test_an_unseen_bucket_is_never_vetoed():
    led = ReachabilityLedger()
    assert led.veto(9, 45.0, 100.0) is False


# --- the live case --------------------------------------------------------
def test_the_dead_far_cell_is_refused():
    """269 attempts at two rows, zero touches, quoted at 25x."""
    led = _feed(ReachabilityLedger(), 2, 30.0, n=269, hits=0)
    assert led.veto(2, 30.0, 25.0) is True
    assert led.vetoes == 1


def test_the_profitable_near_cell_is_left_alone():
    """One row in: 146 trades, 60 wins, +0.483 per unit. Do not touch it."""
    led = _feed(ReachabilityLedger(), 1, 8.0, n=146, hits=60)
    assert led.veto(1, 8.0, 3.0) is False


def test_a_thin_multiplier_is_refused_even_on_a_reachable_cell():
    """The guard is priced, not positional, and this case caught a wrong
    assumption while it was being written.

    One row out is the most profitable distance in the book, so the obvious
    expectation is "never veto it". But the observed rate there is 60/146 =
    0.411 with an upper bound near 0.49, and a 1.5x quote needs 0.667 to break
    even. Refusing it is correct: distance is not what makes a bet good, price
    is. The +0.483 the book earned at this distance came from fatter quotes.
    """
    led = _feed(ReachabilityLedger(), 1, 8.0, n=146, hits=60)
    ceil = led.ceiling(1, 8.0)
    assert 0.40 < ceil < 0.55
    assert led.veto(1, 8.0, 1.5) is True
    assert led.veto(1, 8.0, 1.0 / ceil * 1.10) is False


def test_at_the_money_is_left_alone():
    led = _feed(ReachabilityLedger(), 0, 8.0, n=159, hits=127)
    assert led.veto(0, 8.0, 1.2) is False


def test_a_fat_enough_multiplier_still_gets_through():
    """The veto is an EV statement, not a distance ban. With 269 empty trials
    the bound is about 1.4%, so anything over ~70x is still arguable -- and
    saying so is the honest reading of that evidence."""
    led = _feed(ReachabilityLedger(), 2, 30.0, n=269, hits=0)
    ceil = led.ceiling(2, 30.0)
    assert led.veto(2, 30.0, 1.0 / ceil * 1.10) is False
    assert led.veto(2, 30.0, 1.0 / ceil * 0.90) is True


def test_it_self_heals_when_the_market_wakes_up():
    """Not a hardcoded distance. If far cells start being touched the veto
    lifts on its own -- a constant would need a human and would be wrong at
    every other volatility."""
    led = _feed(ReachabilityLedger(), 3, 30.0, n=200, hits=0)
    assert led.veto(3, 30.0, 20.0) is True
    _feed(led, 3, 30.0, n=200, hits=40)
    assert led.veto(3, 30.0, 20.0) is False


# --- it can only ever subtract -------------------------------------------
def test_the_guard_can_refuse_a_bet_but_never_justify_one():
    """Everything that has cost money in this codebase did so by making the
    numbers look better. A component that can only say no cannot join that
    list. `veto` returns a bool and touches nothing else."""
    led = _feed(ReachabilityLedger(), 1, 8.0, n=500, hits=500)
    assert led.veto(1, 8.0, 100.0) is False       # never True-in-reverse
    assert isinstance(led.veto(1, 8.0, 2.0), bool)


def test_an_unquoted_cell_is_not_vetoed():
    led = _feed(ReachabilityLedger(), 4, 30.0, n=300, hits=0)
    for mult in (None, 0.0, 1.0):
        assert led.veto(4, 30.0, mult) is False


# --- keying ---------------------------------------------------------------
def test_horizon_is_bucketed_because_reach_depends_on_time():
    assert horizon_slot(3.0) == 0
    assert horizon_slot(15.0) == 1
    assert horizon_slot(40.0) == 2
    assert horizon_slot(90.0) == 3


def test_distance_is_unsigned_because_up_and_down_are_the_same_reach():
    led = ReachabilityLedger()
    _feed(led, 2, 30.0, n=40)
    _feed(led, -2, 30.0, n=40)
    assert led.evidence(2, 30.0)[1] == 80


def test_a_short_horizon_veto_does_not_bind_a_long_one():
    led = _feed(ReachabilityLedger(), 3, 3.0, n=300, hits=0)
    assert led.veto(3, 3.0, 20.0) is True
    assert led.veto(3, 90.0, 20.0) is False, "different question, no evidence"


def test_nonsense_observations_are_ignored():
    led = ReachabilityLedger()
    for bad in (("x", 5.0, True), (2, "y", True), (None, None, False)):
        led.observe(*bad)
    assert led.evidence(2, 5.0)[1] == 0


def test_malformed_rows_do_not_stop_the_batch():
    led = ReachabilityLedger()
    led.observe_many([(1, 5.0, True), "nonsense", (1, 5.0, False), None])
    assert led.evidence(1, 5.0)[1] == 2


# --- reporting ------------------------------------------------------------
def test_stats_say_what_it_would_take_to_make_the_bet_sensible():
    led = _feed(ReachabilityLedger(), 2, 30.0, n=269, hits=0)
    row = next(r for r in led.stats()["buckets"] if r["distance"] == 2)
    assert row["touched"] == 0
    assert row["n"] == 269
    assert row["needs_multiplier"] > 50


def test_thin_buckets_are_not_reported_as_findings():
    led = _feed(ReachabilityLedger(), 5, 30.0, n=MIN_OBSERVATIONS - 1)
    assert led.stats()["buckets"] == []
    assert led.stats()["tracked"] == 1


# --- through the policy ---------------------------------------------------
def _cell(distance, mult, horizon=30.0):
    from src.analytics.forecast import CellForecast
    return CellForecast(
        cell_x=3, cell_y=distance, forward=3, row_offset=distance,
        side="up" if distance >= 0 else "down", distance=abs(distance),
        lo=100.0 + distance, hi=100.5 + distance, t_start=horizon - 5.0,
        t_end=horizon, p_touch=0.40, edge_cells=float(distance),
        sd_cells=1.0, multiplier=mult, breakeven=1.0 / mult if mult else None,
    )


def test_the_policy_refuses_a_cell_the_tape_has_never_reached():
    from src.analytics.calibration import Calibrator
    from src.analytics.policy import Bankroll, Policy

    cal = Calibrator()
    # Warm the bucket so the cell would otherwise be considered on its merits.
    cal.observe_many([(0.40, 30.0, "MED", i % 2 == 0) for i in range(400)])
    led = _feed(ReachabilityLedger(), 2, 30.0, n=400, hits=0)

    guarded = Policy().score_cell(_cell(2, 25.0), cal, vol_bucket="MED",
                                  bankroll=Bankroll(), reachability=led)
    assert guarded.verdict == "untouched"
    assert guarded.stake == 0.0
    assert guarded.tappable is False

    # Same cell, no ledger: the guard is what changed the answer.
    plain = Policy().score_cell(_cell(2, 25.0), cal, vol_bucket="MED",
                                bankroll=Bankroll())
    assert plain.verdict != "untouched"


def test_the_policy_is_unchanged_where_the_tape_does_reach():
    from src.analytics.calibration import Calibrator
    from src.analytics.policy import Bankroll, Policy

    cal = Calibrator()
    cal.observe_many([(0.40, 8.0, "MED", i % 2 == 0) for i in range(400)])
    led = _feed(ReachabilityLedger(), 1, 8.0, n=400, hits=160)
    guarded = Policy().score_cell(_cell(1, 3.0, horizon=8.0), cal,
                                  vol_bucket="MED", bankroll=Bankroll(),
                                  reachability=led)
    plain = Policy().score_cell(_cell(1, 3.0, horizon=8.0), cal,
                                vol_bucket="MED", bankroll=Bankroll())
    assert guarded.verdict == plain.verdict
    assert guarded.stake == plain.stake


# --- through the room -----------------------------------------------------
def _room(tmp_path, **kw):
    from src.control.room import ControlRoom
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, stem in (("session_path", "s"), ("token_path", "t"),
                       ("calibration_path", "c"), ("bankroll_path", "b"),
                       ("traversal_path", "tr"), ("pnl_path", "p"),
                       ("player_path", "pl"), ("player_calibration_path", "pc")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


def test_the_room_feeds_the_ledger_from_the_same_free_labels(tmp_path):
    """The grading pass already knows whether each cell was touched. Asking it
    a second question costs nothing."""
    room = _room(tmp_path)
    assert room.board.reachability is room.reachability
    room.close()


def test_the_room_exposes_the_ledger(tmp_path):
    room = _room(tmp_path)
    st = room.snapshot()["think"].get("reachability")
    assert st is not None and "min_observations" in st
    room.close()


# --- the guard must survive a restart -------------------------------------
def test_the_ledger_persists_because_a_cold_guard_is_a_silent_guard(tmp_path):
    """The house anchor is biased HIGH, so it makes far cells look more
    reachable than they are, and this ledger is one of only two things in front
    of it. It also correctly refuses to have an opinion under MIN_OBSERVATIONS.
    Put those together on a cold start and every restart opens a window where
    the model is at its most eager and its guard is silent. Measured: a
    restarted room went to ruin in 20 minutes over 583 trades at 6.7%."""
    room = _room(tmp_path, reachability_path=tmp_path / "reach.json")
    _feed(room.reachability, 3, 30.0, n=300, hits=0)
    room.save_reachability()
    room.close()

    revived = _room(tmp_path, reachability_path=tmp_path / "reach.json")
    assert revived.reachability.evidence(3, 30.0) == (0.0, 300.0)
    assert revived.reachability.veto(3, 30.0, 20.0) is True, "guard is armed at boot"
    revived.close()


def test_a_corrupt_ledger_does_not_brick_startup(tmp_path):
    bad = tmp_path / "reach.json"
    bad.write_text("{not json")
    room = _room(tmp_path, reachability_path=bad)
    assert room.reachability.stats()["tracked"] == 0
    room.close()


def test_impossible_rows_are_dropped_on_restore():
    """More hits than trials, or a negative count, is corruption not evidence."""
    led = ReachabilityLedger.from_json({"counts": [
        [1, 1, 5.0, 2.0], [1, 2, -1.0, 5.0], [2, 1, 0.0, 0.0], ["x", 1, 1, 2],
        [3, 1, 4.0, 100.0],
    ]})
    assert list(led.counts) == [(3, 1)]


# --- finer evidence wins ---------------------------------------------------
class _Quotes:
    """Stand-in for the quote-keyed ledger's read surface."""
    min_observations = 200

    def __init__(self, table):
        self.table = table          # {multiplier: (hits, n)}

    def evidence(self, multiplier):
        h, n = self.table.get(round(float(multiplier), 2), (0, 0))
        return (h, n, h / n if n else 0.0)


def test_the_rail_prefers_the_rate_measured_at_that_exact_quote():
    """Measured live: the (d=0, slot 2) bucket pools to 0.8746 and so demands
    1.14x, while the 1.03x subset inside it resolved 131/131. The rail was
    refusing the best-evidenced cells on the board -- the same coarseness error
    it exists to catch, pointed the other way."""
    led = _feed(ReachabilityLedger(), 0, 30.0, n=15000, hits=13119)   # 0.8746
    assert led.veto(0, 30.0, 1.03) is True, "pooled bucket refuses it"

    led.quotes = _Quotes({1.03: (400, 400)})
    assert led.veto(0, 30.0, 1.03) is False, "its own quote says otherwise"
    assert led.stats()["quote_keyed"] is True


def test_finer_evidence_can_also_refuse_what_the_pool_would_clear():
    """Not a licence to bet. It cuts both ways or it is not evidence."""
    led = _feed(ReachabilityLedger(), 0, 30.0, n=15000, hits=14700)   # 0.98
    assert led.veto(0, 30.0, 1.05) is False
    led.quotes = _Quotes({1.05: (300, 400)})                          # 0.75
    assert led.veto(0, 30.0, 1.05) is True


def test_a_cold_quote_key_falls_back_to_the_pooled_bucket():
    led = _feed(ReachabilityLedger(), 0, 30.0, n=15000, hits=13119)
    led.quotes = _Quotes({1.03: (40, 40)})       # warm-looking but under 200
    assert led.veto(0, 30.0, 1.03) is True, "thin quote evidence must not rule"


def test_a_broken_quote_source_cannot_break_the_rail():
    class Boom:
        min_observations = 200
        def evidence(self, m):
            raise RuntimeError("ledger exploded")

    led = _feed(ReachabilityLedger(), 0, 30.0, n=15000, hits=13119)
    led.quotes = Boom()
    assert led.veto(0, 30.0, 1.03) is True, "falls back, does not raise"


def test_without_a_quote_source_nothing_changes():
    led = _feed(ReachabilityLedger(), 2, 30.0, n=269, hits=0)
    assert led.quotes is None
    assert led.veto(2, 30.0, 25.0) is True
    assert led.stats()["quote_keyed"] is False
