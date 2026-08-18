import importlib.util
import json
import os
import subprocess
import sys
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
        "quoted_grid_ref_time": 1_786_768_200_000,
        "received_at": 1_786_768_200_000,
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
    assert view["grid"]["authoritative"] is True
    assert view["grid"]["stale"] is False
    assert view["grid"]["grid_age_ms"] == 2000
    assert view["grid"]["cells"][0]["multiplier"] == 2.5
    assert view["gridAgeSeconds"] == 2.0


def test_status_view_expires_grid_from_its_own_timestamp_not_fresh_envelope():
    bridge = load_script("euphoria_bridge_server_stale_grid", "euphoria-bridge-server.py")
    now = 1_786_768_202.0
    state = {
        "ts": "2026-08-15T04:30:02Z",
        "grid": {
            "authoritative": True,
            "received_at": int((now - 5.001) * 1000),
            "quoted_grid_ref_time": int((now - 5.001) * 1000),
            "cells": [{"multiplier": 2.5}],
        },
    }
    view = bridge.status_view(state, now=now)
    assert view["ageSeconds"] == 0.0
    assert view["gridAgeSeconds"] == 5.001
    assert view["grid"]["authoritative"] is False
    assert view["grid"]["stale"] is True
    assert view["grid"]["cells"] == [{"multiplier": 2.5}]


def test_status_view_rejects_authority_without_grid_received_at():
    bridge = load_script("euphoria_bridge_server_missing_grid_time", "euphoria-bridge-server.py")
    view = bridge.status_view({
        "ts": "2026-08-15T04:30:02Z",
        "grid": {"authoritative": True, "cells": [{"multiplier": 2.5}]},
    }, now=1_786_768_202.0)
    assert view["gridAgeSeconds"] is None
    assert view["grid"]["authoritative"] is False
    assert view["grid"]["stale"] is True


def test_bridge_token_requires_explicit_atomic_provisioning_and_rotation(tmp_path):
    bridge = load_script("euphoria_bridge_server_token", "euphoria-bridge-server.py")
    token_path = tmp_path / "bridge-token"
    first = "a" * 64
    second = "b" * 64
    assert bridge.authorize_bridge_token(first, token_path) is False
    assert not token_path.exists()
    bridge.rotate_bridge_token(first, token_path)
    assert token_path.read_text() == first
    assert os.stat(token_path).st_mode & 0o777 == 0o600
    assert bridge.authorize_bridge_token(first, token_path) is True
    assert bridge.authorize_bridge_token(second, token_path) is False
    bridge.rotate_bridge_token(second, token_path)
    assert token_path.read_text() == second
    assert bridge.authorize_bridge_token(first, token_path) is False
    assert bridge.authorize_bridge_token(second, token_path) is True
    assert bridge.authorize_bridge_token("short", token_path) is False


def test_bridge_token_rotation_cli_reads_stdin_without_echoing_token(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "euphoria-bridge-server.py"
    token = "c" * 64
    env = os.environ.copy()
    env["EUPHORIA_BRIDGE_DATA"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(script), "token-rotate"],
        input=token,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert token not in result.stdout
    assert token not in result.stderr
    target = tmp_path / "bridge-token"
    assert target.read_text() == token
    assert os.stat(target).st_mode & 0o777 == 0o600


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
    bridge.rotate_bridge_token(token, bridge.BRIDGE_TOKEN)

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

        rotated = "b" * 64
        bridge.rotate_bridge_token(rotated, bridge.BRIDGE_TOKEN)
        try:
            urllib.request.urlopen(authorized, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("pre-rotation bridge token remained authorized")
        rotated_request = urllib.request.Request(
            base + "/euphoria/state", data=payload,
            headers={"Content-Type": "application/json", "X-Euphoria-Bridge-Token": rotated},
            method="POST",
        )
        with urllib.request.urlopen(rotated_request, timeout=2) as response:
            assert json.load(response) == {"ok": True}

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
