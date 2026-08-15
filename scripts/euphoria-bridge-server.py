#!/usr/bin/env python3
"""Localhost-only Euphoria extension bridge.

The channel accepts state, read-only command results, and manual player-learning
observations. It does not expose a trade, tap, or order endpoint.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DATA = Path(os.environ.get("EUPHORIA_BRIDGE_DATA", "~/.legion-trading-bot/data/euphoria-bridge")).expanduser()
STATE = DATA / "state.json"
HIST = DATA / "state-history.jsonl"
QUEUE = DATA / "commands-queue.json"
RESULTS = DATA / "results.jsonl"
PLAYER_EVENTS = DATA / "player-events.jsonl"
PLAYER_MODEL = DATA / "player-model.json"
BRIDGE_TOKEN = DATA / "bridge-token"
PORT = int(os.environ.get("EUPHORIA_BRIDGE_PORT", "18901"))
MAX_BODY = 65_536
PLAYER_EVENT_TYPES = frozenset({"player-tap", "player-settle"})
EVENT_LOCK = threading.Lock()
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def _ensure_data() -> None:
    DATA.mkdir(parents=True, exist_ok=True)


def _read_queue() -> list[dict[str, Any]]:
    try:
        value = json.loads(QUEUE.read_text())
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _write_queue(queue: list[dict[str, Any]]) -> None:
    _ensure_data()
    QUEUE.write_text(json.dumps(queue))
    QUEUE.chmod(0o600)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _valid_cell(value: Any) -> bool:
    return value is None or (
        isinstance(value, dict)
        and _finite_number(value.get("x")) is not None
        and _finite_number(value.get("y")) is not None
    )


def _validate_player_event(event: Any) -> None:
    if not isinstance(event, dict) or event.get("event_type") not in PLAYER_EVENT_TYPES:
        raise ValueError("unknown player event type")
    tap_id = event.get("tap_id")
    if not isinstance(tap_id, str) or not 3 <= len(tap_id) <= 96 or _finite_number(event.get("ts")) is None:
        raise ValueError("invalid player event identity")
    if not _valid_cell(event.get("cell")):
        raise ValueError("invalid player event cell")
    if event["event_type"] == "player-tap":
        pointer = event.get("pointer")
        if not isinstance(pointer, dict) or _finite_number(pointer.get("client_x")) is None or _finite_number(pointer.get("client_y")) is None:
            raise ValueError("invalid trusted pointer provenance")
        if event.get("cell") is not None and (
            event.get("side") not in {"up", "down", "at-price"}
            or (_finite_number(event.get("multiplier")) or 0) <= 0
        ):
            raise ValueError("invalid quoted cell provenance")
        return
    delay = _finite_number(event.get("join_delay_ms"))
    stake = _finite_number(event.get("stake"))
    payout = _finite_number(event.get("payout"))
    if event.get("join_status") != "joined" or delay is None or not 0 <= delay <= 10_000:
        raise ValueError("invalid settlement join")
    if not isinstance(event.get("win"), bool) or stake is None or stake <= 0 or payout is None or payout < 0:
        raise ValueError("invalid settlement outcome")


def authorize_bridge_token(token: Any, target: Path = BRIDGE_TOKEN) -> bool:
    if not isinstance(token, str) or TOKEN_RE.fullmatch(token) is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        expected = target.read_text().strip()
    except FileNotFoundError:
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, token.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            expected = token
        except FileExistsError:
            expected = target.read_text().strip()
    os.chmod(target, 0o600)
    return bool(TOKEN_RE.fullmatch(expected)) and hmac.compare_digest(expected, token)


def _existing_player_events(target: Path) -> list[dict[str, Any]]:
    try:
        rows = []
        for line in target.read_text().splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        return rows
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _verify_event_provenance(event: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    tap_id = event["tap_id"]
    matching_taps = [row for row in rows if row.get("event_type") == "player-tap" and row.get("tap_id") == tap_id]
    settlements = [row for row in rows if row.get("event_type") == "player-settle" and row.get("tap_id") == tap_id]
    if event["event_type"] == "player-tap":
        if matching_taps or settlements:
            raise ValueError("duplicate player tap identity")
        return
    if len(matching_taps) != 1 or settlements:
        raise ValueError("orphan or duplicate settlement")
    tap = matching_taps[0]
    if event.get("cell") != tap.get("cell") or event.get("cell") is None:
        raise ValueError("settlement cell does not match trusted tap")
    tap_ts = _finite_number(tap.get("ts"))
    settle_ts = _finite_number(event.get("ts"))
    if tap_ts is None or settle_ts is None:
        raise ValueError("invalid settlement timestamp")
    expected_delay = settle_ts - tap_ts
    if expected_delay != _finite_number(event.get("join_delay_ms")):
        raise ValueError("settlement delay does not match trusted tap")


def append_player_event(event: dict[str, Any], target: Path = PLAYER_EVENTS) -> dict[str, Any]:
    _validate_player_event(event)
    encoded = json.dumps(event, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_BODY:
        raise ValueError("player event too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOCK:
        _verify_event_provenance(event, _existing_player_events(target))
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, encoded + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(target, 0o600)
    return {"ok": True, "event_type": event["event_type"], "tap_id": event.get("tap_id")}


def status_view(state: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Public bridge snapshot, including raw quoted-grid context.

    The grid is relayed independently of the optional control room so manual
    play remains measurable while the control loop is stopped or unavailable.
    """
    age: float | None = None
    if state.get("ts"):
        dt = datetime.fromisoformat(str(state["ts"]).replace("Z", "+00:00"))
        current = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
        age = (current - dt).total_seconds()
    return {
        "ok": True,
        "lastState": state.get("ts"),
        "ageSeconds": None if age is None else round(age, 1),
        "tabs": state.get("tabs"),
        "cookieNames": state.get("cookieNames"),
        "control": state.get("control"),
        "grid": state.get("grid"),
        "extVersion": state.get("extVersion"),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _json_body(self) -> dict[str, Any] | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY:
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def do_POST(self) -> None:
        body = self._json_body()
        if body is None:
            return self._send({"ok": False, "error": "bad or oversized json"}, 400)
        _ensure_data()

        if self.path == "/euphoria/state":
            STATE.write_text(json.dumps(body, indent=1, allow_nan=False))
            STATE.chmod(0o600)
            hist = {
                "ts": body.get("ts"),
                "tabs": len(body.get("tabs") or []),
                "cookieNames": body.get("cookieNames"),
                "control": (body.get("control") or {}).get("running") if isinstance(body.get("control"), dict) else None,
            }
            with HIST.open("a") as stream:
                stream.write(json.dumps(hist) + "\n")
            HIST.chmod(0o600)
            return self._send({"ok": True})

        if self.path == "/euphoria/result":
            with RESULTS.open("a") as stream:
                stream.write(json.dumps(body, allow_nan=False) + "\n")
            RESULTS.chmod(0o600)
            return self._send({"ok": True})

        if self.path == "/euphoria/player-event":
            if not authorize_bridge_token(self.headers.get("X-Euphoria-Bridge-Token")):
                return self._send({"ok": False, "error": "unauthorized player event"}, 401)
            try:
                return self._send(append_player_event(body))
            except (TypeError, ValueError) as exc:
                return self._send({"ok": False, "error": str(exc)}, 400)

        return self._send({"ok": False, "error": "unknown path"}, 404)

    def do_GET(self) -> None:
        if self.path == "/euphoria/commands":
            queue = _read_queue()
            _write_queue([])
            return self._send({"commands": queue})

        if self.path == "/euphoria/player-model":
            try:
                return self._send(json.loads(PLAYER_MODEL.read_text()))
            except Exception:
                return self._send({"ok": False, "error": "player model unavailable"}, 404)

        if self.path == "/euphoria/status":
            try:
                state = json.loads(STATE.read_text())
                return self._send(status_view(state))
            except Exception:
                return self._send({"ok": False, "error": "no state yet — extension not connected"})

        return self._send({"ok": False, "error": "unknown path"}, 404)


def main() -> None:
    _ensure_data()
    if len(sys.argv) > 1 and sys.argv[1] == "cmd":
        command_type = sys.argv[2] if len(sys.argv) > 2 else "read_page"
        queue = _read_queue()
        command_id = uuid.uuid4().hex[:8]
        queue.append({"id": command_id, "type": command_type})
        _write_queue(queue)
        print(f"queued {command_type} id={command_id} — result lands in {RESULTS}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "state":
        try:
            state = json.loads(STATE.read_text())
            state.pop("cookies", None)
            print(json.dumps(state, indent=1)[:3000])
        except Exception:
            print("no state yet")
        return
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"euphoria-bridge-server on 127.0.0.1:{PORT} · data={DATA}")
    server.serve_forever()


if __name__ == "__main__":
    main()
