"""Cell-edge ledger. SYNTHETIC units; window 8765 is not measured here."""
from __future__ import annotations

from src.analytics.cell_edge import (
    CANNOT_JUSTIFY_A_BET,
    HOUSE_UNQUOTED,
    MIN_OBSERVATIONS,
    CellEdgeLedger,
    quote_key,
    quote_multiplier,
    wilson_se,
)


def test_quote_key_matches_house_cents_grain():
    """House wire: uint16 LE, value/100 = multiplier, 10000 = unquoted.

    ``key = int(round(m * 100))`` is the house's own 'same price' grain.
    SYNTHETIC. This remote does not have tests/test_quote_grid.py.
    """
    assert quote_key(1.04) == 104
    assert quote_key(1.13) == 113
    assert quote_key(104 / 100) == 104
    assert quote_multiplier(104) == 1.04
    assert quote_multiplier(113) == 1.13
    raw_104 = (104).to_bytes(2, "little")
    raw_113 = (113).to_bytes(2, "little")
    raw_unquoted = (HOUSE_UNQUOTED).to_bytes(2, "little")
    assert int.from_bytes(raw_104, "little") / 100 == 1.04
    assert int.from_bytes(raw_113, "little") / 100 == 1.13
    assert int.from_bytes(raw_unquoted, "little") == 10_000
    assert quote_key(HOUSE_UNQUOTED / 100) is None
    assert MIN_OBSERVATIONS == 200


def test_observe_ignores_none_and_multipliers_at_or_below_one():
    """None and m <= 1 are not quotes. SYNTHETIC."""
    ledger = CellEdgeLedger()
    ledger.observe(None, True)
    ledger.observe(1.0, True)
    ledger.observe(0.99, True)
    ledger.observe(True, True)  # bool is not 1.0x
    ledger.observe(float("nan"), True)
    ledger.observe(100.0, True)  # unquoted sentinel as a multiplier
    assert ledger.evidence(1.04) == (0, 0, 0.0)
    assert ledger.stats() == []
    assert ledger.to_json()["cells"] == []


def test_thin_keys_have_no_opinion():
    """n < 200 → edge is None. SYNTHETIC. Not window 8765."""
    ledger = CellEdgeLedger()
    ledger.observe_many((1.13, True) for _ in range(199))
    assert ledger.evidence(1.13) == (199, 199, 1.0)
    assert ledger.edge(1.13) is None
    assert ledger.stats() == []
    ledger.observe(1.13, False)
    scored = ledger.edge(1.13)
    assert scored is not None
    assert scored["rate"] == 199 / 200
    assert abs(scored["breakeven"] - 1.0 / 1.13) < 1e-12


def test_distance_horizon_do_not_key_or_pool_quotes():
    """Same (d, h) with 1.04x and 1.13x stay two keys. SYNTHETIC."""
    ledger = CellEdgeLedger()
    ledger.observe(1.04, True, distance=0, horizon_s=2)
    ledger.observe(1.13, False, distance=0, horizon_s=2)
    assert ledger.evidence(1.04) == (1, 1, 1.0)
    assert ledger.evidence(1.13) == (0, 1, 0.0)
    assert quote_key(1.04) != quote_key(1.13)


def _naive_dh_pool(rows: list[tuple[float, bool]]) -> tuple[int, int, float]:
    """SYNTHETIC stand-in for ReachabilityLedger (d, h) pooling."""
    hits = sum(1 for _m, touched in rows if touched)
    n = len(rows)
    return hits, n, hits / n if n else 0.0


