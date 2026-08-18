"""The last nine column closes, by row. No network.

The surface says where price might go. This says where it has just been: which
row each settled 5-second column closed on, and whether that was a step up,
down, or a hold. Read together they separate a grid that is genuinely drifting
from one sitting on the same row printing near-certainties -- which is the state
the book has been quietly paying the vig into.
"""
import pytest


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


def _windows(room, closes, *, dpl=0.5):
    """Push settled columns straight onto the board, as settle() would."""
    room.grid = {"dollars_per_line": dpl, "cells": []}
    for i, c in enumerate(closes):
        room.board.windows.append({
            "start_ts": 1000.0 + i * 5, "end_ts": 1005.0 + i * 5,
            "cells": 40, "touched": 3, "lo": c - 0.1, "hi": c + 0.1,
            "open": c, "close": c, "ticks": 12,
        })
    return room


# --- direction ------------------------------------------------------------
def test_a_climb_reads_as_up(tmp_path):
    room = _windows(_room(tmp_path), [100.0, 100.6, 101.1, 101.7])
    v = room.closes_view()
    assert [c["dir"] for c in v["closes"]] == ["up", "up", "up"]
    assert v["up"] == 3 and v["down"] == 0
    assert v["net_rows"] == 3
    room.close()


def test_a_slide_reads_as_down(tmp_path):
    room = _windows(_room(tmp_path), [101.7, 101.1, 100.6, 100.0])
    v = room.closes_view()
    assert [c["dir"] for c in v["closes"]] == ["down", "down", "down"]
    assert v["net_rows"] == -3
    room.close()


def test_sitting_on_one_row_reads_as_held(tmp_path):
    """The case that matters: nine near-identical closes are not nine events,
    they are one row being sat on."""
    room = _windows(_room(tmp_path), [100.1, 100.2, 100.15, 100.05, 100.2])
    v = room.closes_view()
    assert v["flat"] == 4
    assert v["up"] == 0 and v["down"] == 0
    assert v["net_rows"] == 0
    room.close()


def test_chop_is_distinguishable_from_drift(tmp_path):
    """Same net movement, different tape. A strip that alternates is chop; the
    dwell line alone cannot tell them apart."""
    chop = _windows(_room(tmp_path),
                    [100.0, 100.6, 100.0, 100.6, 100.0, 100.6, 100.0])
    v = chop.closes_view()
    assert v["up"] == 3 and v["down"] == 3
    assert v["net_rows"] == 0, "six steps, nowhere"
    chop.close()


# --- shape ----------------------------------------------------------------
def test_it_returns_at_most_nine_newest_first(tmp_path):
    room = _windows(_room(tmp_path), [100.0 + i * 0.6 for i in range(30)])
    v = room.closes_view()
    assert v["n"] == 9
    assert len(v["closes"]) == 9
    ts = [c["ts"] for c in v["closes"]]
    assert ts == sorted(ts, reverse=True), "newest first"
    room.close()


def test_the_limit_is_adjustable(tmp_path):
    room = _windows(_room(tmp_path), [100.0 + i * 0.6 for i in range(30)])
    assert room.closes_view(limit=4)["n"] == 4
    room.close()


def test_each_entry_carries_its_row_and_step(tmp_path):
    room = _windows(_room(tmp_path), [100.0, 101.0])
    c = room.closes_view()["closes"][0]
    assert c["row"] == int(101.0 // 0.5)
    assert c["step"] == 2
    assert c["close"] == 101.0
    room.close()


# --- it refuses to invent -------------------------------------------------
def test_the_first_close_has_no_direction_so_it_is_not_shown(tmp_path):
    """One close is not a step. Reporting it as 'flat' would be a made-up
    observation about a comparison that has no predecessor."""
    room = _windows(_room(tmp_path), [100.0])
    assert room.closes_view()["n"] == 0
    room.close()


def test_no_grid_means_no_rows(tmp_path):
    room = _room(tmp_path)
    room.board.windows.append({"start_ts": 1.0, "end_ts": 6.0, "close": 100.0,
                               "open": 100.0, "lo": 99.0, "hi": 101.0, "ticks": 9})
    v = room.closes_view()
    assert v["dollars_per_line"] is None
    assert v["n"] == 0, "without a row height there is no row to report"
    room.close()


def test_an_empty_board_says_so(tmp_path):
    room = _room(tmp_path)
    v = room.closes_view()
    assert v["n"] == 0 and v["closes"] == []
    room.close()


def test_windows_without_a_close_are_skipped(tmp_path):
    room = _room(tmp_path)
    room.grid = {"dollars_per_line": 0.5}
    room.board.windows.append({"start_ts": 1.0, "end_ts": 6.0, "lo": 99.0, "hi": 101.0})
    room.board.windows.append({"start_ts": 6.0, "end_ts": 11.0, "close": 100.0})
    room.board.windows.append({"start_ts": 11.0, "end_ts": 16.0, "close": 100.6})
    assert room.closes_view()["n"] == 1
    room.close()


# --- through the payload --------------------------------------------------
def test_the_overlay_payload_carries_it(tmp_path):
    room = _windows(_room(tmp_path), [100.0, 100.6, 101.2])
    body = room.snapshot()["think"]
    assert "closes" in body
    assert body["closes"]["n"] == 2
    room.close()


def test_the_card_renders_the_strip():
    """The markup and the paint must agree, or the card throws on every frame
    and freezes -- which has happened here before."""
    from pathlib import Path
    js = Path("extension/content.js").read_text()
    assert 'id="ebo-closes"' in js
    assert 'id="ebo-closes-sum"' in js
    assert 'getElementById("ebo-closes")' in js
    assert 'set("ebo-closes-sum"' in js
    css = Path("extension/overlay.css").read_text()
    assert ".ebo-closes" in css
