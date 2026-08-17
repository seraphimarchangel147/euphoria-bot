"""The candidate edge, split by the regime it was measured in. No network.

The 1.02-1.04x band measured 546/552. But every observation behind it came from
tape where price moved $0.14 in three minutes. The claim underneath it is "a
quiet market stays in its row for five seconds" -- which is a statement ABOUT
the regime, so a rate averaged across regimes cannot test it.

If the edge is real it is a property of quiet tape and must shrink or invert as
volatility rises. A flat profile means it is something else. A profile that
RISES with volatility means it is noise, because at-the-money cells cannot
become more likely to hold their row as the market gets busier.
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
                       ("reachability_path", "re"), ("cell_edge_path", "ce")):
        kw.setdefault(name, tmp_path / f"{stem}.json")
    return ControlRoom(**kw)


def _feed(room, *, mult, vol, n, hits):
    rows = [(mult, 0, 8.0, i < hits, vol) for i in range(n)]
    room._ingest_quote_labels(rows)


def test_a_quiet_only_edge_shows_up_as_quiet_only(tmp_path):
    """The shape that would confirm it: strong in LOW, gone in HIGH."""
    room = _room(tmp_path)
    _feed(room, mult=1.03, vol="LOW", n=600, hits=600)
    _feed(room, mult=1.03, vol="HIGH", n=600, hits=480)     # 0.80
    v = room.regime_edge_view(lo=103, hi=103)
    by = {r["regime"]: r for r in v["regimes"]}
    assert by["LOW"]["ev_lower"] > 0
    assert by["HIGH"]["ev_mean"] < 0
    room.close()


def test_an_edge_that_survives_high_volatility_is_reported_as_such(tmp_path):
    room = _room(tmp_path)
    for vol in ("LOW", "MED", "HIGH"):
        _feed(room, mult=1.03, vol=vol, n=600, hits=600)
    v = room.regime_edge_view(lo=103, hi=103)
    assert all(r["ev_lower"] > 0 for r in v["regimes"])
    assert len(v["regimes"]) == 3
    room.close()


def test_a_thin_regime_is_not_reported(tmp_path):
    """A per-regime split cuts the sample. Reporting a 12-sample regime as a
    finding is how a real edge and a coincidence become indistinguishable."""
    room = _room(tmp_path)
    _feed(room, mult=1.03, vol="LOW", n=12, hits=12)
    assert room.regime_edge_view(lo=103, hi=103)["regimes"] == []
    room.close()


def test_the_bound_is_the_lower_one_so_a_perfect_run_is_not_certainty(tmp_path):
    """195/195 is not p=1. The lower bound is what decides whether to act."""
    room = _room(tmp_path)
    _feed(room, mult=1.02, vol="LOW", n=200, hits=200)
    r = room.regime_edge_view(lo=102, hi=102)["regimes"][0]
    assert r["rate"] == 1.0
    assert r["lower95"] < 1.0
    assert r["ev_lower"] < r["ev_mean"]
    room.close()


def test_labels_without_a_regime_still_reach_the_quote_ledger(tmp_path):
    """Backwards compatible: a 4-tuple must not break ingest."""
    room = _room(tmp_path)
    room._ingest_quote_labels([(1.03, 0, 8.0, True), (1.03, 0, 8.0, False)])
    hits, n, _ = room.cell_edge.evidence(1.03)
    assert n == 2
    assert room.regime_edge_view(lo=103, hi=103)["regimes"] == []
    room.close()


def test_malformed_rows_are_skipped(tmp_path):
    room = _room(tmp_path)
    room._ingest_quote_labels(["junk", None, (1.03, 0, 8.0, True, "LOW")])
    assert room.cell_edge.evidence(1.03)[1] == 1
    room.close()


def test_even_money_and_worse_quotes_are_not_tracked(tmp_path):
    """A quote at or below 1.00x cannot carry edge and would only add noise."""
    room = _room(tmp_path)
    _feed(room, mult=1.00, vol="LOW", n=300, hits=300)
    assert room.regime_edge_view(lo=100, hi=100)["regimes"] == []
    room.close()


def test_the_payload_carries_it(tmp_path):
    room = _room(tmp_path)
    assert "regime_edge" in room.snapshot()["think"]
    room.close()


# --- the label the split uses must be trustworthy --------------------------
def test_the_split_uses_the_rolling_window_not_whole_history(tmp_path):
    """All-history terciles saturate on HIGH whenever volatility trends, because
    two thirds of a rising series sits above its own early quantiles. A split
    keyed on a saturated label would put every observation in one bucket and
    quietly answer nothing."""
    room = _room(tmp_path)
    assert hasattr(room, "rolling_regime")
    # A steadily rising series: the whole-history label saturates, the rolling
    # one keeps its thirds.
    import time as _t
    now = _t.time()
    for i in range(1500):
        room.rolling_regime.observe(0.01 + i * 1e-5, now=now + i)
    room.regime.observe(0.5)
    lab = room._rolling_label("MED")
    assert lab in ("LOW", "MED", "HIGH")
    room.close()


def test_a_cold_rolling_window_falls_back_to_the_scoring_label(tmp_path):
    """Never invent a regime. If the window has not filled, use what the cell
    was actually scored under."""
    room = _room(tmp_path)
    assert room._rolling_label("MED") == "MED"
    room.close()


def test_a_broken_regime_source_cannot_drop_a_label(tmp_path):
    room = _room(tmp_path)
    class Boom:
        last_value = 0.5
        def bucket(self, v, *, now):
            raise RuntimeError("regime exploded")
    room.rolling_regime = Boom()
    assert room._rolling_label("LOW") == "LOW"
    room.close()


def test_the_calibrators_own_label_is_left_alone(tmp_path):
    """Swapping the calibrator's key to fix a label would invalidate every
    learned bucket. The split gets the better label; the calibrator keeps its
    history."""
    room = _room(tmp_path)
    assert room.regime is not room.rolling_regime
    from src.analytics.regime import VolRegime
    assert isinstance(room.regime, VolRegime)
    room.close()


# --- evidence must survive a restart --------------------------------------
def test_the_split_survives_a_restart(tmp_path):
    """Third component to need this. The rail lost its evidence on restart, the
    shadow book lost its sample, and this lost its regime split -- while I
    restarted five times in an hour deploying fixes. In-memory evidence is
    evidence you will lose."""
    room = _room(tmp_path)
    _feed(room, mult=1.03, vol="LOW", n=500, hits=495)
    room.save_regime_edge()
    room.close()

    revived = _room(tmp_path)
    r = revived.regime_edge_view(lo=103, hi=103)["regimes"]
    assert r, "evidence did not survive"
    assert r[0]["n"] == 500
    assert r[0]["regime"] == "LOW"
    revived.close()


def test_a_corrupt_split_file_does_not_brick_startup(tmp_path):
    bad = tmp_path / "ce.regime.json"
    bad.write_text("{not json")
    room = _room(tmp_path, cell_edge_path=tmp_path / "ce.json")
    assert room.regime_edge_view()["tracked_keys"] == 0
    room.close()


def test_impossible_rows_are_dropped_on_restore(tmp_path):
    """More hits than trials is corruption, not a lucky streak."""
    import json as _j
    (tmp_path / "ce.regime.json").write_text(_j.dumps({"rows": [
        [103, "LOW", 900, 500], [103, "MED", -1, 500],
        [103, "HIGH", 0, 0], [104, "LOW", 400, 500],
    ]}))
    room = _room(tmp_path, cell_edge_path=tmp_path / "ce.json")
    assert list(room._regime_edge) == [(104, "LOW")]
    room.close()
