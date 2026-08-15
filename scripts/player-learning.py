#!/usr/bin/env python3
"""Regenerate player-model.json from append-only manual-player observations."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.player_learning import build_player_model, read_jsonl, write_model_atomic

DEFAULT_DATA = Path("~/.legion-trading-bot/data/euphoria-bridge").expanduser()
DEFAULT_GRADER = Path("~/.legion-trading-bot/data/euphoria-grader/ledger.jsonl").expanduser()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, default=DEFAULT_DATA / "player-events.jsonl")
    parser.add_argument("--state-history", type=Path, default=DEFAULT_DATA / "state-history.jsonl")
    parser.add_argument("--grader", type=Path, default=DEFAULT_GRADER)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA / "player-model.json")
    args = parser.parse_args()
    # State history is accepted as an explicit provenance input. D1 observations
    # already carry the capture-time page-price history used for volatility.
    state_rows = read_jsonl(args.state_history)
    model = build_player_model(
        read_jsonl(args.events),
        grader_rows=read_jsonl(args.grader),
        state_rows=state_rows,
    )
    model["inputs"] = {
        "player_events": str(args.events),
        "state_history": str(args.state_history),
        "state_history_rows": len(state_rows),
        "grader": str(args.grader),
    }
    write_model_atomic(args.output, model)
    print(f"wrote {args.output} · settled_taps={model['settled_taps']} · squares={len(model['squares'])} · tilt={model['tilt'].get('flagged', False)}")


if __name__ == "__main__":
    main()
