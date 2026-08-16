"""Honest paper multipliers. Live /pnl numbers live in the docstrings."""
from __future__ import annotations

import pytest

from src.analytics.pnl_metrics import audit_notes, paper_stats


def _trade(outcome: str, multiplier: float, stake: float = 1.0) -> dict:
    """Losses pay 0: win delta = stake * (multiplier - 1), loss delta = -stake."""
    if outcome == "win":
        delta = stake * (multiplier - 1.0)
    else:
        delta = -stake
    return {
        "outcome": outcome,
        "stake": stake,
        "delta": delta,
        "multiplier": multiplier,
    }


def _live_334_vs_270_book() -> list[dict]:
    """Reproduce the live /pnl pair: raw mean 33.4, implied 2.70.

    Live paper_summary.avg_multiplier was 33.4. Back-solving from hit
    rate and expectancy gives 2.70. Equal unit stakes, 10 wins at 2.70
    and 17 losses at the quote that forces the raw mean to 33.4.

    PnL is exactly 0. This book does not invent profit.
    """
    win_m = 2.70
    n_wins = 10
    n_losses = 17
    n = n_wins + n_losses
    loss_m = (33.4 * n - n_wins * win_m) / n_losses
    return [_trade("win", win_m) for _ in range(n_wins)] + [
        _trade("loss", loss_m) for _ in range(n_losses)
    ]


def test_avg_multiplier_no_longer_reports_the_334_tail_mean():
    """Live /pnl: avg_multiplier 33.4; implied from hit rate + expectancy is 2.70.

    Book: 10 unit wins at 2.70, 17 unit losses at ~51.46. Raw arithmetic
    mean of all quotes is 33.4. Headline implied_multiplier is 2.70.
    Expectancy is 0 — the 33.4 is not a payout and not a profit.
    """
    book = _live_334_vs_270_book()
    stats = paper_stats(book)
    assert stats.trades == 27
    assert stats.wins == 10
    assert stats.pnl == pytest.approx(0.0)
    assert stats.expectancy_per_unit == pytest.approx(0.0)
    assert stats.avg_multiplier_raw == pytest.approx(33.4)
    assert stats.implied_multiplier == pytest.approx(2.70)
    assert stats.win_multiplier_mean == pytest.approx(2.70)
    assert stats.implied_multiplier < 4.0
    assert stats.avg_multiplier_raw > 30.0
    notes = " ".join(audit_notes())
    assert "33.4" in notes and "2.70" in notes
    assert "avg_multiplier" in notes
    assert "implied_multiplier" in notes
    assert "expectancy_per_unit" in notes


def test_implied_multiplier_reconciles_hit_rate_and_expectancy():
    """expectancy ≈ hit_rate * implied_multiplier - 1 when losses pay 0.

    Equal-stake identity is exact by construction. When stakes vary,
    implied still uses per-unit expectancy and trade-count hit_rate;
    stake_weighted_multiplier is the stake-weighted twin.
    """
    even = [
        _trade("win", 3.0),
        _trade("win", 2.4),
        _trade("loss", 12.0),
        _trade("loss", 2.0),
    ]
    even_stats = paper_stats(even)
    assert even_stats.hit_rate == pytest.approx(0.5)
    assert even_stats.expectancy_per_unit == pytest.approx(
        even_stats.hit_rate * even_stats.implied_multiplier - 1.0
    )
    assert even_stats.implied_multiplier == pytest.approx(
        even_stats.win_multiplier_mean
    )
    assert even_stats.stake_weighted_multiplier == pytest.approx(
        even_stats.implied_multiplier
    )

    uneven = [
        _trade("win", 2.70, stake=10.0),
        _trade("win", 2.70, stake=1.0),
        _trade("loss", 80.0, stake=1.0),
        _trade("loss", 2.70, stake=4.0),
    ]
    uneven_stats = paper_stats(uneven)
    assert uneven_stats.expectancy_per_unit == pytest.approx(
        uneven_stats.hit_rate * uneven_stats.implied_multiplier - 1.0
    )
    assert uneven_stats.expectancy_per_unit == pytest.approx(
        uneven_stats.hit_rate_staked * uneven_stats.stake_weighted_multiplier
        - 1.0
    )
    assert uneven_stats.hit_rate != pytest.approx(uneven_stats.hit_rate_staked)
    assert uneven_stats.implied_multiplier != pytest.approx(
        uneven_stats.avg_multiplier_raw
    )


def test_a_single_80x_loser_cannot_pull_the_headline_multiplier_into_the_teens():
    """One 80x loser walks the raw mean into the teens; headline stays 2.70-class.

    Book: 1 unit win at 2.70, 3 unit losses at 2.70, 1 unit loss at 80.
    Raw mean is 18.16. implied_multiplier stays 2.70. PnL is negative.
    """
    book = [
        _trade("win", 2.70),
        _trade("loss", 2.70),
        _trade("loss", 2.70),
        _trade("loss", 2.70),
        _trade("loss", 80.0),
    ]
    stats = paper_stats(book)
    assert stats.avg_multiplier_raw == pytest.approx(18.16)
    assert 10.0 <= stats.avg_multiplier_raw < 20.0
    assert stats.implied_multiplier == pytest.approx(2.70)
    assert stats.implied_multiplier < 4.0
    assert stats.pnl < 0.0
    assert stats.expectancy_per_unit < 0.0
