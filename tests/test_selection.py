"""The winner's curse, measured rather than modelled. No network.

The ranking is not the problem: over 111,381 labelled cells the rank
correlation between predicted and realised is +0.996. The problem is the level
of the one cell we act on. We take the argmax over ~180 cells, and the maximum
of N noisy estimates is biased upward -- enough to turn a true 0.35 into a
displayed 0.60. Our reliability table reads predicted 0.60 -> actual 0.365.

The closed form assumes independent errors. Ours are not: every cell in a window
rides one price path, so effective N is far below the cell count and the
textbook mu_N overstates the correction. So this measures the bias on the cells
we actually ranked first, rather than computing it from an assumption we know
to be false.
"""
from src.analytics.selection import (
    BANDS,
    MAX_SHRINK,
    MIN_OBSERVATIONS,
    SelectionBias,
)


def _feed(sb, *, rank, n, predicted, realised_rate):
    """n cells at `rank`, each predicted `predicted`, hitting at `realised_rate`."""
    for i in range(n):
        sb.observe(predicted, rank, (i % 1000) < int(realised_rate * 1000))
    return sb


# --- it measures the live gap --------------------------------------------
def test_it_recovers_the_measured_winners_curse():
    """The live case: rank-0 cells predicted 0.60 and realised 0.365."""
    sb = _feed(SelectionBias(), rank=0, n=2000, predicted=0.60, realised_rate=0.365)
    assert abs(sb.bias(0) - 0.235) < 0.01
    assert abs(sb.adjust(0.60, 0) - 0.365) < 0.01


def test_the_curse_fades_with_rank():
    """Rank is the conditioning variable that carries the bias. A cell at p=0.6
    that ranked 20th did not have to win a maximisation to get there."""
    sb = SelectionBias()
    _feed(sb, rank=0, n=2000, predicted=0.60, realised_rate=0.365)
    _feed(sb, rank=2, n=2000, predicted=0.60, realised_rate=0.50)
    _feed(sb, rank=8, n=2000, predicted=0.60, realised_rate=0.58)
    assert sb.bias(0) > sb.bias(1) > sb.bias(2)


def test_lower_ranks_are_kept_as_a_control():
    """If the shrink is really selection, it fades with rank. A flat profile
    means we are measuring something else and should not be correcting."""
    sb = SelectionBias()
    for rank in (0, 2, 8):
        _feed(sb, rank=rank, n=2000, predicted=0.60, realised_rate=0.40)
    biases = [sb.bias(i) for i in range(len(BANDS))]
    assert max(biases) - min(biases) < 0.02, "flat -- not a selection effect"


# --- it can only ever subtract -------------------------------------------
def test_it_never_lifts_a_probability():
    """A band that came in above prediction is not evidence the next pick will.
    Everything here that has cost money did so by making numbers look better."""
    sb = _feed(SelectionBias(), rank=0, n=2000, predicted=0.30, realised_rate=0.55)
    assert sb.bias(0) < 0
    assert sb.adjust(0.30, 0) == 0.30


def test_the_shrink_is_capped():
    """A band claiming the model is wrong by more than half is more likely a
    broken feed than a discovery."""
    sb = _feed(SelectionBias(), rank=0, n=2000, predicted=0.95, realised_rate=0.0)
    assert sb.bias(0) > MAX_SHRINK
    assert sb.adjust(0.95, 0) == 0.95 - MAX_SHRINK


def test_the_result_stays_a_probability():
    sb = _feed(SelectionBias(), rank=0, n=2000, predicted=0.40, realised_rate=0.0)
    for p in (0.0, 0.05, 0.5, 1.0):
        assert 0.0 <= sb.adjust(p, 0) <= 1.0


# --- it refuses to act on thin evidence ----------------------------------
def test_no_opinion_until_there_is_enough_evidence():
    sb = _feed(SelectionBias(), rank=0, n=MIN_OBSERVATIONS - 1,
               predicted=0.60, realised_rate=0.10)
    assert sb.bias(0) is None
    assert sb.adjust(0.60, 0) == 0.60


def test_an_opinion_appears_once_the_evidence_does():
    sb = _feed(SelectionBias(), rank=0, n=MIN_OBSERVATIONS,
               predicted=0.60, realised_rate=0.10)
    assert sb.bias(0) is not None
    assert sb.adjust(0.60, 0) < 0.60


def test_an_unseen_rank_is_untouched():
    sb = SelectionBias()
    assert sb.adjust(0.7, 0) == 0.7
    assert sb.adjust(0.7, 999) == 0.7


# --- robustness -----------------------------------------------------------
def test_nonsense_observations_are_ignored():
    sb = SelectionBias()
    for bad in (("x", 0, True), (1.5, 0, True), (-0.2, 0, False), (None, 0, True)):
        sb.observe(*bad)
    assert sb.seen.get(0, [0, 0, 0])[2] == 0


def test_malformed_rows_do_not_stop_the_batch():
    sb = SelectionBias()
    sb.observe_many([(0.5, 0, True), "nonsense", (0.5, 0, False), None])
    assert sb.seen[0][2] == 2


def test_a_rank_outside_every_band_is_dropped():
    sb = SelectionBias()
    sb.observe(0.5, 999, True)
    assert sum(v[2] for v in sb.seen.values()) == 0


# --- persistence ----------------------------------------------------------
def test_it_survives_a_restart():
    sb = _feed(SelectionBias(), rank=0, n=2000, predicted=0.60, realised_rate=0.365)
    back = SelectionBias.from_json(sb.to_json())
    assert abs(back.bias(0) - sb.bias(0)) < 1e-9


def test_impossible_rows_are_dropped_on_restore():
    """More hits than trials, or predicted mass above n, is corruption."""
    led = SelectionBias.from_json({"seen": [
        [0, 5.0, 9.0, 4.0], [1, 2.0, -1.0, 8.0], [2, 1.0, 1.0, 0.0],
        [0, 600.0, 400.0, 1000.0],
    ]})
    assert list(led.seen) == [0]
    assert led.seen[0][2] == 1000.0


def test_a_corrupt_payload_yields_an_empty_ledger():
    for bad in (None, {}, {"seen": "x"}, {"seen": [[1]]}):
        assert SelectionBias.from_json(bad).seen == {}


# --- reporting ------------------------------------------------------------
def test_stats_show_the_gap_and_whether_it_is_trusted():
    sb = _feed(SelectionBias(), rank=0, n=2000, predicted=0.60, realised_rate=0.365)
    row = sb.stats()["bands"][0]
    assert row["n"] == 2000
    assert abs(row["predicted"] - 0.60) < 0.01
    assert abs(row["realised"] - 0.365) < 0.01
    assert row["warm"] is True


def test_a_cold_band_is_reported_as_cold():
    sb = SelectionBias()
    assert sb.stats()["bands"][0]["warm"] is False
    assert sb.stats()["bands"][0]["bias"] is None
