"""Honest paper-book multipliers. No I/O.

Live /pnl paper_summary reports avg_multiplier 33.4 by taking the
arithmetic mean of every trade's quoted multiplier, winners and losers
together. That is a heavy-tail lie: one unread 80x (or a cluster of
lottery quotes that all lost) walks the mean into the thirties.

Back-solving the same book from hit_rate and expectancy_per_unit gives
implied_multiplier 2.70. That is the headline. avg_multiplier_raw is
kept only so the lie can be audited, never displayed as the multiplier.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class HonestPaperStats:
    """Paper-book rates that do not invent a 33.4x payout.

    implied_multiplier is the headline: (1 + expectancy_per_unit) / hit_rate.
    It is the 2.70-class number. avg_multiplier_raw is the 33.4-class lie.

    When losses pay 0 and stakes are equal:
        expectancy_per_unit == hit_rate * implied_multiplier - 1
    and implied_multiplier equals win_multiplier_mean.

    When stakes vary, implied_multiplier still uses per-unit expectancy
    and the trade-count hit_rate (so the identity above still holds by
    definition). The stake-weighted twin is stake_weighted_multiplier:
        (1 + expectancy_per_unit) / hit_rate_staked
    which equals the stake-weighted mean of winning quotes when losses
    pay 0. expectancy_per_unit = pnl / staked stays the honest return.
    """

    trades: int
    wins: int
    hit_rate: float
    hit_rate_staked: float
    staked: float
    pnl: float
    expectancy_per_unit: float
    implied_multiplier: float
    median_multiplier: float
    stake_weighted_multiplier: float
    win_multiplier_mean: float
    avg_multiplier_raw: float


def paper_stats(trades: list[dict]) -> HonestPaperStats:
    """Summarise a paper book. Each trade: outcome, stake, delta, multiplier.

    outcome is win|loss. delta is trusted as given — this function does
    not recompute PnL from the quote, so it cannot invent profit.
    """
    n = 0
    wins = 0
    staked = 0.0
    win_staked = 0.0
    pnl = 0.0
    multipliers: list[float] = []
    win_multipliers: list[float] = []

    for raw in trades:
        outcome = str(raw.get("outcome", "")).strip().lower()
        stake = float(raw.get("stake") or 0.0)
        delta = float(raw.get("delta") or 0.0)
        quote = float(raw.get("multiplier") or 0.0)
        n += 1
        staked += stake
        pnl += delta
        multipliers.append(quote)
        if outcome == "win":
            wins += 1
            win_staked += stake
            win_multipliers.append(quote)

    hit_rate = wins / n if n else 0.0
    hit_rate_staked = win_staked / staked if staked else 0.0
    expectancy = pnl / staked if staked else 0.0
    implied = (1.0 + expectancy) / hit_rate if hit_rate else 0.0
    stake_weighted = (
        (1.0 + expectancy) / hit_rate_staked if hit_rate_staked else 0.0
    )
    return HonestPaperStats(
        trades=n,
        wins=wins,
        hit_rate=hit_rate,
        hit_rate_staked=hit_rate_staked,
        staked=staked,
        pnl=pnl,
        expectancy_per_unit=expectancy,
        implied_multiplier=implied,
        median_multiplier=float(median(multipliers)) if multipliers else 0.0,
        stake_weighted_multiplier=stake_weighted,
        win_multiplier_mean=(
            sum(win_multipliers) / len(win_multipliers) if win_multipliers else 0.0
        ),
        avg_multiplier_raw=(
            sum(multipliers) / len(multipliers) if multipliers else 0.0
        ),
    )


def audit_notes() -> list[str]:
    """Same-class errors in pnl.py paper_summary, for the owner-side file.

    pnl.py is not in this repo. These notes name the stats that share the
    unweighted-quote / unweighted-count lie. expectancy_per_unit stays.
    """
    return [
        "paper_summary.avg_multiplier is the unweighted mean of every quoted "
        "multiplier, winners and losers. That is avg_multiplier_raw — the "
        "33.4-class heavy-tail lie. Headline implied_multiplier (2.70-class).",
        "paper_summary.hit_rate is wins/trades. When stakes vary it is not "
        "hit_rate_staked (win stake / total stake). Same class of error: a "
        "tiny lottery ticket gets the same vote as a full-size staple.",
        "implied_multiplier uses per-unit expectancy and trade-count "
        "hit_rate: (1 + expectancy_per_unit) / hit_rate. When stakes vary "
        "that is not the stake-weighted twin; stake_weighted_multiplier = "
        "(1 + expectancy_per_unit) / hit_rate_staked is.",
        "expectancy_per_unit = pnl / staked is the honest money-weighted "
        "return. Keep it. A mean of per-trade (delta/stake) ratios restarts "
        "the unweighted lie and can invent a better book than the cash.",
        "win_multiplier_mean equals implied_multiplier only when winning "
        "stakes are equal. It is a diagnostic, not a headline.",
        "median_multiplier is the median of all quotes, including losers. "
        "It resists a single 80x outlier but not a majority of lottery "
        "quotes that lost — on the 33.4 book it sits on the loser cluster, "
        "not on 2.70. Not a headline.",
        "Any pnl.py avg_win, avg_loss, or profit_factor built from "
        "unweighted per-trade ratios has this class of error. Do not "
        "headline them. Keep expectancy_per_unit; stop headlining "
        "avg_multiplier.",
    ]
