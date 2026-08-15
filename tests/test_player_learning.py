import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.analytics.player_learning import (
    build_player_model,
    realized_volatility_bucket,
    write_model_atomic,
)

BASE_MS = 1_700_000_000_000


def snapshot(step=0.01):
    return {
        "samples": [
            {"ts": BASE_MS - 60_000, "price": 3000.0},
            {"ts": BASE_MS - 30_000, "price": 3000.0 + step},
            {"ts": BASE_MS, "price": 3000.0 - step},
        ]
    }


def settle(i, *, win=True, multiplier=1.2, side="up", distance=1, step=0.01):
    stake = 1.0
    return {
        "event_type": "player-settle",
        "tap_id": f"tap-{i}",
        "join_status": "joined",
        "ts": BASE_MS + i * 1000,
        "side": side,
        "distance": distance,
        "multiplier": multiplier,
        "win": win,
        "stake": stake,
        "payout": multiplier * stake if win else 0.0,
        "volatility_snapshot": snapshot(step),
    }


def test_realized_volatility_buckets_are_deterministic():
    assert realized_volatility_bucket(snapshot(0.01)) == "LOW"
    assert realized_volatility_bucket(snapshot(2.0)) == "MED"
    assert realized_volatility_bucket(snapshot(10.0)) == "HIGH"
    assert realized_volatility_bucket({"samples": []}) == "UNKNOWN"


def test_square_floor_and_wilson_ev_lower_bound():
    model_19 = build_player_model([settle(i) for i in range(19)])
    only_19 = next(iter(model_19["squares"].values()))
    assert only_19["n"] == 19
    assert only_19["status"] == "INSUFFICIENT"
    assert only_19["hot"] is False

    model_20 = build_player_model([settle(i) for i in range(20)])
    only_20 = next(iter(model_20["squares"].values()))
    assert only_20["n"] == 20
    assert only_20["win_rate"] == 1.0
    assert only_20["ev"] == 0.2
    assert only_20["wilson_lb"] > 0.8
    assert only_20["ev_lower_bound"] > 0
    assert only_20["status"] == "HOT"
    assert only_20["hot"] is True


def test_variable_multipliers_cannot_turn_negative_realized_ev_into_hot_edge():
    rows = []
    for i in range(20):
        won = i < 19
        rows.append(settle(i, win=won, multiplier=1.01 if won else 100.0))
    square = next(iter(build_player_model(rows)["squares"].values()))
    assert square["ev"] < 0
    assert square["ev_lower_bound"] < 0
    assert square["status"] == "COLD"
    assert square["hot"] is False


def test_tilt_fires_on_manufactured_rolling_ten_collapse():
    rows = [settle(i, win=True, multiplier=2) for i in range(10)]
    rows += [settle(i + 10, win=False, multiplier=2) for i in range(10)]
    tilt = build_player_model(rows)["tilt"]
    assert tilt["flagged"] is True
    assert tilt["baseline_win_rate"] == 1.0
    assert tilt["rolling_10_win_rate"] == 0.0
    assert tilt["drop_points"] == 100.0
    assert tilt["session_length"] == 20
    assert tilt["loss_streak_at_flag"] >= 3


def test_player_vs_engine_compares_only_matching_context():
    players = [settle(i, win=True, multiplier=1.2) for i in range(20)]
    graders = [
        {
            "source": "think-signal",
            "ts": BASE_MS + i * 1000,
            "side": "up",
            "distance": 1,
            "vol_bucket": "LOW",
            "win": False,
            "stake": 1,
            "payout": 0,
        }
        for i in range(20)
    ]
    comparisons = build_player_model(players, grader_rows=graders)["player_vs_engine"]
    row = next(iter(comparisons.values()))
    assert row["player_n"] == 20
    assert row["engine_n"] == 20
    assert row["player_ev"] == 0.2
    assert row["engine_ev"] == -1.0
    assert row["edge"] == "player"


def test_player_vs_engine_accepts_the_live_yahcob_touch_ledger_schema():
    players = [settle(i, win=True, multiplier=1.2) for i in range(20)]
    graders = [
        {"id": f"g-{i}", "ts": (BASE_MS + i * 1000) / 1000, "coin": "ETH", "side": "up", "dist": 1, "touched": False, "mult": None}
        for i in range(20)
    ]
    state_rows = [{"ts": BASE_MS, "history": snapshot(0.01)}]
    comparisons = build_player_model(players, grader_rows=graders, state_rows=state_rows)["player_vs_engine"]
    row = next(iter(comparisons.values()))
    assert row["player_n"] == 20
    assert row["engine_n"] == 20
    assert row["player_win_rate"] == 1.0
    assert row["engine_touch_rate"] == 0.0
    assert row["engine_ev"] is None
    assert row["comparison_metric"] == "touch_rate"
    assert row["edge"] == "player"


