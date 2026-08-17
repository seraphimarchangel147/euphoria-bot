"""Settled labels must carry the price they were offered at. No network.

Two desks independently tried to test whether any cell beats its own quote and
both stopped at the same wall: `PendingCell` stored no multiplier, so the label
tape could only be keyed by (distance, horizon).

That pooling is not a neutral simplification. Reachability slot 2 covers roughly
five at-the-money columns, and the house quoted 1.04, 1.06, 1.07, 1.09 and 1.13
across them -- it prices them differently because it rates them differently.
Comparing one pooled rate against whichever member the house rates LEAST likely
manufactures an edge by construction, and that is exactly how a +1.85% "edge"
appeared at 1.13x.

The mix math makes the ambiguity concrete. Equal-weighting those five implied
probabilities gives 0.9284; the pool measured 0.897. A calibrated house needs
~78% weight on 1.13 to produce that, in which case the 1.13 subset sits exactly
at breakeven and the apparent edge is the leftover easier cells. A flat ~3pp vig
fits the same number with zero edge at 1.13. Neither is distinguishable without
the quote on the label.
"""
from src.analytics.gridboard import GridBoard, PendingCell
from src.analytics.policy import CellScore


def _score(**kw):
    base = dict(
        cell_x=3, cell_y=10, forward=3, row_offset=0, side="at-price", distance=0,
        lo=99.75, hi=100.25, t_start=10.0, t_end=15.0, horizon_s=15.0,
        edge_cells=0.0, sd_cells=1.0, p_model=0.9, p_cal=0.9, p_lcb=0.88,
        n_obs=500, trusted=True, multiplier=1.13, breakeven=0.885,
        ev=0.017, ev_lcb=-0.006, kelly=0.0, stake=0.0, verdict="thin",
    )
    base.update(kw)
    return CellScore(**base)


class _Tick:
    def __init__(self, ts, price, symbol="ETH"):
        self.ts, self.price, self.symbol = ts, price, symbol


def _settle(board, *, close, now=1030.0):
    ticks = [_Tick(1010.0 + i, close) for i in range(6)]
    return board.settle(ticks, now=now)


# --- the label carries the quote -----------------------------------------
def test_a_settled_label_reports_the_price_it_was_offered_at():
    seen = []
    b = GridBoard(on_quote_label=lambda rows: seen.extend(rows))
    b.record([_score(multiplier=1.13)], now=1000.0, vol="MED", column_s=5.0)
    _settle(b, close=100.0)
    assert seen, "no quote-keyed label emitted"
    mult, dist, horizon, touched, vol = seen[0]
    assert mult == 1.13
    assert dist == 0
    assert touched is True, "close 100.0 is inside [99.75, 100.25]"
    # The regime rides along, because the candidate edge is really the claim
    # "a quiet market holds its row", and a rate averaged across regimes
    # cannot test a statement about the regime.
    assert vol == "MED"


def test_the_same_bucket_can_now_be_split_by_quote():
    """The whole point: two cells the ledger would have pooled are now
    distinguishable, because the house priced them differently."""
    seen = []
    b = GridBoard(on_quote_label=lambda rows: seen.extend(rows))
    b.record([_score(cell_y=10, multiplier=1.04),
              _score(cell_y=11, lo=101.0, hi=101.5, multiplier=1.13)],
             now=1000.0, vol="MED", column_s=5.0)
    _settle(b, close=100.0)
    quotes = sorted(r[0] for r in seen)
    assert quotes == [1.04, 1.13]
    by_quote = {r[0]: r[3] for r in seen}
    assert by_quote[1.04] is True and by_quote[1.13] is False


def test_a_miss_is_labelled_as_a_miss():
    seen = []
    b = GridBoard(on_quote_label=lambda rows: seen.extend(rows))
    b.record([_score(lo=105.0, hi=105.5, multiplier=9.0)],
             now=1000.0, vol="MED", column_s=5.0)
    _settle(b, close=100.0)
    assert seen[0][3] is False


# --- it must not invent or interfere -------------------------------------
def test_an_unquoted_cell_emits_nothing():
    """No price, no comparison. A missing quote is not a quote of zero."""
    seen = []
    b = GridBoard(on_quote_label=lambda rows: seen.extend(rows))
    b.record([_score(multiplier=None)], now=1000.0, vol="MED", column_s=5.0)
    _settle(b, close=100.0)
    assert seen == []


def test_a_broken_consumer_cannot_stop_the_grading():
    """This hook is a reader. Grading feeds the calibrator and the rail, and
    must not be at the mercy of whatever is listening."""
    def boom(rows):
        raise RuntimeError("consumer exploded")

    b = GridBoard(on_quote_label=boom)
    b.record([_score()], now=1000.0, vol="MED", column_s=5.0)
    out = _settle(b, close=100.0)
    assert out["graded"] == 1
    assert out["touched"] == 1


def test_no_consumer_is_fine():
    b = GridBoard()
    b.record([_score()], now=1000.0, vol="MED", column_s=5.0)
    assert _settle(b, close=100.0)["graded"] == 1


def test_the_pending_cell_stores_it():
    b = GridBoard()
    b.record([_score(multiplier=1.07)], now=1000.0, vol="MED", column_s=5.0)
    cell = next(iter(b.pending.values()))
    assert isinstance(cell, PendingCell)
    assert cell.multiplier == 1.07
