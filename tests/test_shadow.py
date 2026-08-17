"""Shadow trading: the policy plays alongside, risking nothing. No network."""
import time

from src.analytics.signal import Tick
from src.control.room import SHADOW_STAKE, ControlRoom


def _room(tmp_path, **kw):
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, stem in (("session_path", "s"), ("token_path", "t"), ("calibration_path", "c"),
                       ("bankroll_path", "b"), ("traversal_path", "tr"), ("pnl_path", "p"),
                       ("player_path", "pl"), ("player_calibration_path", "pc")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


def _lively(now, *, px=1000.0, n=120, step=0.4, amp=0.22):
    return [Tick("ETH", px + (amp if i % 2 else -amp), now - (n - i) * step, source="page")
            for i in range(n)]


def _grid(now, price, *, dpl=0.5, cells=None):
    return {
        "now_ms": now * 1000.0, "square_duration": 5000,
        "dollars_per_line": dpl, "cell_height": dpl, "price": price,
        "authoritative": True, "multiplier_source": "nats:quotes",
        "cells": cells if cells is not None else [],
    }


def _quoted(now, price, dpl=0.5):
    """A small quoted board around the current row."""
    cur_x = int((now * 1000.0) // 5000)
    cur_y = int(price // dpl)
    out = []
    for fwd in (1, 2, 3):
        for dy, mult in ((0, 1.05), (1, 6.0), (-1, 6.2), (2, 40.0)):
            out.append({
                "cell_x": cur_x + fwd, "cell_y": cur_y + dy, "forward": fwd,
                "row_offset": dy, "multiplier": mult,
                "break_even_probability": round(1 / mult, 6),
            })
    return out


# --- the shadow book exists and is separate -------------------------------
def test_the_shadow_book_starts_separate_from_the_paper_roll(tmp_path):
    room = _room(tmp_path)
    assert room.shadow_bankroll is not room.bankroll
    assert room.shadow_board is not room.board
    # And it must not teach the free-label calibrator twice.
    assert room.shadow_board.calibrator is None
    room.close()


def test_shadow_takes_a_position_the_disciplined_policy_refuses(tmp_path):
    """Everything is `unproven` on a cold calibrator; shadow trades anyway."""
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    room.set_grid(_grid(now, price, cells=_quoted(now, price)))
    room.running = True
    room.think(now=now)
    assert room.board.stats()["open_taps"] == 0, "disciplined policy should abstain"
    assert room.shadow_board.stats()["open_taps"] >= 1, "shadow should still play"
    room.close()


def test_shadow_uses_a_flat_stake_so_the_record_reads_as_skill(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    room.set_grid(_grid(now, price, cells=_quoted(now, price)))
    room.running = True
    room.think(now=now)
    for tap in room.shadow_board.open_taps:
        assert tap.stake == SHADOW_STAKE
    room.close()


def test_shadow_never_touches_the_real_or_paper_ledgers(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    room.set_grid(_grid(now, price, cells=_quoted(now, price)))
    room.running = True
    paper_before = room.bankroll.balance
    labels_before = room.calibrator.observed
    room.think(now=now)
    # Settle everything a minute later.
    for i in range(40):
        room.ticks.push("ETH", price + 0.05, now + 20 + i * 0.5, source="page")
    room.think(now=now + 60.0)
    assert room.bankroll.balance == paper_before
    assert room.bankroll.trades == 0
    assert room.pnl.paper_summary()["trades"] == 0
    # The grid's own free labels still accrue; the shadow adds none of its own.
    assert room.calibrator.observed >= labels_before
    room.close()


def test_a_settled_shadow_trade_moves_only_the_shadow_roll(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    room.set_grid(_grid(now, price, cells=_quoted(now, price)))
    room.running = True
    room.think(now=now)
    opened = room.shadow_board.stats()["open_taps"]
    assert opened >= 1
    # Tape must cover the tap's own window, or it settles as void rather than
    # win/loss -- the same guard that stops a feed dropout being read as a miss.
    for i in range(240):
        room.ticks.push("ETH", price + 0.02, now + i * 0.4, source="page")
    room.think(now=now + 90.0)
    assert room.shadow_bankroll.trades >= 1
    assert room.bankroll.trades == 0
    room.close()


def test_shadow_trades_even_when_every_cell_is_negative_ev(tmp_path):
    """A silent shadow measures nothing.

    Against a uniform house overround the model rates everything negative, so
    demanding a positive edge kept the book empty exactly when it would have
    been most informative. Taking the top-ranked cell regardless turns it into
    a test of the ranking: noise loses about the overround, skill loses less.
    """
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    cur_x = int((now * 1000.0) // 5000)
    cur_y = int(price // 0.5)
    # Deliberately mean odds: every cell is a losing proposition.
    room.set_grid(_grid(now, price, cells=[
        {"cell_x": cur_x + 1, "cell_y": cur_y, "forward": 1, "row_offset": 0, "multiplier": 1.02},
        {"cell_x": cur_x + 2, "cell_y": cur_y + 1, "forward": 2, "row_offset": 1, "multiplier": 3.0},
    ]))
    room.running = True
    room.think(now=now)
    assert room.shadow_board.stats()["open_taps"] >= 1
    room.close()


def test_the_view_states_the_baseline_it_is_measured_against(tmp_path):
    room = _room(tmp_path)
    view = room.shadow_view()
    assert view["house_overround_baseline"] < 0
    assert "per_unit" in " ".join(view["compare"].keys())
    room.close()


def test_shadow_will_not_take_an_unquoted_or_even_money_cell(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    cur_x = int((now * 1000.0) // 5000)
    cur_y = int(price // 0.5)
    room.set_grid(_grid(now, price, cells=[
        {"cell_x": cur_x + 1, "cell_y": cur_y, "forward": 1, "row_offset": 0,
         "multiplier": 1.0},          # pays nothing over the stake
    ]))
    room.running = True
    room.think(now=now)
    assert room.shadow_board.stats()["open_taps"] == 0
    room.close()


def test_shadow_stands_down_when_the_room_is_stopped(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _lively(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="page")
    price = room.ticks.latest("ETH").price
    room.set_grid(_grid(now, price, cells=_quoted(now, price)))
    room.running = False
    room.think(now=now)
    assert room.shadow_board.stats()["open_taps"] == 0
    room.close()


# --- the comparison -------------------------------------------------------
def test_the_view_compares_all_three_books(tmp_path):
    room = _room(tmp_path)
    room.pnl.mark_balance(100.0)
    room.pnl.begin_session()
    view = room.shadow_view()
    assert {"you_real", "policy_paper", "shadow_paper", "shadow_trades",
            "shadow_hit_rate", "shadow_per_unit"} <= set(view["compare"])
    assert view["stake"] == SHADOW_STAKE
    assert "exploratory" in view["note"]
    room.close()


def test_the_comparison_reflects_real_balance_moves(tmp_path):
    room = _room(tmp_path)
    room.set_wallet({"balance": 100.0})
    room.pnl.begin_session()
    room.pnl.record_paper({"ts": time.time(), "outcome": "win", "stake": 1.0,
                           "delta": 0.5, "seq": 1, "cell_x": 1, "cell_y": 1})
    room.set_wallet({"balance": 98.0})
    c = room.shadow_view()["compare"]
    assert c["you_real"] == -2.0
    assert c["policy_paper"] == 0.5
    room.close()
