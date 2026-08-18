"""Localhost-only HTTP control API + dashboard.

    python -m src.ui
    python -m src.control
"""
from __future__ import annotations

import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from io import BytesIO
from urllib.parse import parse_qs, unquote, urlparse

from config import settings
from src.control.room import ControlError, ControlRoom

log = logging.getLogger("euphoria.control")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 256 * 1024
MAX_UPLOAD = 8 * 1024 * 1024
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BG_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\.(?:png|jpg|jpeg|webp|gif)")
ALLOWED_BG_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
BG_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8"


def _bg_slug(name: str) -> str:
    stem = Path(name).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or "wall")[:48]


def _sniff_image(data: bytes, declared: str) -> str:
    declared = (declared or "").split(";")[0].strip().lower()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if declared in ALLOWED_BG_TYPES:
        return declared
    raise ControlError("unsupported image type")


def _image_to_png(data: bytes) -> bytes:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    try:
        from PIL import Image
    except ImportError as exc:
        raise ControlError("could not convert image") from exc
    im = Image.open(BytesIO(data))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def _parse_multipart_file(raw: bytes, content_type: str) -> tuple[str, str, bytes]:
    m = re.search(r"boundary=([^;]+)", content_type, re.I)
    if not m:
        raise ControlError("missing boundary")
    boundary = m.group(1).strip().strip('"').encode("ascii", "ignore")
    if not boundary:
        raise ControlError("missing boundary")
    chosen: tuple[str, str, bytes] | None = None
    for part in raw.split(b"--" + boundary):
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]
        if b"\r\n\r\n" in part:
            header_blob, body = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_blob, body = part.split(b"\n\n", 1)
        else:
            continue
        headers = header_blob.decode("utf-8", "replace")
        if "filename=" not in headers.lower() and 'name="file"' not in headers:
            continue
        fm = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)"?', headers, re.I)
        filename = fm.group(1).strip().strip('"') if fm else "upload.png"
        filename = Path(unquote(filename.replace("\\", "/"))).name or "upload.png"
        cm = re.search(r"Content-Type:\s*([^\r\n]+)", headers, re.I)
        part_ctype = cm.group(1).strip() if cm else ""
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\n"):
            body = body[:-1]
        item = (filename, part_ctype, body)
        if 'name="file"' in headers or re.search(r"name=file\b", headers, re.I):
            return item
        if chosen is None:
            chosen = item
    if chosen:
        return chosen
    raise ControlError("no file field")


