"""Fallback canvas-tile mapping when React gridState is missing.

Must stay in lockstep with extension/inject.js (ETH_DPL / BTC_DPL / SQUARE_MS /
FALLBACK_ROWS / FALLBACK_COLS). Nearby up/down squares are placed on the
canvas so they stay visible without a fiber snapshot.
"""
from __future__ import annotations

from typing import Any

ETH_DPL = 0.5
BTC_DPL = 10.0
SQUARE_MS = 5000
FALLBACK_ROWS = 24
FALLBACK_COLS = 12


def dollars_per_line(symbol: str | None) -> float:
    return BTC_DPL if str(symbol or "ETH").upper() == "BTC" else ETH_DPL


def grid_xy(now_ms: float, price: float, dpl: float | None = None, symbol: str = "ETH") -> tuple[int, int]:
    step = dpl if dpl and dpl > 0 else dollars_per_line(symbol)
    grid_x = int(now_ms // SQUARE_MS) + 1
    grid_y = int(price // step)
    return grid_x, grid_y


def nearby_tiles(now_ms: float, price: float, symbol: str = "ETH", dpl: float | None = None) -> list[dict[str, Any]]:
    step = dpl if dpl and dpl > 0 else dollars_per_line(symbol)
    gx, gy = grid_xy(now_ms, price, step, symbol)
    return [
        {"x": gx, "y": gy + 1, "role": "look", "side": "up", "hint": "above"},
        {"x": gx, "y": gy - 1, "role": "look", "side": "down", "hint": "below"},
    ]


def fallback_cell_box(
    gx: int,
    gy: int,
    *,
    css_w: float,
    css_h: float,
    cur_x: int,
    cur_y: int,
) -> dict[str, float]:
    cell_w = css_w / FALLBACK_COLS
    cell_h = css_h / FALLBACK_ROWS
    left = css_w / 2 + (gx - cur_x) * cell_w - cell_w / 2
    top = css_h / 2 - (gy - cur_y) * cell_h - cell_h / 2
    return {"x": left, "y": top, "w": cell_w, "h": cell_h}


def box_on_screen(box: dict[str, float], css_w: float, css_h: float) -> bool:
    if box["w"] < 4 or box["h"] < 4:
        return False
    if box["x"] + box["w"] < 0 or box["y"] + box["h"] < 0:
        return False
    if box["x"] > css_w or box["y"] > css_h:
        return False
    return True


def overlay_draw_plan(
    *,
    think: dict[str, Any] | None,
    now_ms: float,
    price: float,
    css_w: float,
    css_h: float,
    symbol: str = "ETH",
    grid_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What the overlay paints. Pink candidates do not require a pick.

    Without grid_state this uses the fallback map so nearby squares land
    on-screen. Lesson fade/waiting does not hide tiles.
    """
    from src.control.overlay_rules import stand_aside

    if css_w < 1 or css_h < 1 or not price:
        return {"hook": "no-canvas", "pink": [], "blue": None, "stand_aside": True}

    dpl = dollars_per_line(symbol)
    hook = "hooked" if grid_state else "fallback"
    snap_now = now_ms
    if grid_state:
        dpl = float(grid_state.get("dpl") or grid_state.get("dollarsPerLine") or dpl)
        snap_now = float(grid_state.get("now") or now_ms)

    cur_x, cur_y = grid_xy(snap_now, price, dpl, symbol)
    looking = nearby_tiles(snap_now, price, symbol, dpl)
    pink: list[dict[str, Any]] = []
    for tile in looking:
        if hook == "hooked" and isinstance(grid_state, dict) and grid_state.get("cellToScreen"):
            box = dict(grid_state["cellToScreen"](tile["x"], tile["y"]))
        else:
            box = fallback_cell_box(tile["x"], tile["y"], css_w=css_w, css_h=css_h, cur_x=cur_x, cur_y=cur_y)
        if not box_on_screen(box, css_w, css_h):
            continue
        pink.append({**tile, **box})

    if hook == "hooked" and not pink:
        hook = "fallback"
        for tile in looking:
            box = fallback_cell_box(tile["x"], tile["y"], css_w=css_w, css_h=css_h, cur_x=cur_x, cur_y=cur_y)
            if box_on_screen(box, css_w, css_h):
                pink.append({**tile, **box})

    pick = (think or {}).get("pick")
    blue = None
    if pick and pick != "no trade":
        side = pick.get("side") if isinstance(pick, dict) else None
        match = next((t for t in pink if t.get("side") == side), None)
        blue = match or (pink[0] if pink else None)

    return {
        "hook": hook,
        "pink": pink,
        "blue": blue,
        "stand_aside": stand_aside(think),
        "grid_x": cur_x,
        "grid_y": cur_y,
        "dpl": dpl,
    }
