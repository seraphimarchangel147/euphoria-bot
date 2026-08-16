"""PnLTracker accounting tests. No ControlRoom / GridBoard / room imports."""
from __future__ import annotations

from src.analytics.pnl import PnLTracker


def _book_334_vs_270() -> PnLTracker:
    """Synthetic reproduction of the owner's /pnl 33.4-vs-2.70 book.

    Unit stake, losses pay 0. 27 wins at 2.70x (hit_rate 0.27) plus a
    heavy tail of losing quotes (40 at 80x, 33 at 2.033...) so the
    arithmetic mean of quoted multipliers is 33.4. Cash identity:
    E = 0.27 * 2.70 - 1 = -0.271, so (1 + E) / hit_rate = 2.70.
    That -0.271 is paper expectancy, not the unrelated shadow baseline
    −0.2712. This is not the live /pnl JSON; that file was never pushed.
    """
    book = PnLTracker()
    for _ in range(27):
        book.record_paper(stake=1.0, outcome="win", multiplier=2.70)
    for _ in range(40):
        book.record_paper(stake=1.0, outcome="loss", multiplier=80.0)
    filler = 67.1 / 33.0
    for _ in range(33):
        book.record_paper(stake=1.0, outcome="loss", multiplier=filler)
    return book


def test_avg_multiplier_no_longer_reports_the_334_tail_mean():
    """Live /pnl reported avg_multiplier 33.4 (raw quoted mean).

    Back-solve from hit rate and expectancy on that book is 2.70. The
    headline must be the 2.70-class reconciled number, not 33.4. Sample
    is a synthetic reproduction of the owner's /pnl book.
    """
    summary = _book_334_vs_270().paper_summary()
    raw = summary["avg_multiplier_raw"]
    headline = summary["avg_multiplier"]
    assert raw is not None and 33.0 <= raw <= 34.0
    assert headline is not None and 2.65 <= headline <= 2.75
    assert headline != raw
    assert headline == 2.70


def test_implied_multiplier_reconciles_hit_rate_and_expectancy():
    book = _book_334_vs_270()
    summary = book.paper_summary()
    hit_rate = summary["hit_rate"]
    expectancy = summary["expectancy_per_unit"]
    implied = (1.0 + expectancy) / hit_rate
    assert summary["implied_multiplier"] == round(implied, 3)
    assert summary["avg_multiplier"] == summary["implied_multiplier"]
    assert summary["avg_multiplier"] == 2.70
    # Negative expectancy cannot produce a teen/lottery headline.
    assert expectancy < 0
    assert summary["avg_multiplier"] < 4.0


def test_a_single_80x_loser_cannot_pull_the_headline_multiplier_into_the_teens():
    book = PnLTracker()
    for _ in range(6):
        book.record_paper(stake=1.0, outcome="win", multiplier=2.0)
    book.record_paper(stake=1.0, outcome="loss", multiplier=80.0)
    summary = book.paper_summary()
    raw = summary["avg_multiplier_raw"]
    headline = summary["avg_multiplier"]
    assert raw is not None and raw >= 13.0
    assert headline == 2.0
    assert headline < 10.0


def test_count_hit_rate_does_not_pretend_a_whale_loss_is_one_equal_trade():
    """Unweighted wins/n looks like 90% when almost all the stake lost.

    Headline hit_rate stays the count rate so (1+E)/hit_rate still
    reconciles. The honest twin is hit_rate_stake_weighted.
    """
    book = PnLTracker()
    for _ in range(9):
        book.record_paper(stake=1.0, outcome="win", multiplier=2.0)
    book.record_paper(stake=91.0, outcome="loss", multiplier=2.0)
    summary = book.paper_summary()
    assert summary["hit_rate"] == 0.9
    assert summary["hit_rate_stake_weighted"] == 0.09
    implied = (1.0 + summary["expectancy_per_unit"]) / summary["hit_rate"]
    assert summary["avg_multiplier"] == round(implied, 3)


def test_session_paper_hit_rate_does_not_hide_unequal_stakes():
    book = PnLTracker()
    for _ in range(9):
        book.record_paper(stake=1.0, outcome="win", multiplier=2.0)
    book.record_paper(stake=91.0, outcome="loss", multiplier=2.0)
    session = book.session()
    assert session["paper_hit_rate"] == 0.9
    assert session["paper_hit_rate_stake_weighted"] == 0.09
    assert session["paper_pnl"] == 9.0 * 1.0 + (-91.0)


def test_attribution_hit_rate_does_not_hide_unequal_stakes():
    book = PnLTracker()
    for _ in range(9):
        book.record_paper(stake=1.0, outcome="win", multiplier=2.0, tag="grid")
    book.record_paper(stake=91.0, outcome="loss", multiplier=2.0, tag="grid")
    view = book.attribution()["grid"]
    assert view["hit_rate"] == 0.9
    assert view["hit_rate_stake_weighted"] == 0.09
    assert view["expectancy"] == (9.0 - 91.0) / 100.0
