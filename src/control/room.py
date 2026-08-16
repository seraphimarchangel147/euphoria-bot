"""Control room: CellEdgeLedger wired to GridBoard.on_quote_label.

Measurement only. ``beats=True`` cannot justify a bet.
Clock starts at next process start. Hours, not minutes.
``MIN_OBSERVATIONS=200``.

``on_quote_label`` rows are ``(multiplier, distance, horizon_s, touched)``.
``CellEdgeLedger.observe_many`` treats a tuple as
``(multiplier, touched, distance, horizon_s)``. Do not pass quote_rows
straight to ``observe_many`` — that would treat distance as touched.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import settings
from src.analytics.cell_edge import CellEdgeLedger

log = logging.getLogger("euphoria.control")


def _optional_gridboard() -> Any | None:
    """Owner-local GridBoard if present. This remote main has no gridboard.py."""
    try:
        from src.analytics.gridboard import GridBoard
    except ImportError:
        return None
    try:
        return GridBoard()
    except Exception:
        return None


def _load_cell_edge(path: Path) -> CellEdgeLedger:
    """Empty file / missing file = empty ledger. No (d, h) backfill."""
    if not path.is_file():
        return CellEdgeLedger()
    try:
        raw = path.read_text()
    except OSError:
        return CellEdgeLedger()
    if not raw.strip():
        return CellEdgeLedger()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return CellEdgeLedger()
    if not isinstance(payload, dict):
        return CellEdgeLedger()
    # Historical 15k (d, h) reachability rows cannot be imported.
    if payload.get("schema") not in (None, "cell_edge"):
        return CellEdgeLedger()
    return CellEdgeLedger.from_json(payload)


def _save_cell_edge(ledger: CellEdgeLedger, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ledger.to_json(), separators=(",", ":")))
    tmp.replace(path)


class ControlRoom:
    """Operator room. This file lands the cell-edge tape; measurement only."""

    def __init__(
        self,
        *,
        board: Any | None = None,
        cell_edge_path: Path | str | None = None,
        **_kwargs: Any,
    ) -> None:
        self.cell_edge_path = Path(cell_edge_path) if cell_edge_path is not None else Path(
            settings.CELL_EDGE_STORE
        )
        self.cell_edge = _load_cell_edge(self.cell_edge_path)
        self.board = board if board is not None else _optional_gridboard()
        if self.board is not None:
            self.board.on_quote_label = self._ingest_quote_labels

    def _ingest_quote_labels(self, rows):
        for row in rows or ():
            try:
                m, d, h, touched = row
            except (TypeError, ValueError):
                continue
            self.cell_edge.observe(m, touched, distance=d, horizon_s=h)
        # Same cadence as reachability: persist when labels land, and on close.
        self._save_cell_edge()

    def _save_cell_edge(self) -> None:
        try:
            _save_cell_edge(self.cell_edge, self.cell_edge_path)
        except OSError as exc:
            log.warning("cell_edge persist: %s", exc)

    def _cell_edge_view(self) -> list[dict[str, Any]]:
        """Warm keys only. Empty until n >= 200. Not a live 8765 result."""
        return self.cell_edge.stats()

    def payload(self, sig: Any = None) -> dict[str, Any]:
        """Think / learning payload. ``cell_edge`` is ``stats()``, like reachability."""
        stats = self._cell_edge_view()
        body: dict[str, Any] = {}
        if isinstance(sig, dict):
            body.update(sig)
        body["cell_edge"] = stats
        learning = body.get("learning")
        if not isinstance(learning, dict):
            learning = {}
        learning["cell_edge"] = stats
        body["learning"] = learning
        return body

    def think(self, now: float | None = None, **_kwargs: Any) -> Any:
        return None

    def think_payload(self, now: float | None = None) -> dict[str, Any]:
        return self.payload(self.think(now=now))

    def close(self) -> None:
        self._save_cell_edge()