class ControlHandler(BaseHTTPRequestHandler):
    room: ControlRoom

    def log_message(self, fmt: str, *args: Any) -> None:
        # Access log only — never request bodies (session posts carry cookies).
        path = urlparse(self.path).path
        log.info("http %s %s", self.command, path)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
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
            self._send(*_json_bytes(self.room.snapshot(refresh_think=not self.room.running)))
            return
        if path == "/think":
            self._send(*_json_bytes(self.room.think_payload()))
            return
        if path == "/surface":
            think = self.room.think_payload()
            self._send(*_json_bytes({
                "price": think.get("price"),
                "diffusion": think.get("diffusion"),
                "vol_bucket": think.get("vol_bucket"),
                "stats": think.get("surface_stats"),
                "plan": think.get("plan"),
                "cells": think.get("surface") or [],
            }))
            return
        if path == "/learning":
            self._send(*_json_bytes(self.room.learning_view()))
            return
        if path == "/wallet":
            self._send(*_json_bytes(self.room.wallet_view()))
            return
        if path == "/traversal":
            self._send(*_json_bytes(self.room.traversal_view()))
            return
        if path == "/coil":
            self._send(*_json_bytes(self.room.coil_view()))
            return
        if path == "/pnl":
            self._send(*_json_bytes(self.room.pnl_view()))
            return
        if path == "/regime":
            self._send(*_json_bytes(self.room.regime_view()))
            return
        if path == "/tilt":
            self._send(*_json_bytes(self.room.tilt_view()))
            return
        if path == "/player":
            self._send(*_json_bytes(self.room.player_view()))
            return
        if path == "/shadow":
            self._send(*_json_bytes(self.room.shadow_view()))
            return
        if path == "/experiment":
            self._send(*_json_bytes(self.room.experiment_view()))
            return
        if path == "/frames":
            self._send(*_json_bytes(self.room.frames_view()))
            return
        if path == "/grid":
            grid = self.room.grid if isinstance(self.room.grid, dict) else {}
            cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
            self._send(*_json_bytes({
                "have_grid": bool(grid),
                "authoritative": bool(grid.get("authoritative")),
                "reason": grid.get("reason"),
                "multiplier_source": grid.get("multiplier_source"),
                "quoted_cells": len(cells),
                "dollars_per_line": grid.get("dollars_per_line"),
                "square_duration": grid.get("square_duration"),
                "grid_x": grid.get("gridX"),
                "grid_y": grid.get("gridY"),
                "hook": grid.get("hook"),
                "discovery": grid.get("discovery"),
                "sample": cells[:8],
            }))
            return
        if path == "/dashboard-bg.png":
            img = (STATIC_DIR / "dashboard-bg.png").read_bytes()
            self._send(200, img, "image/png")
            return
        if path == "/money-man-avatar.png":
            img = (STATIC_DIR / "money-man-avatar.png").read_bytes()
            self._send(200, img, "image/png")
            return
        if path.startswith("/bg/"):
            name = path[len("/bg/"):]
            if not BG_NAME_RE.fullmatch(name):
                self._send(*_json_bytes({"error": "not found"}, 404))
                return
            bg_root = (STATIC_DIR / "bg").resolve()
            target = (STATIC_DIR / "bg" / name).resolve()
            if target.parent != bg_root:
                self._send(*_json_bytes({"error": "not found"}, 404))
                return
            if not target.is_file():
                self._send(*_json_bytes({"error": "not found"}, 404))
                return
            ctype = BG_CONTENT_TYPES.get(target.suffix.lower(), "image/png")
            self._send(200, target.read_bytes(), ctype)
            return
        if path == "/ui-motion.js":
            js = (STATIC_DIR / "ui-motion.js").read_bytes()
            self._send(200, js, "application/javascript; charset=utf-8")
            return
        if path == "/liquid-glass.js":
            js = (STATIC_DIR / "liquid-glass.js").read_bytes()
            self._send(200, js, "application/javascript; charset=utf-8")
            return
        if path == "/thinking-orb.js":
            js = (STATIC_DIR / "thinking-orb.js").read_bytes()
            self._send(200, js, "application/javascript; charset=utf-8")
            return
        if path == "/engine.es.js":
            js = (STATIC_DIR / "engine.es.js").read_bytes()
            self._send(200, js, "application/javascript; charset=utf-8")
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
            self._send(*_json_bytes(self.room.snapshot(refresh_think=not self.room.running)))
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
                snap = self.room.snapshot(refresh_think=not self.room.running)
                payload = json.dumps(snap).encode("utf-8")
                self.wfile.write(b"event: state\ndata: " + payload + b"\n\n")
                self.wfile.flush()
                after = int(snap.get("seq") or 0)
                self.room.wait_for(after, timeout=15.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/bg/upload":
            try:
                self._send(*self._bg_upload())
            except ControlError as exc:
                self._send(*_json_bytes({"error": str(exc)}, 400))
            return
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
            if path == "/experiment":
                # Flip an experiment arm without restarting. A restart resets
                # the reachability ledger and the shadow book, which is both a
                # cold guard and a fresh sample -- so a restart between arms
                # changes more than the variable under test.
                arm = payload.get("arm") if isinstance(payload, dict) else payload
                self._send(*_json_bytes(self.room.set_experiment(str(arm or ""))))
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
            if path == "/player-event":
                if not isinstance(payload, dict):
                    raise ControlError("player event must be a JSON object")
                self._send(*_json_bytes(self.room.ingest_player_event(payload)))
                return
            if path == "/quotes":
                n = self.room.ingest_quotes(payload if payload is not None else [], source="extension")
                self._send(*_json_bytes({"ok": True, "quotes_ingested": n}))
                return
        except ControlError as exc:
            self._send(*_json_bytes({"error": str(exc)}, 400))
            return
        self._send(*_json_bytes({"error": "not found"}, 404))


    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/bg/"):
            try:
                self._send(*self._bg_delete(path[len("/bg/"):]))
            except ControlError as exc:
                status = 404 if str(exc) == "not found" else 400
                self._send(*_json_bytes({"error": str(exc)}, status))
            return
        self._send(*_json_bytes({"error": "not found"}, 404))

    def _bg_upload(self) -> tuple[int, bytes, str]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ControlError("empty body")
        if length > MAX_UPLOAD:
            raise ControlError("body too large")
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or ""
        parsed = urlparse(self.path)
        if "multipart/form-data" in ctype.lower():
            filename, part_ctype, data = _parse_multipart_file(raw, ctype)
        else:
            qs = parse_qs(parsed.query)
            filename = unquote((qs.get("filename") or ["upload.png"])[0])
            filename = Path(filename.replace("\\", "/")).name
            part_ctype = ctype.split(";")[0].strip()
            data = raw
        if not data:
            raise ControlError("empty file")
        mime = _sniff_image(data, part_ctype)
        if mime not in ALLOWED_BG_TYPES:
            raise ControlError("unsupported image type")
        try:
            png = _image_to_png(data)
        except ControlError:
            raise
        except Exception as exc:
            raise ControlError("could not convert image") from exc
        slug = _bg_slug(filename)
        bg_dir = STATIC_DIR / "bg"
        bg_dir.mkdir(parents=True, exist_ok=True)
        stem = f"custom-{slug}"
        out_name = f"{stem}.png"
        n = 2
        while (bg_dir / out_name).exists():
            out_name = f"{stem}-{n}.png"
            n += 1
        bg_root = bg_dir.resolve()
        target = (bg_dir / out_name).resolve()
        if target.parent != bg_root:
            raise ControlError("invalid name")
        target.write_bytes(png)
        wall_id = Path(out_name).stem
        label = Path(filename).stem.replace("-", " ").replace("_", " ").strip() or "Custom"
        label = re.sub(r"\s+", " ", label)[:40]
        return _json_bytes({"id": wall_id, "src": f"/bg/{out_name}", "label": label})

    def _bg_delete(self, name: str) -> tuple[int, bytes, str]:
        name = unquote(name)
        if "/" in name or "\\" in name or name.startswith("."):
            raise ControlError("not found")
        if not name.startswith("custom-"):
            raise ControlError("cannot delete built-in wall")
        if not BG_NAME_RE.fullmatch(name):
            raise ControlError("not found")
        bg_root = (STATIC_DIR / "bg").resolve()
        target = (STATIC_DIR / "bg" / name).resolve()
        if target.parent != bg_root:
            raise ControlError("not found")
        if target.is_file():
            target.unlink()
        return _json_bytes({"ok": True})

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
