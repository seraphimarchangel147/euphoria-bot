"""Fallback tile mapping and overlay draw plan. No browser required."""
from src.analytics.signal import Signal, Suggestion
from src.control.grid_map import (
    BTC_DPL,
    ETH_DPL,
    SQUARE_MS,
    fallback_cell_box,
    grid_xy,
    nearby_tiles,
    overlay_draw_plan,
)
from src.control.overlay_rules import stand_aside


def test_fallback_tiles_are_on_screen_without_grid_state():
    now_ms = 1_700_000_005_000
    price = 3000.25
    css_w, css_h = 900.0, 640.0
    plan = overlay_draw_plan(
        think={"pick": None, "lesson": "waiting", "suggested": "no trade"},
        now_ms=now_ms,
        price=price,
        css_w=css_w,
        css_h=css_h,
        symbol="ETH",
        grid_state=None,
    )
    assert plan["hook"] == "fallback"
    assert plan["dpl"] == ETH_DPL
    assert len(plan["pink"]) == 2
    assert {t["side"] for t in plan["pink"]} == {"up", "down"}
    for box in plan["pink"]:
        assert box["w"] >= 4 and box["h"] >= 4
        assert box["x"] + box["w"] > 0
        assert box["y"] + box["h"] > 0
        assert box["x"] < css_w
        assert box["y"] < css_h
    assert plan["blue"] is None
    gx, gy = grid_xy(now_ms, price, ETH_DPL)
    assert gx == int(now_ms // SQUARE_MS) + 1
    assert gy == int(price // ETH_DPL)
    assert nearby_tiles(now_ms, price)[0]["y"] == gy + 1
    assert nearby_tiles(now_ms, price)[1]["y"] == gy - 1


def test_btc_fallback_uses_ten_dollar_cells():
    plan = overlay_draw_plan(
        think=None,
        now_ms=5_000,
        price=64010.0,
        css_w=800,
        css_h=600,
        symbol="BTC",
    )
    assert plan["dpl"] == BTC_DPL
    assert plan["grid_y"] == int(64010.0 // BTC_DPL)
    assert len(plan["pink"]) == 2


def test_overlay_does_not_require_pick_to_draw_pink():
    plan = overlay_draw_plan(
        think={"pick": None, "lesson": "fade", "action": "sit", "suggested": "no trade"},
        now_ms=10_000,
        price=3010.4,
        css_w=720,
        css_h=540,
    )
    assert plan["pink"]
    assert plan["blue"] is None
    assert plan["stand_aside"] is True


def test_pick_paints_blue_even_when_lesson_is_fade():
    think = {
        "pick": {"side": "up", "distance": 1},
        "lesson": "fade",
        "action": "sit",
        "suggested": {"side": "up"},
    }
    plan = overlay_draw_plan(
        think=think,
        now_ms=15_000,
        price=3010.4,
        css_w=720,
        css_h=540,
    )
    assert plan["pink"]
    assert plan["blue"] is not None
    assert plan["blue"]["side"] == "up"
    assert plan["stand_aside"] is False


def test_wrong_hooked_coords_fall_back_on_screen():
    def offscreen(_gx, _gy):
        return {"x": -4000, "y": -4000, "w": 40, "h": 40}

    plan = overlay_draw_plan(
        think={"pick": None},
        now_ms=20_000,
        price=2999.8,
        css_w=800,
        css_h=600,
        grid_state={"dpl": 0.5, "now": 20_000, "cellToScreen": offscreen},
    )
    assert plan["hook"] == "fallback"
    assert len(plan["pink"]) == 2
    for box in plan["pink"]:
        assert 0 <= box["x"] < 800 or box["x"] + box["w"] > 0


def test_fallback_cell_box_centers_current_cell():
    box = fallback_cell_box(10, 50, css_w=240, css_h=240, cur_x=10, cur_y=50)
    assert abs(box["x"] + box["w"] / 2 - 120) < 1e-6
    assert abs(box["y"] + box["h"] / 2 - 120) < 1e-6


def test_stand_aside_only_when_pick_is_null():
    assert stand_aside(None) is True
    assert stand_aside({"pick": None, "lesson": "waiting"}) is True
    assert stand_aside({"pick": None, "suggested": "no trade", "lesson": "fade"}) is True
    assert stand_aside({"pick": {"side": "up"}, "lesson": "fade", "action": "sit"}) is False
    assert stand_aside({"pick": None, "suggested": {"side": "down"}}) is False


def test_fade_lesson_with_pick_keeps_pick():
    sug = Suggestion(
        asset="ETH",
        side="up",
        size=0.1,
        cell="nearest-up",
        distance=1,
        label="up",
        hint="nearest square above",
        cell_x=12,
        cell_y=6000,
    )
    sig = Signal(
        bias="up",
        confidence=0.7,
        reason="fade but still the nearby square",
        suggested=sug,
        lesson="fade",
        why="fade — 5s tape is against the higher-TF lean, still the nearby square",
        action="tap",
    )
    body = sig.to_dict()
    assert body["pick"]["side"] == "up"
    assert body["lesson"] == "fade"
    assert stand_aside(body) is False
