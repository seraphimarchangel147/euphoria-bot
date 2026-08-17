"""In-memory operator control room: start/stop, mode, think, session ingest."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from config import settings
from src.analytics.calibration import Calibrator
from src.analytics.gridboard import GridBoard
from src.analytics.lesson import GradeBook, price_band, price_touched
from src.analytics.policy import Bankroll, Policy
from src.analytics.player import PlayerLedger, validate_event
from src.analytics.pnl import PnLTracker
from src.analytics.pressure import CoilTracker
from src.analytics.forecast import DiffusionSmoother
from src.analytics.reachability import ReachabilityLedger
from src.analytics.cell_edge import CellEdgeLedger
from src.analytics.regime import VolRegime
from src.analytics.rolling_regime import RollingVolRegime
from src.analytics.setups import SetupMemory
from src.analytics.tilt import TiltGuard
from src.analytics.traversal import Traversal
from src.analytics.signal import GRID_STALE_S, Signal, TickBuffer, _column_geometry, compute_signal
from src.monitor.wallet import WalletWatcher
from src.auth.session import SessionState, apply_payload, load_session, persist_session
from src.control.overlay_rules import stand_aside

log = logging.getLogger("euphoria.control")

Mode = str  # "manual" | "auto"
ORACLE_STALE_S = 3.0
ORACLE_POLL_S = 5.0
OHLC_TTL_S = 60.0
LOOP_S = 0.5
DECISION_KEEP = 40
EXTENSION_STALE_S = 20.0
EXTENSION_SOURCES = frozenset({"extension", "page", "dom", "ws"})
CALIBRATION_SAVE_S = 30.0
# Shadow book: flat stake so the record reads as skill, not sizing.
SHADOW_NOTIONAL = 1_000_000.0
SHADOW_STAKE = 1.0
# The shadow book measures edge per unit staked, and that quantity is defined
# regardless of how much money is left. Sizing it like a real roll made ruin
# truncate the measurement instead of the market ending it: a 50-minute window
# hit zero after 20 minutes and 583 trades, stopped tapping, and then reported
# a per-unit figure that was really just the floor. Give it a notional deep
# enough that a window ends when time runs out.
# It only shadows cells it can actually price and that pay more than even.
SHADOW_MIN_MULTIPLIER = 1.01
# How long quoted cells may be carried across a cell-less geometry update.
CELLS_GRACE_S = 10.0


class ControlError(ValueError):
    pass


class ControlRoom:
    def __init__(
        self,
        *,
        dry_run: bool | None = None,
        enable_oracle: bool = True,
        enable_ohlc: bool | None = None,
        oracle_fn: Callable[[list[str]], dict] | None = None,
        ohlc_fn: Callable[..., dict] | None = None,
        submit_fn: Callable[[Signal, SessionState], Any] | None = None,
        session: SessionState | None = None,
        session_path=None,
        token_path=None,
        size: float | None = None,
        calibration_path=None,
        bankroll_path=None,
        traversal_path=None,
        pnl_path=None,
        player_path=None,
        player_calibration_path=None,
        reachability_path=None,
        cell_edge_path=None,
        learn: bool = True,
        enable_wallet: bool | None = None,
    ) -> None:
        self.dry_run = settings.DRY_RUN if dry_run is None else dry_run
        self.enable_oracle = enable_oracle
        self.enable_ohlc = enable_oracle if enable_ohlc is None else enable_ohlc
        self._oracle_fn = oracle_fn
        self._ohlc_fn = ohlc_fn
        self._submit_fn = submit_fn
        self.session_path = session_path
        self.token_path = token_path
        self.size = size if size is not None else min(0.10, settings.MAX_TRADE_USDM)
        self.grid: dict[str, Any] | None = None

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self.running = False
        self.mode: Mode = "manual"
        self.ticks = TickBuffer()
        self.session = session if session is not None else load_session(
            session_path=session_path, token_path=token_path
        )
        self.last_signal: Signal | None = None
        self.last_decision: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.decisions: deque[dict[str, Any]] = deque(maxlen=DECISION_KEEP)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_oracle = 0.0
        self._ohlc: dict[str, Any] | None = None
        self._ohlc_at = 0.0
        self._last_fingerprint: tuple | None = None
        self.scoreboard = GradeBook()
        self.setups = SetupMemory()

        # -- surface / learning stack --------------------------------------
        # `learn=False` keeps a room purely advisory (tests, replay): the
        # surface is still drawn, but nothing is booked or persisted.
        self.learn = learn
        store = calibration_path if calibration_path is not None else (
            settings.CALIBRATION_STORE if learn else None
        )
        self.calibrator = Calibrator(store)
        self.policy = Policy(
            min_edge=settings.MIN_EDGE,
            kelly_fraction_used=settings.KELLY_FRACTION,
            max_stake=settings.MAX_TRADE_USDM,
        )
        self.bankroll_path = (
            bankroll_path if bankroll_path is not None
            else (settings.BANKROLL_STORE if learn else None)
        )
        self.bankroll = self._load_bankroll()
        # Interim rail: what the tape has actually reached, independent of
        # what the model thinks it can reach. Cells two rows out returned
        # -1.0000 over 269 trades with zero wins while the model rated them
        # better than one in four. Fed from the same free labels the
        # calibrator gets; consulted only to refuse a bet, never to justify
        # one. See src/analytics/reachability.py.
        self.reachability_path = (
            reachability_path if reachability_path is not None
            else (settings.REACHABILITY_STORE if learn else None)
        )
        self.reachability = self._load_reachability()
        self._reach_saved = 0.0
        # Quote-key edge ledger. Hook rows are (m, d, h, touched);
        # CellEdgeLedger.observe is (m, touched, d, h). Adapter below.
        # Measurement only — beats=True cannot justify a bet.
        self.cell_edge_path = (
            cell_edge_path if cell_edge_path is not None
            else (settings.CELL_EDGE_STORE if learn else None)
        )
        self.cell_edge = self._load_cell_edge()
        # The rail defers to per-quote evidence when a key is warm. Live,
        # it was demanding 1.14x on a bucket whose 1.03x subset resolved
        # 131/131 -- refusing the best-evidenced cells on the board.
        self.reachability.quotes = self.cell_edge
        # (quote key, vol regime) -> [hits, n]. Splits the candidate edge
        # by the regime it was measured in; see regime_edge_view.
        # Persisted for the same reason the rail is: this is the third
        # component whose evidence was silently thrown away on restart,
        # and I restarted five times in an hour while deploying fixes.
        # In-memory evidence is evidence you will lose.
        self._regime_edge: dict[tuple[int, str], list[float]] = {}
        self._load_regime_edge()
        self._regime_edge_saved = 0.0
        self._cell_edge_saved = 0.0
        self.board = GridBoard(calibrator=self.calibrator, bankroll=self.bankroll,
                               reachability=self.reachability)
        self.board.on_quote_label = self._ingest_quote_labels
        # Shadow book: what the policy WOULD do if it were willing to trade on
        # unproven evidence. Deliberately exploratory and deliberately separate.
        #
        # The disciplined policy currently takes nothing -- every cell is
        # `unproven` or `unreachable` -- so a shadow that honoured those gates
        # would sit flat and teach nothing. This one takes its best cell every
        # window at a flat stake, so its record is a clean read on whether the
        # ranking has any skill at all, uncontaminated by Kelly sizing.
        #
        # No calibrator: these are the same cells the grid already grades for
        # free, and feeding them twice would double-count one outcome.
        self.shadow_bankroll = Bankroll(
            start=SHADOW_NOTIONAL, balance=SHADOW_NOTIONAL, peak=SHADOW_NOTIONAL,
        )
        self.shadow_board = GridBoard(calibrator=None, bankroll=self.shadow_bankroll)
        self._calib_saved = 0.0
        self._bankroll_saved = 0.0
        self.traversal_path = (
            traversal_path if traversal_path is not None
            else (settings.TRAVERSAL_STORE if learn else None)
        )
        self.traversal = Traversal.load(self.traversal_path)
        self._traversal_saved = 0.0
        self.coil = CoilTracker()
        self._last_coil: dict[str, Any] | None = None
        # Regime label from the house's volatility, independent of our sigma.
        self.regime = VolRegime()
        # Same source, 20-minute window instead of the whole retained
        # history. The all-history terciles saturate on HIGH whenever
        # volatility trends, because two thirds of a rising series sits
        # above its own early quantiles. Used ONLY to label the quote-edge
        # split: swapping the calibrator's key would invalidate 174k
        # learned observations to fix a label, which is a bad trade.
        self.rolling_regime = RollingVolRegime()
        # Sigma is refit from scratch every pass and has no memory of the last
        # one, so it swung 16-fold over 50 minutes in which price moved six
        # rows. Carrying it across passes is what stops the cell ranking from
        # simply selecting the largest current estimation error.
        self.smoother = DiffusionSmoother()
        self.pnl_path = (
            pnl_path if pnl_path is not None
            else (settings.PNL_STORE if learn else None)
        )
        self.pnl = PnLTracker.load(self.pnl_path)
        self._pnl_saved = 0.0
        # Book every settlement the instant it resolves. Re-scanning the
        # board's bounded tap ring meant a burst could roll off before anyone
        # looked, and that money would simply never have existed.
        self.board.on_settle = self.pnl.record_paper
        self.pnl.begin_session()
        self.tilt = TiltGuard()
        self.tilt.begin_session()
        # Real money, kept apart from the free labels and the paper roll.
        self.player_path = (
            player_path if player_path is not None
            else (settings.PLAYER_STORE if learn else None)
        )
        self.player = PlayerLedger.load(self.player_path)
        self.player.backfill()
        self.player_calibrator = Calibrator(
            player_calibration_path if player_calibration_path is not None
            else (settings.PLAYER_CALIBRATION_STORE if learn else None)
        )
        self._player_saved = 0.0
        # Live balance: the tab's in-game figure, plus an on-chain reading.
        self.wallet_page: dict[str, Any] | None = None
        self.frames: dict[str, Any] | None = None
        self.limits: dict[str, Any] | None = None
        self.wallet = WalletWatcher()
        # Follows the oracle switch so offline tests never touch the RPC.
        self.enable_wallet = enable_oracle if enable_wallet is None else enable_wallet

        self._seq = 0
        self._ext_seen: float | None = None
        self._ext_quote_source: str | None = None
        self._session_seen: float | None = None
        self._tape_wid: int | None = None
        self._tape_open: dict[str, Any] | None = None
        self._last_closed: dict[str, Any] | None = None

    # -- commands -----------------------------------------------------------
    def start(self) -> dict[str, Any]:
        with self._lock:
            self.running = True
            self.last_error = None
            self._ensure_loop()
            self._record_unlocked(
                None, action="start", detail=f"mode={self.mode} dry_run={self.dry_run}"
            )
            self._notify_unlocked()
        log.info("control started mode=%s dry_run=%s", self.mode, self.dry_run)
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.running = False
            self._record_unlocked(None, action="stop", detail="operator stop")
            self._notify_unlocked()
        log.info("control stopped")
        return self.status()

    def set_mode(self, mode: str) -> dict[str, Any]:
        mode = (mode or "").strip().lower()
        if mode not in ("manual", "auto"):
            raise ControlError("mode must be 'manual' or 'auto'")
        with self._lock:
            self.mode = mode
            self._record_unlocked(None, action="mode", detail=mode)
            self._notify_unlocked()
        log.info("control mode=%s", mode)
        return self.status()

    def set_grid(self, grid: dict[str, Any] | None) -> None:
        """Store the page's grid snapshot, stamped with when it arrived.

        `now_ms` is a reading of the server clock taken in the tab. By the time
        the think loop uses it, it is 200-1500ms old -- emit throttle, worker
        hop, HTTP POST, loop cadence -- and always old in the same direction.
        Recording arrival lets the geometry advance the anchor instead of
        replaying a stale instant, and lets a frozen tab be detected rather
        than silently trusted forever.
        """
        if isinstance(grid, dict) and grid:
            now = time.time()
            snapshot = dict(grid)
            snapshot["_received_local"] = now
            # Do not let a cell-less update wipe a live board. Geometry-only
            # frames arrive whenever the quote feed hiccups, and clobbering the
            # quoted cells with an empty list blinds the surface to every
            # multiplier until the next quote frame lands. Stale cells are safe
            # to carry: each names an absolute column, so an expired one is
            # dropped by the forecast rather than mispriced.
            if not snapshot.get("cells") and isinstance(self.grid, dict):
                held = self.grid.get("cells")
                age = now - float(self.grid.get("_received_local") or 0.0)
                if held and age <= CELLS_GRACE_S:
                    snapshot["cells"] = held
                    snapshot["cells_carried_s"] = round(age, 2)
                    for key in ("authoritative", "multiplier_source", "server_volatility"):
                        if key in self.grid and key not in grid:
                            snapshot[key] = self.grid[key]
            self.grid = snapshot
            # The house's own volatility estimate. When it marks that up before
            # our realised tape moves, it is forecasting an expansion we cannot
            # see yet -- worth reading, and free.
            if snapshot.get("server_volatility") is not None:
                self.coil.note_house_volatility(snapshot.get("server_volatility"))
                self.regime.observe(snapshot.get("server_volatility"))
                self.rolling_regime.observe(snapshot.get("server_volatility"),
                                            now=time.time())

    def ingest_quotes(self, quotes: Any, source: str = "extension") -> int:
        if quotes is None:
            return 0
        if isinstance(quotes, dict) and "quotes" in quotes:
            self.set_grid(quotes.get("grid"))
            quotes = quotes.get("quotes")
        if isinstance(quotes, dict) and not any(k in quotes for k in ("symbol", "asset", "price")):
            # {"ETH": 3000, "BTC": {"price": 1}} or {"ETH": {"price": 3000, "ts": ...}}
            items = []
            for sym, val in quotes.items():
                if isinstance(val, dict):
                    items.append({"symbol": sym, **val, "source": val.get("source") or source})
                else:
                    items.append({"symbol": sym, "price": val, "source": source})
            quotes = items
        elif isinstance(quotes, dict):
            quotes = [{**quotes, "source": quotes.get("source") or source}]
        n = self.ticks.extend(quotes if isinstance(quotes, list) else [])
        ext_source = source if source in EXTENSION_SOURCES else None
        if isinstance(quotes, list):
            for item in quotes:
                raw = item.get("source") if isinstance(item, dict) else getattr(item, "source", None)
                if raw in EXTENSION_SOURCES:
                    ext_source = str(raw)
                    break
        if n or ext_source:
            with self._cond:
                if ext_source:
                    self._ext_seen = time.time()
                    self._ext_quote_source = ext_source
                self._notify_unlocked()
        return n

    def set_wallet(self, wallet: Any) -> None:
        """Adopt the in-game balance the tab just reported.

        Euphoria settles server-side, so the chain shows zero on a funded
        account -- the page's own number is the real one. First sighting rebases
        the paper bankroll so Kelly sizes against a real roll instead of the
        placeholder.
        """
        if not isinstance(wallet, dict):
            return
        limits = wallet.get("limits") if isinstance(wallet.get("limits"), dict) else None
        balance = None
        if wallet.get("balance") is not None:
            try:
                balance = float(wallet["balance"])
            except (TypeError, ValueError):
                balance = None
            if balance is not None and balance < 0:
                balance = None
        if balance is None and not limits:
            return
        with self._lock:
            if balance is not None:
                self.wallet_page = {
                    "balance": balance,
                    "field": str(wallet.get("field") or ""),
                    "source": str(wallet.get("source") or "page"),
                    "ts": float(wallet.get("ts") or time.time()),
                }
                if not self.bankroll.live_synced:
                    self.bankroll.sync_live(balance)
                # Real money moving, whoever moved it.
                self.pnl.mark_balance(balance)
                self.tilt.note_balance(balance)
            if limits:
                self.limits = limits
                self._apply_limits_unlocked(limits)
                # The house's lifetime counter makes trade rate observable.
                self.tilt.note_trade_count(limits.get("settledTrades"))
            self._notify_unlocked()

    def _apply_limits_unlocked(self, limits: dict[str, Any]) -> None:
        """Size against the account's real caps, never above our own."""
        caps = []
        for key in ("maxTrade", "maxStakePerSquare"):
            try:
                val = float(limits.get(key))
            except (TypeError, ValueError):
                continue
            if val > 0:
                caps.append(val)
        if caps:
            self.policy.max_stake = min(settings.MAX_TRADE_USDM, *caps)

    def set_frames(self, frames: Any) -> None:
        """Distinct WebSocket frame layouts, for decoding the quote grid."""
        if not isinstance(frames, dict):
            return
        shapes = frames.get("shapes")
        if not isinstance(shapes, list):
            return
        with self._lock:
            self.frames = {
                "collected": int(frames.get("collected") or len(shapes)),
                "shapes": shapes[:40],
                "updated_at": time.time(),
            }

    def frames_view(self) -> dict[str, Any]:
        return self.frames or {"collected": 0, "shapes": [], "updated_at": 0.0}

    def wallet_view(self) -> dict[str, Any]:
        return {
            "page": self.wallet_page,
            "chain": self.wallet.to_dict(),
            "limits": self.limits,
            "max_stake": self.policy.max_stake,
            "sizing_balance": round(self.bankroll.balance, 6),
            "sizing_source": "page" if self.bankroll.live_synced else "paper",
        }

    def ingest_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ControlError("session body must be a JSON object")
        quotes = payload.get("quotes")
        n_quotes = self.ingest_quotes(quotes, source="extension")
        if payload.get("grid"):
            self.set_grid(payload.get("grid"))
        if payload.get("wallet"):
            self.set_wallet(payload.get("wallet"))
        if payload.get("frames"):
            self.set_frames(payload.get("frames"))
        with self._lock:
            changed = apply_payload(self.session, payload, now=time.time())
            view = self.session.public_view()
            if changed:
                persist_session(self.session, session_path=self.session_path, token_path=self.token_path)
            # Session ping keeps the helper handshake; it is not a tab quote.
            self._session_seen = time.time()
            self._notify_unlocked()
        log.info(
            "session ingest: cookies=%s privyUserId=%s artefacts=%s quotes=%d",
            view["cookie_names"],
            "set" if view["privy_user_id_set"] else "absent",
            view["artefact_names"],
            n_quotes,
        )
        return {"ok": True, "session": view, "quotes_ingested": n_quotes}

    def think(self, now: float | None = None, *, notify: bool = False) -> Signal:
        ohlc = self._refresh_ohlc()
        now = time.time() if now is None else now
        with self._lock:
            snapshot = self.ticks.snapshot(now=now)
            # Grade everything whose column has closed *before* scoring the next
            # surface, so this pass already benefits from what just resolved.
            self.board.settle(snapshot, now=now)
            self.shadow_board.settle(snapshot, now=now)

            sig = compute_signal(
                self.ticks, now=now, size=self.size, grid=self.grid, ohlc=ohlc,
                memory=self.setups,
                calibrator=self.calibrator,
                policy=self.policy,
                bankroll=self.bankroll,
                vol_bucket_override=(
                    self.regime.bucket() if self.regime.ready else None
                ),
                smoother=self.smoother,
                reachability=self.reachability,
            )
            self.last_signal = sig
            last = self.ticks.latest("ETH")
            self._observe_traversal(snapshot, now)

            if sig.surface:
                scores = self._scores_for_board(sig)
                _, _, column_s = _column_geometry(self.grid, now)
                self.board.record(
                    scores, now=now, vol=sig.vol_bucket, column_s=column_s,
                    server_offset_s=self._server_offset_s(now, column_s),
                )
                # Paper-book the chosen cell whenever the operator has the room
                # running, in either mode. This is what lets the balance curve
                # answer "would this have made money" before a cent is risked;
                # live submission stays gated exactly as before.
                if self.running and self.learn and sig.plan:
                    for s in scores:
                        if s.cell_x == sig.plan.get("cell_x") and s.cell_y == sig.plan.get("cell_y"):
                            self.board.register_tap(s, now=now)
                            break
                if self.running:
                    self._shadow_pick(scores, now)
            self.scoreboard.update(
                sig,
                snapshot,
                now=now,
                grid=self.grid,
                last_price=last.price if last else None,
            )
            self._maybe_save_calibration(now)
            self._maybe_save_bankroll(now)
            self._maybe_save_reachability(now)
            self._maybe_save_regime_edge(now)
            self._maybe_save_cell_edge(now)
            self._maybe_save_traversal(now)
            self._maybe_save_pnl(now)
            self._maybe_save_player(now)
            if notify:
                self._notify_unlocked()
            return sig

    def _column_mapper(self, now: float):
        """ts -> grid column, on the same clock the board is drawn on."""
        cur_x, offset, column_s = _column_geometry(self.grid, now)
        if column_s <= 0:
            column_s = 5.0
        server_now = cur_x * column_s + offset
        skew = server_now - now
        return (lambda ts: int((ts + skew) // column_s)), column_s

    def _row_height(self) -> float:
        if isinstance(self.grid, dict):
            for key in ("dollars_per_line", "cell_height", "price_interval", "priceInterval"):
                try:
                    val = float(self.grid.get(key))
                except (TypeError, ValueError):
                    continue
                if val > 0:
                    return val
        return 0.5

    def _observe_traversal(self, snapshot, now: float) -> None:
        """Feed the tape to the grid-walk tracker. Never fatal."""
        try:
            column_of, _ = self._column_mapper(now)
            self.traversal.observe(snapshot, dpl=self._row_height(), column_of=column_of)
        except Exception as exc:
            log.warning("traversal: %s", exc)

    def ingest_player_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Accept one of the operator's real taps or settlements.

        Re-validated here rather than trusted: the extension's own check runs on
        the far side of an unauthenticated localhost socket, so anything able to
        POST could otherwise write straight into a ledger of real bets. A tap is
        only matched if the cell was one we actually quoted, and every tap_id is
        accepted once.
        """
        now = time.time()
        event = validate_event(payload, now=now)
        if event is None:
            self.player.rejected += 1
            return {"ok": False, "error": "invalid player event"}
        tap_id = event["tap_id"]
        if self.player.already_seen(tap_id) and event["event_type"] == "player-tap":
            return {"ok": False, "error": "duplicate tap"}

        with self._lock:
            if event["event_type"] == "player-tap":
                self.player.mark_seen(tap_id)
                score = self.board.score_at(event["cell_x"], event["cell_y"], event["ts"]) \
                    if event.get("cell_x") is not None else None
                # A cell we never quoted cannot have been tapped on our board.
                if score is not None and event.get("multiplier") and score.get("multiplier"):
                    drift = abs(event["multiplier"] - score["multiplier"]) / score["multiplier"]
                    if drift > 0.05:
                        score = None
                tap = self.player.record_tap(event, score)
                if tap is not None and not tap.square_known:
                    # Late tap against an already-closed window, or startup backfill.
                    self.player.backfill()
                self._notify_unlocked()
                return {"ok": True, "kind": "tap", "matched": bool(tap and tap.matched),
                        "join_status": tap.join_status if tap else None,
                        "square_known": bool(tap.square_known) if tap else False}

            settle = self.player.record_settle(event, calibrator=self.player_calibrator)
            self._notify_unlocked()
            return {"ok": True, "kind": "settle", "matched": settle is not None}

    def player_view(self) -> dict[str, Any]:
        try:
            self._harvest_grade_windows()
            self.player.backfill()
            body = self.player.report()
            body["calibration"] = self.player_calibrator.stats()
            return body
        except Exception as exc:
            log.warning("player: %s", exc)
            return {"summary": {}, "agreement": {}, "recent": [], "calibration": {}}

    def _maybe_save_player(self, now: float) -> None:
        if not self.player_path or now - self._player_saved < CALIBRATION_SAVE_S:
            return
        self._player_saved = now
        self.player.save()
        if self.player_calibrator.path:
            self.player_calibrator.save()

    def regime_view(self) -> dict[str, Any]:
        return self.regime.stats()

    def tilt_view(self) -> dict[str, Any]:
        """Behaviour, not markets: pace against drawdown."""
        try:
            return self.tilt.assess()
        except Exception as exc:
            log.warning("tilt: %s", exc)
            return {"flagged": False, "severity": "calm", "headline": "unavailable",
                    "reasons": []}

    def pnl_view(self) -> dict[str, Any]:
        """Settled results with attribution, plus the real balance history.

        Settlements arrive live via the board callback; the sweep here is a
        backstop for anything booked before the callback was attached.
        """
        try:
            self.pnl.record_taps(list(self.board.taps))
            return self.pnl.report()
        except Exception as exc:
            log.warning("pnl: %s", exc)
            return {"session": {}, "paper": {}, "real": {}, "attribution": {},
                    "by_day": [], "integrity": {}, "balance_curve": [], "recent": []}

    def _maybe_save_pnl(self, now: float) -> None:
        if not self.pnl_path or now - self._pnl_saved < CALIBRATION_SAVE_S:
            return
        self._pnl_saved = now
        self.pnl.record_taps(list(self.board.taps))
        self.pnl.save()

    def coil_view(self, now: float | None = None, walk: dict[str, Any] | None = None) -> dict[str, Any]:
        """Is the tape winding up for a move? Direction is not this question."""
        now = time.time() if now is None else now
        walk = walk if walk is not None else self.traversal_view(now)
        cur = walk.get("current") or {}
        rows = [r for r in walk.get("by_hour", []) if r.get("mean_dwell_columns")]
        typical = (sum(r["mean_dwell_columns"] for r in rows) / len(rows)) if rows else None
        try:
            return self.coil.assess(
                self.ticks.snapshot(now=now), now=now,
                dwell_columns=int(cur.get("dwell_columns") or 0),
                typical_dwell=typical,
                position_in_row=cur.get("position_in_row"),
            ).to_dict()
        except Exception as exc:
            log.warning("coil: %s", exc)
            return {"score": 0.0, "state": "unknown", "headline": "unavailable",
                    "factors": [], "ready": False, "confident": False}

    # -- experiment control -------------------------------------------------
    # Two windows of the SAME configuration came in at -0.1066 and -0.5220 --
    # 0.35 apart, which the per-trade standard error calls a 5-sigma event. It
    # is not one. Trades inside a window all ride a single price path, so the
    # effective sample size scales with windows, not trades, and a sequential
    # A/B cannot separate a change from the hour it ran in.
    #
    # Arms are switched in place instead, in short alternating blocks, so both
    # see the same tape. Switching without a restart is the point: a restart
    # empties the reachability ledger and the shadow book, so it would change
    # more than the variable under test.
    ARMS = ("anchor_on", "anchor_off")

    def set_experiment(self, arm: str) -> dict[str, Any]:
        arm = (arm or "").strip().lower()
        if arm not in self.ARMS:
            raise ControlError(f"arm must be one of {self.ARMS}")
        settings.USE_HOUSE_ANCHOR = (arm == "anchor_on")
        self._experiment_arm = arm
        self._experiment_switched = time.time()
        log.info("experiment arm: %s", arm)
        return self.experiment_view()

    def experiment_view(self) -> dict[str, Any]:
        sh = self.shadow_bankroll
        return {
            "arm": getattr(self, "_experiment_arm",
                           "anchor_on" if settings.USE_HOUSE_ANCHOR else "anchor_off"),
            "arms": list(self.ARMS),
            "switched_at": getattr(self, "_experiment_switched", 0.0),
            "house_anchor": bool(settings.USE_HOUSE_ANCHOR),
            # Cumulative counters the harness differences to get a block result.
            "shadow_trades": sh.trades,
            "shadow_staked": round(sh.staked, 6),
            "shadow_pnl": round(sh.pnl, 6),
        }

    def closes_view(self, limit: int = 9) -> dict[str, Any]:
        """Where the last few 5-second columns actually closed, by row.

        The surface says where price *might* go. This says where it has just
        been: one entry per settled column, which row it closed on, and whether
        that was a step up, down, or a hold. Reading the two together is how you
        tell a grid that is genuinely drifting from one that has been sitting on
        the same row printing near-certainties.
        """
        dpl = 0.0
        if isinstance(self.grid, dict):
            try:
                dpl = float(self.grid.get("dollars_per_line") or 0.0)
            except (TypeError, ValueError):
                dpl = 0.0
        rows: list[dict[str, Any]] = []
        prev_row: int | None = None
        # Oldest first so the deltas read forward in time; reversed at the end.
        for w in list(self.board.windows)[-(limit + 1):]:
            close = w.get("close")
            if close is None:
                continue
            row = int(close // dpl) if dpl > 0 else None
            step = None if (row is None or prev_row is None) else row - prev_row
            rows.append({
                "ts": w.get("end_ts"),
                "close": close,
                "open": w.get("open"),
                "row": row,
                "step": step,
                "dir": ("up" if step > 0 else "down" if step < 0 else "flat")
                       if step is not None else None,
                "lo": w.get("lo"),
                "hi": w.get("hi"),
                "ticks": w.get("ticks"),
            })
            if row is not None:
                prev_row = row
        # The first entry has no predecessor, so it has no direction to report.
        rows = [r for r in rows if r["step"] is not None][-limit:]
        ups = sum(1 for r in rows if r["dir"] == "up")
        downs = sum(1 for r in rows if r["dir"] == "down")
        flats = sum(1 for r in rows if r["dir"] == "flat")
        return {
            "closes": list(reversed(rows)),     # newest first for display
            "n": len(rows),
            "up": ups,
            "down": downs,
            "flat": flats,
            "net_rows": sum(r["step"] or 0 for r in rows),
            "dollars_per_line": dpl or None,
        }

    def traversal_view(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        column_of, _ = self._column_mapper(now)
        last = self.ticks.latest("ETH")
        return self.traversal.stats(
            now=now, column_of=column_of,
            price=last.price if last else 0.0, dpl=self._row_height(),
        )

    def _maybe_save_traversal(self, now: float) -> None:
        if not self.traversal_path or now - self._traversal_saved < CALIBRATION_SAVE_S:
            return
        self._traversal_saved = now
        self.traversal.save()

    def _shadow_pick(self, scores, now: float) -> None:
        """Take the best-ranked quoted cell, evidence gate or not.

        Flat stake, so the resulting record measures whether the ranking picks
        winners -- not whether Kelly sized them well. Ranking is by the
        calibrated mean rather than the lower bound, because the point of a
        shadow book is to find out if the unproven cells are any good.
        """
        # Take the best-ranked quoted cell whatever the sign of its EV.
        #
        # Requiring a positive edge silenced the book entirely -- the model
        # rates every quoted cell negative, which is probably correct against a
        # uniform ~18% overround, and a silent shadow measures nothing. Taking
        # the top-ranked cell regardless turns the book into a test of the
        # *ranking*: noise loses about the overround, skill loses less. Either
        # answer is worth having, and neither costs anything.
        best = None
        for s in scores:
            if not s.multiplier or s.multiplier < SHADOW_MIN_MULTIPLIER:
                continue
            if s.t_end <= 0 or s.ev is None:
                continue
            if best is None or s.ev > best.ev:
                best = s
        if best is None:
            return
        try:
            from dataclasses import replace as _replace

            self.shadow_board.register_tap(
                _replace(best, stake=SHADOW_STAKE, verdict="tap"), now=now,
            )
        except Exception as exc:
            log.warning("shadow: %s", exc)

    def shadow_view(self) -> dict[str, Any]:
        """The shadow book, and how it compares with the real account."""
        stats = self.shadow_board.stats()
        roll = self.shadow_bankroll.to_dict()
        session = self.pnl.session()
        return {
            "bankroll": roll,
            "open": stats.get("open_taps", 0),
            "settled": roll.get("trades", 0),
            "recent": stats.get("recent_taps", []),
            "stake": SHADOW_STAKE,
            "note": ("exploratory — takes its best-ranked cell every window regardless of "
                     "the evidence gate or the sign of its EV. Noise loses about the house "
                     "overround (~18% of stake); skill loses less."),
            "house_overround_baseline": -0.18,
            "compare": {
                "you_real": session.get("real_trading_change"),
                "policy_paper": session.get("paper_pnl"),
                "shadow_paper": round(roll.get("pnl") or 0.0, 4),
                "shadow_trades": roll.get("trades", 0),
                "shadow_hit_rate": roll.get("hit_rate"),
                # The number that matters: P&L per unit staked, against the
                # ~-0.18 a random pick would return.
                "shadow_per_unit": (
                    round((roll.get("pnl") or 0.0) / roll["staked"], 4)
                    if roll.get("staked") else None
                ),
            },
        }

    def _server_offset_s(self, now: float, column_s: float) -> float:
        """server_clock - local_clock, from the page's own advanced snapshot."""
        cur_x, offset, _ = _column_geometry(self.grid, now)
        if not isinstance(self.grid, dict) or self.grid.get("_received_local") is None:
            return 0.0
        return (cur_x * column_s + offset) - now

    @staticmethod
    def _scores_for_board(sig: Signal):
        """Re-hydrate the surface rows the board needs to grade later."""
        from src.analytics.policy import CellScore

        out = []
        for row in sig.surface:
            if not isinstance(row, dict) or "verdict" not in row:
                continue
            try:
                out.append(CellScore(**row))
            except TypeError:
                continue
        return out

    # -- bankroll persistence ----------------------------------------------
    def _load_bankroll(self) -> Bankroll:
        """Restore the equity curve and settled-tap ledger from the last run."""
        blank = Bankroll(
            start=settings.BANKROLL_START, balance=settings.BANKROLL_START,
            peak=settings.BANKROLL_START,
        )
        if not self.bankroll_path:
            return blank
        try:
            data = json.loads(Path(self.bankroll_path).read_text())
        except (OSError, ValueError):
            return blank
        try:
            return Bankroll.from_json(data.get("bankroll"), default_start=settings.BANKROLL_START)
        except Exception as exc:                       # a corrupt file must not brick startup
            log.warning("bankroll store unreadable, starting fresh: %s", exc)
            return blank

    def save_bankroll(self) -> None:
        if not self.bankroll_path:
            return
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "bankroll": self.bankroll.to_json(),
            "taps": list(self.board.taps)[-200:],
        }
        try:
            path = Path(self.bankroll_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        except OSError:
            pass    # the ledger is a record, never a reason to stop trading

    def _load_reachability(self) -> ReachabilityLedger:
        """Restore what the tape has reached. A cold guard is a silent guard."""
        if not self.reachability_path:
            return ReachabilityLedger()
        try:
            data = json.loads(Path(self.reachability_path).read_text())
        except (OSError, ValueError):
            return ReachabilityLedger()
        try:
            return ReachabilityLedger.from_json(data)
        except Exception as exc:            # a corrupt file must not brick startup
            log.warning("reachability restore failed: %s", exc)
            return ReachabilityLedger()

    def save_reachability(self) -> None:
        if not self.reachability_path:
            return
        try:
            path = Path(self.reachability_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.reachability.to_json()))
            tmp.replace(path)
        except OSError:
            pass    # the ledger is a record, never a reason to stop trading

    def _maybe_save_reachability(self, now: float) -> None:
        if not self.reachability_path or now - self._reach_saved < CALIBRATION_SAVE_S:
            return
        self._reach_saved = now
        self.save_reachability()

    def _ingest_quote_labels(self, rows) -> None:
        """GridBoard emits (m, d, h, touched, vol). Ledger wants (m, touched, d, h).

        The regime is split out separately rather than folded in, because the
        1.02-1.04x band is really the claim "a quiet market stays in its row
        for five seconds". That is a statement ABOUT the regime, so a rate
        averaged across regimes cannot test it -- and every observation behind
        it so far came from tape where price moved $0.14 in three minutes.
        """
        for item in rows or ():
            vol = None
            try:
                if len(item) == 5:
                    m, d, h, touched, vol = item
                else:
                    m, d, h, touched = item
            except (TypeError, ValueError):
                continue
            self.cell_edge.observe(m, touched, d, h)
            if vol:
                self._regime_edge_observe(m, touched, self._rolling_label(vol))

    def _regime_edge_file(self) -> Path | None:
        if not self.cell_edge_path:
            return None
        p = Path(self.cell_edge_path)
        return p.with_name(p.stem + ".regime.json")

    def _load_regime_edge(self) -> None:
        path = self._regime_edge_file()
        if not path:
            return
        try:
            rows = json.loads(path.read_text()).get("rows") or []
        except (OSError, ValueError):
            return
        for row in rows:
            try:
                key, vol, hits, n = int(row[0]), str(row[1]), float(row[2]), float(row[3])
            except (TypeError, ValueError, IndexError):
                continue
            if n <= 0 or hits < 0 or hits > n:
                continue        # corruption, not evidence
            self._regime_edge[(key, vol)] = [hits, n]

    def save_regime_edge(self) -> None:
        path = self._regime_edge_file()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "version": 1,
                "rows": [[k, v, h, n] for (k, v), (h, n) in self._regime_edge.items()],
            }))
            tmp.replace(path)
        except OSError:
            pass        # a record is never a reason to stop trading

    def _maybe_save_regime_edge(self, now: float) -> None:
        if now - self._regime_edge_saved < CALIBRATION_SAVE_S:
            return
        self._regime_edge_saved = now
        self.save_regime_edge()

    def _rolling_label(self, fallback: str) -> str:
        """Rolling-window regime, or the scoring-time label if it is cold."""
        try:
            now = time.time()
            val = self.rolling_regime.last_value
            if val is None:
                return fallback
            label = self.rolling_regime.bucket(val, now=now)
        except Exception:
            return fallback
        return label if label and label != "UNKNOWN" else fallback

    def _regime_edge_observe(self, multiplier, touched, vol: str) -> None:
        try:
            key = int(round(float(multiplier) * 100))
        except (TypeError, ValueError):
            return
        if key <= 100:
            return
        bucket = self._regime_edge.setdefault((key, str(vol)), [0.0, 0.0])
        bucket[0] += 1.0 if touched else 0.0
        bucket[1] += 1.0

    def regime_edge_view(self, lo: int = 102, hi: int = 104,
                         min_n: int = 40) -> dict[str, Any]:
        """The candidate edge, split by the regime it was measured in.

        If the edge is real it is a property of quiet tape and should shrink or
        invert as volatility rises. A flat profile across regimes would mean it
        is something else, and a rising one would mean it is noise.
        """
        import math
        out = []
        for vol in ("LOW", "MED", "HIGH", "UNKNOWN"):
            hits = n = 0.0
            for (key, v), (h, c) in self._regime_edge.items():
                if v == vol and lo <= key <= hi:
                    hits += h
                    n += c
            if n < min_n:
                continue
            rate = hits / n
            z = 1.96
            den = 1 + z * z / n
            ctr = (rate + z * z / (2 * n)) / den
            half = z * math.sqrt(max(rate * (1 - rate) / n + z * z / (4 * n * n), 0)) / den
            lower = max(0.0, ctr - half)
            m = lo / 100.0
            out.append({
                "regime": vol, "n": int(n), "hits": int(hits),
                "rate": round(rate, 5), "lower95": round(lower, 5),
                "ev_mean": round(rate * m - 1.0, 5),
                "ev_lower": round(lower * m - 1.0, 5),
            })
        return {"band": f"{lo / 100:.2f}-{hi / 100:.2f}x", "min_n": min_n,
                "regimes": out, "tracked_keys": len(self._regime_edge)}

    def _load_cell_edge(self) -> CellEdgeLedger:
        if not self.cell_edge_path:
            return CellEdgeLedger()
        try:
            data = json.loads(Path(self.cell_edge_path).read_text())
        except (OSError, ValueError):
            return CellEdgeLedger()
        try:
            return CellEdgeLedger.from_json(data)
        except Exception as exc:
            log.warning("cell_edge restore failed: %s", exc)
            return CellEdgeLedger()

    def save_cell_edge(self) -> None:
        if not self.cell_edge_path:
            return
        try:
            path = Path(self.cell_edge_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cell_edge.to_json()))
            tmp.replace(path)
        except OSError:
            pass

    def _maybe_save_cell_edge(self, now: float) -> None:
        if not self.cell_edge_path or now - self._cell_edge_saved < CALIBRATION_SAVE_S:
            return
        self._cell_edge_saved = now
        self.save_cell_edge()

    def _maybe_save_bankroll(self, now: float) -> None:
        if not self.bankroll_path or now - self._bankroll_saved < CALIBRATION_SAVE_S:
            return
        self._bankroll_saved = now
        self.save_bankroll()

    def _maybe_save_calibration(self, now: float) -> None:
        if not self.learn or self.calibrator.path is None:
            return
        if now - self._calib_saved < CALIBRATION_SAVE_S:
            return
        self._calib_saved = now
        self.calibrator.save()

    def _grid_cells(self) -> list[dict[str, Any]]:
        grid = self.grid if isinstance(self.grid, dict) else None
        raw = grid.get("cells") if grid else None
        if not isinstance(raw, list):
            return []
        return [c for c in raw if isinstance(c, dict)]

    def _quote_for(self, tile: dict[str, Any] | None) -> dict[str, Any] | None:
        """Match a pick/candidate to a live quoted cell (cell, then side+distance)."""
        if not isinstance(tile, dict):
            return None
        cells = self._grid_cells()
        if not cells:
            return None
        cx, cy = tile.get("cell_x"), tile.get("cell_y")
        if cx is not None and cy is not None:
            for c in cells:
                if c.get("cell_x") == cx and c.get("cell_y") == cy:
                    return c
        side = tile.get("side")
        try:
            dist = int(tile["distance"]) if tile.get("distance") is not None else None
        except (TypeError, ValueError):
            dist = None
        if not side or dist is None:
            return None
        matches = []
        for c in cells:
            if c.get("side") != side:
                continue
            try:
                if int(c.get("distance") or 0) != dist:
                    continue
            except (TypeError, ValueError):
                continue
            matches.append(c)
        if not matches:
            return None

        def _fwd(c: dict[str, Any]) -> int:
            try:
                return int(c.get("forward") or 99)
            except (TypeError, ValueError):
                return 99

        matches.sort(key=_fwd)
        return matches[0]

    @staticmethod
    def _quote_fields(cell: dict[str, Any]) -> tuple[Any, Any]:
        mult = cell.get("multiplier")
        be = cell.get("break_even_probability")
        if be is None and mult:
            try:
                m = float(mult)
                be = round(1.0 / m, 6) if m > 0 else None
            except (TypeError, ValueError):
                be = None
        return mult, be

    def _stamp_quote(self, tile: dict[str, Any]) -> None:
        q = self._quote_for(tile)
        if not q:
            return
        mult, be = self._quote_fields(q)
        if mult is not None:
            tile["multiplier"] = mult
        if be is not None:
            tile["break_even_probability"] = be

    def _stamp_think_quotes(self, body: dict[str, Any]) -> None:
        pick = body.get("pick")
        if isinstance(pick, dict):
            self._stamp_quote(pick)
        sug = body.get("suggested")
        if isinstance(sug, dict):
            self._stamp_quote(sug)
        for key in ("candidates", "looking_at"):
            rows = body.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    self._stamp_quote(row)

    def _quote_surface_rows(self, limit: int = 96) -> list[dict[str, Any]]:
        """Dashboard fallback: paint the live quote grid even if scoring skipped it."""
        rows: list[dict[str, Any]] = []
        for c in self._grid_cells()[:limit]:
            try:
                fwd = int(c.get("forward") or 1)
            except (TypeError, ValueError):
                fwd = 1
            try:
                t_end = float(c["forward_s"]) if c.get("forward_s") is not None else float(fwd) * 5.0
            except (TypeError, ValueError):
                t_end = float(fwd) * 5.0
            mult, be = self._quote_fields(c)
            rows.append({
                "cell_x": c.get("cell_x"),
                "cell_y": c.get("cell_y"),
                "forward": fwd,
                "row_offset": c.get("row_offset"),
                "side": c.get("side"),
                "distance": c.get("distance"),
                "multiplier": mult,
                "breakeven": be,
                "t_end": t_end,
                "p_touch": c.get("p_touch"),
                "p_cal": c.get("p_cal"),
                "ev_lcb": c.get("ev_lcb"),
                "verdict": c.get("verdict"),
                "n_obs": c.get("n_obs") or 0,
                "stake": c.get("stake") or 0,
            })
        return rows

    def _ensure_surface(self, body: dict[str, Any]) -> None:
        if body.get("surface"):
            return
        rows = self._quote_surface_rows()
        if not rows:
            return
        body["surface"] = rows
        if not body.get("surface_stats"):
            quoted = sum(1 for r in rows if r.get("multiplier"))
            body["surface_stats"] = {
                "cells": len(rows),
                "quoted": quoted,
                "tappable": 0,
                "best_ev_lcb": None,
                "reachable": 0,
                "verdicts": {},
            }

    def grid_view(self) -> dict[str, Any]:
        """Compact quote-grid stamp for GET /status (overlay ignores extra keys)."""
        grid = self.grid if isinstance(self.grid, dict) else None
        cells = self._grid_cells()
        received = None
        if grid:
            try:
                received = float(grid.get("_received_local"))
            except (TypeError, ValueError):
                received = None
        age = (time.time() - received) if received is not None else None
        sample = []
        for c in cells[:8]:
            mult, be = self._quote_fields(c)
            sample.append({
                "side": c.get("side"),
                "distance": c.get("distance"),
                "cell_x": c.get("cell_x"),
                "cell_y": c.get("cell_y"),
                "multiplier": mult,
                "break_even_probability": be,
                "forward": c.get("forward"),
            })
        return {
            "have_grid": bool(cells),
            "n_cells": len(cells),
            "sample": sample,
            "multiplier_source": (grid or {}).get("multiplier_source"),
            "stale": bool(not cells or age is None or age > GRID_STALE_S),
        }

    def player_edge_view(self) -> dict[str, Any]:
        """Settled ledger only — never invent a hit rate from unresolved taps.

        Window-settled / square-unknown joins move a tap out of unresolved
        but do not write hit_rate. Those joins grade the helper window, not
        the player's square.
        """
        try:
            s = self.player.summary()
        except Exception:
            s = {}
        proven = int(s.get("trades") or 0)
        window_settled = int(s.get("window_settled") or 0)
        unresolved = int(s.get("unresolved_taps") or 0)
        # Proven money-settles only. Window joins never contribute.
        hit = s.get("hit_rate") if proven else None
        settled = proven + window_settled
        if proven:
            pct = f"{round(float(hit) * 100)}%" if hit is not None else "—"
            line = f"{proven} settled · {pct}"
            if window_settled:
                line += f" · {window_settled} square-unknown"
        elif window_settled:
            line = f"{window_settled} window-settled · square unknown"
        else:
            line = "no proven edge"
        return {
            "settled": settled,
            "unresolved": unresolved,
            "hit_rate": hit,
            "line": line,
            "window_settled": window_settled,
        }

    def payload(self, sig: Signal | None = None) -> dict[str, Any]:
        sig = sig if sig is not None else self.last_signal
        if sig is None:
            body: dict[str, Any] = {"suggested": "no trade", "pick": None, "grade": self.scoreboard.to_dict()}
            body["learning"] = self.learning_view()
            body["surface"] = []
            body["stand_aside"] = True
            last = self.ticks.latest("ETH") or self.ticks.latest("BTC")
            if last:
                body["price"] = last.price
                body["asset"] = last.symbol
            body["player_edge"] = self.player_edge_view()
            body["smoother"] = self.smoother.stats()
            body["reachability"] = self.reachability.stats()
            body["cell_edge"] = self.cell_edge.stats()
            body["closes"] = self.closes_view()
            body["regime_edge"] = self.regime_edge_view()
            self._stamp_think_quotes(body)
            self._ensure_surface(body)
            body["tape"] = self._tape_view(body)
            return body
        body = sig.to_dict()
        body["grade"] = self.scoreboard.to_dict()
        body["learning"] = self.learning_view()
        walk = self.traversal_view()
        body["traversal"] = walk
        body["coil"] = self.coil_view(walk=walk)
        body["tilt"] = self.tilt_view()
        body["regime"] = self.regime_view()
        body["smoother"] = self.smoother.stats()
        body["reachability"] = self.reachability.stats()
        body["cell_edge"] = self.cell_edge.stats()
        body["closes"] = self.closes_view()
        body["regime_edge"] = self.regime_edge_view()
        body["stand_aside"] = stand_aside(body)
        if not body.get("price"):
            last = self.ticks.latest(body.get("asset") or "ETH") or self.ticks.latest("ETH")
            if last:
                body["price"] = last.price
        body["player_edge"] = self.player_edge_view()
        self._stamp_think_quotes(body)
        self._ensure_surface(body)
        body["tape"] = self._tape_view(body)
        return body

    def think_payload(self, now: float | None = None) -> dict[str, Any]:
        return self.payload(self.think(now=now))

    def learning_view(self) -> dict[str, Any]:
        """Everything the dashboard needs to show whether it is actually improving."""
        return {
            "calibration": self.calibrator.stats(),
            "board": self.board.stats(),
            "bankroll": self.bankroll.to_dict(),
            "pnl": self.pnl_view(),
            "player": self.player_view(),
            "shadow": self.shadow_view(),
            "wallet": self.wallet_view(),
            "enabled": self.learn,
            "min_edge": self.policy.min_edge,
            "kelly_fraction": self.policy.kelly_fraction_used,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            sig = self.last_signal
            think = self.payload(sig)
            return {
                "running": self.running,
                "mode": self.mode,
                "dry_run": self.dry_run,
                "quotes": self.ticks.latest_quotes(),
                "last_decision": self.last_decision,
                "last_error": self.last_error,
                "think": think,
                "grid": self.grid_view(),
                "session": self.session.public_view(),
                "extension": self._extension_view_unlocked(),
                "seq": self._seq,
                "decisions": list(self.decisions)[-20:],
            }

    def snapshot(self, *, refresh_think: bool = False) -> dict[str, Any]:
        """Shared operator+overlay payload. Does not notify waiters."""
        if refresh_think:
            self.think(notify=False)
        return self.status()

    def wait_for(self, after: int, timeout: float = 15.0) -> int:
        """Block until seq > after or timeout. Returns the current seq."""
        deadline = time.time() + max(0.0, timeout)
        with self._cond:
            while self._seq <= after and not self._stop.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return self._seq

    def _tab_quotes_fresh(self, now: float) -> bool:
        src = self._ext_quote_source
        seen = self._ext_seen
        return bool(src in EXTENSION_SOURCES and seen is not None and now - seen <= EXTENSION_STALE_S)

    def _session_fresh(self, now: float) -> bool:
        seen = self._session_seen
        return bool(seen is not None and now - seen <= EXTENSION_STALE_S)

    def _extension_view_unlocked(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        tab = self._tab_quotes_fresh(now)
        sess = self.session.public_view()
        has_cookies = bool(sess.get("has_cookies"))
        return {
            "connected": tab,
            "session_only": bool(self._session_fresh(now) and not tab),
            "last_seen": self._ext_seen,
            "last_session": self._session_seen,
            "last_quote_source": self._ext_quote_source,
            "has_cookies": has_cookies,
            "has_session": bool(has_cookies or sess.get("privy_user_id_set")),
        }

    def _notify_unlocked(self) -> None:
        self._seq += 1
        self._cond.notify_all()


    def _harvest_grade_windows(self, think: dict[str, Any] | None = None) -> None:
        """Remember recent helper grades as [t0, t1] windows for orphan backfill."""
        grade = think.get("grade") if isinstance(think, dict) else None
        if not isinstance(grade, dict):
            try:
                grade = self.scoreboard.to_dict()
            except Exception:
                return
        recent = grade.get("recent") if isinstance(grade, dict) else None
        if not isinstance(recent, list) or not recent:
            return
        dur = 5.0
        if isinstance(self.grid, dict):
            raw = self.grid.get("square_duration") or self.grid.get("squareDuration")
            try:
                d = float(raw)
                if d > 0:
                    dur = d / 1000.0 if d > 20 else d
            except (TypeError, ValueError):
                pass
        offset = 0.0
        last = grade.get("last") if isinstance(grade, dict) else None
        if self._last_closed and self._last_closed.get("t0") is not None and isinstance(last, dict):
            try:
                offset = float(self._last_closed["t0"]) - float(last["window_id"]) * dur
            except (TypeError, ValueError, KeyError):
                offset = 0.0
        for g in recent:
            if not isinstance(g, dict) or g.get("window_id") is None:
                continue
            try:
                wid = int(g["window_id"])
            except (TypeError, ValueError):
                continue
            t0 = wid * dur + offset
            t1 = t0 + dur
            raw = g.get("outcome")
            if raw in ("stood-out", "stood_out", "unknown", "sit"):
                outcome = "sit"
            elif raw in ("hit", "miss"):
                outcome = raw
            else:
                outcome = raw
            self.player.remember_window(t0, t1, outcome=outcome,
                                        lo=g.get("lo"), hi=g.get("hi"))

    def _join_closed_player_window(self, closed: dict[str, Any] | None,
                                   think: dict[str, Any] | None = None) -> int:
        """Window-settle cell-less taps that fall in this helper window."""
        if not isinstance(closed, dict):
            return 0
        t0, t1 = closed.get("t0"), closed.get("t1")
        if t0 is None or t1 is None:
            return 0
        outcome = closed.get("outcome")
        last_g = (think.get("grade") or {}).get("last") if isinstance(think, dict) else None
        if isinstance(last_g, dict) and last_g.get("outcome"):
            raw = last_g.get("outcome")
            if raw in ("stood-out", "stood_out", "unknown", "sit"):
                outcome = "sit"
            elif raw in ("hit", "miss"):
                outcome = raw
        self.player.remember_window(float(t0), float(t1), outcome=outcome,
                                    lo=closed.get("lo"), hi=closed.get("hi"))
        joined = self.player.join_window(float(t0), float(t1), outcome=outcome)
        return len(joined)

    def _tape_view(self, think: dict[str, Any]) -> dict[str, Any]:
        last = self.ticks.latest("ETH")
        price = float(last.price) if last else float(think.get("price") or 0)
        pick = think.get("suggested") if isinstance(think.get("suggested"), dict) else think.get("pick")
        if not isinstance(pick, dict):
            pick = {}
        height = float(pick.get("cell_height") or 0)
        if height <= 0 and isinstance(self.grid, dict):
            for key in ("cell_height", "dollars_per_line", "dollarsPerLine", "price_interval", "priceInterval"):
                raw = self.grid.get(key)
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    continue
                if val > 0:
                    height = val
                    break
        if height <= 0 and price > 0:
            height = 0.5
        side = str(pick.get("side") or "")
        try:
            distance = int(pick.get("distance") or 1)
        except (TypeError, ValueError):
            distance = 1
        cell_y = pick.get("cell_y")
        try:
            cell_y = int(cell_y) if cell_y is not None else None
        except (TypeError, ValueError):
            cell_y = None
        # No named pick: fall back to the at-price / open-window row so the
        # tape ghost still has a real band whenever a quote exists.
        if height > 0 and (cell_y is None or side not in ("up", "down")):
            at = None
            best_fwd = 99
            for c in self._grid_cells():
                is_at = c.get("side") == "at-price" or c.get("distance") == 0
                if not is_at or c.get("cell_y") is None:
                    continue
                try:
                    fwd = int(c.get("forward") or 99)
                except (TypeError, ValueError):
                    fwd = 99
                if at is None or fwd < best_fwd:
                    at, best_fwd = c, fwd
            if at is not None:
                try:
                    cell_y = int(at["cell_y"])
                except (TypeError, ValueError):
                    cell_y = None
            if cell_y is None and price > 0:
                cell_y = int(price // height)
        band = None
        if height > 0:
            if cell_y is not None:
                band = price_band(price, height, side if side in ("up", "down") else "up", distance, cell_y)
            elif side in ("up", "down"):
                band = price_band(price, height, side, distance, None)
        lo, hi = band if band else (None, None)
        now = time.time()
        dur_ms = 5000.0
        now_ms = now * 1000.0
        grid = self.grid if isinstance(self.grid, dict) else None
        if grid:
            raw_dur = grid.get("square_duration") or grid.get("squareDuration")
            raw_now = grid.get("now_ms") or grid.get("now") or grid.get("timestamp_ms")
            try:
                if raw_dur is not None:
                    d = float(raw_dur)
                    dur_ms = d if d > 20 else d * 1000.0
            except (TypeError, ValueError):
                pass
            try:
                if raw_now is not None:
                    now_ms = float(raw_now)
            except (TypeError, ValueError):
                pass
        elapsed_ms = (now_ms % dur_ms) if dur_ms > 0 else 0.0
        remaining = (dur_ms - elapsed_ms) / 1000.0 if dur_ms > 0 else 0.0
        dur = dur_ms / 1000.0
        win_start = now - elapsed_ms / 1000.0
        tagged = False
        if lo is not None and hi is not None:
            touched, _n = price_touched(
                self.ticks.snapshot(now=now), lo, hi, win_start, now + 1e-6, symbol="ETH"
            )
            tagged = bool(touched)
        wid = int(now_ms // dur_ms) if dur_ms > 0 else 0
        if self._tape_wid is not None and self._tape_wid != wid and self._tape_open:
            prev = self._tape_open
            last_g = (think.get("grade") or {}).get("last") if isinstance(think.get("grade"), dict) else None
            raw = last_g.get("outcome") if isinstance(last_g, dict) else None
            if raw in ("stood-out", "stood_out", "unknown", "sit"):
                outcome = "sit"
            elif raw in ("hit", "miss"):
                outcome = raw
            elif prev.get("tagged"):
                outcome = "hit"
            elif prev.get("lo") is not None:
                outcome = "miss"
            else:
                outcome = "sit"
            self._last_closed = {
                "lo": prev.get("lo"),
                "hi": prev.get("hi"),
                "outcome": outcome,
                "t0": prev.get("t0"),
                "t1": prev.get("t1"),
            }
            self._join_closed_player_window(self._last_closed, think)
        self._tape_wid = wid
        self._tape_open = {
            "lo": lo,
            "hi": hi,
            "tagged": tagged,
            "t0": win_start,
            "t1": win_start + dur,
        }
        out = {
            "lo": lo,
            "hi": hi,
            "tagged": tagged,
            "window_s": dur,
            "window_remaining_s": round(remaining, 3),
        }
        if self._last_closed:
            out["last_closed"] = self._last_closed
        return out

    def close(self) -> None:
        # Flush the track record before shutting down, not just on the timer.
        if self.learn:
            self.save_bankroll()
            self.traversal.save()
            self.pnl.record_taps(list(self.board.taps))
            self.pnl.save()
            self.player.save()
            if self.player_calibrator.path:
                self.player_calibrator.save()
            if self.calibrator.path:
                self.calibrator.save()
            self.save_cell_edge()
        self._stop.set()
        self.running = False
        with self._cond:
            self._notify_unlocked()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    # -- loop ---------------------------------------------------------------
    def _ensure_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="euphoria-control", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.running:
                    self._maybe_oracle()
                    self._maybe_wallet()
                    sig = self.think(notify=True)
                    self._maybe_act(sig)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("control loop: %s", self.last_error)
            self._stop.wait(LOOP_S)

    def _refresh_ohlc(self) -> dict[str, Any] | None:
        """Public ETH OHLC for higher-TF bias. Tests inject ``ohlc_fn`` or disable it."""
        if not self.enable_ohlc:
            return self._ohlc
        now = time.time()
        if self._ohlc is not None and now - self._ohlc_at < OHLC_TTL_S:
            return self._ohlc
        fn = self._ohlc_fn
        if fn is None:
            from src.analytics.ohlc import fetch_ohlc_stack

            fn = fetch_ohlc_stack
        try:
            stack = fn("ETH")
        except Exception as exc:
            log.warning("ohlc: %s", exc)
            return self._ohlc
        if isinstance(stack, dict):
            self._ohlc = stack
            self._ohlc_at = now
        return self._ohlc

    def _maybe_wallet(self) -> None:
        """On-chain balance, on its own interval. Never fatal."""
        if not self.enable_wallet:
            return
        try:
            self.wallet.maybe_refresh(time.time())
        except Exception as exc:
            log.warning("wallet: %s", exc)

    def _maybe_oracle(self) -> None:
        if not self.enable_oracle:
            return
        now = time.time()
        latest = self.ticks.latest("ETH")
        if latest and now - latest.ts < ORACLE_STALE_S:
            return
        if now - self._last_oracle < ORACLE_POLL_S:
            return
        self._last_oracle = now
        fn = self._oracle_fn
        if fn is None:
            from src.monitor import oracle

            fn = oracle.fetch_quotes
        try:
            quotes = fn(["ETH", "BTC"])
        except Exception as exc:
            if not self.ticks.latest("ETH"):
                self.last_error = f"oracle: {exc}"
            return
        for sym, quote in (quotes or {}).items():
            price = getattr(quote, "price", None)
            ts = getattr(quote, "timestamp", None)
            if price is None and isinstance(quote, dict):
                price = quote.get("price")
                ts = quote.get("timestamp") or quote.get("ts")
            if price is not None:
                self.ticks.push(str(sym), float(price), ts, source="redstone")

    def _maybe_act(self, sig: Signal) -> None:
        fingerprint = (sig.bias, sig.reason, str(sig.suggested))
        with self._lock:
            if self.mode != "auto":
                if fingerprint != self._last_fingerprint:
                    self._last_fingerprint = fingerprint
                    self._record_unlocked(sig, action="think", detail="manual — advisory only")
                return

            if isinstance(sig.suggested, str):
                if fingerprint != self._last_fingerprint:
                    self._last_fingerprint = fingerprint
                    self._record_unlocked(sig, action="think", detail="no trade")
                return

            if not self.dry_run and not self.session.has_live_artefacts():
                if fingerprint != self._last_fingerprint:
                    self._last_fingerprint = fingerprint
                    self._record_unlocked(
                        sig,
                        action="blocked",
                        detail="live submit needs botSignature, deviceFingerprint, approvalPermit",
                    )
                return

            if self.dry_run or self._submit_fn is None:
                if fingerprint != self._last_fingerprint:
                    self._last_fingerprint = fingerprint
                    if self.dry_run:
                        action, detail = "dry-run", (
                            f"would {sig.suggested.side} {sig.suggested.asset} {sig.suggested.size}"
                        )
                    else:
                        action, detail = "blocked", "no submit hook — signal only, not live-submitting"
                    self._record_unlocked(sig, action=action, detail=detail)
                return

            try:
                self._submit_fn(sig, self.session)
                self._last_fingerprint = fingerprint
                self._record_unlocked(sig, action="submitted", detail="execute_trade")
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._record_unlocked(sig, action="error", detail=self.last_error)

    def _record_unlocked(self, sig: Signal | None, *, action: str, detail: str) -> None:
        entry = {
            "ts": time.time(),
            "action": action,
            "detail": detail,
            "bias": sig.bias if sig else None,
            "confidence": sig.confidence if sig else None,
            "reason": sig.reason if sig else None,
        }
        self.decisions.append(entry)
        self.last_decision = entry
