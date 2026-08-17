"""Arms switch in place, so both see the same tape. No network.

Two windows of the SAME configuration came in at -0.1066 and -0.5220 -- 0.35
apart, which the per-trade standard error calls a 5-sigma event. It is not one.
Every trade inside a window rides a single price path, so the effective sample
size scales with the number of windows, not the number of trades, and a
sequential A/B cannot separate a change from the hour it happened to run in.
Measured this way, a difference below about 0.5 per unit is unresolvable.

Alternating short blocks fixes the confound directly: both arms see the same
market. Switching in place rather than restarting is the point -- a restart
empties the reachability ledger and the shadow book, so it would change more
than the variable under test.
"""
import pytest

from config import settings
from src.control.room import ControlError


def _room(tmp_path, **kw):
    from src.control.room import ControlRoom
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, stem in (("session_path", "s"), ("token_path", "t"),
                       ("calibration_path", "c"), ("bankroll_path", "b"),
                       ("traversal_path", "tr"), ("pnl_path", "p"),
                       ("player_path", "pl"), ("player_calibration_path", "pc"),
                       ("reachability_path", "re")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


@pytest.fixture(autouse=True)
def _restore_anchor():
    prev = settings.USE_HOUSE_ANCHOR
    yield
    settings.USE_HOUSE_ANCHOR = prev


def test_an_arm_can_be_switched_without_restarting(tmp_path):
    room = _room(tmp_path)
    room.set_experiment("anchor_on")
    assert settings.USE_HOUSE_ANCHOR is True
    assert room.experiment_view()["arm"] == "anchor_on"
    room.set_experiment("anchor_off")
    assert settings.USE_HOUSE_ANCHOR is False
    assert room.experiment_view()["arm"] == "anchor_off"
    room.close()


def test_switching_does_not_reset_the_learned_state(tmp_path):
    """The whole reason not to restart. A restart empties the rail and the
    shadow book, changing more than the variable under test."""
    room = _room(tmp_path)
    for i in range(300):
        room.reachability.observe(2, 30.0, i < 5)
    before = room.reachability.evidence(2, 30.0)
    trades_before = room.shadow_bankroll.trades

    room.set_experiment("anchor_on")
    room.set_experiment("anchor_off")

    assert room.reachability.evidence(2, 30.0) == before
    assert room.shadow_bankroll.trades == trades_before
    room.close()


def test_an_unknown_arm_is_refused(tmp_path):
    room = _room(tmp_path)
    for bad in ("", "banana", "ANCHOR_MAYBE", None):
        with pytest.raises(ControlError):
            room.set_experiment(bad)
    room.close()


def test_arm_names_are_case_and_space_insensitive(tmp_path):
    room = _room(tmp_path)
    room.set_experiment("  Anchor_On ")
    assert room.experiment_view()["arm"] == "anchor_on"
    room.close()


def test_the_view_exposes_the_counters_a_block_is_differenced_from(tmp_path):
    """A block result is (pnl_end - pnl_start) / (staked_end - staked_start).
    The harness needs cumulative counters, not a per-window summary."""
    room = _room(tmp_path)
    v = room.experiment_view()
    for key in ("shadow_trades", "shadow_staked", "shadow_pnl", "arm", "switched_at"):
        assert key in v
    room.close()


def test_the_view_reports_the_real_setting_not_a_cached_label(tmp_path):
    """If someone flips the setting directly the view must not keep claiming
    the old arm -- that would silently mislabel every block after it."""
    room = _room(tmp_path)
    settings.USE_HOUSE_ANCHOR = True
    assert room.experiment_view()["house_anchor"] is True
    settings.USE_HOUSE_ANCHOR = False
    assert room.experiment_view()["house_anchor"] is False
    room.close()
