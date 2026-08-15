"""Offline contextual-UCB policy for Euphoria manual-player learning.

This module can rank ``sit`` or grid-cell actions. It has no browser, network,
wallet, or order-submission dependency; promotion means dry-run grading only.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

POLICY_VERSION = 1
MIN_PROMOTION_EPISODES = 500
DEFAULT_HOLDOUT_WEEKDAY = 6  # Sunday UTC, frozen and never trained on.
DISCIPLINE_REWARD = 0.01


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _epoch_ms(value: Any) -> int | None:
    number = _finite(value)
    if number is not None:
        return int(number * 1000 if number < 10_000_000_000 else number)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _hour_bucket(ts: int) -> str:
    hour = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour
    start = hour // 6 * 6
    return f"{start:02d}-{start + 5:02d}Z"


def _direction(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("up", "long", "bull")):
        return "UP"
    if any(token in text for token in ("down", "short", "bear")):
        return "DOWN"
    return "FLAT"


def state_for(row: dict[str, Any], ts: int) -> dict[str, Any]:
    raw_projection = row.get("projection")
    projection: dict[str, Any] = raw_projection if isinstance(raw_projection, dict) else {}
    p_up = _finite(projection.get("p_up"))
    p_down = _finite(projection.get("p_down"))
    cone = row.get("cone") or row.get("cone_direction")
    if not cone and p_up is not None and p_down is not None:
        cone = "up" if p_up > p_down else "down" if p_down > p_up else "flat"
    compression = row.get("compression")
    if compression is None:
        compression = bool(row.get("compression_box"))
    return {
        "vol_bucket": str(row.get("vol_bucket") or "UNKNOWN").upper(),
        "compression": bool(compression),
        "tf_lean": _direction(row.get("tf_lean") or row.get("lean") or row.get("timeframe_lean")),
        "cone": _direction(cone),
        "hour_bucket": str(row.get("hour_bucket") or _hour_bucket(ts)),
    }


def state_key(state: dict[str, Any]) -> str:
    return "|".join((
        f"vol={state['vol_bucket']}",
        f"compression={int(bool(state['compression']))}",
        f"tf={state['tf_lean']}",
        f"cone={state['cone']}",
        f"hour={state['hour_bucket']}",
    ))


def action_for(row: dict[str, Any]) -> str | None:
    raw = str(row.get("action") or row.get("decision") or "").lower()
    if raw in {"sit", "no trade", "stand-aside", "stood-out"}:
        return "sit"
    side = str(row.get("side") or "").lower()
    distance = _finite(row.get("distance") if row.get("distance") is not None else row.get("dist"))
    if side in {"up", "down", "at-price"} and distance is not None and distance >= 0:
        return f"tap:{side}:{int(distance)}"
    return None


def shaped_reward(row: dict[str, Any], action: str) -> float | None:
    stake = _finite(row.get("stake")) or 1.0
    if stake <= 0:
        return None
    if action == "sit":
        learned_ev = _finite(row.get("learned_ev") if row.get("learned_ev") is not None else row.get("counterfactual_ev"))
        if learned_ev is None:
            return None
        return -learned_ev if learned_ev > 0 else DISCIPLINE_REWARD
    pnl = _finite(row.get("pnl") if row.get("pnl") is not None else row.get("pnl_usd"))
    if pnl is not None:
        return pnl / stake
    win = row.get("win")
    payout = _finite(row.get("payout"))
    if isinstance(win, bool) and payout is not None and payout >= 0:
        return (payout - stake) / stake
    touched = row.get("touched")
    multiplier = _finite(row.get("mult") if row.get("mult") is not None else row.get("multiplier"))
    if isinstance(touched, bool) and multiplier is not None and multiplier > 0:
        return multiplier - 1.0 if touched else -1.0
    return None


def normalize_episode(row: dict[str, Any], source: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    ts = _epoch_ms(row.get("tap_ts") if row.get("tap_ts") is not None else row.get("ts"))
    action = action_for(row)
    if ts is None or action is None:
        return None
    reward = shaped_reward(row, action)
    if reward is None:
        return None
    state = state_for(row, ts)
    raw_counterfactuals = row.get("action_rewards")
    counterfactuals: dict[str, Any] = raw_counterfactuals if isinstance(raw_counterfactuals, dict) else {}
    clean_counterfactuals = {
        str(key): value for key, raw in counterfactuals.items()
        if (value := _finite(raw)) is not None
    }
    baseline_action = str(row.get("baseline_action") or row.get("think_action") or action)
    learned_ev = _finite(row.get("learned_ev") if row.get("learned_ev") is not None else row.get("counterfactual_ev"))
    return {
        "ts": ts,
        "date_utc": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat(),
        "source": source,
        "state": state,
        "state_key": state_key(state),
        "action": action,
        "reward": round(reward, 8),
        "learned_ev": learned_ev,
        "baseline_action": baseline_action,
        "action_rewards": clean_counterfactuals,
    }


def build_episodes(grader_rows: Iterable[dict[str, Any]], player_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    for source, rows in (("grader", grader_rows), ("player", player_rows)):
        for row in rows:
            episode = normalize_episode(row, source)
            if episode is not None:
                episodes.append(episode)
    episodes.sort(key=lambda row: (row["ts"], row["source"], row["action"]))
    return episodes


def split_weekly_holdout(episodes: Iterable[dict[str, Any]], weekday: int = DEFAULT_HOLDOUT_WEEKDAY) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if weekday not in range(7):
        raise ValueError("holdout weekday must be 0..6")
    train, holdout = [], []
    for episode in episodes:
        day = datetime.fromtimestamp(int(episode["ts"]) / 1000, tz=timezone.utc).weekday()
        (holdout if day == weekday else train).append(episode)
    return train, holdout


def _reward_for(episode: dict[str, Any], action: str) -> float | None:
    value = episode.get("action_rewards", {}).get(action)
    if value is not None:
        return float(value)
    return float(episode["reward"]) if action == episode["action"] else None


def train_policy(episodes: Iterable[dict[str, Any]], *, holdout_weekday: int = DEFAULT_HOLDOUT_WEEKDAY, exploration: float = 0.35) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    all_episodes = list(episodes)
    train, holdout = split_weekly_holdout(all_episodes, holdout_weekday)
    stats: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for episode in train:
        stats[episode["state_key"]][episode["action"]].append(float(episode["reward"]))

    table: dict[str, Any] = {}
    positive_states = sit_positive = exploration_picks = 0
    for key, actions in sorted(stats.items()):
        total = sum(len(values) for values in actions.values())
        rows: dict[str, Any] = {}
        for action, values in sorted(actions.items()):
            n = len(values)
            mean = sum(values) / n
            bonus = exploration * math.sqrt(math.log(total + 1) / n)
            rows[action] = {"n": n, "mean_reward": round(mean, 8), "ucb_bonus": round(bonus, 8), "ucb_score": round(mean + bonus, 8)}
        selected = max(rows, key=lambda action: (rows[action]["ucb_score"], action != "sit", action))
        greedy = max(rows, key=lambda action: (rows[action]["mean_reward"], action != "sit", action))
        best_tap_ev = max((row["mean_reward"] for action, row in rows.items() if action.startswith("tap:")), default=-math.inf)
        if best_tap_ev > 0:
            positive_states += 1
            sit_positive += int(selected == "sit")
        exploration_picks += int(selected != greedy)
        table[key] = {"selected_action": selected, "greedy_action": greedy, "actions": rows}

    policy_rewards, baseline_rewards = [], []
    for episode in holdout:
        state = table.get(episode["state_key"])
        if not state:
            continue
        policy_reward = _reward_for(episode, state["selected_action"])
        baseline_reward = _reward_for(episode, episode["baseline_action"])
        if policy_reward is not None:
            policy_rewards.append(policy_reward)
        if baseline_reward is not None:
            baseline_rewards.append(baseline_reward)
    policy_ev = sum(policy_rewards) / len(policy_rewards) if policy_rewards else None
    baseline_ev = sum(baseline_rewards) / len(baseline_rewards) if baseline_rewards else None
    beats_baseline = policy_ev is not None and baseline_ev is not None and policy_ev > baseline_ev
    enough = len(train) >= MIN_PROMOTION_EPISODES
    sit_rate = sit_positive / positive_states if positive_states else 0.0
    training_dates = sorted({row["date_utc"] for row in train})
    holdout_dates = sorted({row["date_utc"] for row in holdout})
    policy = {
        "schema_version": POLICY_VERSION,
        "algorithm": "contextual-ucb",
        "mode": "dry-run-only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state_fields": ["vol_bucket", "compression", "tf_lean", "cone", "hour_bucket"],
        "action_space": "sit | tap:<side>:<distance>",
        "reward": {"profit_normalized": True, "missed_positive_ev_penalty": True, "negative_ev_sit_reward": DISCIPLINE_REWARD},
        "training_window": {"episodes": len(train), "dates_utc": training_dates, "holdout_weekday": holdout_weekday, "holdout_dates_utc": holdout_dates},
        "policy": table,
        "eval": {
            "holdout_episodes": len(holdout),
            "policy_scored": len(policy_rewards),
            "baseline_scored": len(baseline_rewards),
            "policy_ev": None if policy_ev is None else round(policy_ev, 8),
            "think_signal_ev": None if baseline_ev is None else round(baseline_ev, 8),
            "beats_think_signal": beats_baseline,
            "sit_rate_positive_ev": round(sit_rate, 8),
            "exploration_rate": round(exploration_picks / len(table), 8) if table else 0.0,
        },
        "promotion": {"eligible_for_dry_run": enough and beats_baseline and sit_rate < 0.5, "minimum_episodes": MIN_PROMOTION_EPISODES, "live_money_authorized": False},
    }
    return policy, holdout


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    path.chmod(0o600)
