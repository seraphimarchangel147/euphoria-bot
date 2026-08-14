"""Control room start/stop, mode, think, session ingest. No live network."""
import json
import logging
import threading
import time
from pathlib import Path

import httpx
import pytest

from src.analytics.signal import Tick
from src.analytics.timeframes import TF_KEYS, Bar
from src.control.room import ControlError, ControlRoom
from src.control.server import make_server


def _room(tmp_path: Path, **kw) -> ControlRoom:
    kw.setdefault("enable_oracle", False)
    kw.setdefault("dry_run", True)
    kw.setdefault("session_path", tmp_path / "session.json")
    kw.setdefault("token_path", tmp_path / "tokens.json")
    return ControlRoom(**kw)


def _up_ticks(now: float) -> list[Tick]:
    return [
        Tick("ETH", 3000.0 + i * 0.6, now - 5 + i * 5 / 7, source="test")
        for i in range(8)
    ]


def test_start_stop_toggles_running(tmp_path):
    room = _room(tmp_path)
    assert room.running is False
    room.start()
    assert room.running is True
    room.stop()
    assert room.running is False
    room.close()


def test_mode_manual_and_auto(tmp_path):
    room = _room(tmp_path)
    assert room.mode == "manual"
    room.set_mode("auto")
    assert room.mode == "auto"
    room.set_mode("manual")
    assert room.mode == "manual"
    with pytest.raises(ControlError):
        room.set_mode("yolo")
    room.close()


