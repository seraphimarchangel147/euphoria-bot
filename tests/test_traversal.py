"""Grid-walk statistics: dwell, break direction, hour-of-day. No network."""
import time

from src.analytics.signal import Tick
from src.analytics.traversal import (
    MIN_SAMPLES_TRUSTED,
    Traversal,
    dwell_bucket,
    hour_bucket,
)
from src.control.room import ControlRoom

DPL = 0.5
COLUMN_S = 5.0


def _column_of(ts):
    return int(ts // COLUMN_S)


def _hold(row, columns, *, t0, dpl=DPL, per_column=3):
    """Ticks that sit inside `row` for `columns` grid squares."""
    px = row * dpl + dpl / 2
    out = []
    for c in range(columns):
        for i in range(per_column):
            out.append(Tick("ETH", px, t0 + c * COLUMN_S + i * 1.2, source="page"))
    return out


def test_price_resting_on_a_boundary_does_not_fake_a_break():
    """Cent-level jitter across a row edge is not a row change.

    Seen live: 181 visits in under two minutes, every one exactly one square
    long, break chance 100% -- entirely manufactured by boundary flicker.
    """
    tr = Traversal()
    edge = 100 * DPL          # exactly on the line between rows 99 and 100
    ticks = []
    for i in range(60):
        ticks.append(Tick("ETH", edge + (0.002 if i % 2 else -0.002),
                          1000.0 + i * 0.5, source="page"))
    assert tr.observe(ticks, dpl=DPL, column_of=_column_of) == 0
    assert tr.visits == []


def test_a_genuine_break_still_registers():
    tr = Traversal()
    t0 = 1000.0
    committed = 101 * DPL + 0.2      # well clear of the boundary
    ticks = _hold(100, 2, t0=t0) + [Tick("ETH", committed, t0 + 12.0, source="page")]
    assert tr.observe(ticks, dpl=DPL, column_of=_column_of) == 1


def test_the_oracle_feed_is_kept_out_of_the_walk():
    """Two feeds a few cents apart look like constant row crossings."""
    tr = Traversal()
    ticks = []
    for i in range(40):
        ticks.append(Tick("ETH", 100 * DPL + 0.4, 1000.0 + i * 1.0, source="page"))
        ticks.append(Tick("ETH", 101 * DPL + 0.1, 1000.0 + i * 1.0 + 0.4, source="redstone"))
    tr.observe(ticks, dpl=DPL, column_of=_column_of)
    assert tr.visits == []           # the oracle never enters the walk


# --- bucketing ------------------------------------------------------------
def test_dwell_buckets_cover_short_and_long_holds():
    assert dwell_bucket(1) == "1"
    assert dwell_bucket(2) == "2"
    assert dwell_bucket(5) == "4-5"
    assert dwell_bucket(7) == "6-8"
    assert dwell_bucket(99) == "13+"


def test_hour_blocks_are_four_hours_of_local_time():
    ts = time.mktime((2026, 8, 15, 3, 30, 0, 0, 0, -1))
    assert hour_bucket(ts) == "00-04"
    ts = time.mktime((2026, 8, 15, 14, 5, 0, 0, 0, -1))
    assert hour_bucket(ts) == "12-16"


# --- dwell and direction --------------------------------------------------
def test_a_row_visit_is_closed_when_price_leaves_it():
    tr = Traversal()
    t0 = 1000.0
    ticks = _hold(100, 3, t0=t0) + _hold(101, 1, t0=t0 + 15.0)
    assert tr.observe(ticks, dpl=DPL, column_of=_column_of) == 1
    assert len(tr.visits) == 1
    visit = tr.visits[0]
    assert visit["held"] == 3
    assert visit["exit"] == "up"
    assert visit["rows_crossed"] == 1


def test_downward_exits_are_recorded_as_down():
    tr = Traversal()
    t0 = 1000.0
    tr.observe(_hold(100, 2, t0=t0) + _hold(99, 1, t0=t0 + 10.0),
               dpl=DPL, column_of=_column_of)
    assert tr.visits[-1]["exit"] == "down"


def test_every_surviving_column_counts_as_a_trial():
    """A 4-column hold is three survivals and one break, not one sample."""
    tr = Traversal()
    t0 = 1000.0
    tr.observe(_hold(100, 4, t0=t0) + _hold(101, 1, t0=t0 + 20.0),
               dpl=DPL, column_of=_column_of)
    assert tr.hazard["1"] == [0.0, 1.0]        # survived column 1
    assert tr.hazard["2"] == [0.0, 1.0]
    assert tr.hazard["3"] == [0.0, 1.0]
    assert tr.hazard["4-5"] == [1.0, 1.0]      # broke here


def test_break_chance_rises_when_long_holds_reliably_break():
    """The pattern the user described: after so many squares, it moves."""
    tr = Traversal()
    t0 = 1000.0
    # Every visit holds exactly 6 columns then breaks.
    for i in range(40):
        base = t0 + i * 200.0
        tr.observe(_hold(100 + i * 2, 6, t0=base) + _hold(101 + i * 2, 1, t0=base + 30.0),
                   dpl=DPL, column_of=_column_of)
    early = tr.break_chance(1)
    late = tr.break_chance(6)
    assert late["p_break"] > early["p_break"]
    assert late["n"] >= MIN_SAMPLES_TRUSTED
    assert late["trusted"] is True


def test_direction_split_is_learned_not_assumed():
    tr = Traversal()
    t0 = 1000.0
    # Rows spaced so each episode is an independent visit: if the next episode
    # opened on the row the last one exited to, the two would merge into one
    # long dwell instead of two short ones.
    for i in range(30):                       # always breaks upward
        base = t0 + i * 200.0
        tr.observe(_hold(100 + i * 2, 2, t0=base) + _hold(101 + i * 2, 1, t0=base + 10.0),
                   dpl=DPL, column_of=_column_of)
    d = tr.break_direction(2)
    assert d["p_up"] > 0.8
    assert abs(d["p_up"] + d["p_down"] - 1.0) < 1e-9
    assert d["trusted"] is True


def test_an_unseen_dwell_falls_back_to_the_base_rate_and_says_so():
    tr = Traversal()
    out = tr.break_chance(9)
    assert out["n"] == 0
    assert out["trusted"] is False
    assert 0.0 <= out["p_break"] <= 1.0
    assert out["p_break_lcb"] <= out["p_break"]


# --- time of day ----------------------------------------------------------
def test_quiet_and_busy_hours_are_separated():
    """Overnight the grid crosses fewer rows per minute."""
    tr = Traversal()
    quiet = time.mktime((2026, 8, 15, 3, 0, 0, 0, 0, -1))
    busy = time.mktime((2026, 8, 15, 14, 0, 0, 0, 0, -1))
    # Night: long holds, one row crossed each time.
    for i in range(10):
        base = quiet + i * 300.0
        tr.observe(_hold(200 + i, 10, t0=base) + _hold(201 + i, 1, t0=base + 50.0),
                   dpl=DPL, column_of=_column_of)
    tr.open_visit = None
    tr._last_ts = 0.0
    # Day: short holds, same one row crossed but far quicker.
    for i in range(10):
        base = busy + i * 300.0
        tr.observe(_hold(300 + i, 1, t0=base) + _hold(301 + i, 1, t0=base + 5.0),
                   dpl=DPL, column_of=_column_of)
    rows = {r["hour"]: r for r in tr.by_hour()}
    assert rows["00-04"]["rows_per_min"] < rows["12-16"]["rows_per_min"]
    assert rows["00-04"]["mean_dwell_columns"] > rows["12-16"]["mean_dwell_columns"]
    assert tr.quietest_hour() == "00-04"
    assert tr.busiest_hour() == "12-16"


# --- live read ------------------------------------------------------------
def test_a_visit_spanning_hour_blocks_is_split_across_them():
    """A row held from 1am to 5am belongs to two blocks, not just to 00-04."""
    tr = Traversal()
    start = time.mktime((2026, 8, 15, 1, 0, 0, 0, 0, -1))
    end = time.mktime((2026, 8, 15, 5, 0, 0, 0, 0, -1))
    tr._attribute_time(start, end, rows_crossed=1)
    hours = {r["hour"]: r for r in tr.by_hour()}
    assert "00-04" in hours and "04-08" in hours
    assert hours["00-04"]["minutes"] > 100      # 1am-4am
    assert hours["04-08"]["minutes"] > 30       # 4am-5am
    # The crossing is credited once, where it happened.
    assert tr.activity["04-08"][0] == 1.0
    assert tr.activity["00-04"][0] == 0.0


def test_a_short_visit_stays_in_one_block():
    tr = Traversal()
    start = time.mktime((2026, 8, 15, 14, 0, 0, 0, 0, -1))
    tr._attribute_time(start, start + 120.0, rows_crossed=2)
    hours = {r["hour"]: r for r in tr.by_hour()}
    assert list(hours) == ["12-16"]
    assert tr.activity["12-16"][0] == 2.0


def test_a_long_quiet_hold_reads_as_quiet_not_as_a_burst():
    """The whole point: three still hours must not look like activity at 1am."""
    tr = Traversal()
    quiet_start = time.mktime((2026, 8, 15, 1, 0, 0, 0, 0, -1))
    tr._attribute_time(quiet_start, quiet_start + 3 * 3600.0, rows_crossed=1)
    busy_start = time.mktime((2026, 8, 15, 13, 0, 0, 0, 0, -1))
    for i in range(30):
        tr._attribute_time(busy_start + i * 60.0, busy_start + i * 60.0 + 55.0, rows_crossed=1)
    hours = {r["hour"]: r for r in tr.by_hour()}
    assert hours["00-04"]["rows_per_min"] < hours["12-16"]["rows_per_min"]


def test_current_reports_dwell_and_where_price_sits_in_the_row():
    tr = Traversal()
    t0 = 1000.0
    tr.observe(_hold(100, 3, t0=t0), dpl=DPL, column_of=_column_of)
    now = t0 + 12.0
    cur = tr.current(now, _column_of, price=100 * DPL + 0.4, dpl=DPL)
    assert cur["in_row"] == 100
    assert cur["dwell_columns"] >= 3
    assert 0.79 < cur["position_in_row"] < 0.81      # near the ceiling
    assert "p_break" in cur["break"]
    assert "p_up" in cur["direction"]


def test_current_is_safe_before_any_tick_arrives():
    cur = Traversal().current(1000.0, _column_of, price=0.0, dpl=DPL)
    assert cur["in_row"] is None
    assert cur["dwell_columns"] == 0


def test_ticks_are_never_processed_twice():
    tr = Traversal()
    t0 = 1000.0
    ticks = _hold(100, 2, t0=t0) + _hold(101, 1, t0=t0 + 10.0)
    tr.observe(ticks, dpl=DPL, column_of=_column_of)
    assert tr.observe(ticks, dpl=DPL, column_of=_column_of) == 0
    assert len(tr.visits) == 1


# --- persistence ----------------------------------------------------------
def test_learned_behaviour_survives_a_restart(tmp_path):
    path = tmp_path / "traversal.json"
    tr = Traversal.load(path)
    t0 = 1000.0
    for i in range(30):
        base = t0 + i * 200.0
        tr.observe(_hold(100 + i, 4, t0=base) + _hold(101 + i, 1, t0=base + 20.0),
                   dpl=DPL, column_of=_column_of)
    before = tr.break_chance(4)
    tr.save()

    again = Traversal.load(path)
    assert again.break_chance(4)["n"] == before["n"]
    assert again.break_chance(4)["p_break"] == before["p_break"]


def test_a_corrupt_store_does_not_brick_startup(tmp_path):
    path = tmp_path / "traversal.json"
    path.write_text("{not json")
    tr = Traversal.load(path)
    assert tr.visits == []
    assert tr.break_chance(1)["n"] == 0


# --- through the room -----------------------------------------------------
def test_the_room_tracks_traversal_and_exposes_it(tmp_path):
    room = ControlRoom(
        enable_oracle=False, enable_wallet=False, dry_run=True,
        session_path=tmp_path / "s.json", token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json", bankroll_path=tmp_path / "b.json",
        traversal_path=tmp_path / "tr.json",
    )
    now = time.time()
    room.set_grid({"now_ms": now * 1000.0, "square_duration": 5000,
                   "dollars_per_line": 0.5, "cell_height": 0.5})
    for tick in _hold(3758, 3, t0=now - 20.0) + _hold(3759, 1, t0=now - 4.0):
        room.ticks.push(tick.symbol, tick.price, tick.ts, source="page")
    room.think(now=now)
    view = room.traversal_view(now=now)
    assert view["visits"] >= 1
    assert "hazard_curve" in view and "by_hour" in view
    assert view["current"]["in_row"] is not None
    room.close()
    assert (tmp_path / "tr.json").is_file()
