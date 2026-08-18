"""A/B setup detectors: stall, compression, sweep, late pink. No network."""
from src.analytics.setups import (
    SETUP_PAYLOAD_KEYS,
    SetupMemory,
    detect_compression,
    detect_stall,
    detect_sweep,
    detect_swing_1m,
    missing_setup_keys,
    pair_lean,
)
from src.analytics.forecast import estimate_diffusion
from src.analytics.signal import MIN_TAP_PROB, Tick, compute_signal, reach_prob
from src.analytics.timeframes import TF_KEYS, Bar, build_stack


def _ohlc_keys(*keys: str, direction: str = "up") -> dict[str, list[Bar]]:
    out = {}
    for key in TF_KEYS:
        if key not in keys:
            continue
        bars = []
        px = 3000.0
        for i in range(6):
            close = px * (1.015 if direction == "up" else 0.985)
            bars.append(Bar(ts=100.0 + i * 60, open=px, high=max(px, close), low=min(px, close), close=close))
            px = close
        out[key] = bars
    return out


def _htf(*keys: str, direction: str = "up"):
    return build_stack([], symbol="ETH", ohlc=_ohlc_keys(*keys, direction=direction))


def _ticks(prices, t0, step=0.4):
    return [Tick("ETH", p, t0 + i * step, source="test") for i, p in enumerate(prices)]


def test_stall_overlap_sits_without_htf():
    t0 = 1_700_000_000.0
    # 2-square pull down (height 1.5 → $3), then overlap — not a new low.
    down = [3000.0 - 3.0 * i / 6 for i in range(7)]  # 3000 → 2997
    back = [2997.0 + 1.6 * i / 5 for i in range(1, 6)]  # → 2998.6
    ticks = _ticks(down + back, t0, step=0.4)
    found = detect_stall(ticks, 1.5)
    assert found is not None
    assert found["pull"] == "down"
    sig = compute_signal(ticks, now=t0 + 5.0, cell_height=1.5)
    assert sig.setup == "stall"
    assert sig.action == "sit"
    assert sig.suggested == "no trade"
    assert "sitting" in sig.sit_reason
    d = sig.to_dict()
    assert d["setup"] == "stall"
    assert d["action"] == "sit"
    assert missing_setup_keys(d) == []
    assert d["stall"] is True
    assert d["stall_squares"] in (1, 2)
    assert d["new_extreme"] is False
    assert d["blue_age_s"] is None


def test_stall_names_htf_side_but_the_gate_holds_an_unreachable_tap():
    """The stall fades back toward the 1h+4h lean -- but only if reachable.

    This tape pulls down and stalls while the higher timeframes lean up, so the
    setup wants a tap one square *up*. Price ends 0.10 above its row floor, so
    that square is a full 1.40 away while the drift still points down. The gate
    prices it at roughly one chance in five and sits. Before the gate existed
    this path named the square regardless, which is what produced a live run of
    named squares that never got touched.
    """
    t0 = 1_700_000_000.0
    down = [3000.0 - 3.0 * i / 6 for i in range(7)]
    back = [2997.0 + 1.6 * i / 5 for i in range(1, 6)]
    ticks = _ticks(down + back, t0, step=0.4)
    ohlc = _ohlc_keys("1h", "4h", "1m", "5m", "D", "M", direction="up")
    sig = compute_signal(ticks, now=t0 + 5.0, cell_height=1.5, ohlc=ohlc)
    assert sig.setup == "stall"
    assert sig.action == "sit"
    assert sig.suggested == "no trade"
    assert sig.lesson == "out-of-reach"
    assert sig.reach_prob is not None and sig.reach_prob < MIN_TAP_PROB
    assert sig.wick_squares >= 1.0
    d = sig.to_dict()
    assert missing_setup_keys(d) == []
    assert d["stall"] is True
    assert d["stall_squares"] in (1, 2)


