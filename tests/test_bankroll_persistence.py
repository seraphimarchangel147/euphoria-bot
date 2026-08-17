"""The equity curve must survive a restart. No network.

The track record is the only evidence that any of this works. Holding it in
memory means every restart erases exactly the history you need to read before
risking real money.
"""
import json
from pathlib import Path

from src.analytics.policy import Bankroll
from src.control.room import ControlRoom


def _room(tmp_path: Path, **kw) -> ControlRoom:
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    kw.setdefault("session_path", tmp_path / "s.json")
    kw.setdefault("token_path", tmp_path / "t.json")
    kw.setdefault("calibration_path", tmp_path / "c.json")
    kw.setdefault("bankroll_path", tmp_path / "bank.json")
    return ControlRoom(**kw)


# --- the dataclass round-trip --------------------------------------------
def test_bankroll_survives_a_json_round_trip():
    roll = Bankroll(start=87.7, balance=87.7, peak=87.7)
    roll.settle(2.0, 1.5, won=True)
    roll.settle(1.0, 13.0, won=False)
    again = Bankroll.from_json(roll.to_json())
    assert again.start == roll.start
    assert again.balance == roll.balance
    assert again.wins == 1 and again.losses == 1
    assert again.staked == roll.staked
    assert again.peak == roll.peak
    assert again.max_drawdown == roll.max_drawdown
    assert len(again.equity) == len(roll.equity)


def test_a_live_synced_flag_is_not_lost():
    roll = Bankroll()
    roll.sync_live(87.735)
    again = Bankroll.from_json(roll.to_json())
    assert again.live_synced is True
    assert again.start == 87.735


def test_corrupt_or_missing_state_falls_back_to_a_fresh_roll():
    for junk in (None, "nope", [], {"balance": "abc"}, {"start": float("nan")}):
        roll = Bankroll.from_json(junk, default_start=50.0)
        assert roll.balance > 0
        assert roll.start > 0
        assert roll.equity            # always has at least one point


def test_peak_never_reads_below_the_restored_balance():
    roll = Bankroll.from_json({"start": 100.0, "balance": 140.0, "peak": 90.0})
    assert roll.peak >= roll.balance


# --- through the control room --------------------------------------------
def test_the_room_reloads_its_equity_curve_after_a_restart(tmp_path):
    room = _room(tmp_path)
    room.bankroll.sync_live(87.735)
    room.bankroll.settle(3.0, 1.44, won=True)
    room.bankroll.settle(2.0, 13.0, won=False)
    expected = room.bankroll.balance
    room.save_bankroll()
    room.close()

    revived = _room(tmp_path)
    assert revived.bankroll.balance == expected
    assert revived.bankroll.wins == 1
    assert revived.bankroll.losses == 1
    assert revived.bankroll.live_synced is True
    assert revived.bankroll.start == 87.735       # not re-based on restart
    revived.close()


def test_the_board_shares_the_restored_bankroll(tmp_path):
    """A reloaded roll must be the same object the board settles taps into."""
    room = _room(tmp_path)
    room.bankroll.settle(1.0, 2.0, won=True)
    room.save_bankroll()
    room.close()

    revived = _room(tmp_path)
    assert revived.board.bankroll is revived.bankroll
    before = revived.bankroll.balance
    revived.board.bankroll.settle(1.0, 3.0, won=True)
    assert revived.bankroll.balance == before + 2.0
    revived.close()


def test_closing_the_room_flushes_the_ledger(tmp_path):
    room = _room(tmp_path)
    room.bankroll.settle(5.0, 2.0, won=True)
    room.close()                                   # no explicit save
    saved = json.loads((tmp_path / "bank.json").read_text())
    assert saved["bankroll"]["wins"] == 1
    assert saved["bankroll"]["balance"] == room.bankroll.balance


def test_a_learning_disabled_room_writes_nothing(tmp_path):
    room = _room(tmp_path, learn=False, bankroll_path=None)
    room.bankroll.settle(1.0, 2.0, won=True)
    room.save_bankroll()
    room.close()
    assert not (tmp_path / "bank.json").exists()


def test_settled_taps_are_recorded_alongside_the_balance(tmp_path):
    room = _room(tmp_path)
    room.board.taps.append({"ts": 1.0, "cell_x": 5, "cell_y": 9, "outcome": "win",
                            "stake": 2.0, "delta": 1.0, "multiplier": 1.5})
    room.save_bankroll()
    saved = json.loads((tmp_path / "bank.json").read_text())
    assert saved["taps"][-1]["outcome"] == "win"
    room.close()


# --- ruin is a floor, and it must announce itself -------------------------
def test_a_bankroll_cannot_go_negative():
    """You cannot stake what you do not have. Letting the balance go under did
    its damage to the measurements rather than the book: `score_cell` sizes
    from `balance`, so a negative roll made every stake compute to zero, every
    tappable cell silently became "thin", and the shadow stopped trading. A
    50-minute replication window booked 154 trades instead of 1265 and read as
    a quiet market. It was a bankrupt one."""
    from src.analytics.policy import Bankroll
    roll = Bankroll(start=10.0, balance=10.0)
    for _ in range(50):
        roll.settle(1.0, 3.0, False)
    assert roll.balance == 0.0
    assert roll.pnl == -10.0


def test_ruin_is_reported_rather_than_looking_like_a_quiet_market():
    from src.analytics.policy import Bankroll
    roll = Bankroll(start=1.0, balance=1.0)
    assert roll.ruined is False
    roll.settle(1.0, 2.0, False)
    assert roll.ruined is True
    assert roll.to_dict()["ruined"] is True


def test_a_win_still_pays_from_the_floor():
    from src.analytics.policy import Bankroll
    roll = Bankroll(start=5.0, balance=5.0)
    roll.settle(5.0, 2.0, True)
    assert roll.balance == 5.0 + 5.0
