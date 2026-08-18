"""The operator's own trades: what was tapped, what it paid, what we thought.

Real money is the only ground truth this system can get. Everything else is a
free label -- honest, plentiful, and never staked on. A settled human tap says
three things at once that no free label can: the multiplier we scored is the
multiplier that actually paid, the settlement rule matches our touch predicate,
and the decision-to-fill delay did not eat the edge.

Three deliberate separations:

* **A separate calibrator.** Real taps are not a random sample of the grid --
  they land where a human wants to bet, which correlates with the multiplier
  and therefore with probability. Beta posteriors assume exchangeable draws, so
  mixing a few dozen selected taps into buckets built from tens of thousands of
  unconditional labels would distort exactly the buckets the policy trades on.
  Kept apart, the *divergence* between the two is the most useful diagnostic
  the system can produce.
* **A separate ledger.** The paper bankroll answers "would the policy have made
  money". Folding in discretionary human taps destroys the only clean answer to
  that question, and the balance feed already books real results once.
* **Agreement, tracked explicitly.** For every real tap we record what our
  surface said about that cell at that moment -- so "the model is beating the
  human" or the reverse becomes a number rather than an impression.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TAP_KEEP = 500
WINDOW_KEEP = 40
# A tap older or newer than this cannot be genuine; the clock or the sender is
# wrong, and either way it must not enter the ledger.
MAX_CLOCK_SKEW_S = 120.0
MAX_SETTLE_DELAY_MS = 10_000
SEEN_KEEP = 4000
SIDES = ("up", "down", "at-price")
# Window-join tag for taps the overlay never resolved to a square.
JOIN_SQUARE_UNKNOWN = "square-unknown"
JOIN_WINDOW_SETTLED = "window-settled"
JOIN_UNRESOLVED = "unresolved"
JOIN_JOINED = "joined"


@dataclass
class PlayerTap:
    tap_id: str
    ts: float
    cell_x: int | None
    cell_y: int | None
    side: str
    distance: int | None
    multiplier: float | None
    quoted_breakeven: float | None
    price: float | None
    # What our surface said about this cell at the moment it was tapped.
    our_p_cal: float | None = None
    our_p_lcb: float | None = None
    our_ev_lcb: float | None = None
    our_verdict: str | None = None
    horizon_s: float | None = None
    vol: str | None = None
    matched: bool = False
    capture_source: str = "pointer"
    stake_requested: float | None = None
    # Window-join bookkeeping. A cell-less tap is kept, then tagged
    # window-settled / square-unknown when its ts falls in a closed window.
    # window_outcome is the *helper* grade (tape/grade.last), never the
    # player's own square — we do not know which cell they hit.
    join_status: str = "unresolved"
    square_known: bool = False
    join_tag: str | None = None
    window_outcome: str | None = None
    window_t0: float | None = None
    window_t1: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlayerSettle:
    tap_id: str
    ts: float
    win: bool
    stake: float
    payout: float
    delta: float
    cell_x: int | None
    cell_y: int | None
    multiplier: float | None
    our_p_cal: float | None
    our_p_lcb: float | None
    our_verdict: str | None
    join_delay_ms: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_event(payload: Any, *, now: float | None = None) -> dict[str, Any] | None:
    """Re-validate server-side. The extension's own check ran on the far side
    of an unauthenticated socket and cannot be trusted on its own."""
    if not isinstance(payload, dict):
        return None
    kind = payload.get("event_type")
    if kind not in ("player-tap", "player-settle"):
        return None
    tap_id = payload.get("tap_id")
    if not isinstance(tap_id, str) or not (3 <= len(tap_id) <= 96):
        return None
    try:
        ts = float(payload.get("ts"))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    now = time.time() if now is None else now
    # Events carry epoch milliseconds.
    seconds = ts / 1000.0 if ts > 1e11 else ts
    if abs(seconds - now) > MAX_CLOCK_SKEW_S:
        return None

    out: dict[str, Any] = {"event_type": kind, "tap_id": tap_id, "ts": seconds,
                           "capture_source": payload.get("capture_source") or "pointer"}
    try:
        raw_stake = payload.get("stake_requested")
        out["stake_requested"] = float(raw_stake) if raw_stake is not None else None
    except (TypeError, ValueError):
        out["stake_requested"] = None
    for key in ("cell_x", "cell_y"):
        val = payload.get(key)
        out[key] = int(val) if isinstance(val, (int, float)) else None
    side = payload.get("side")
    out["side"] = side if side in SIDES else ""
    for key, cast in (("distance", int), ("multiplier", float),
                      ("quoted_breakeven", float), ("price", float)):
        try:
            raw = payload.get(key)
            out[key] = cast(raw) if raw is not None else None
        except (TypeError, ValueError):
            out[key] = None
    if out["multiplier"] is not None and out["multiplier"] <= 0:
        return None

    if kind == "player-settle":
        if payload.get("join_status") != "joined":
            return None
        if not isinstance(payload.get("win"), bool):
            return None
        try:
            stake = float(payload.get("stake"))
            payout = float(payload.get("payout"))
            delay = float(payload.get("join_delay_ms"))
        except (TypeError, ValueError):
            return None
        if stake <= 0 or payout < 0 or not (0 <= delay <= MAX_SETTLE_DELAY_MS):
            return None
        out.update({"win": bool(payload.get("win")), "stake": stake,
                    "payout": payout, "join_delay_ms": delay})
    return out


@dataclass
class PlayerLedger:
    """Real taps, real settlements, and how our surface rated each one."""

    taps: dict[str, PlayerTap] = field(default_factory=dict)
    settles: deque = field(default_factory=lambda: deque(maxlen=TAP_KEEP))
    order: deque = field(default_factory=lambda: deque(maxlen=TAP_KEEP))
    seen: deque = field(default_factory=lambda: deque(maxlen=SEEN_KEEP))
    staked: float = 0.0
    returned: float = 0.0
    wins: int = 0
    losses: int = 0
    unmatched_settles: int = 0
    unresolved_taps: int = 0
    window_settled: int = 0
    rejected: int = 0
    path: Path | None = None
    closed_windows: deque = field(default_factory=lambda: deque(maxlen=WINDOW_KEEP))

    # -- ingestion ----------------------------------------------------------
    def record_tap(self, event: dict[str, Any] | None,
                   score: dict[str, Any] | None = None) -> PlayerTap | None:
        if not isinstance(event, dict) or not event.get("tap_id"):
            self.rejected += 1
            return None
        tap_id = event["tap_id"]
        if tap_id in self.taps:
            return None
        cell_x, cell_y = event.get("cell_x"), event.get("cell_y")
        square_known = cell_x is not None and cell_y is not None
        if not square_known:
            # Overlay gridState has no screenToCell — keep the tap anyway.
            # It stays unresolved until a closed window covers its timestamp.
            self.unresolved_taps += 1
        tap = PlayerTap(
            tap_id=tap_id, ts=event["ts"], cell_x=cell_x, cell_y=cell_y,
            side=event.get("side") or "", distance=event.get("distance"),
            multiplier=event.get("multiplier"),
            quoted_breakeven=event.get("quoted_breakeven"),
            price=event.get("price"),
            our_p_cal=(score or {}).get("p_cal"),
            our_p_lcb=(score or {}).get("p_lcb"),
            our_ev_lcb=(score or {}).get("ev_lcb"),
            our_verdict=(score or {}).get("verdict"),
            horizon_s=(score or {}).get("horizon_s"),
            vol=(score or {}).get("vol"),
            matched=bool(square_known and score is not None),
            capture_source=event.get("capture_source") or "pointer",
            stake_requested=event.get("stake_requested"),
            join_status=JOIN_UNRESOLVED,
            square_known=square_known,
        )
        self.taps[tap_id] = tap
        self.order.append(tap_id)
        while len(self.taps) > TAP_KEEP and self.order:
            self.taps.pop(self.order.popleft(), None)
        return tap

    def record_settle(self, event: dict[str, Any] | None,
                      *, calibrator: Any = None) -> PlayerSettle | None:
        if not isinstance(event, dict) or not event.get("tap_id"):
            self.rejected += 1
            return None
        tap = self.taps.get(event["tap_id"])
        if tap is None:
            # The settle arrived without its tap. Counted so a silently broken
            # joiner is distinguishable from "the operator is not trading".
            self.unmatched_settles += 1
            return None
        stake = float(event["stake"])
        payout = float(event["payout"])
        settle = PlayerSettle(
            tap_id=tap.tap_id, ts=event["ts"], win=bool(event["win"]),
            stake=stake, payout=payout, delta=payout - stake,
            cell_x=tap.cell_x, cell_y=tap.cell_y, multiplier=tap.multiplier,
            our_p_cal=tap.our_p_cal, our_p_lcb=tap.our_p_lcb,
            our_verdict=tap.our_verdict, join_delay_ms=event.get("join_delay_ms"),
        )
        self.settles.append(settle)
        self.staked += stake
        self.returned += payout
        if settle.win:
            self.wins += 1
        else:
            self.losses += 1
        # Weight 1.0, into its own buckets. A real outcome carries the same one
        # bit a free label does; the money changes the loss function, not the
        # likelihood.
        if calibrator is not None and tap.our_p_cal is not None and tap.horizon_s:
            calibrator.observe(tap.our_p_cal, tap.horizon_s, tap.vol or "MED", settle.win)
        tap.join_status = JOIN_JOINED
        tap.square_known = tap.cell_x is not None and tap.cell_y is not None
        return settle

    def remember_window(self, t0: float, t1: float, *,
                        outcome: str | None = None,
                        lo: float | None = None,
                        hi: float | None = None) -> dict[str, Any]:
        """Store a closed helper window so later orphans can be backfilled."""
        win = {"t0": float(t0), "t1": float(t1), "outcome": outcome,
               "lo": lo, "hi": hi}
        if self.closed_windows:
            last = self.closed_windows[-1]
            if last.get("t0") == win["t0"] and last.get("t1") == win["t1"]:
                last.update(win)
                return last
        self.closed_windows.append(win)
        return win

    def join_window(self, t0: float, t1: float, *,
                    outcome: str | None = None) -> list[PlayerTap]:
        """Tag unmatched cell-less taps whose ts falls in [t0, t1].

        SAFE join: window-settled / square-unknown. The window outcome is the
        helper's grade (tape.last_closed / grade.last), not the player's square.
        Does not book wins, losses, stake, or payout — no invented PnL, and
        hit_rate stays null until a real cell-matched settle exists.
        """
        try:
            t0_f, t1_f = float(t0), float(t1)
        except (TypeError, ValueError):
            return []
        if t1_f < t0_f:
            return []
        joined: list[PlayerTap] = []
        for tap in self.taps.values():
            if tap.join_status != JOIN_UNRESOLVED:
                continue
            if tap.cell_x is not None and tap.cell_y is not None:
                # Known square waits for a real player-settle, not the helper pin.
                continue
            if tap.ts < t0_f or tap.ts > t1_f:
                continue
            tap.join_status = JOIN_WINDOW_SETTLED
            tap.square_known = False
            tap.join_tag = JOIN_SQUARE_UNKNOWN
            tap.window_outcome = outcome
            tap.window_t0 = t0_f
            tap.window_t1 = t1_f
            if self.unresolved_taps > 0:
                self.unresolved_taps -= 1
            self.window_settled += 1
            joined.append(tap)
        return joined

    def backfill(self) -> int:
        """One-shot walk: join stored orphans against remembered windows."""
        n = 0
        for win in list(self.closed_windows):
            t0, t1 = win.get("t0"), win.get("t1")
            if t0 is None or t1 is None:
                continue
            n += len(self.join_window(float(t0), float(t1),
                                      outcome=win.get("outcome")))
        return n

    def already_seen(self, tap_id: str) -> bool:
        return tap_id in self.seen

    def mark_seen(self, tap_id: str) -> None:
        self.seen.append(tap_id)

    # -- reporting ----------------------------------------------------------
    @property
    def trades(self) -> int:
        return self.wins + self.losses

    def summary(self) -> dict[str, Any]:
        pnl = self.returned - self.staked
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "hit_rate": round(self.wins / self.trades, 4) if self.trades else None,
            "staked": round(self.staked, 4),
            "returned": round(self.returned, 4),
            "pnl": round(pnl, 4),
            "expectancy_per_unit": round(pnl / self.staked, 4) if self.staked > 0 else None,
            "open_taps": len(self.taps),
            "unmatched_settles": self.unmatched_settles,
            "unresolved_taps": self.unresolved_taps,
            "window_settled": self.window_settled,
            "rejected": self.rejected,
        }

    def agreement(self) -> dict[str, Any]:
        """How the operator's taps rated against our own surface."""
        rows = [s for s in self.settles if s.our_verdict]
        buckets: dict[str, dict[str, float]] = {}
        for s in rows:
            row = buckets.setdefault(s.our_verdict, {"n": 0.0, "wins": 0.0, "pnl": 0.0, "staked": 0.0})
            row["n"] += 1
            row["staked"] += s.stake
            row["pnl"] += s.delta
            if s.win:
                row["wins"] += 1
        out = []
        for verdict, row in sorted(buckets.items(), key=lambda kv: -kv[1]["pnl"]):
            out.append({
                "our_verdict": verdict,
                "n": int(row["n"]),
                "hit_rate": round(row["wins"] / row["n"], 4) if row["n"] else None,
                "staked": round(row["staked"], 4),
                "pnl": round(row["pnl"], 4),
                "expectancy": round(row["pnl"] / row["staked"], 4) if row["staked"] > 0 else None,
            })
        matched = [s for s in self.settles if s.our_p_cal is not None]
        brier = None
        if matched:
            brier = round(sum((s.our_p_cal - (1.0 if s.win else 0.0)) ** 2
                              for s in matched) / len(matched), 6)
        return {
            "by_our_verdict": out,
            "matched": len(matched),
            "player_brier": brier,
        }

    def report(self) -> dict[str, Any]:
        window_joins = [
            {
                "tap_id": t.tap_id,
                "ts": t.ts,
                "join_status": t.join_status,
                "join_tag": t.join_tag,
                "square_known": t.square_known,
                "window_outcome": t.window_outcome,
                "window_t0": t.window_t0,
                "window_t1": t.window_t1,
                "stake_requested": t.stake_requested,
                "cell_x": t.cell_x,
                "cell_y": t.cell_y,
            }
            for t in self.taps.values()
            if t.join_status == JOIN_WINDOW_SETTLED
        ]
        return {
            "summary": self.summary(),
            "agreement": self.agreement(),
            "recent": [s.to_dict() for s in list(self.settles)[-20:]],
            "window_joins": window_joins[-20:],
        }

    # -- persistence --------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "version": 2,
            "taps": [self.taps[i].to_dict() for i in list(self.order) if i in self.taps],
            "settles": [s.to_dict() for s in list(self.settles)[-TAP_KEEP:]],
            "closed_windows": list(self.closed_windows)[-WINDOW_KEEP:],
            "staked": self.staked, "returned": self.returned,
            "wins": self.wins, "losses": self.losses,
            "unmatched_settles": self.unmatched_settles,
            "unresolved_taps": self.unresolved_taps,
            "window_settled": self.window_settled,
            "rejected": self.rejected,
        }

    @classmethod
    def from_json(cls, data: Any) -> "PlayerLedger":
        out = cls()
        if not isinstance(data, dict):
            return out
        for raw in (data.get("settles") or []):
            if not isinstance(raw, dict):
                continue
            try:
                out.settles.append(PlayerSettle(**{
                    k: raw.get(k) for k in PlayerSettle.__annotations__ if k in raw}))
            except TypeError:
                continue
        for name in ("staked", "returned"):
            try:
                setattr(out, name, float(data.get(name) or 0.0))
            except (TypeError, ValueError):
                pass
        for name in ("wins", "losses", "unmatched_settles", "unresolved_taps",
                     "window_settled", "rejected"):
            try:
                setattr(out, name, int(data.get(name) or 0))
            except (TypeError, ValueError):
                pass
        for raw in (data.get("taps") or []):
            if not isinstance(raw, dict) or not raw.get("tap_id"):
                continue
            try:
                tap = PlayerTap(
                    tap_id=str(raw["tap_id"]),
                    ts=float(raw.get("ts") or 0.0),
                    cell_x=raw.get("cell_x"),
                    cell_y=raw.get("cell_y"),
                    side=raw.get("side") or "",
                    distance=raw.get("distance"),
                    multiplier=raw.get("multiplier"),
                    quoted_breakeven=raw.get("quoted_breakeven"),
                    price=raw.get("price"),
                    our_p_cal=raw.get("our_p_cal"),
                    our_p_lcb=raw.get("our_p_lcb"),
                    our_ev_lcb=raw.get("our_ev_lcb"),
                    our_verdict=raw.get("our_verdict"),
                    horizon_s=raw.get("horizon_s"),
                    vol=raw.get("vol"),
                    matched=bool(raw.get("matched")),
                    capture_source=raw.get("capture_source") or "pointer",
                    stake_requested=raw.get("stake_requested"),
                    join_status=raw.get("join_status") or JOIN_UNRESOLVED,
                    square_known=bool(raw["square_known"]) if "square_known" in raw
                    else (raw.get("cell_x") is not None and raw.get("cell_y") is not None),
                    join_tag=raw.get("join_tag"),
                    window_outcome=raw.get("window_outcome"),
                    window_t0=raw.get("window_t0"),
                    window_t1=raw.get("window_t1"),
                )
            except (TypeError, ValueError):
                continue
            out.taps[tap.tap_id] = tap
            out.order.append(tap.tap_id)
        live_unresolved = sum(
            1 for t in out.taps.values()
            if t.join_status == JOIN_UNRESOLVED and not t.square_known
        )
        live_windowed = sum(
            1 for t in out.taps.values() if t.join_status == JOIN_WINDOW_SETTLED
        )
        # Persisted counter may include historical orphans that were never stored.
        out.unresolved_taps = max(out.unresolved_taps, live_unresolved)
        out.window_settled = max(out.window_settled, live_windowed)
        for raw in (data.get("closed_windows") or []):
            if not isinstance(raw, dict):
                continue
            try:
                t0, t1 = float(raw["t0"]), float(raw["t1"])
            except (KeyError, TypeError, ValueError):
                continue
            out.closed_windows.append({
                "t0": t0, "t1": t1,
                "outcome": raw.get("outcome"),
                "lo": raw.get("lo"), "hi": raw.get("hi"),
            })
        return out

    def save(self) -> None:
        if not self.path:
            return
        try:
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2))
            os.chmod(tmp, 0o600)      # a record of real bets; treat it as private
            tmp.replace(path)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path | None) -> "PlayerLedger":
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
