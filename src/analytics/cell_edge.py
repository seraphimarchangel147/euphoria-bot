"""Per-quote cell-edge ledger. Measurement only; cannot justify a bet.

ReachabilityLedger keys ``(distance, horizon_slot)``. That pooling is
the suspected artifact behind a one-cell "+EV" print: the house can
quote 1.04x and 1.13x in the same (d, h) bucket. Cheap 1.04x touches
often enough to lift the *pooled* hit rate (~0.90) above the *expensive*
quote's breakeven (1/1.13 ≈ 0.885), so the bucket looks +EV versus the
1.13x price even when every individual quote is −EV or too thin to
score. This module keys at the house's own grain — integer cents,
``key = int(round(multiplier * 100))`` — and never pools different
quotes.

House wire (see tests/test_quote_grid.py on the owner's machine):
uint16 little-endian, value/100 = multiplier, 10000 = unquoted.

``beats=True`` is not a tap signal. A warm +EV key is a measurement of
past touches at that exact price. It cannot justify a bet, a tap, or a
policy change. Window 8765 is a confirmation window on the user's
machine; this remote cannot see it and does not claim a live result.

Pure: no I/O, no wall-clock. Thin keys (n < MIN_OBSERVATIONS) have no
opinion — ``edge`` returns None.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

MIN_OBSERVATIONS = 200
HOUSE_UNQUOTED = 10_000  # uint16 sentinel; value/100 would be 100.00x
WILSON_Z = 1.959963984540054  # Φ^{-1}(0.975); SE construction only

# beats=True is a measurement flag, not a tap / policy signal.
CANNOT_JUSTIFY_A_BET = (
    "CellEdgeLedger is measurement only. beats=True cannot justify a bet."
)


def quote_key(multiplier: Any) -> int | None:
    """House cents key, or None if the print is not a real quote.

    Ignores None, non-finite, ``m <= 1``, and the uint16 unquoted
    sentinel (key >= 10000). Bool is rejected (``True`` is not 1.0x).
    """
    if multiplier is None or isinstance(multiplier, bool):
        return None
    try:
        m = float(multiplier)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(m) or m <= 1.0:
        return None
    key = int(round(m * 100.0))
    if key <= 100 or key >= HOUSE_UNQUOTED:
        return None
    return key


def quote_multiplier(key: int) -> float:
    """Reconstruct the house cents price from an integer key."""
    return int(key) / 100.0


def wilson_se(rate: float, n: int, z: float = WILSON_Z) -> float:
    """Wilson standard error of a binomial rate.

    ``sqrt((p(1-p) + z²/4n) / n) / (1 + z²/n)``. Stays positive at
    p ∈ {0, 1}, unlike the raw binomial SE ``sqrt(p(1-p)/n)``.
    """
    if n <= 0:
        return float("inf")
    p = min(max(float(rate), 0.0), 1.0)
    z2 = float(z) * float(z)
    denom = 1.0 + z2 / n
    inner = (p * (1.0 - p) + z2 / (4.0 * n)) / n
    return math.sqrt(max(0.0, inner)) / denom


def binomial_se(rate: float, n: int) -> float:
    """Raw binomial SE. Zero at p ∈ {0, 1}; prefer ``wilson_se``."""
    if n <= 0:
        return float("inf")
    p = min(max(float(rate), 0.0), 1.0)
    return math.sqrt(p * (1.0 - p) / n)


def _as_row(item: Any) -> tuple[Any, Any, Any, Any]:
    if isinstance(item, Mapping):
        return (
            item.get("multiplier", item.get("m")),
            item.get("touched", item.get("hit")),
            item.get("distance"),
            item.get("horizon_s", item.get("horizon")),
        )
    multiplier, touched, *rest = item
    distance = rest[0] if rest else None
    horizon_s = rest[1] if len(rest) > 1 else None
    return multiplier, touched, distance, horizon_s


class CellEdgeLedger:
    """Per-cents-key hit ledger. ``(distance, horizon)`` is accepted and ignored.

    This cannot justify a bet. ``beats=True`` is not a tap signal.
    """

    min_observations = MIN_OBSERVATIONS

    def __init__(self) -> None:
        # key -> [hits, n]
        self._cells: dict[int, list[int]] = {}

    def observe(
        self,
        multiplier: Any,
        touched: Any,
        distance: Any = None,
        horizon_s: Any = None,
    ) -> None:
        """Record one quote. ``distance`` / ``horizon_s`` are discarded.

        Ignores ``m is None`` or ``m <= 1``. Different quotes never share
        a bucket, even when they share a (d, h) cell.
        """
        del distance, horizon_s
        key = quote_key(multiplier)
        if key is None:
            return
        rec = self._cells.get(key)
        if rec is None:
            rec = [0, 0]
            self._cells[key] = rec
        rec[1] += 1
        if touched:
            rec[0] += 1

    def observe_many(self, rows: Iterable[Any]) -> None:
        """Ingest many rows. Mappings or ``(m, touched[, d[, h]])``."""
        for item in rows:
            multiplier, touched, distance, horizon_s = _as_row(item)
            self.observe(multiplier, touched, distance=distance, horizon_s=horizon_s)

    def evidence(self, multiplier: Any) -> tuple[int, int, float]:
        """``(hits, n, rate)`` at the house cents key. ``(0, 0, 0.0)`` if unknown."""
        key = quote_key(multiplier)
        if key is None:
            return (0, 0, 0.0)
        rec = self._cells.get(key)
        if rec is None or rec[1] <= 0:
            return (0, 0, 0.0)
        hits, n = rec[0], rec[1]
        return (hits, n, hits / n)

    def edge(self, multiplier: Any) -> dict[str, Any] | None:
        """Score one quote key, or None if n < MIN_OBSERVATIONS.

        Returns ``{rate, breakeven, ev, se, z, beats}``. ``beats`` is
        ``ev > 0`` at this exact price. That is not a tap signal and
        cannot justify a bet.
        """
        key = quote_key(multiplier)
        if key is None:
            return None
        hits, n, rate = self.evidence(multiplier)
        if n < MIN_OBSERVATIONS:
            return None
        m = quote_multiplier(key)
        breakeven = 1.0 / m
        ev = rate * m - 1.0
        se = wilson_se(rate, n)
        if se > 0.0 and math.isfinite(se):
            z = (rate - breakeven) / se
        elif rate == breakeven:
            z = 0.0
        else:
            z = math.copysign(float("inf"), rate - breakeven)
        return {
            "rate": rate,
            "breakeven": breakeven,
            "ev": ev,
            "se": se,
            "z": z,
            "beats": ev > 0.0,
        }

    def stats(self) -> list[dict[str, Any]]:
        """Warm keys only (n >= MIN_OBSERVATIONS), sorted by cents key."""
        out: list[dict[str, Any]] = []
        for key in sorted(self._cells):
            hits, n = self._cells[key]
            if n < MIN_OBSERVATIONS:
                continue
            scored = self.edge(quote_multiplier(key))
            if scored is None:
                continue
            out.append(
                {
                    "key": key,
                    "multiplier": quote_multiplier(key),
                    "hits": hits,
                    "n": n,
                    **scored,
                }
            )
        return out

    def to_json(self) -> dict[str, Any]:
        """JSON-ready snapshot. No I/O. Same shape family as ReachabilityLedger."""
        return {
            "version": 1,
            "schema": "cell_edge",
            "min_observations": MIN_OBSERVATIONS,
            "cells": [
                {"key": key, "hits": hits, "n": n}
                for key, (hits, n) in sorted(self._cells.items())
            ],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> CellEdgeLedger:
        """Rebuild from ``to_json`` (or a ``cells`` dict keyed by cents)."""
        ledger = cls()
        raw = payload.get("cells", ())
        if isinstance(raw, Mapping):
            items = raw.items()
        else:
            items = (
                (row.get("key"), row) for row in raw if isinstance(row, Mapping)
            )
        for key_s, rec in items:
            if rec is None:
                continue
            if isinstance(rec, Mapping):
                hits = int(rec.get("hits", 0))
                n = int(rec.get("n", 0))
                key_val = rec.get("key", key_s)
            else:
                continue
            try:
                key = int(key_val)
            except (TypeError, ValueError):
                continue
            if key <= 100 or key >= HOUSE_UNQUOTED or n < 0 or hits < 0:
                continue
            ledger._cells[key] = [min(hits, n), n]
        return ledger
