"""Localhost-only HTTP control API + dashboard.

    python -m src.ui
    python -m src.control
"""
from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from config import settings
from src.control.room import ControlError, ControlRoom

log = logging.getLogger("euphoria.control")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 256 * 1024
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8"


class ControlHandler(BaseHTTPRequestHandler):
    room: ControlRoom

    def log_message(self, fmt: str, *args: Any) -> None:
        # Access log only — never request bodies (session posts carry cookies).
        path = urlparse(self.path).path
        log.info("http %s %s", self.command, path)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            html = (STATIC_DIR / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/status":
            self._send(*_json_bytes(self.room.status()))
            return
        if path == "/think":
            self._send(*_json_bytes(self.room.think_payload()))
            return
        if path == "/events":
            self._events(parsed)
            return
        self._send(*_json_bytes({"error": "not found"}, 404))

    def _events(self, parsed) -> None:
        qs = parse_qs(parsed.query)
        wants_poll = "after" in qs or "wait" in qs
        if wants_poll:
            try:
                after = int((qs.get("after") or ["0"])[0] or 0)
            except ValueError:
                after = 0
            try:
                wait = float((qs.get("wait") or ["10"])[0] or 10)
            except ValueError:
                wait = 10.0
            self.room.wait_for(after, timeout=min(max(wait, 0.0), 25.0))
            self._send(*_json_bytes(self.room.snapshot(refresh_think=True)))
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        after = -1
        try:
            while not self.room._stop.is_set():
                snap = self.room.snapshot(refresh_think=True)
                payload = json.dumps(snap).encode("utf-8")
                self.wfile.write(b"event: state\ndata: " + payload + b"\n\n")
                self.wfile.flush()
                after = int(snap.get("seq") or 0)
                self.room.wait_for(after, timeout=15.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except ControlError as exc:
            self._send(*_json_bytes({"error": str(exc)}, 400))
            return
        try:
            if path == "/start":
                self._send(*_json_bytes(self.room.start()))
                return
            if path == "/stop":
                self._send(*_json_bytes(self.room.stop()))
                return
            if path == "/mode":
                mode = payload.get("mode") if isinstance(payload, dict) else payload
                if isinstance(mode, dict):
                    mode = mode.get("mode")
                self._send(*_json_bytes(self.room.set_mode(str(mode or ""))))
                return
            if path == "/session":
                if not isinstance(payload, dict):
                    raise ControlError("session body must be a JSON object")
                self._send(*_json_bytes(self.room.ingest_session(payload)))
                return
            if path == "/quotes":
                n = self.room.ingest_quotes(payload if payload is not None else [], source="extension")
                self._send(*_json_bytes({"ok": True, "quotes_ingested": n}))
                return
        except ControlError as exc:
            self._send(*_json_bytes({"error": str(exc)}, 400))
            return
        self._send(*_json_bytes({"error": "not found"}, 404))

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ControlError("body too large")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ControlError("invalid JSON") from exc


def make_server(
    room: ControlRoom,
    host: str = settings.CONTROL_HOST,
    port: int = settings.CONTROL_PORT,
) -> ThreadingHTTPServer:
    if host not in LOCAL_HOSTS:
        raise ValueError(f"control room binds localhost only, not {host!r}")
    handler = type("BoundControlHandler", (ControlHandler,), {"room": room})
    return ThreadingHTTPServer((host, port), handler)


def serve(
    host: str = settings.CONTROL_HOST,
    port: int = settings.CONTROL_PORT,
    room: ControlRoom | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    room = room or ControlRoom()
    try:
        httpd = make_server(room, host=host, port=port)
    except OSError as exc:
        log.error("could not bind %s:%s: %s", host, port, exc)
        return 1
    bound = httpd.server_address[1]
    log.info(
        "control room http://%s:%s  (localhost only)  dry_run=%s mode=%s",
        host, bound, room.dry_run, room.mode,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("control room shutting down")
    finally:
        httpd.server_close()
        room.close()
    return 0


def main() -> int:
    host = os.environ.get("EUPHORIA_CONTROL_HOST", settings.CONTROL_HOST)
    port = int(os.environ.get("EUPHORIA_CONTROL_PORT", str(settings.CONTROL_PORT)))
    return serve(host=host, port=port)


if __name__ == "__main__":
    raise SystemExit(main())