def test_think_from_canned_ticks(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    sig = room.think()
    assert sig.bias == "up"
    assert "nearest square above" in sig.reason
    assert sig.suggested != "no trade"
    assert sig.suggested.cell == "nearest-up"
    status = room.status()
    assert status["think"]["bias"] == "up"
    assert "ETH" in status["quotes"]
    room.close()


def test_manual_never_calls_submit(tmp_path):
    called = []
    room = _room(tmp_path, submit_fn=lambda sig, sess: called.append(sig), dry_run=False)
    room.set_mode("manual")
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    room._maybe_act(room.think())
    assert called == []
    assert room.last_decision["action"] == "think"
    room.close()


def test_auto_dry_run_does_not_submit(tmp_path):
    called = []
    room = _room(tmp_path, submit_fn=lambda sig, sess: called.append(sig), dry_run=True)
    room.set_mode("auto")
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    room._maybe_act(room.think())
    assert called == []
    assert room.last_decision["action"] == "dry-run"
    room.close()


def test_auto_live_with_artefacts_calls_submit(tmp_path):
    called = []
    room = _room(tmp_path, submit_fn=lambda sig, sess: called.append((sig.bias, sess.has_live_artefacts())), dry_run=False)
    room.set_mode("auto")
    room.ingest_session({
        "botSignature": "0xbot",
        "deviceFingerprint": "fp",
        "approvalPermit": "0xpermit",
    })
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    room._maybe_act(room.think())
    assert called == [("up", True)]
    assert room.last_decision["action"] == "submitted"
    room.close()


def test_auto_live_without_artefacts_is_blocked(tmp_path):
    called = []
    room = _room(tmp_path, submit_fn=lambda sig, sess: called.append(sig), dry_run=False)
    room.set_mode("auto")
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    room._maybe_act(room.think())
    assert called == []
    assert room.last_decision["action"] == "blocked"
    room.close()


def test_session_persists_0600_and_never_logs_secrets(tmp_path, caplog):
    room = _room(tmp_path)
    caplog.set_level(logging.DEBUG)
    secret = "SUPERSECRET_COOKIE_VALUE_9f3"
    room.ingest_session({
        "cookies": {
            "privy-token": secret,
            "privy-id-token": "IDTOKEN",
            "privy-session": "SESS",
        },
        "privyUserId": "did:privy:abc",
        "quotes": [{"symbol": "ETH", "price": 3010.5, "ts": time.time()}],
    })
    session_path = tmp_path / "session.json"
    token_path = tmp_path / "tokens.json"
    assert session_path.exists()
    assert oct(session_path.stat().st_mode & 0o777) == "0o600"
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"
    saved = json.loads(session_path.read_text())
    assert saved["cookies"]["privy-token"] == secret
    assert saved["privyUserId"] == "did:privy:abc"
    tokens = json.loads(token_path.read_text())
    assert tokens["cookies"]["privy-token"] == secret
    assert secret not in caplog.text
    assert "IDTOKEN" not in caplog.text
    assert "SESS" not in caplog.text
    public = room.status()["session"]
    assert public["has_cookies"] is True
    assert "privy-token" in public["cookie_names"]
    assert secret not in json.dumps(public)
    room.close()


def test_session_merges_without_clobbering_refresh_token(tmp_path):
    token_path = tmp_path / "tokens.json"
    token_path.write_text(json.dumps({
        "refresh_token": "keep-me",
        "identity_token": "id-1",
        "access_token": "acc-1",
        "expires_at": 1,
    }))
    room = _room(tmp_path, token_path=token_path)
    room.ingest_session({"cookies": {"privy-token": "c1"}})
    saved = json.loads(token_path.read_text())
    assert saved["refresh_token"] == "keep-me"
    assert saved["identity_token"] == "id-1"
    assert saved["cookies"]["privy-token"] == "c1"
    room.close()


def _serve(room: ControlRoom):
    httpd = make_server(room, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def test_http_start_stop_and_think(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    httpd, port = _serve(room)
    base = f"http://127.0.0.1:{port}"
    try:
        status = httpx.get(f"{base}/status", timeout=3.0).json()
        assert status["running"] is False
        assert status["mode"] == "manual"
        assert status["dry_run"] is True
        started = httpx.post(f"{base}/start", timeout=3.0).json()
        assert started["running"] is True
        stopped = httpx.post(f"{base}/stop", timeout=3.0).json()
        assert stopped["running"] is False
        httpx.post(f"{base}/mode", json={"mode": "auto"}, timeout=3.0)
        assert httpx.get(f"{base}/status", timeout=3.0).json()["mode"] == "auto"
        think = httpx.get(f"{base}/think", timeout=3.0).json()
        assert think["bias"] == "up"
        assert think["hint"] == "nearest square above, ~5s, touch once"
        assert think["suggested"]["cell"] == "nearest-up"
        assert set(think["timeframes"]) == {"1m", "5m", "1h", "4h", "D", "M"}
        assert think["candidates"] == think["looking_at"]
        assert "pick" in think
        assert think["tf_lean"] in ("up", "down", "mixed", "unknown")
        dash = httpx.get(f"{base}/", timeout=3.0)
        assert dash.status_code == 200
        assert "Live indicator" in dash.text
        assert "tfChips" in dash.text
        assert think["lesson"]
        assert think["why"]
        assert "grade" in think
        assert think["setup"] in ("stall", "compression", "sweep", "late_pink", "none")
        assert think["action"] in ("sit", "tap")
        assert "sit_reason" in think
        assert "pink_age_s" in think
        assert "range_shrinking" in think
        assert "wick_squares" in think
        assert "compression_box" in think
        assert "swing_1m" in think
        assert "extension" in status
        assert status["extension"]["connected"] is False
        assert "extBadge" in dash.text
        assert "tab offline" in dash.text
    finally:
        httpd.shutdown()
        httpd.server_close()
        room.close()


def test_http_session_redacts_and_feeds_quotes(tmp_path, caplog):
    room = _room(tmp_path)
    httpd, port = _serve(room)
    caplog.set_level(logging.DEBUG)
    secret = "HTTP_SECRET_COOKIE"
    try:
        resp = httpx.post(
            f"http://127.0.0.1:{port}/session",
            json={
                "cookies": {"privy-token": secret, "privy-id-token": "x", "privy-session": "y"},
                "privyUserId": "user-1",
                "quotes": [{"symbol": "ETH", "price": 2222.0, "ts": time.time()}],
            },
            timeout=3.0,
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["session"]["has_cookies"] is True
        assert secret not in resp.text
        assert secret not in caplog.text
        think = httpx.get(f"http://127.0.0.1:{port}/think", timeout=3.0).json()
        assert think["asset"] == "ETH"
    finally:
        httpd.shutdown()
        httpd.server_close()
        room.close()


def test_think_payload_includes_mtf_stack_from_injected_ohlc(tmp_path):
    def ohlc_fn(asset: str):
        assert asset == "ETH"
        out = {}
        for key in TF_KEYS:
            px = 3000.0
            bars = []
            for i in range(5):
                close = px * 1.02
                bars.append(Bar(ts=i * 60.0, open=px, high=close, low=px, close=close))
                px = close
            out[key] = bars
        return out

    room = _room(tmp_path, enable_ohlc=True, ohlc_fn=ohlc_fn)
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    sig = room.think()
    body = sig.to_dict()
    assert body["tf_lean"] == "up"
    assert body["alignment"] == "with-trend"
    assert body["pick"]["side"] == "up"
    assert set(body["timeframes"]) == set(TF_KEYS)
    assert all(body["timeframes"][k]["source"] == "ohlc" for k in TF_KEYS)
    room.close()


def test_quotes_and_session_mark_extension_connected(tmp_path):
    room = _room(tmp_path)
    assert room.status()["extension"]["connected"] is False
    room.ticks.push("ETH", 3000.0, time.time(), source="redstone")
    assert room.status()["extension"]["connected"] is False
    n = room.ingest_quotes(
        [{"symbol": "ETH", "price": 3010.0, "ts": time.time(), "source": "page"}],
        source="extension",
    )
    assert n == 1
    ext = room.status()["extension"]
    assert ext["connected"] is True
    assert ext["last_seen"]
    assert ext["last_quote_source"] == "page"
    assert ext["has_cookies"] is False
    room.ingest_session({
        "cookies": {"privy-token": "tok", "privy-id-token": "id", "privy-session": "s"},
    })
    ext = room.status()["extension"]
    assert ext["connected"] is True
    assert ext["has_cookies"] is True
    assert ext["has_session"] is True
    room._ext_seen = time.time() - 60
    assert room.status()["extension"]["connected"] is False
    room.close()


def test_http_mode_and_start_show_in_status_overlay_reads(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    httpd, port = _serve(room)
    base = f"http://127.0.0.1:{port}"
    try:
        httpx.post(f"{base}/quotes", json=[
            {"symbol": "ETH", "price": 3003.6, "ts": now, "source": "page"},
        ], timeout=3.0)
        started = httpx.post(f"{base}/start", timeout=3.0).json()
        assert started["running"] is True
        assert started["extension"]["connected"] is True
        assert started["extension"]["last_quote_source"] == "page"
        httpx.post(f"{base}/mode", json={"mode": "auto"}, timeout=3.0)
        status = httpx.get(f"{base}/status", timeout=3.0).json()
        assert status["running"] is True
        assert status["mode"] == "auto"
        assert status["dry_run"] is True
        assert status["extension"]["connected"] is True
        think = status["think"] or httpx.get(f"{base}/think", timeout=3.0).json()
        assert think["candidates"]
        assert "pick" in think
        assert set(think["timeframes"]) == {"1m", "5m", "1h", "4h", "D", "M"}
        assert "grade" in think
        httpx.post(f"{base}/mode", json={"mode": "manual"}, timeout=3.0)
        assert httpx.get(f"{base}/status", timeout=3.0).json()["mode"] == "manual"
    finally:
        httpd.shutdown()
        httpd.server_close()
        room.close()


def test_events_long_poll_wakes_on_start(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    for t in _up_ticks(now):
        room.ticks.push(t.symbol, t.price, t.ts, source="test")
    httpd, port = _serve(room)
    base = f"http://127.0.0.1:{port}"
    try:
        before = httpx.get(f"{base}/events?after=0&wait=0.2", timeout=3.0).json()
        assert "extension" in before
        assert "seq" in before
        think = before.get("think") or {}
        if think:
            assert "candidates" in think
            assert "timeframes" in think
            assert "grade" in think
        seq = before["seq"]

        def later():
            time.sleep(0.15)
            httpx.post(f"{base}/start", timeout=3.0)

        threading.Thread(target=later, daemon=True).start()
        woken = httpx.get(f"{base}/events?after={seq}&wait=2", timeout=4.0).json()
        assert woken["running"] is True
        assert woken["seq"] > seq
        assert woken["mode"] == "manual"
    finally:
        httpd.shutdown()
        httpd.server_close()
        room.close()


def test_make_server_rejects_non_localhost(tmp_path):
    room = _room(tmp_path)
    with pytest.raises(ValueError, match="localhost only"):
        make_server(room, host="0.0.0.0", port=0)
    room.close()
