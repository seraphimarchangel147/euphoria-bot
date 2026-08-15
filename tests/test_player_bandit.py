import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.analytics.player_bandit import (
    build_episodes,
    normalize_episode,
    shaped_reward,
    split_weekly_holdout,
    train_policy,
)

MONDAY = int(datetime(2026, 8, 10, 12, tzinfo=timezone.utc).timestamp() * 1000)
SUNDAY = int(datetime(2026, 8, 16, 12, tzinfo=timezone.utc).timestamp() * 1000)


def row(i, *, ts=MONDAY, action="tap", learned_ev=0.2, reward=0.2):
    side = "up" if action == "tap" else None
    value = {
        "ts": ts + i * 1000,
        "action": action,
        "side": side,
        "distance": 1 if side else None,
        "vol_bucket": "LOW",
        "compression": False,
        "tf_lean": "up",
        "cone": "up",
        "hour_bucket": "12-17Z",
        "stake": 1,
        "learned_ev": learned_ev,
    }
    if action == "tap":
        value["pnl"] = reward
    return value


def test_profit_and_opportunity_cost_reward_shaping():
    assert shaped_reward(row(0, action="tap", reward=0.4), "tap:up:1") == 0.4
    assert shaped_reward(row(0, action="sit", learned_ev=0.25), "sit") == -0.25
    assert shaped_reward(row(0, action="sit", learned_ev=-0.25), "sit") == 0.01


def test_frozen_weekly_holdout_never_enters_training_window():
    episodes = build_episodes([row(0), row(1, ts=SUNDAY)], [])
    train, holdout = split_weekly_holdout(episodes, weekday=6)
    assert [episode["date_utc"] for episode in train] == ["2026-08-10"]
    assert [episode["date_utc"] for episode in holdout] == ["2026-08-16"]
    assert not ({episode["ts"] for episode in train} & {episode["ts"] for episode in holdout})


def test_context_contains_vol_compression_tf_cone_and_hour():
    episode = normalize_episode(row(0), "grader")
    assert episode is not None
    assert episode["state"] == {
        "vol_bucket": "LOW",
        "compression": False,
        "tf_lean": "UP",
        "cone": "UP",
        "hour_bucket": "12-17Z",
    }


def test_ucb_policy_does_not_hide_in_positive_ev_states_and_beats_baseline():
    training = [row(i, action="tap", reward=0.2) for i in range(400)]
    training += [row(400 + i, action="sit", learned_ev=0.2) for i in range(100)]
    holdout = []
    for i in range(20):
        value = row(i, ts=SUNDAY, action="tap", reward=0.3)
        value["baseline_action"] = "sit"
        value["action_rewards"] = {"tap:up:1": 0.3, "sit": 0.0}
        holdout.append(value)
    policy, held = train_policy(build_episodes(training + holdout, []), holdout_weekday=6)
    assert policy["training_window"]["episodes"] == 500
    assert len(held) == 20
    assert policy["eval"]["sit_rate_positive_ev"] < 0.5
    assert policy["eval"]["beats_think_signal"] is True
    assert policy["promotion"]["eligible_for_dry_run"] is True
    assert policy["promotion"]["live_money_authorized"] is False
    assert "2026-08-16" not in policy["training_window"]["dates_utc"]
    assert policy["training_window"]["holdout_dates_utc"] == ["2026-08-16"]


def test_under_500_episodes_cannot_promote():
    policy, _ = train_policy(build_episodes([row(i) for i in range(499)], []))
    assert policy["promotion"]["eligible_for_dry_run"] is False


def test_offline_cli_writes_policy_and_episode_ledger(tmp_path):
    grader = tmp_path / "grader.jsonl"
    grader.write_text(json.dumps(row(0)) + "\n")
    player = tmp_path / "player.jsonl"
    policy = tmp_path / "rl-policy.json"
    episodes = tmp_path / "rl-episodes.jsonl"
    script = Path(__file__).resolve().parents[1] / "scripts" / "player-bandit.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--grader", str(grader), "--player-events", str(player), "--policy", str(policy), "--episodes", str(episodes)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        check=True,
        capture_output=True,
        text=True,
    )
    artifact = json.loads(policy.read_text())
    assert artifact["algorithm"] == "contextual-ucb"
    assert artifact["mode"] == "dry-run-only"
    assert artifact["promotion"]["live_money_authorized"] is False
    assert len(episodes.read_text().splitlines()) == 1
    assert "train=1" in completed.stdout