def test_stall_gate_is_directional_not_a_blanket_veto():
    """The same tape reaches the square below easily -- the gate is not a mute."""
    t0 = 1_700_000_000.0
    down = [3000.0 - 3.0 * i / 6 for i in range(7)]
    back = [2997.0 + 1.6 * i / 5 for i in range(1, 6)]
    ticks = _ticks(down + back, t0, step=0.4)
    diff = estimate_diffusion(ticks, now=t0 + 5.0)
    last = ticks[-1].price
    assert diff.ok
    up = reach_prob("up", 1, last, 1.5, diff)
    downward = reach_prob("down", 1, last, 1.5, diff)
    assert up < MIN_TAP_PROB
    assert downward > 0.9
    assert downward > up


def test_compression_sits_inside_box_then_taps_first_break():
    t0 = 1_700_000_000.0
    height = 1.5
    ticks = []
    # Three overlapping 5s bars, range ~1 square, last close inside.
    for bar, base in enumerate((3000.0, 3000.3, 3000.2)):
        for i in range(6):
            px = base + 0.2 * (i % 3)
            ticks.append(Tick("ETH", px, t0 + bar * 5.0 + i * 0.7, source="test"))
    now_in = t0 + 14.5
    box = detect_compression(ticks, height, now=now_in)
    assert box is not None
    assert box["inside"] is True
    sit = compute_signal(ticks, now=now_in, cell_height=height)
    assert sit.setup == "compression"
    assert sit.action == "sit"
    assert sit.suggested == "no trade"
    assert sit.compression_box is not None
    assert sit.compression_box["low"] <= sit.compression_box["high"]
    sit_d = sit.to_dict()
    assert missing_setup_keys(sit_d) == []
    assert sit_d["compression"] is True
    assert sit_d["first_close_outside"] is False
    assert sit_d["break_side"] is None
    assert sit_d["box_lo"] <= sit_d["box_hi"]

    # First close outside the box. The break is still detected and reported --
    # but a compressed tape carries only ~0.5 cells of 5s range, and the break
    # leaves price sitting on its row floor with the next square a full 1.50
    # away. The gate prices that near one in ten and sits. Naming it anyway is
    # exactly the failure this gate was added for.
    ticks.append(Tick("ETH", box["high"] + 0.8, t0 + 14.8, source="test"))
    brk = compute_signal(ticks, now=t0 + 15.0, cell_height=height)
    assert brk.setup == "compression"
    assert brk.action == "sit"
    assert brk.suggested == "no trade"
    assert brk.lesson == "out-of-reach"
    assert brk.reach_prob is not None and brk.reach_prob < MIN_TAP_PROB
    brk_d = brk.to_dict()
    assert missing_setup_keys(brk_d) == []
    assert brk_d["compression"] is True
    assert brk_d["first_close_outside"] is True
    assert brk_d["break_side"] == "up"


def test_one_square_sweep_reclaims_and_skips_two_plus():
    t0 = 1_700_000_000.0
    height = 1.5
    swing_high = 3010.0
    ohlc = _ohlc_keys("4h", "D", "1h", "5m", "M", direction="up")
    ohlc["1m"] = [
        Bar(ts=t0 - 180, open=3004, high=3008, low=3003, close=3006),
        Bar(ts=t0 - 120, open=3006, high=swing_high, low=3005, close=3008),
        Bar(ts=t0 - 60, open=3008, high=3007, low=3004, close=3005),
        Bar(ts=t0, open=3005, high=3006, low=3004, close=3005),
    ]
    swing = detect_swing_1m([], now=t0 + 5.0, ohlc=ohlc)
    assert swing is not None
    assert swing["high"] == swing_high

    # Exactly 1 square past the swing, then back inside.
    one = [
        Tick("ETH", 3008.0, t0 + 0.2),
        Tick("ETH", 3011.5, t0 + 1.5),  # +1.0 square over 3010
        Tick("ETH", 3009.2, t0 + 4.5),
    ]
    found = detect_sweep(one, height, swing)
    assert found is not None
    assert found["side"] == "down"
    sig = compute_signal(one, now=t0 + 5.0, cell_height=height, ohlc=ohlc)
    assert sig.setup == "sweep"
    assert sig.action == "tap"
    assert sig.suggested.side == "down"
    assert sig.suggested.distance == 1
    assert sig.swing_1m["high"] == swing_high
    assert 0.75 <= sig.wick_squares < 2.0
    d = sig.to_dict()
    assert missing_setup_keys(d) == []
    assert d["sweep_1m"] is True
    assert d["swing_1m_hi"] == swing_high
    assert d["swing_1m_lo"] > 0
    assert d["wick_squares_past"] >= 1
    assert d["reclaim"] is True

    # 2+ square sweep is skipped.
    two = [
        Tick("ETH", 3008.0, t0 + 0.2),
        Tick("ETH", 3013.2, t0 + 1.5),  # +2.13 squares
        Tick("ETH", 3009.2, t0 + 4.5),
    ]
    assert detect_sweep(two, height, swing) is None
    skip = compute_signal(two, now=t0 + 5.0, cell_height=height, ohlc=ohlc)
    assert skip.setup != "sweep"


