"""End-to-end: a quoted NATS grid must produce real expected value.

The packing is decoded in extension/inject.js; these tests pin the *contract*
that decode produces, so a change in either half fails loudly here.

Wire format (subject ``quotes.<asset>_<duration>_<interval>``):
    timeSteps * priceSteps uint16 little-endian, time-major
    index = t * priceSteps + p
    value / 100 = multiplier, 10000 = not quoted
    price step 24 of 50 is the at-the-money row
"""
import base64
import struct
from pathlib import Path

from src.analytics.calibration import Calibrator
from src.analytics.policy import Bankroll, Policy
from src.analytics.signal import Tick, compute_signal
from src.control.room import ControlRoom

SENTINEL = 10000
T_STEPS, P_STEPS = 18, 50
CENTER_P = (P_STEPS - 1) // 2          # 24


def pack_grid(quotes: dict[tuple[int, int], float]) -> str:
    """quotes[(t, p)] = multiplier -> the base64 payload the page sends."""
    vals = [SENTINEL] * (T_STEPS * P_STEPS)
    for (t, p), mult in quotes.items():
        vals[t * P_STEPS + p] = int(round(mult * 100))
    return base64.b64encode(struct.pack("<" + "H" * len(vals), *vals)).decode()


def decode_grid(b64: str, *, price: float, dpl: float, ts_ms: float, dur_s: float) -> list[dict]:
    """Reference decoder mirroring extension/inject.js parseQuoteGrid."""
    data = base64.b64decode(b64)
    vals = struct.unpack("<" + "H" * (len(data) // 2), data)
    current_x = int(ts_ms // (dur_s * 1000))
    current_y = int(price // dpl)
    cells = []
    for t in range(T_STEPS):
        for p in range(P_STEPS):
            v = vals[t * P_STEPS + p]
            if v == SENTINEL or v <= 0:
                continue
            row_offset = p - CENTER_P
            cells.append({
                "cell_x": current_x + 1 + t,
                "cell_y": current_y + row_offset,
                "forward": t + 1,
                "row_offset": row_offset,
                "side": "down" if row_offset < 0 else "up" if row_offset > 0 else "at-price",
                "distance": abs(row_offset),
                "multiplier": v / 100,
                "break_even_probability": round(100 / v, 6),
            })
    return cells


# --- the packing itself ---------------------------------------------------
def test_round_trip_preserves_multipliers_and_positions():
    quotes = {(0, 24): 1.03, (0, 23): 13.0, (0, 25): 14.1, (7, 22): 27.0}
    cells = decode_grid(
        pack_grid(quotes), price=1879.7894931, dpl=0.5,
        ts_ms=1786774316800, dur_s=5.0,
    )
    assert len(cells) == len(quotes)
    by_pos = {(c["forward"] - 1, c["row_offset"] + CENTER_P): c for c in cells}
    for (t, p), mult in quotes.items():
        assert by_pos[(t, p)]["multiplier"] == mult


def test_the_at_the_money_row_lands_on_the_current_price_row():
    price = 1879.7894931
    cells = decode_grid(pack_grid({(0, CENTER_P): 1.03}), price=price, dpl=0.5,
                        ts_ms=1786774316800, dur_s=5.0)
    assert cells[0]["cell_y"] == int(price // 0.5) == 3759
    assert cells[0]["side"] == "at-price"
    assert cells[0]["distance"] == 0


def test_rows_above_and_below_map_to_the_right_side():
    cells = {c["row_offset"]: c for c in decode_grid(
        pack_grid({(0, CENTER_P - 2): 80.0, (0, CENTER_P + 2): 90.0}),
        price=1000.0, dpl=0.5, ts_ms=5000, dur_s=5.0)}
    assert cells[-2]["side"] == "down" and cells[-2]["cell_y"] == 2000 - 2
    assert cells[2]["side"] == "up" and cells[2]["cell_y"] == 2000 + 2


def test_sentinel_cells_are_not_quoted():
    cells = decode_grid(pack_grid({(3, 24): 1.15}), price=1000.0, dpl=0.5,
                        ts_ms=5000, dur_s=5.0)
    assert len(cells) == 1          # 899 sentinels dropped
    assert cells[0]["forward"] == 4


def test_breakeven_is_the_reciprocal_of_the_multiplier():
    cells = decode_grid(pack_grid({(0, 23): 13.0}), price=1000.0, dpl=0.5,
                        ts_ms=5000, dur_s=5.0)
    assert cells[0]["break_even_probability"] == round(1 / 13.0, 6)


# --- the surface prices it ------------------------------------------------
def _tape(now: float, *, px: float = 1000.0, jitter: float = 0.25, n: int = 90):
    """A tape with enough range that nearby rows are genuinely reachable."""
    return [
        Tick("ETH", px + (jitter if i % 2 else -jitter), now - (n - i) * 0.4, source="page")
        for i in range(n)
    ]


def test_a_quoted_grid_turns_the_surface_into_expected_value():
    now = 1_700_000_000.0
    ticks = _tape(now)
    price = ticks[-1].price
    grid = {
        "now_ms": now * 1000.0,
        "square_duration": 5000,
        "dollars_per_line": 0.5,
        "cell_height": 0.5,
        "price": price,
        "cells": decode_grid(
            pack_grid({(0, 23): 13.0, (0, 25): 14.1, (7, 23): 4.97, (7, 25): 5.21}),
            price=price, dpl=0.5, ts_ms=now * 1000.0, dur_s=5.0,
        ),
    }
    sig = compute_signal(
        ticks, now=now, grid=grid, cell_height=0.5,
        calibrator=Calibrator(), policy=Policy(), bankroll=Bankroll(),
    )
    rows = list(sig.surface)
    assert rows, "surface should be populated from the quoted cells"
    assert all(r["multiplier"] for r in rows)
    assert all(r["verdict"] != "unquoted" for r in rows)
    # Every quoted cell now carries a real EV number, signed either way.
    assert all(r["ev_lcb"] is not None for r in rows)
    assert sig.surface_stats["quoted"] == len(rows)


def test_a_far_cheap_cell_is_refused_even_with_a_fat_multiplier():
    """13x on a row the tape cannot reach is still a losing bet."""
    now = 1_700_000_000.0
    ticks = _tape(now, jitter=0.005)          # nearly frozen tape
    price = ticks[-1].price
    grid = {
        "now_ms": now * 1000.0, "square_duration": 5000,
        "dollars_per_line": 0.5, "cell_height": 0.5, "price": price,
        "cells": decode_grid(pack_grid({(0, 20): 13.0}), price=price, dpl=0.5,
                             ts_ms=now * 1000.0, dur_s=5.0),
    }
    sig = compute_signal(ticks, now=now, grid=grid, cell_height=0.5,
                         calibrator=Calibrator(), policy=Policy(), bankroll=Bankroll())
    assert sig.plan is None
    assert all(r["verdict"] in ("unreachable", "negative", "thin") for r in sig.surface)


def test_the_room_accepts_a_quoted_grid_and_reports_it(tmp_path):
    now = 1_700_000_000.0
    room = ControlRoom(
        enable_oracle=False, enable_wallet=False, dry_run=True,
        session_path=tmp_path / "s.json", token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
    )
    for tick in _tape(now):
        room.ticks.push(tick.symbol, tick.price, tick.ts, source="page")
    price = room.ticks.latest("ETH").price
    room.ingest_session({"grid": {
        "now_ms": now * 1000.0, "square_duration": 5000,
        "dollars_per_line": 0.5, "cell_height": 0.5, "price": price,
        "authoritative": True, "multiplier_source": "nats:quotes",
        "cells": decode_grid(pack_grid({(0, 23): 13.0, (7, 23): 4.97}),
                             price=price, dpl=0.5, ts_ms=now * 1000.0, dur_s=5.0),
    }})
    body = room.payload(room.think(now=now))
    assert body["surface_stats"]["quoted"] == 2
    assert room.grid["multiplier_source"] == "nats:quotes"
    room.close()


def test_inject_js_ships_the_decoder_the_python_side_expects():
    inject = (Path(__file__).resolve().parent.parent / "extension" / "inject.js").read_text()
    assert "parseQuoteGrid" in inject
    assert "QUOTE_SENTINEL = 10000" in inject
    assert "getUint16" in inject
    assert "nats:quotes" in inject
    assert "break_even_probability" in inject


def test_status_stamps_quotes_grid_and_player_edge(tmp_path):
    now = 1_700_000_000.0
    room = ControlRoom(
        enable_oracle=False, enable_wallet=False, dry_run=True,
        session_path=tmp_path / "s.json", token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
        player_path=tmp_path / "player.json",
    )
    for tick in _tape(now):
        room.ticks.push(tick.symbol, tick.price, tick.ts, source="page")
    price = room.ticks.latest("ETH").price
    cells = decode_grid(
        pack_grid({(0, 23): 2.06, (0, 25): 33.9, (0, 24): 1.2}),
        price=price, dpl=0.5, ts_ms=now * 1000.0, dur_s=5.0,
    )
    room.ingest_session({"grid": {
        "now_ms": now * 1000.0, "square_duration": 5000,
        "dollars_per_line": 0.5, "cell_height": 0.5, "price": price,
        "authoritative": True, "multiplier_source": "nats:quotes",
        "cells": cells,
    }})
    room.think(now=now)
    status = room.status()
    think = status["think"]
    grid = status["grid"]
    assert grid["have_grid"] is True
    assert grid["n_cells"] == 3
    assert grid["multiplier_source"] == "nats:quotes"
    assert "stale" in grid
    assert think["surface"]
    assert any(r.get("multiplier") for r in think["surface"])
    cands = think.get("candidates") or []
    assert cands
    stamped = [c for c in cands if c.get("multiplier")]
    assert stamped, "candidates should carry live multipliers"
    down = next((c for c in cands if c.get("side") == "down" and c.get("distance") == 1), None)
    if down:
        assert down["multiplier"] == 2.06
        assert down["break_even_probability"] == round(1 / 2.06, 6)
    pick = think.get("pick")
    if isinstance(pick, dict) and pick.get("side"):
        assert pick.get("multiplier")
        assert pick.get("break_even_probability")
    edge = think["player_edge"]
    assert edge["settled"] == 0
    assert edge["unresolved"] == 0
    assert edge["hit_rate"] is None
    assert edge["line"] == "no proven edge"
    tape = think["tape"]
    assert tape["lo"] is not None and tape["hi"] is not None
    assert tape["hi"] > tape["lo"]
    room.close()


def test_player_edge_does_not_invent_hit_rate_from_unresolved(tmp_path):
    room = ControlRoom(
        enable_oracle=False, enable_wallet=False, dry_run=True,
        session_path=tmp_path / "s.json", token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json",
        player_path=tmp_path / "player.json",
    )
    room.player.unresolved_taps = 77
    edge = room.player_edge_view()
    assert edge["settled"] == 0
    assert edge["unresolved"] == 77
    assert edge["hit_rate"] is None
    assert edge["line"] == "no proven edge"
    room.player.wins = 3
    room.player.losses = 1
    edge = room.player_edge_view()
    assert edge["settled"] == 4
    assert edge["hit_rate"] == 0.75
    assert edge["line"] == "4 settled · 75%"
    room.close()
