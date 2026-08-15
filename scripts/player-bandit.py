#!/usr/bin/env python3
"""Train the offline PLS contextual-UCB policy; never submits trades."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.player_bandit import build_episodes, train_policy, write_json_atomic
from src.analytics.player_learning import read_jsonl

DEFAULT_BRIDGE = Path("~/.legion-trading-bot/data/euphoria-bridge").expanduser()
DEFAULT_GRADER = Path("~/.legion-trading-bot/data/euphoria-grader/ledger.jsonl").expanduser()


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    payload = "".join(json.dumps(row, sort_keys=True, allow_nan=False) + "\n" for row in rows)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline-only PLS contextual bandit trainer")
    parser.add_argument("--grader", type=Path, default=DEFAULT_GRADER)
    parser.add_argument("--player-events", type=Path, default=DEFAULT_BRIDGE / "player-events.jsonl")
    parser.add_argument("--policy", type=Path, default=DEFAULT_BRIDGE / "rl-policy.json")
    parser.add_argument("--episodes", type=Path, default=DEFAULT_BRIDGE / "rl-episodes.jsonl")
    parser.add_argument("--holdout-weekday", type=int, default=6, choices=range(7))
    args = parser.parse_args()

    normalized = build_episodes(read_jsonl(args.grader), read_jsonl(args.player_events))
    policy, _ = train_policy(normalized, holdout_weekday=args.holdout_weekday)
    policy["inputs"] = {"grader": str(args.grader), "player_events": str(args.player_events)}
    write_jsonl_atomic(args.episodes, normalized)
    write_json_atomic(args.policy, policy)
    print(
        f"wrote {args.policy} · episodes={len(normalized)} · "
        f"train={policy['training_window']['episodes']} · "
        f"dry_run_eligible={policy['promotion']['eligible_for_dry_run']}"
    )


if __name__ == "__main__":
    main()