def test_state_history_supplies_nearest_capture_time_volatility():
    row = settle(0)
    row.pop("volatility_snapshot")
    model = build_player_model([row], state_rows=[{"ts": BASE_MS - 100, "history": snapshot(0.01)}])
    square = next(iter(model["squares"].values()))
    assert square["vol_bucket"] == "LOW"


def test_player_context_uses_tap_time_when_settlement_crosses_hour_bucket():
    tap_ts = int(datetime(2026, 8, 15, 5, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    row = settle(0)
    row["tap_ts"] = tap_ts
    row["ts"] = tap_ts + 2_000
    model = build_player_model([row])
    square = next(iter(model["squares"].values()))
    assert square["hour_bucket"] == "00-05Z"
    assert model["current_context"] is None
    assert model["hot_squares_current"] == []


def test_model_write_is_atomic_json(tmp_path):
    target = tmp_path / "player-model.json"
    model = build_player_model([settle(i) for i in range(20)])
    write_model_atomic(target, model)
    loaded = json.loads(target.read_text())
    assert loaded["schema_version"] == 1
    assert loaded["squares"] == model["squares"]
    assert not (tmp_path / "player-model.json.tmp").exists()


def test_player_learning_script_runs_from_repo_without_installed_package(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps(settle(0)) + "\n")
    output = tmp_path / "model.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "player-learning.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--events", str(events), "--state-history", str(tmp_path / "state.jsonl"), "--output", str(output)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert "settled_taps=1" in completed.stdout
    loaded = json.loads(output.read_text())
    assert loaded["settled_taps"] == 1
    assert loaded["inputs"]["grader"] == str(tmp_path / ".legion-trading-bot/data/euphoria-grader/ledger.jsonl")


def test_projector_accuracy_recounts_last_100_grader_rows_and_recalibrates():
    graders = []
    for i in range(120):
        hit = i >= 41  # last 100 contain 79 hits
        graders.append({
            "source": "projector-grader",
            "ts": BASE_MS + i * 1000,
            "horizon_s": 5,
            "expected_cell_y": 6000,
            "range_low": 5999,
            "range_high": 6001,
            "actual_cell_y": 6001 if hit else 6004,
            "drift_cells": 0.5,
            "lean_score": 1,
            "base_width_cells": 1,
        })
    projector = build_player_model([], grader_rows=graders)["projector"]
    assert projector["rolling_accuracy"] == {"n": 100, "hits": 79, "cone_hit_rate": 0.79}
    assert projector["confidence"] == "CALIBRATED"
    assert projector["enabled_default"] is True
    assert projector["calibration"]["samples"] == 120
    assert projector["calibration"]["before_accuracy"] == round(79 / 120, 6)
    assert 0.5 <= projector["coefficients"]["width_scale"] <= 3.0


def test_projector_honesty_gate_below_sixty_percent():
    graders = [
        {"source": "projector-grader", "ts": BASE_MS + i, "horizon_s": 5, "expected_cell_y": 10, "range_low": 9, "range_high": 11, "actual_cell_y": 10 if i < 59 else 20, "base_width_cells": 1}
        for i in range(100)
    ]
    projector = build_player_model([], grader_rows=graders)["projector"]
    assert projector["rolling_accuracy"]["cone_hit_rate"] == 0.59
    assert projector["confidence"] == "LOW CONFIDENCE"
    assert projector["enabled_default"] is False


def test_projector_does_not_claim_confidence_from_one_lucky_sample():
    grader = {"source": "projector-grader", "ts": BASE_MS, "horizon_s": 5, "expected_cell_y": 10, "range_low": 9, "range_high": 11, "actual_cell_y": 10, "base_width_cells": 1}
    projector = build_player_model([], grader_rows=[grader])["projector"]
    assert projector["rolling_accuracy"] == {"n": 1, "hits": 1, "cone_hit_rate": 1.0}
    assert projector["confidence"] == "LOW CONFIDENCE"
    assert projector["enabled_default"] is False
