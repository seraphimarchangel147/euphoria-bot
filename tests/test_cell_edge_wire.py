"""Cell-edge wire: GridBoard.on_quote_label → CellEdgeLedger.

SYNTHETIC. Not window 8765. No live rate is claimed.
Hook rows are (multiplier, distance, horizon_s, touched).
observe_many would treat that tuple as (m, touched, d, h).
"""
from __future__ import annotations

import json
from pathlib import Path

from config import settings
from src.analytics.cell_edge import MIN_OBSERVATIONS, quote_key
from src.control.room import ControlRoom


class GridBoard:
    """Contract double for main GridBoard.on_quote_label.

    settle emits quote_rows as (multiplier, distance, horizon_s, touched).
    A throwing consumer is swallowed. This is not the owner-local board.
    """

    def __init__(self) -> None:
        self.on_quote_label = None
        self.pending: list[PendingCell] = []
        self.settled_rows: list[tuple] = []

    def settle(self, quote_rows=None):
        if quote_rows is None:
            quote_rows = [
                (c.multiplier, c.distance, c.horizon_s, c.touched)
                for c in self.pending
            ]
            self.pending = []
        rows = list(quote_rows)
        self.settled_rows.append(tuple(rows))
        hook = self.on_quote_label
        if hook is None:
            return rows
        try:
            hook(rows)
        except Exception:
            pass
        return rows


class PendingCell:
    def __init__(self, multiplier, distance, horizon_s, touched=False) -> None:
        self.multiplier = multiplier
        self.distance = distance
        self.horizon_s = horizon_s
        self.touched = touched


def _room(tmp_path: Path, board=None, **kw) -> ControlRoom:
    kw.setdefault("cell_edge_path", tmp_path / "cell_edge.json")
    if board is not None:
        kw["board"] = board
    return ControlRoom(**kw)


def test_gridboard_on_quote_label_records_m_d_h_touched_and_keys_by_cents(tmp_path):
    """Two quotes in one (d, h) split. Keys are cents, not (d, h). SYNTHETIC."""
    board = GridBoard()
    room = _room(tmp_path, board=board)
    assert board.on_quote_label == room._ingest_quote_labels

    board.pending = [
        PendingCell(1.04, 0, 2.0, touched=True),
        PendingCell(1.13, 0, 2.0, touched=False),
    ]
    board.settle()

    assert board.settled_rows == [((1.04, 0, 2.0, True), (1.13, 0, 2.0, False))]
    assert quote_key(1.04) == 104
    assert quote_key(1.13) == 113
    assert room.cell_edge.evidence(1.04) == (1, 1, 1.0)
    assert room.cell_edge.evidence(1.13) == (0, 1, 0.0)
    # Cold tape: n << MIN_OBSERVATIONS. Do not invent a live rate.
    assert MIN_OBSERVATIONS == 200
    assert room.cell_edge.stats() == []
    assert room.think_payload()["cell_edge"] == []
    assert room.think_payload()["learning"]["cell_edge"] == []
    room.close()


def test_observe_many_is_not_called_with_the_raw_hook_tuple_order(tmp_path):
    """Raw (m, d, h, touched) into observe_many would treat d as touched.

    distance=0 is falsy; a raw pass would record a miss on a hit.
    distance=5 is truthy; a raw pass would record a hit on a miss.
    SYNTHETIC. Not a live rate.
    """
    room = _room(tmp_path)
    seen: list[list] = []
    real = room.cell_edge.observe_many

    def wrapped(rows):
        seen.append(list(rows))
        return real(rows)

    room.cell_edge.observe_many = wrapped
    room._ingest_quote_labels(
        [
            (1.04, 0, 2.0, True),
            (1.13, 5, 2.0, False),
        ]
    )
    assert seen == []
    assert room.cell_edge.evidence(1.04) == (1, 1, 1.0)
    assert room.cell_edge.evidence(1.13) == (0, 1, 0.0)
    room.close()


def test_raising_consumer_does_not_stop_settle(tmp_path):
    """Hook throw is swallowed by settle. Already pinned on owner gridboard tests."""
    board = GridBoard()
    graded = []

    def boom(rows):
        graded.append(("hook", list(rows)))
        raise RuntimeError("consumer")

    board.on_quote_label = boom
    rows = board.settle([(1.04, 0, 2.0, True), (1.13, 0, 2.0, False)])
    assert rows == [(1.04, 0, 2.0, True), (1.13, 0, 2.0, False)]
    assert graded == [("hook", [(1.04, 0, 2.0, True), (1.13, 0, 2.0, False)])]
    # Grading / settle completed; room adapter is not required for this pin.
    room = _room(tmp_path, board=board)
    board.settle([(1.07, 0, 2.0, True)])
    assert room.cell_edge.evidence(1.07) == (1, 1, 1.0)
    room.close()


def test_missing_and_empty_store_are_empty_ledgers(tmp_path):
    """Empty file / missing file = empty ledger. Tape starts empty. SYNTHETIC."""
    missing = tmp_path / "absent.json"
    room = _room(tmp_path, cell_edge_path=missing)
    assert not missing.exists()
    assert room.cell_edge.to_json()["cells"] == []
    assert room.think_payload()["cell_edge"] == []
    room.close()

    empty = tmp_path / "empty.json"
    empty.write_text("")
    room2 = _room(tmp_path, cell_edge_path=empty)
    assert room2.cell_edge.to_json()["cells"] == []
    room2.close()


def test_historical_dh_rows_cannot_be_imported(tmp_path):
    """Reachability-shaped (d, h) tape is not a cell-edge tape. SYNTHETIC."""
    path = tmp_path / "reachability.json"
    path.write_text(
        json.dumps(
            {
                "schema": "reachability",
                "cells": [
                    {"distance": 0, "horizon_s": 2, "hits": 15000, "n": 15000},
                    {"d": 1, "h": 5, "hits": 8000, "n": 9000},
                ],
            }
        )
    )
    room = _room(tmp_path, cell_edge_path=path)
    assert room.cell_edge.to_json()["cells"] == []
    assert room.cell_edge.stats() == []
    room.close()


def test_store_round_trip_and_settings_default(tmp_path):
    """Persist like reachability. SYNTHETIC counts only. No live rate."""
    assert settings.CELL_EDGE_STORE == Path.home() / ".euphoria" / "cell_edge.json"
    path = tmp_path / "cell_edge.json"
    board = GridBoard()
    room = _room(tmp_path, board=board, cell_edge_path=path)
    board.settle([(1.04, 0, 2.0, True), (1.04, 1, 5.0, False)])
    room.close()
    saved = json.loads(path.read_text())
    assert saved["schema"] == "cell_edge"
    assert saved["cells"] == [{"key": 104, "hits": 1, "n": 2}]

    room2 = _room(tmp_path, cell_edge_path=path)
    assert room2.cell_edge.evidence(1.04) == (1, 2, 0.5)
    assert room2.cell_edge.stats() == []  # still cold; do not invent warmth
    room2.close()


def test_malformed_hook_rows_are_skipped(tmp_path):
    """Bad rows do not stop later quotes. SYNTHETIC."""
    room = _room(tmp_path)
    room._ingest_quote_labels(
        [
            "bad",
            None,
            (1.04,),
            (1.04, 0, 2.0, True),
            {"multiplier": 1.13},
        ]
    )
    assert room.cell_edge.evidence(1.04) == (1, 1, 1.0)
    assert room.cell_edge.evidence(1.13) == (0, 0, 0.0)
    room.close()