def test_dh_pool_looks_plus_ev_vs_max_m_while_each_quote_key_does_not():
    """The pooling artifact. SYNTHETIC. Not a live 8765 result.

    Construct a (d=0, h=2) mix the house can actually quote: many 1.04x
    and some 1.13x. Cheap touches lift the *pooled* rate to 0.90, which
    beats 1/1.13. Each cents-key is −EV (1.04, 1.13) or too_small (1.07).
    CellEdgeLedger must not inherit the manufactured +EV.
    """
    cheap = [(1.04, i < 760) for i in range(800)]  # rate 0.95, −EV vs 1.04
    rich = [(1.13, i < 140) for i in range(200)]  # rate 0.70, −EV vs 1.13
    thin = [(1.07, i < 40) for i in range(50)]  # too_small
    rows = [(m, t, 0, 2.0) for m, t in cheap + rich + thin]

    hits, n, pooled_rate = _naive_dh_pool([(m, t) for m, t, _d, _h in rows])
    max_m = 1.13
    pooled_ev_vs_max = pooled_rate * max_m - 1.0
    assert n == 1050
    assert hits == 940
    assert abs(pooled_rate - 940 / 1050) < 1e-12
    # 800*0.95 + 200*0.70 + 50*0.80 = 760+140+40 = 940; 940/1050 ≈ 0.895
    # Pin the shape the owner named: pooled rate ~0.90 vs a 1.13x quote.
    assert 0.88 < pooled_rate < 0.92
    assert pooled_ev_vs_max > 0.0

    ledger = CellEdgeLedger()
    ledger.observe_many(rows)

    cheap_e = ledger.edge(1.04)
    rich_e = ledger.edge(1.13)
    thin_e = ledger.edge(1.07)
    assert cheap_e is not None
    assert rich_e is not None
    assert thin_e is None  # too_small
    assert cheap_e["rate"] == 0.95
    assert rich_e["rate"] == 0.70
    assert cheap_e["ev"] < 0.0
    assert rich_e["ev"] < 0.0
    assert cheap_e["beats"] is False
    assert rich_e["beats"] is False
    assert cheap_e["ev"] == 0.95 * 1.04 - 1.0
    assert rich_e["ev"] == 0.70 * 1.13 - 1.0

    warm = ledger.stats()
    assert [row["key"] for row in warm] == [104, 113]
    assert all(row["beats"] is False for row in warm)
    assert all(row["ev"] < 0.0 for row in warm)


def test_edge_z_is_rate_minus_breakeven_over_wilson_se():
    """z = (rate − 1/m) / se. SYNTHETIC Wilson math, not a tape."""
    ledger = CellEdgeLedger()
    ledger.observe_many((1.25, i < 100) for i in range(200))
    scored = ledger.edge(1.25)
    assert scored is not None
    rate = 100 / 200
    breakeven = 1.0 / 1.25
    se = wilson_se(rate, 200)
    assert scored["rate"] == rate
    assert scored["breakeven"] == breakeven
    assert scored["ev"] == rate * 1.25 - 1.0
    assert abs(scored["se"] - se) < 1e-12
    assert abs(scored["z"] - (rate - breakeven) / se) < 1e-12
    assert scored["beats"] is (scored["ev"] > 0.0)
    assert scored["beats"] is False


def test_beats_true_is_not_a_tap_signal():
    """A manufactured +EV key is still measurement-only. SYNTHETIC.

    192/200 at 1.05x is +EV on these rows (breakeven 1/1.05 ≈ 0.952).
    That cannot justify a bet. Window 8765 is on the user's machine;
    this is not that window.
    """
    ledger = CellEdgeLedger()
    ledger.observe_many((1.05, i < 192) for i in range(200))
    scored = ledger.edge(1.05)
    assert scored is not None
    assert scored["beats"] is True
    assert scored["ev"] > 0.0
    assert "cannot justify a bet" in CANNOT_JUSTIFY_A_BET
    assert "cannot justify a bet" in CellEdgeLedger.__doc__.lower()


def test_stats_lists_warm_keys_only():
    """Cold cents keys stay off the board. SYNTHETIC."""
    ledger = CellEdgeLedger()
    ledger.observe_many((1.04, True) for _ in range(200))
    ledger.observe_many((1.13, False) for _ in range(50))
    keys = [row["key"] for row in ledger.stats()]
    assert keys == [104]
    assert ledger.edge(1.13) is None


def test_json_round_trip_preserves_cents_keys():
    """to_json / from_json like ReachabilityLedger. SYNTHETIC. No I/O."""
    ledger = CellEdgeLedger()
    ledger.observe_many((1.04, True, 0, 2.0) for _ in range(10))
    ledger.observe_many((1.13, False, 0, 2.0) for _ in range(3))
    payload = ledger.to_json()
    assert payload["version"] == 1
    assert payload["schema"] == "cell_edge"
    assert payload["min_observations"] == 200
    assert payload["cells"] == [
        {"key": 104, "hits": 10, "n": 10},
        {"key": 113, "hits": 0, "n": 3},
    ]
    restored = CellEdgeLedger.from_json(payload)
    assert restored.evidence(1.04) == (10, 10, 1.0)
    assert restored.evidence(1.13) == (0, 3, 0.0)
    assert restored.to_json() == payload

    from_dict = CellEdgeLedger.from_json(
        {"cells": {"104": {"hits": 4, "n": 5}, "113": {"hits": 1, "n": 2}}}
    )
    assert from_dict.evidence(1.04) == (4, 5, 0.8)
    assert from_dict.evidence(1.13) == (1, 2, 0.5)
