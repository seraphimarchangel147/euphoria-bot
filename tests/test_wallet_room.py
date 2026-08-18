"""The control room's handling of a live in-game balance. No network."""
from pathlib import Path

from src.control.room import ControlRoom


def _room(tmp_path: Path) -> ControlRoom:
    return ControlRoom(
        enable_oracle=False,
        enable_wallet=False,
        dry_run=True,
        session_path=tmp_path / "s.json",
        token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
    )


def test_a_reported_balance_rebases_the_paper_bankroll_once(tmp_path):
    room = _room(tmp_path)
    assert room.bankroll.live_synced is False
    room.set_wallet({"balance": 250.5, "field": "usdmBalance", "source": "api"})
    assert room.bankroll.live_synced is True
    assert room.bankroll.start == 250.5
    assert room.bankroll.balance == 250.5

    # A later reading must not silently rewrite the starting point, or the
    # paper P&L would reset every time the tab refreshes.
    room.set_wallet({"balance": 900.0})
    assert room.bankroll.start == 250.5
    room.close()


def test_wallet_arrives_through_the_session_post(tmp_path):
    room = _room(tmp_path)
    room.ingest_session({"wallet": {"balance": 77.25, "source": "api"}})
    view = room.wallet_view()
    assert view["page"]["balance"] == 77.25
    assert view["sizing_source"] == "page"
    assert view["sizing_balance"] == 77.25
    room.close()


def test_nonsense_balances_are_ignored(tmp_path):
    room = _room(tmp_path)
    for bad in ({"balance": "abc"}, {"balance": -5}, {"nope": 1}, "not-a-dict", None):
        room.set_wallet(bad)
    assert room.wallet_page is None
    assert room.bankroll.live_synced is False
    room.close()


def test_wallet_view_is_exposed_in_the_learning_payload(tmp_path):
    room = _room(tmp_path)
    room.set_wallet({"balance": 12.0})
    learning = room.learning_view()
    assert learning["wallet"]["page"]["balance"] == 12.0
    assert "chain" in learning["wallet"]
    room.close()


def test_account_tier_limits_tighten_the_stake_cap(tmp_path):
    room = _room(tmp_path)
    before = room.policy.max_stake
    room.set_wallet({"balance": 87.7, "limits": {
        "tier": "standard", "maxTrade": 4, "maxStakePerSquare": 50,
        "maxExposurePerColumn": 2000, "settledTrades": 44159,
    }})
    assert room.policy.max_stake == 4          # the account's cap, not ours
    assert room.policy.max_stake < before
    assert room.wallet_view()["limits"]["tier"] == "standard"
    room.close()


def test_our_own_cap_still_wins_when_it_is_tighter(tmp_path):
    room = _room(tmp_path)
    room.set_wallet({"balance": 87.7, "limits": {"tier": "vip", "maxTrade": 5000}})
    assert room.policy.max_stake <= 10         # settings.MAX_TRADE_USDM
    room.close()


def test_limits_arrive_without_a_balance(tmp_path):
    room = _room(tmp_path)
    room.set_wallet({"limits": {"tier": "standard", "maxTrade": 3}})
    assert room.policy.max_stake == 3
    assert room.wallet_page is None            # no balance was claimed
    room.close()


def test_sizing_uses_the_real_balance_not_the_placeholder(tmp_path):
    room = _room(tmp_path)
    default = room.bankroll.balance
    room.set_wallet({"balance": 8.0})
    assert room.bankroll.balance == 8.0
    assert room.bankroll.balance != default
    # 5% cap on a real 8 USDM roll is 0.40, not 5.00 off the placeholder.
    assert room.policy.max_bankroll_fraction * room.bankroll.balance == 0.4
    room.close()
