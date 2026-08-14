"""In-memory operator control room: start/stop, mode, think, session ingest."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from config import settings
from src.analytics.lesson import GradeBook
from src.analytics.setups import SetupMemory
from src.analytics.signal import Signal, TickBuffer, compute_signal
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
        self._seq = 0
        self._ext_seen: float | None = None
        self._ext_quote_source: str | None = None
        self._session_seen: float | None = None

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
        if isinstance(grid, dict) and grid:
            self.grid = dict(grid)

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

    def ingest_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ControlError("session body must be a JSON object")
        quotes = payload.get("quotes")
        n_quotes = self.ingest_quotes(quotes, source="extension")
        if payload.get("grid"):
            self.set_grid(payload.get("grid"))
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
            sig = compute_signal(
                self.ticks, now=now, size=self.size, grid=self.grid, ohlc=ohlc, memory=self.setups
            )
            self.last_signal = sig
            last = self.ticks.latest("ETH")
            self.scoreboard.update(
                sig,
                self.ticks.snapshot(now=now),
                now=now,
                grid=self.grid,
                last_price=last.price if last else None,
            )
            if notify:
                self._notify_unlocked()
            return sig

    def payload(self, sig: Signal | None = None) -> dict[str, Any]:
        sig = sig if sig is not None else self.last_signal
        if sig is None:
            body: dict[str, Any] = {"suggested": "no trade", "pick": None, "grade": self.scoreboard.to_dict()}
            body["stand_aside"] = True
            last = self.ticks.latest("ETH") or self.ticks.latest("BTC")
            if last:
                body["price"] = last.price
                body["asset"] = last.symbol
            return body
        body = sig.to_dict()
        body["grade"] = self.scoreboard.to_dict()
        body["stand_aside"] = stand_aside(body)
        if not body.get("price"):
            last = self.ticks.latest(body.get("asset") or "ETH") or self.ticks.latest("ETH")
            if last:
                body["price"] = last.price
        return body

    def think_payload(self, now: float | None = None) -> dict[str, Any]:
        return self.payload(self.think(now=now))

    def status(self) -> dict[str, Any]:
        with self._lock:
            sig = self.last_signal
            think = self.payload(sig) if sig is not None else None
            return {
                "running": self.running,
                "mode": self.mode,
                "dry_run": self.dry_run,
                "quotes": self.ticks.latest_quotes(),
                "last_decision": self.last_decision,
                "last_error": self.last_error,
                "think": think,
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

    def close(self) -> None:
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
