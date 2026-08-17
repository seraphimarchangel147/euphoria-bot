"""P&L: what actually happened, and which kinds of bets caused it.

A running balance says whether you are up. It does not say why, and "why" is
the only part that transfers to the next session. This tracks two ledgers side
by side and never mixes them:

* **Paper** -- every settled position the policy took. Attributed by distance,
  horizon, multiplier band and verdict, so a win rate can be read against the
  kind of bet that produced it.
* **Real** -- the account balance as the page reports it. Deposits and
  withdrawals are indistinguishable from trading results in a balance feed, so
  unusually large jumps are tagged rather than quietly booked as skill.

Expectancy is reported per unit staked, not per trade. A 90%-win 1.05x strategy
and a 10%-win 12x strategy can carry the same edge, and only the staked-unit
figure lets them be compared.

Headline implied_multiplier (and avg_multiplier, same number) is
(1 + expectancy_per_unit) / hit_rate, not the arithmetic mean of quoted
multipliers. Live /pnl reported 33.4 for that mean (a heavy tail of lottery
tickets, including losers). Back-solving from hit rate and expectancy gave
2.70. The 33.4 figure is kept on avg_multiplier_raw. median_multiplier is
not a headline: on a loser-cluster book it sits at 51.46, not 2.70.
Field names match src/analytics/pnl_metrics.py (PR #6).
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

TRADE_KEEP = 2000
MARK_KEEP = 2000
# A balance jump larger than this multiple of the largest stake we have taken
# is almost certainly a deposit or withdrawal, not a result.
TRANSFER_SUSPECT_X = 5.0
MIN_TRANSFER_ABS = 5.0

MULTIPLIER_BANDS = ((1.0, 1.5), (1.5, 3.0), (3.0, 8.0), (8.0, 25.0), (25.0, 1e9))
HORIZON_BANDS = ((0.0, 10.0), (10.0, 25.0), (25.0, 50.0), (50.0, 1e9))


def multiplier_band(mult: float | None) -> str:
    if not mult:
        return "—"
    for lo, hi in MULTIPLIER_BANDS:
        if lo <= mult < hi:
            return f"{lo:g}-{hi:g}x" if hi < 1e9 else f"{lo:g}x+"
    return "—"


def horizon_band(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    for lo, hi in HORIZON_BANDS:
        if lo <= seconds < hi:
            return f"{lo:g}-{hi:g}s" if hi < 1e9 else f"{lo:g}s+"
    return "—"


def day_key(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


@dataclass
class Trade:
    ts: float
    kind: str                 # paper | real
    outcome: str              # win | loss | void
    stake: float
    delta: float
    multiplier: float | None = None
    distance: int | None = None
    forward: int | None = None
    horizon_s: float | None = None
    side: str = ""
    cell_x: int | None = None
    cell_y: int | None = None
    p_lcb: float | None = None
    ev_lcb: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BalanceMove:
    ts: float
    before: float
    after: float
    delta: float
    suspect_transfer: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _agg() -> dict[str, float]:
    return {"n": 0.0, "wins": 0.0, "staked": 0.0, "win_staked": 0.0, "pnl": 0.0}


def _roll(target: dict[str, dict[str, float]], key: str, trade: Trade) -> None:
    row = target.setdefault(key, _agg())
    stake = max(0.0, trade.stake)
    row["n"] += 1.0
    row["staked"] += stake
    row["pnl"] += trade.delta
    if trade.outcome == "win":
        row["wins"] += 1.0
        row["win_staked"] += stake


def _view(rows: dict[str, dict[str, float]], *, min_n: int = 1) -> list[dict[str, Any]]:
    out = []
    for key, row in rows.items():
        if row["n"] < min_n:
            continue
        staked = row["staked"]
        out.append({
            "key": key,
            "n": int(row["n"]),
            "wins": int(row["wins"]),
            "hit_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
            "hit_rate_staked": round(row["win_staked"] / staked, 4) if staked > 0 else None,
            "hit_rate_stake_weighted": round(row["win_staked"] / staked, 4) if staked > 0 else None,
            "staked": round(staked, 4),
            "pnl": round(row["pnl"], 4),
            # Per unit staked, so a 1.05x grind and a 12x lottery compare.
            "expectancy": round(row["pnl"] / staked, 4) if staked > 0 else None,
        })
    out.sort(key=lambda r: r["pnl"], reverse=True)
    return out


@dataclass
class PnLTracker:
    trades: deque = field(default_factory=lambda: deque(maxlen=TRADE_KEEP))
    moves: deque = field(default_factory=lambda: deque(maxlen=MARK_KEEP))
    by_day_: dict[str, dict[str, float]] = field(default_factory=dict)
    by_distance: dict[str, dict[str, float]] = field(default_factory=dict)
    by_horizon: dict[str, dict[str, float]] = field(default_factory=dict)
    by_multiplier: dict[str, dict[str, float]] = field(default_factory=dict)
    by_side: dict[str, dict[str, float]] = field(default_factory=dict)
    last_balance: float | None = None
    first_balance: float | None = None
    max_stake_seen: float = 0.0
    path: Path | None = None
    _seen_taps: set = field(default_factory=set)
    last_seq: int = 0
    missed_settlements: int = 0
    # "Live" means since this run started, not since the ledger was created.
    session_start_ts: float = 0.0
    session_start_balance: float | None = None
    session_start_pnl: float = 0.0
    balance_curve: deque = field(default_factory=lambda: deque(maxlen=MARK_KEEP))

    def begin_session(self, *, now: float | None = None) -> None:
        """Mark the start of this run so live figures are not all-time ones."""
        self.session_start_ts = time.time() if now is None else now
        self.session_start_balance = self.last_balance
        self.session_start_pnl = sum(
            t.delta for t in self.trades if t.kind == "paper" and t.outcome in ("win", "loss")
        )

    # -- ingestion ----------------------------------------------------------
    def record_paper(self, tap: dict[str, Any]) -> Trade | None:
        """Book one settled paper position. Ignores repeats and voids."""
        if not isinstance(tap, dict):
            return None
        outcome = str(tap.get("outcome") or "")
        if outcome not in ("win", "loss"):
            return None
        # Prefer the settlement's own sequence number. Keying on cell and
        # timestamp meant two settlements landing in the same instant could
        # collide, and a re-scan of the ring could re-book one that had already
        # been counted.
        seq = tap.get("seq")
        key = ("seq", int(seq)) if seq is not None else (
            tap.get("cell_x"), tap.get("cell_y"), round(float(tap.get("ts") or 0.0), 3))
        if key in self._seen_taps:
            return None
        self._seen_taps.add(key)
        if seq is not None:
            if self.last_seq and int(seq) > self.last_seq + 1:
                # A settlement rolled off the ring before it was booked.
                self.missed_settlements += int(seq) - self.last_seq - 1
            self.last_seq = max(self.last_seq, int(seq))
        if len(self._seen_taps) > TRADE_KEEP * 2:
            self._seen_taps = set(list(self._seen_taps)[-TRADE_KEEP:])

        try:
            stake = float(tap.get("stake") or 0.0)
            delta = float(tap.get("delta") or 0.0)
        except (TypeError, ValueError):
            return None
        mult = tap.get("multiplier")
        try:
            mult = float(mult) if mult is not None else None
        except (TypeError, ValueError):
            mult = None

        trade = Trade(
            ts=float(tap.get("ts") or time.time()),
            kind="paper", outcome=outcome, stake=stake, delta=delta,
            multiplier=mult,
            distance=tap.get("distance"),
            forward=tap.get("forward"),
            horizon_s=tap.get("horizon_s"),
            side=str(tap.get("side") or ""),
            cell_x=tap.get("cell_x"), cell_y=tap.get("cell_y"),
            p_lcb=tap.get("p_lcb"), ev_lcb=tap.get("ev_lcb"),
        )
        self.trades.append(trade)
        self.max_stake_seen = max(self.max_stake_seen, stake)
        _roll(self.by_day_, day_key(trade.ts), trade)
        _roll(self.by_distance,
              "at price" if trade.distance == 0 else
              (f"{trade.distance} row" if trade.distance == 1 else
               f"{trade.distance} rows" if trade.distance is not None else "—"), trade)
        _roll(self.by_horizon, horizon_band(trade.horizon_s), trade)
        _roll(self.by_multiplier, multiplier_band(trade.multiplier), trade)
        _roll(self.by_side, trade.side or "—", trade)
        return trade

    def record_taps(self, taps: Iterable[dict[str, Any]]) -> int:
        return sum(1 for tap in taps if self.record_paper(tap) is not None)

    def mark_balance(self, balance: Any, *, now: float | None = None) -> BalanceMove | None:
        """Note the account balance; a change is real money moving."""
        try:
            value = float(balance)
        except (TypeError, ValueError):
            return None
        if value < 0 or not math.isfinite(value):
            return None
        now = time.time() if now is None else now
        self.balance_curve.append({"ts": now, "balance": round(value, 6)})
        if self.session_start_balance is None:
            self.session_start_balance = value
        if self.first_balance is None:
            self.first_balance = value
            self.last_balance = value
            return None
        before = self.last_balance if self.last_balance is not None else value
        delta = value - before
        if abs(delta) < 1e-9:
            return None
        self.last_balance = value
        limit = max(MIN_TRANSFER_ABS, self.max_stake_seen * TRANSFER_SUSPECT_X)
        move = BalanceMove(ts=now, before=before, after=value, delta=delta,
                           suspect_transfer=abs(delta) > limit)
        self.moves.append(move)
        return move

    # -- reporting ----------------------------------------------------------
    def streaks(self) -> dict[str, int]:
        best = worst = cur = 0
        for t in self.trades:
            if t.outcome == "win":
                cur = cur + 1 if cur > 0 else 1
            elif t.outcome == "loss":
                cur = cur - 1 if cur < 0 else -1
            else:
                continue
            best = max(best, cur)
            worst = min(worst, cur)
        return {"current": cur, "longest_win": best, "longest_loss": abs(worst)}

    def paper_summary(self) -> dict[str, Any]:
        """Settled paper book.

        Live /pnl once reported avg_multiplier 33.4 — the arithmetic mean of
        quoted multipliers, losers included. One 80x tail print dominates.
        Back-solving (1 + expectancy_per_unit) / hit_rate gave 2.70. Headline
        implied_multiplier is that reconciled number (avg_multiplier is the
        same value so old /pnl consumers do not keep showing the tail mean).
        The 33.4 tail mean lives on avg_multiplier_raw. median_multiplier is
        a diagnostic only — a loser-cluster book puts it at 51.46, not 2.70.
        """
        rows = [t for t in self.trades if t.kind == "paper" and t.outcome in ("win", "loss")]
        wins = sum(1 for t in rows if t.outcome == "win")
        staked = sum(max(0.0, t.stake) for t in rows)
        win_staked = sum(max(0.0, t.stake) for t in rows if t.outcome == "win")
        pnl = sum(t.delta for t in rows)
        peak = run = 0.0
        drawdown = 0.0
        for t in rows:
            run += t.delta
            peak = max(peak, run)
            drawdown = max(drawdown, peak - run)
        quoted = [t.multiplier for t in rows if t.multiplier]
        win_quoted = [t.multiplier for t in rows if t.outcome == "win" and t.multiplier]
        hit_rate = (wins / len(rows)) if rows else None
        expectancy = (pnl / staked) if staked > 0 else None
        if hit_rate and hit_rate > 0 and expectancy is not None:
            implied = (1.0 + expectancy) / hit_rate
        else:
            implied = None
        hit_rate_staked = (win_staked / staked) if staked > 0 else None
        if hit_rate_staked and hit_rate_staked > 0 and expectancy is not None:
            stake_weighted = (1.0 + expectancy) / hit_rate_staked
        else:
            stake_weighted = None
        win_mean = (sum(win_quoted) / len(win_quoted)) if win_quoted else None
        return {
            "trades": len(rows),
            "wins": wins,
            "losses": len(rows) - wins,
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "hit_rate_staked": round(hit_rate_staked, 4) if hit_rate_staked is not None else None,
            "hit_rate_stake_weighted": round(hit_rate_staked, 4) if hit_rate_staked is not None else None,
            "staked": round(staked, 4),
            "pnl": round(pnl, 4),
            "expectancy_per_unit": round(expectancy, 4) if expectancy is not None else None,
            "implied_multiplier": round(implied, 3) if implied is not None else None,
            "avg_multiplier": round(implied, 3) if implied is not None else None,
            "avg_multiplier_raw": round(sum(quoted) / len(quoted), 3) if quoted else None,
            "stake_weighted_multiplier": round(stake_weighted, 3) if stake_weighted is not None else None,
            "median_multiplier": round(statistics.median(quoted), 3) if quoted else None,
            "win_multiplier_mean": round(win_mean, 3) if win_mean is not None else None,
            "avg_win_multiplier": round(win_mean, 3) if win_mean is not None else None,
            "max_drawdown": round(drawdown, 4),
            "streaks": self.streaks(),
        }

    def real_summary(self) -> dict[str, Any]:
        moves = list(self.moves)
        clean = [m for m in moves if not m.suspect_transfer]
        gross = (self.last_balance - self.first_balance) if (
            self.last_balance is not None and self.first_balance is not None) else None
        return {
            "first_balance": round(self.first_balance, 6) if self.first_balance is not None else None,
            "balance": round(self.last_balance, 6) if self.last_balance is not None else None,
            "change": round(gross, 6) if gross is not None else None,
            "moves": len(moves),
            "trading_change": round(sum(m.delta for m in clean), 6) if clean else 0.0,
            "suspect_transfers": sum(1 for m in moves if m.suspect_transfer),
            "recent": [m.to_dict() for m in moves[-12:]],
        }

    def attribution(self) -> dict[str, Any]:
        return {
            "by_distance": _view(self.by_distance),
            "by_horizon": _view(self.by_horizon),
            "by_multiplier": _view(self.by_multiplier),
            "by_side": _view(self.by_side),
        }

    def by_day(self) -> list[dict[str, Any]]:
        rows = _view(self.by_day_)
        rows.sort(key=lambda r: r["key"])
        return rows

    def session(self, *, now: float | None = None) -> dict[str, Any]:
        """Live figures: this run only, and the two ledgers set against each other.

        Reconciliation is the point. If the real balance is falling while the
        paper policy is flat, the losses are coming from somewhere the policy
        never chose -- and that is worth knowing immediately, not at the end.
        """
        now = time.time() if now is None else now
        start = self.session_start_ts or now
        rows = [t for t in self.trades
                if t.kind == "paper" and t.outcome in ("win", "loss") and t.ts >= start]
        wins = sum(1 for t in rows if t.outcome == "win")
        staked = sum(max(0.0, t.stake) for t in rows)
        paper = sum(t.delta for t in rows)
        real = None
        if self.last_balance is not None and self.session_start_balance is not None:
            real = self.last_balance - self.session_start_balance
        moves = [m for m in self.moves if m.ts >= start]
        real_trading = sum(m.delta for m in moves if not m.suspect_transfer)
        return {
            "started_at": start,
            "elapsed_s": round(max(0.0, now - start), 1),
            "paper_pnl": round(paper, 6),
            "paper_trades": len(rows),
            "paper_wins": wins,
            "paper_hit_rate": round(wins / len(rows), 4) if rows else None,
            "paper_hit_rate_staked": (
                round(sum(max(0.0, t.stake) for t in rows if t.outcome == "win") / staked, 4)
                if staked > 0 else None
            ),
            "paper_hit_rate_stake_weighted": (
                round(sum(max(0.0, t.stake) for t in rows if t.outcome == "win") / staked, 4)
                if staked > 0 else None
            ),
            "paper_staked": round(staked, 6),
            "real_change": round(real, 6) if real is not None else None,
            "real_trading_change": round(real_trading, 6),
            "real_moves": len(moves),
            # Real minus paper: what happened that the policy did not choose.
            "unexplained": round(real_trading - paper, 6) if real is not None else None,
            "balance_now": round(self.last_balance, 6) if self.last_balance is not None else None,
            "balance_at_start": round(self.session_start_balance, 6)
                if self.session_start_balance is not None else None,
        }

    def report(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "session": self.session(now=now),
            "paper": self.paper_summary(),
            "real": self.real_summary(),
            "attribution": self.attribution(),
            "by_day": self.by_day(),
            "integrity": {
                "settlements_booked": self.last_seq,
                "missed_settlements": self.missed_settlements,
                "trades_held": len(self.trades),
            },
            "balance_curve": list(self.balance_curve)[-180:],
            "recent": [t.to_dict() for t in list(self.trades)[-25:]],
        }

    # -- persistence --------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "trades": [t.to_dict() for t in list(self.trades)[-TRADE_KEEP:]],
            "moves": [m.to_dict() for m in list(self.moves)[-MARK_KEEP:]],
            "first_balance": self.first_balance,
            "last_balance": self.last_balance,
            "max_stake_seen": self.max_stake_seen,
            "last_seq": self.last_seq,
            "missed_settlements": self.missed_settlements,
            "balance_curve": list(self.balance_curve)[-MARK_KEEP:],
        }

    @classmethod
    def from_json(cls, data: Any) -> "PnLTracker":
        out = cls()
        if not isinstance(data, dict):
            return out
        for raw in (data.get("trades") or []):
            if not isinstance(raw, dict):
                continue
            try:
                trade = Trade(**{k: raw.get(k) for k in Trade.__annotations__ if k in raw})
            except TypeError:
                continue
            out.trades.append(trade)
            out.max_stake_seen = max(out.max_stake_seen, float(trade.stake or 0.0))
            _roll(out.by_day_, day_key(trade.ts), trade)
            _roll(out.by_distance,
                  "at price" if trade.distance == 0 else
                  (f"{trade.distance} row" if trade.distance == 1 else
                   f"{trade.distance} rows" if trade.distance is not None else "—"), trade)
            _roll(out.by_horizon, horizon_band(trade.horizon_s), trade)
            _roll(out.by_multiplier, multiplier_band(trade.multiplier), trade)
            _roll(out.by_side, trade.side or "—", trade)
        for raw in (data.get("moves") or []):
            if isinstance(raw, dict):
                try:
                    out.moves.append(BalanceMove(**{k: raw.get(k) for k in BalanceMove.__annotations__ if k in raw}))
                except TypeError:
                    continue
        for name in ("first_balance", "last_balance"):
            try:
                val = data.get(name)
                setattr(out, name, float(val) if val is not None else None)
            except (TypeError, ValueError):
                pass
        try:
            out.max_stake_seen = max(out.max_stake_seen, float(data.get("max_stake_seen") or 0.0))
        except (TypeError, ValueError):
            pass
        try:
            out.last_seq = int(data.get("last_seq") or 0)
            out.missed_settlements = int(data.get("missed_settlements") or 0)
        except (TypeError, ValueError):
            pass
        for point in (data.get("balance_curve") or []):
            if isinstance(point, dict) and "balance" in point:
                out.balance_curve.append(point)
        return out

    def save(self) -> None:
        if not self.path:
            return
        try:
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path | None) -> "PnLTracker":
        if not path or not Path(path).is_file():
            out = cls()
            out.path = Path(path) if path else None
            return out
        try:
            out = cls.from_json(json.loads(Path(path).read_text()))
        except (OSError, ValueError):
            out = cls()
        out.path = Path(path)
        return out
