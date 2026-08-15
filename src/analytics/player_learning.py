"""Deterministic, fee-neutral manual-player learning summaries.

The model describes observed manual taps. It cannot submit or authorize trades.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MIN_CONTEXT_N = 20
MIN_PROJECTOR_N = 20
SESSION_GAP_MS = 30 * 60 * 1000


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


def hour_bucket(ts: Any) -> str:
    epoch = _epoch_ms(ts)
    if epoch is None:
        return "UNKNOWN"
    hour = datetime.fromtimestamp(epoch / 1000, tz=timezone.utc).hour
    start = (hour // 6) * 6
    return f"{start:02d}-{start + 5:02d}Z"


def realized_volatility_bucket(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        return "UNKNOWN"
    samples = snapshot.get("samples")
    if not isinstance(samples, list):
        return "UNKNOWN"
    clean: list[tuple[float, float]] = []
    for row in samples:
        if not isinstance(row, dict):
            continue
        ts, price = _finite(row.get("ts")), _finite(row.get("price"))
        if ts is not None and price is not None and price > 0:
            clean.append((ts, price))
    clean.sort()
    if len(clean) < 2:
        return "UNKNOWN"
    returns_bps = [math.log(right[1] / left[1]) * 10_000 for left, right in zip(clean, clean[1:]) if left[1] > 0]
    if not returns_bps:
        return "UNKNOWN"
    sigma_bps = statistics.pstdev(returns_bps) if len(returns_bps) > 1 else abs(returns_bps[0])
    if sigma_bps < 3:
        return "LOW"
    if sigma_bps < 25:
        return "MED"
    return "HIGH"


def wilson_lower_bound(wins: int, n: int, z: float = 1.959963984540054) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denominator)


def _context(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    side = str(row.get("side") or "").lower()
    distance = _finite(row.get("distance") if row.get("distance") is not None else row.get("dist"))
    if side not in {"up", "down", "at-price"} or distance is None or distance < 0:
        return None
    vol = str(row.get("vol_bucket") or realized_volatility_bucket(row.get("volatility_snapshot"))).upper()
    return (str(int(distance)), side, vol, hour_bucket(row.get("tap_ts") if row.get("tap_ts") is not None else row.get("ts")))


def _key(context: tuple[str, str, str, str]) -> str:
    distance, side, vol, hour = context
    return f"distance={distance}|side={side}|vol={vol}|hour={hour}"


def _outcome(row: dict[str, Any]) -> tuple[bool, float, float, float] | None:
    win = row.get("win")
    stake = _finite(row.get("stake"))
    payout = _finite(row.get("payout"))
    multiplier = _finite(row.get("multiplier"))
    if not isinstance(win, bool) or stake is None or payout is None or stake <= 0 or payout < 0:
        return None
    realized_multiplier = multiplier if multiplier is not None and multiplier > 0 else (payout / stake if win else 0.0)
    net_return = (payout - stake) / stake
    return win, stake, realized_multiplier, net_return


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [_outcome(row) for row in rows]
    valid = [row for row in outcomes if row is not None]
    n = len(valid)
    wins = sum(1 for row in valid if row[0])
    win_rate = wins / n if n else 0.0
    multipliers = [row[2] for row in valid if row[2] > 0]
    avg_multiplier = statistics.fmean(multipliers) if multipliers else 0.0
    lower_bound_multiplier = min(multipliers) if multipliers else 0.0
    ev = statistics.fmean(row[3] for row in valid) if valid else 0.0
    wilson = wilson_lower_bound(wins, n)
    # A Wilson win-rate bound cannot be paired with an unconditional average
    # multiplier when payouts vary by outcome: covariance can turn a losing
    # sample into a false positive. The minimum observed positive multiplier is
    # conservative for this context and preserves the uniform-odds case.
    ev_lb = wilson * lower_bound_multiplier - 1 if lower_bound_multiplier else -1.0
    hot = n >= MIN_CONTEXT_N and ev > 0 and ev_lb > 0
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(win_rate, 6),
        "avg_multiplier": round(avg_multiplier, 6),
        "lower_bound_multiplier": round(lower_bound_multiplier, 6),
        "ev": round(ev, 6),
        "wilson_lb": round(wilson, 6),
        "ev_lower_bound": round(ev_lb, 6),
        "status": "INSUFFICIENT" if n < MIN_CONTEXT_N else "HOT" if hot else "COLD",
        "hot": hot,
    }


def _joined_player_rows(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[str, dict[str, Any]] = {}
    for row in events:
        if not isinstance(row, dict) or row.get("event_type") != "player-settle" or row.get("join_status") != "joined":
            continue
        tap_id = row.get("tap_id")
        if not isinstance(tap_id, str) or not tap_id or _outcome(row) is None or _context(row) is None:
            continue
        dedup[tap_id] = row
    return sorted(dedup.values(), key=lambda row: _epoch_ms(row.get("ts")) or 0)


def _tilt(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"flagged": False, "reason": "INSUFFICIENT", "session_length": 0}
    sessions: list[list[dict[str, Any]]] = [[]]
    previous: int | None = None
    for row in rows:
        ts = _epoch_ms(row.get("ts")) or 0
        if previous is not None and ts - previous > SESSION_GAP_MS:
            sessions.append([])
        sessions[-1].append(row)
        previous = ts
    current = sessions[-1]
    wins = [bool(row["win"]) for row in current]
    if len(wins) < 10:
        return {"flagged": False, "reason": "INSUFFICIENT", "session_length": len(wins)}
    baseline = sum(wins[:10]) / 10
    flagged_at: int | None = None
    loss_streak_at_flag = 0
    for end in range(10, len(wins) + 1):
        rolling = sum(wins[end - 10:end]) / 10
        if baseline - rolling >= 0.25:
            flagged_at = end
            streak = 0
            for won in reversed(wins[:end]):
                if won:
                    break
                streak += 1
            loss_streak_at_flag = streak
            break
    rolling = sum(wins[-10:]) / 10
    return {
        "flagged": flagged_at is not None,
        "reason": "WR_DROP_25PTS" if flagged_at is not None else "OK",
        "baseline_win_rate": round(baseline, 6),
        "rolling_10_win_rate": round(rolling, 6),
        "drop_points": round((baseline - rolling) * 100, 2),
        "session_length": len(wins),
        "flagged_at_trade": flagged_at,
        "loss_streak_at_flag": loss_streak_at_flag,
    }


def _attach_state_history(events: Iterable[dict[str, Any]], state_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    states = sorted(
        ((stamp, row) for row in state_rows if isinstance(row, dict) and (stamp := _epoch_ms(row.get("ts"))) is not None),
        key=lambda item: item[0],
    )
    enriched: list[dict[str, Any]] = []
    for original in events:
        if not isinstance(original, dict):
            continue
        row = dict(original)
        if realized_volatility_bucket(row.get("volatility_snapshot")) == "UNKNOWN":
            ts = _epoch_ms(row.get("ts"))
            eligible = [(stamp, state) for stamp, state in states if ts is not None and 0 <= ts - stamp <= 300_000]
            if eligible:
                state = eligible[-1][1]
                snapshot = state.get("volatility_snapshot") or state.get("history")
                if isinstance(snapshot, dict):
                    row["volatility_snapshot"] = snapshot
        enriched.append(row)
    return enriched


def _build_projector(grader_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    samples: list[dict[str, float | int]] = []
    for row in grader_rows:
        if not isinstance(row, dict) or "projector" not in str(row.get("source") or "").lower():
            continue
        ts = _epoch_ms(row.get("ts"))
        horizon = _finite(row.get("horizon_s"))
        expected = _finite(row.get("expected_cell_y"))
        low = _finite(row.get("range_low"))
        high = _finite(row.get("range_high"))
        actual = _finite(row.get("actual_cell_y"))
        base_width = _finite(row.get("base_width_cells"))
        if ts is None or horizon not in {5, 10, 15} or expected is None or low is None or high is None or actual is None:
            continue
        if low > high:
            continue
        samples.append({
            "ts": ts,
            "horizon_s": int(horizon),
            "expected": expected,
            "low": low,
            "high": high,
            "actual": actual,
            "base_width": max(0.5, base_width or max(expected - low, high - expected, 0.5)),
            "drift_cells": _finite(row.get("drift_cells")) or 0.0,
            "lean_score": max(-1.0, min(1.0, _finite(row.get("lean_score")) or 0.0)),
        })
    samples.sort(key=lambda row: int(row["ts"]))
    rolling = samples[-100:]
    hits = sum(1 for row in rolling if float(row["low"]) <= float(row["actual"]) <= float(row["high"]))
    rolling_rate = hits / len(rolling) if rolling else 0.0
    before_hits = sum(1 for row in samples if float(row["low"]) <= float(row["actual"]) <= float(row["high"]))
    before_accuracy = before_hits / len(samples) if samples else 0.0

    residual_ratios = sorted(abs(float(row["actual"]) - float(row["expected"])) / float(row["base_width"]) for row in samples)
    if residual_ratios:
        quantile_index = max(0, min(len(residual_ratios) - 1, math.ceil(0.68 * len(residual_ratios)) - 1))
        width_scale = max(0.5, min(3.0, residual_ratios[quantile_index]))
    else:
        width_scale = 1.0
    numerator = denominator = 0.0
    for row in samples:
        x = float(row["drift_cells"]) * float(row["horizon_s"])
        if x:
            numerator += x * (float(row["actual"]) - float(row["expected"]))
            denominator += x * x
    drift_weight = max(-2.0, min(3.0, 1.0 + numerator / denominator)) if denominator else 1.0
    coefficients = {
        "drift_weight": round(drift_weight, 6),
        "lean_cells": 0.25,
        "width_scale": round(width_scale, 6),
        "compression_scale": 0.75,
    }
    after_hits = 0
    for row in samples:
        expected = float(row["expected"]) + (drift_weight - 1) * float(row["drift_cells"]) * float(row["horizon_s"])
        width = float(row["base_width"]) * width_scale
        after_hits += int(expected - width <= float(row["actual"]) <= expected + width)
    after_accuracy = after_hits / len(samples) if samples else 0.0
    enough_accuracy = len(rolling) >= MIN_PROJECTOR_N
    return {
        "schema_version": 1,
        "horizons_s": [5, 10, 15],
        "minimum_accuracy_samples": MIN_PROJECTOR_N,
        "coefficients": coefficients,
        "rolling_accuracy": {"n": len(rolling), "hits": hits, "cone_hit_rate": round(rolling_rate, 6)},
        "confidence": "CALIBRATED" if enough_accuracy and rolling_rate >= 0.60 else "LOW CONFIDENCE",
        "enabled_default": enough_accuracy and rolling_rate > 0.60,
        "calibration": {
            "date_utc": datetime.now(timezone.utc).date().isoformat(),
            "samples": len(samples),
            "before_accuracy": round(before_accuracy, 6),
            "after_accuracy": round(after_accuracy, 6),
        },
    }


def build_player_model(
    events: Iterable[dict[str, Any]],
    grader_rows: Iterable[dict[str, Any]] = (),
    state_rows: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    state_rows = list(state_rows)
    grader_rows = _attach_state_history(grader_rows, state_rows)
    players = _joined_player_rows(_attach_state_history(events, state_rows))
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in players:
        context = _context(row)
        if context:
            grouped[context].append(row)
    squares = {_key(context): {"distance": int(context[0]), "side": context[1], "vol_bucket": context[2], "hour_bucket": context[3], **_summarize(rows)} for context, rows in sorted(grouped.items())}

    engines: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in grader_rows:
        if not isinstance(row, dict):
            continue
        touch_row = isinstance(row.get("touched"), bool) and row.get("dist") is not None
        if _outcome(row) is None and not touch_row:
            continue
        source = str(row.get("source") or row.get("signal_source") or "")
        if not touch_row and "think" not in source and "engine" not in source and "grader" not in source:
            continue
        context = _context(row)
        if context:
            engines[context].append(row)
    comparisons: dict[str, dict[str, Any]] = {}
    for context in sorted(set(grouped) & set(engines)):
        player = _summarize(grouped[context])
        engine_rows = engines[context]
        engine = _summarize(engine_rows)
        touches = [row["touched"] for row in engine_rows if isinstance(row.get("touched"), bool)]
        engine_touch_rate = sum(touches) / len(touches) if touches else engine["win_rate"]
        if engine["n"]:
            delta = player["ev"] - engine["ev"]
            metric = "ev"
            engine_ev: float | None = engine["ev"]
        else:
            delta = player["win_rate"] - engine_touch_rate
            metric = "touch_rate"
            engine_ev = None
        comparisons[_key(context)] = {
            "player_n": player["n"], "player_ev": player["ev"], "player_win_rate": player["win_rate"],
            "engine_n": engine["n"] or len(touches), "engine_ev": engine_ev,
            "engine_touch_rate": round(engine_touch_rate, 6),
            "comparison_metric": metric,
            "delta": round(delta, 6),
            "ev_delta": round(delta, 6) if metric == "ev" else None,
            "edge": "player" if delta > 0 else "engine" if delta < 0 else "tie",
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "statistical_floor": MIN_CONTEXT_N,
        "settled_taps": len(players),
        # An offline build cannot truthfully name the current market context.
        # Consumers must derive it from live page ticks before filtering squares.
        "current_context": None,
        "hot_squares_current": [],
        "squares": squares,
        "tilt": _tilt(players),
        "player_vs_engine": comparisons,
        "projector": _build_projector(grader_rows),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text().splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except (json.JSONDecodeError, ValueError):
                continue
    except FileNotFoundError:
        pass
    return rows


def write_model_atomic(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    data = json.dumps(model, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    path.chmod(0o600)
