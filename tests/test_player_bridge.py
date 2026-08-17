import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path


def load_script(name: str, filename: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_player_event_append_is_jsonl_and_private(tmp_path):
    bridge = load_script("euphoria_bridge_server", "euphoria-bridge-server.py")
    target = tmp_path / "player-events.jsonl"
    tap = {
        "event_type": "player-tap", "tap_id": "tap-fixture", "ts": 1_700_000_000_000,
        "cell": {"x": 10, "y": 20}, "side": "up", "multiplier": 2.5,
        "pointer": {"client_x": 100, "client_y": 200},
    }
    row = {
        "event_type": "player-settle",
        "tap_id": "tap-fixture",
        "ts": 1_700_000_004_200,
        "join_status": "joined",
        "join_delay_ms": 4200,
        "win": True,
        "payout": 2.5,
        "stake": 1,
        "cell": {"x": 10, "y": 20},
    }
    bridge.append_player_event(tap, target)
    receipt = bridge.append_player_event(row, target)
    assert receipt == {"ok": True, "event_type": "player-settle", "tap_id": "tap-fixture"}
    assert [json.loads(line) for line in target.read_text().splitlines()] == [tap, row]
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_player_event_append_rejects_orphan_duplicate_or_mismatched_settlement(tmp_path):
    bridge = load_script("euphoria_bridge_server_provenance", "euphoria-bridge-server.py")
    target = tmp_path / "player-events.jsonl"
    tap = {
        "event_type": "player-tap", "tap_id": "tap-fixture", "ts": 1_700_000_000_000,
        "cell": {"x": 10, "y": 20}, "side": "up", "multiplier": 2.5,
        "pointer": {"client_x": 100, "client_y": 200},
    }
    settle = {
        "event_type": "player-settle", "tap_id": "tap-fixture", "ts": 1_700_000_004_200,
        "join_status": "joined", "join_delay_ms": 4200, "win": True, "payout": 2.5,
        "stake": 1, "cell": {"x": 10, "y": 20},
    }
    for unsafe in (settle, {**settle, "tap_id": "orphan"}):
        try:
            bridge.append_player_event(unsafe, target)
        except ValueError:
            pass
        else:
            raise AssertionError("orphan settlement accepted")
    bridge.append_player_event(tap, target)
    for unsafe in ({**settle, "cell": {"x": 10, "y": 21}},):
        try:
            bridge.append_player_event(unsafe, target)
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched settlement accepted")
    bridge.append_player_event(settle, target)
    try:
        bridge.append_player_event(settle, target)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate settlement accepted")


def test_status_view_exposes_quoted_grid_when_control_loop_is_stopped():
    bridge = load_script("euphoria_bridge_server_status", "euphoria-bridge-server.py")
    grid = {
        "authoritative": True,
        "multiplier_source": "quotesFeed",
        "quoted_grid_ref_time": 1_700_000_000_123,
        "cells": [{
            "cell_x": 340_000_001,
            "cell_y": 6001,
            "side": "up",
            "distance": 1,
            "multiplier": 2.5,
            "break_even_probability": 0.4,
        }],
    }
    state = {
        "ts": "2026-08-15T04:30:02Z",
        "tabs": [],
        "cookieNames": [],
        "control": {"running": False},
        "extVersion": "0.4.0",
        "grid": grid,
    }
    view = bridge.status_view(state, now=1_786_768_202.0)
    assert view["control"] == {"running": False}
    assert view["grid"] == grid
    assert view["grid"]["cells"][0]["multiplier"] == 2.5


def test_bridge_token_is_high_entropy_and_pinned(tmp_path):
    bridge = load_script("euphoria_bridge_server_token", "euphoria-bridge-server.py")
    token_path = tmp_path / "bridge-token"
    first = "a" * 64
    assert bridge.authorize_bridge_token(first, token_path) is True
    assert token_path.read_text() == first
    assert os.stat(token_path).st_mode & 0o777 == 0o600
    assert bridge.authorize_bridge_token(first, token_path) is True
    assert bridge.authorize_bridge_token("b" * 64, token_path) is False
    assert bridge.authorize_bridge_token("short", token_path) is False


def test_bridge_state_and_command_routes_reject_unpinned_clients(tmp_path):
    bridge = load_script("euphoria_bridge_server_route_auth", "euphoria-bridge-server.py")
    bridge.DATA = tmp_path
    bridge.STATE = tmp_path / "state.json"
    bridge.HIST = tmp_path / "state-history.jsonl"
    bridge.QUEUE = tmp_path / "commands-queue.json"
    bridge.RESULTS = tmp_path / "results.jsonl"
    bridge.PLAYER_EVENTS = tmp_path / "player-events.jsonl"
    bridge.BRIDGE_TOKEN = tmp_path / "bridge-token"
    token = "a" * 64

    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        payload = json.dumps({"ts": "2026-08-17T00:00:00Z", "tabs": []}).encode()
        unauthorized = urllib.request.Request(
            base + "/euphoria/state", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(unauthorized, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("unauthenticated bridge state accepted")
        assert not bridge.STATE.exists()

        authorized = urllib.request.Request(
            base + "/euphoria/state", data=payload,
            headers={"Content-Type": "application/json", "X-Euphoria-Bridge-Token": token},
            method="POST",
        )
        with urllib.request.urlopen(authorized, timeout=2) as response:
            assert json.load(response) == {"ok": True}
        assert json.loads(bridge.STATE.read_text())["tabs"] == []

        bridge._write_queue([{"id": "cmd-1", "type": "read_page"}])
        try:
            urllib.request.urlopen(base + "/euphoria/commands", timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("unauthenticated command drain accepted")
        assert json.loads(bridge.QUEUE.read_text()) == [{"id": "cmd-1", "type": "read_page"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_player_event_append_rejects_unknown_or_oversize_payload(tmp_path):
    bridge = load_script("euphoria_bridge_server_bad", "euphoria-bridge-server.py")
    target = tmp_path / "player-events.jsonl"
    for row in (
        {"event_type": "trade"},
        {"event_type": "player-settle"},
        {"event_type": "player-tap", "tap_id": "tap-1", "ts": 1, "cell": None},
        {"event_type": "player-tap", "blob": "x" * 70_000},
    ):
        try:
            bridge.append_player_event(row, target)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe player event accepted")
    assert not target.exists()
