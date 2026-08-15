import importlib.util
import json
import os
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