def test_late_pink_sits_when_range_shrinks_after_two_seconds():
    t0 = 1_700_000_000.0
    # Build a pickable drift, then overlap so the 5s range stops expanding.
    rise = [3000.0 + 3.6 * i / 7 for i in range(8)]  # 2s of +0.12%
    hold = [3003.4, 3003.5, 3003.3, 3003.45, 3003.35, 3003.5]
    ticks = _ticks(rise, t0, step=0.25) + _ticks(hold, t0 + 2.0, step=0.45)
    now = t0 + 5.0
    mem = SetupMemory()
    mem.reset(int(now // 5), t0, 2.4, "up")
    mem.pink_since = t0
    mem.shrunk = True
    mem.expanded_after_shrink = False
    sig = compute_signal(ticks, now=now, cell_height=1.5, memory=mem)
    assert sig.setup == "late_pink"
    assert sig.action == "sit"
    assert sig.suggested == "no trade"
    assert sig.range_shrinking is True
    assert sig.pink_age_s > 2.0
    assert "pink" in sig.sit_reason
    d = sig.to_dict()
    assert missing_setup_keys(d) == []
    assert d["blue_age_s"] is None
    assert d["range_shrinking"] is True


def test_early_blue_still_allowed_in_first_second():
    t0 = 1_700_000_000.0
    ticks = [Tick("ETH", 3000.0 + 3.6 * i / 5, t0 + 0.12 * i, source="test") for i in range(6)]
    now = t0 + 0.7
    mem = SetupMemory()
    mem.reset(int(now // 5), t0, 0.4, "up")
    mem.pink_since = t0
    sig = compute_signal(ticks, now=now, cell_height=1.5, memory=mem)
    assert sig.pink_age_s <= 1.0
    assert sig.setup != "late_pink"
    assert sig.suggested != "no trade"
    assert sig.action == "tap"
    d = sig.to_dict()
    assert missing_setup_keys(d) == []
    assert d["blue_age_s"] is not None
    assert d["blue_age_s"] >= 0


def test_pair_lean_requires_agreement():
    stack = _htf("1h", "4h", direction="up")
    assert pair_lean(stack, "1h", "4h") == "up"
    mixed = _htf("1h", direction="up")
    mixed_down = _htf("4h", direction="down")
    # 1h up only — 4h missing
    assert pair_lean(mixed, "1h", "4h") is None
    both = build_stack([], symbol="ETH", ohlc={**_ohlc_keys("1h", direction="up"), **_ohlc_keys("4h", direction="down")})
    assert pair_lean(both, "1h", "4h") is None


def test_think_payload_includes_setup_fields():
    t0 = 1_700_000_000.0
    ticks = [Tick("ETH", 3000.0 + 3.6 * i / 7, t0 + 5.0 * i / 7, source="test") for i in range(8)]
    d = compute_signal(ticks, now=t0 + 5.0).to_dict()
    assert d["setup"] in ("stall", "compression", "sweep", "late_pink", "none")
    assert d["action"] in ("sit", "tap")
    assert "sit_reason" in d
    assert "wick_squares" in d
    assert "compression_box" in d
    assert "swing_1m" in d
    assert "pink_age_s" in d
    assert "range_shrinking" in d
    assert d["candidates"]
    assert "pick" in d
    assert set(d["timeframes"]) == set(TF_KEYS)
    assert missing_setup_keys(d) == []
    assert d["stall_squares"] in (1, 2)
    assert d["break_side"] in ("up", "down", None)
    assert isinstance(d["wick_squares_past"], int)
    assert set(SETUP_PAYLOAD_KEYS) <= set(d)
