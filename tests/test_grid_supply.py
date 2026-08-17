"""The board must keep arriving. No network.

Multipliers are the only thing that turns a probability into expected value, so
losing them costs the bot everything while it still looks healthy. Two failures
did exactly that, and both are guarded here.
"""
import time
from pathlib import Path

from src.control.room import CELLS_GRACE_S, ControlRoom

ROOT = Path(__file__).resolve().parent.parent / "extension"


def _room(tmp_path, **kw):
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, stem in (("session_path", "s"), ("token_path", "t"), ("calibration_path", "c"),
                       ("bankroll_path", "b"), ("traversal_path", "tr"), ("pnl_path", "p"),
                       ("player_path", "pl"), ("player_calibration_path", "pc")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


def _cells(n=3):
    return [{"cell_x": 100 + i, "cell_y": 3757, "forward": i + 1, "row_offset": 0,
             "multiplier": 1.5 + i} for i in range(n)]


# --- the data path must not depend on the drawing path --------------------
def test_the_quote_grid_is_published_outside_the_render_loop():
    """draw() returns early when the chart canvas is missing. The grid emit
    used to sit at the bottom of it, so a rendering problem silently cut off
    every multiplier and the bot went blind to EV while looking healthy."""
    inject = (ROOT / "inject.js").read_text()
    parse_start = inject.index("function parseQuoteGrid")
    parse_end = inject.index("function onQuotes")
    parse_body = inject[parse_start:parse_end]
    assert 'emit("grid"' in parse_body, "the quote frame must publish the board itself"

    draw_start = inject.index("function draw()")
    draw_body = inject[draw_start:]
    # The render loop may still publish geometry, but only when the quote frame
    # is not already doing so — otherwise it overwrites cells with nothing.
    assert "if (!fresh &&" in draw_body


def test_the_early_return_still_precedes_the_render_emit():
    """Guard the shape of the bug: draw() bails before it can publish."""
    inject = (ROOT / "inject.js").read_text()
    draw_body = inject[inject.index("function draw()"):]
    bail = draw_body.index("if (!ensureLayer()")
    emit = draw_body.index('emit("grid", meta)')
    assert bail < emit, "draw() still returns before its own emit — data must not live here"


# --- a cell-less update must not blind the surface ------------------------
def test_a_geometry_only_update_does_not_wipe_the_quoted_cells(tmp_path):
    room = _room(tmp_path)
    room.set_grid({"now_ms": 1000.0, "square_duration": 5000,
                   "dollars_per_line": 0.5, "cells": _cells(),
                   "authoritative": True, "multiplier_source": "nats:quotes"})
    assert len(room.grid["cells"]) == 3
    # A hiccup in the quote feed sends geometry with no cells.
    room.set_grid({"now_ms": 2000.0, "square_duration": 5000, "dollars_per_line": 0.5})
    assert len(room.grid["cells"]) == 3, "cells must survive a cell-less update"
    assert room.grid["multiplier_source"] == "nats:quotes"
    assert room.grid["cells_carried_s"] is not None
    room.close()


def test_carried_cells_expire_rather_than_persisting_forever(tmp_path):
    room = _room(tmp_path)
    room.set_grid({"now_ms": 1000.0, "cells": _cells()})
    # Age the stored board past the grace window.
    room.grid["_received_local"] = time.time() - CELLS_GRACE_S - 1.0
    room.set_grid({"now_ms": 2000.0, "square_duration": 5000})
    assert not room.grid.get("cells"), "a long-dead feed must stop pretending"
    room.close()


def test_a_fresh_board_always_replaces_the_carried_one(tmp_path):
    room = _room(tmp_path)
    room.set_grid({"now_ms": 1000.0, "cells": _cells(3)})
    room.set_grid({"now_ms": 2000.0, "cells": _cells(5)})
    assert len(room.grid["cells"]) == 5
    assert "cells_carried_s" not in room.grid
    room.close()


def test_the_first_grid_is_stored_as_is(tmp_path):
    room = _room(tmp_path)
    room.set_grid({"now_ms": 1000.0, "square_duration": 5000})
    assert room.grid["now_ms"] == 1000.0
    assert not room.grid.get("cells")
    room.close()


# --- the socket must be caught however it was opened ----------------------
def test_every_socket_is_sniffed_not_just_the_ones_we_constructed():
    """The grid feed died 25 minutes into a measurement while prices kept
    flowing -- the shape of a socket reconnecting through a path that escaped
    the constructor wrap. A wrap on `window.WebSocket` only sees sockets built
    through it afterwards, so the prototype is hooked as well: every consumer
    must eventually listen for messages, one of these two ways."""
    inject = (ROOT / "inject.js").read_text()
    assert "function sniffSocket" in inject
    proto = inject[inject.index("__euphoriaSniffed"):]
    assert "addEventListener" in proto, "the listener path must be hooked"
    assert '"onmessage"' in proto, "the onmessage setter must be hooked too"
    # Both paths must route through the one decoder, or the two will drift.
    assert inject.count("sniffSocket(") >= 3


def test_a_socket_is_never_sniffed_twice():
    """Both hooks can reach the same socket. Settlement frames are read on this
    path, and a duplicated settlement once paid a single square ten times."""
    inject = (ROOT / "inject.js").read_text()
    body = inject[inject.index("function sniffSocket"):]
    body = body[:body.index("\n  }")]
    assert "__euphoriaSniffing" in body
    guard = body.index("__euphoriaSniffing")
    assert body.index("addEventListener") > guard, "the guard must precede the listener"
