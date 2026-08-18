"""Clock alignment. No network.

Three clocks meet in this system: the quote frame's server timestamp (which
mints every `cell_x`), the page's snapshot of the server clock (`now_ms`), and
the Python host clock (which stamps ticks and grades windows).

Every pre-existing test set all three equal, so the bugs below were invisible
by construction. These tests deliberately pull them apart.
"""
import math

from src.analytics.forecast import Diffusion, forecast_grid
from src.analytics.gridboard import GridBoard
from src.analytics.policy import Bankroll, CellScore, Policy
from src.analytics.calibration import Calibrator
from src.analytics.signal import GRID_STALE_S, _column_geometry
from src.control.room import ControlRoom

COLUMN_S = 5.0
DPL = 0.5
DIFF = Diffusion(mu=0.0, mu_raw=0.0, sigma=0.3, pairs=40, span_s=40.0, ok=True)


def _cells(current_x, current_y, forwards=(1, 2, 3)):
    """Cells as the extension mints them: cell_x absolute, forward relative."""
    return [
        {"cell_x": current_x + f, "cell_y": current_y + 1, "forward": f,
         "row_offset": 1, "multiplier": 5.0}
        for f in forwards
    ]


# --- the time axis must be absolute, like the price axis -------------------
def test_timing_comes_from_cell_x_not_from_a_stale_forward_field():
    """A quote frame minted one column ago must not shift every window by 5s.

    `forward` is relative to the frame's own timestamp. If a column boundary
    falls between that timestamp and our read of it, `forward` is stale while
    `cell_x` is still exact. Timing must follow cell_x.
    """
    price = 1000.0
    current_y = math.floor(price / DPL)
    # The frame was minted when the current column was 100; we are now in 101.
    stale = _cells(100, current_y, forwards=(1, 2, 3))
    out = forecast_grid(
        stale, price=price, dpl=DPL, column_s=COLUMN_S,
        current_x=101, now_offset_s=0.0, diffusion=DIFF,
    )
    by_x = {c.cell_x: c for c in out}
    # cell_x 101 IS the current column -> it opened 0s ago and closes in 5s.
    assert by_x[101].t_start == 0.0
    assert by_x[101].t_end == COLUMN_S
    # cell_x 102 is genuinely one column out, whatever its stale `forward` says.
    assert by_x[102].t_start == COLUMN_S
    assert by_x[103].t_start == 2 * COLUMN_S
    # `forward` is recomputed for display, never trusted for timing.
    assert by_x[102].forward == 1


def test_a_closed_column_among_live_ones_is_dropped_not_priced():
    price = 1000.0
    # 101 and 102 have closed; 106 and 107 are still ahead.
    cells = _cells(100, math.floor(price / DPL), forwards=(1, 2)) + \
        _cells(100, math.floor(price / DPL), forwards=(6, 7))
    out = forecast_grid(
        cells, price=price, dpl=DPL, column_s=COLUMN_S,
        current_x=105, now_offset_s=0.0, diffusion=DIFF,
    )
    assert {c.cell_x for c in out} == {106, 107}
    assert all(c.t_end > 0 for c in out)


def test_a_wholly_closed_board_is_re_anchored_not_re_timed_per_cell():
    """A clock disagreement must not blank the board -- nor may it be papered
    over by re-timing each cell from its own `forward` field. That splits the
    absolute price axis from a relative time axis, which is what had the bot
    grading windows five seconds from the cells it named."""
    price = 1000.0
    cells = _cells(100, math.floor(price / DPL), forwards=(1, 2, 3))  # 101..103
    out = forecast_grid(
        cells, price=price, dpl=DPL, column_s=COLUMN_S,
        current_x=105, now_offset_s=0.0, diffusion=DIFF,       # anchor far ahead
    )
    assert len(out) == 3, "a clock offset must not blank a live board"
    by_x = {c.cell_x: c for c in out}
    # Re-anchored uniformly: spacing between columns is preserved exactly.
    assert by_x[102].t_start - by_x[101].t_start == COLUMN_S
    assert by_x[103].t_start - by_x[102].t_start == COLUMN_S
    # And the earliest quoted column is the next one to open, not one already gone.
    assert by_x[101].t_start >= 0


def test_every_cell_starts_where_its_own_column_index_says():
    """The invariant that ties the two axes together."""
    price = 1000.0
    current_x, offset = 200, 2.0
    out = forecast_grid(
        _cells(current_x, math.floor(price / DPL), forwards=(1, 2, 3, 4)),
        price=price, dpl=DPL, column_s=COLUMN_S,
        current_x=current_x, now_offset_s=offset, diffusion=DIFF,
    )
    server_now = current_x * COLUMN_S + offset
    for cell in out:
        assert cell.t_start == cell.cell_x * COLUMN_S - server_now
        assert int((server_now + cell.t_start) // COLUMN_S) == cell.cell_x


# --- the snapshot ages between being taken and being used -----------------
def test_the_column_anchor_advances_with_the_snapshots_age():
    """A grid read 2s after it was built is 2s into the future, not frozen."""
    now = 1_000_000.0
    grid = {"now_ms": 500_000.0, "square_duration": 5000, "_received_local": now}
    fresh_x, fresh_off, _ = _column_geometry(grid, now)
    later_x, later_off, _ = _column_geometry(grid, now + 2.0)
    assert later_off == fresh_off + 2.0 or later_x == fresh_x + 1
    # 2 seconds of real time must advance the anchor by 2 seconds.
    assert (later_x * 5.0 + later_off) - (fresh_x * 5.0 + fresh_off) == 2.0


def test_a_stalled_tab_is_abandoned_rather_than_replayed():
    now = 1_000_000.0
    grid = {"now_ms": 500_000.0, "square_duration": 5000,
            "_received_local": now - GRID_STALE_S - 5.0}
    cur_x, _, _ = _column_geometry(grid, now)
    assert cur_x == int(now // COLUMN_S)      # fell back to the local clock


def test_a_grid_without_an_arrival_stamp_still_works():
    """Older payloads and tests that predate the stamp must not break."""
    grid = {"now_ms": 500_000.0, "square_duration": 5000}
    cur_x, offset, column_s = _column_geometry(grid, 1_000_000.0)
    assert cur_x == 100 and offset == 0.0 and column_s == 5.0


def test_the_room_stamps_arrival_on_every_grid(tmp_path):
    room = ControlRoom(
        enable_oracle=False, enable_wallet=False, dry_run=True,
        session_path=tmp_path / "s.json", token_path=tmp_path / "t.json",
        calibration_path=tmp_path / "c.json", bankroll_path=tmp_path / "b.json",
    )
    room.set_grid({"now_ms": 123.0, "square_duration": 5000})
    assert room.grid["_received_local"] > 0
    room.close()


# --- a settled square can never be re-entered -----------------------------
def _score(cell_x=10, cell_y=100, t_start=0.0, t_end=5.0, stake=10.0):
    return CellScore(
        cell_x=cell_x, cell_y=cell_y, forward=1, row_offset=1, side="up", distance=1,
        lo=cell_y * DPL, hi=cell_y * DPL + DPL, t_start=t_start, t_end=t_end,
        horizon_s=t_end, edge_cells=0.5, sd_cells=1.0,
        p_model=0.9, p_cal=0.9, p_lcb=0.85, n_obs=500, trusted=True,
        multiplier=26.8, breakeven=0.0373, ev=21.8, ev_lcb=21.7,
        kelly=0.5, stake=stake, verdict="tap",
    )


def _board():
    return GridBoard(calibrator=Calibrator(), bankroll=Bankroll())


def test_a_square_can_only_ever_be_booked_once():
    """This is the bug that manufactured a 30x paper profit from one cell."""
    board = _board()
    now = 1000.0
    assert board.register_tap(_score(), now=now) is not None
    assert board.register_tap(_score(), now=now) is None        # still open
    # Settle it, which removes it from open_taps.
    from src.analytics.signal import Tick
    ticks = [Tick("ETH", 50.05, now + i * 0.5, source="test") for i in range(10)]
    board.settle(ticks, now=now + 6.0)
    assert board.bankroll.trades == 1
    # The square is finished. Re-proposing it must not pay again.
    assert board.register_tap(_score(), now=now + 6.0) is None
    assert board.bankroll.trades == 1


def test_a_closed_column_is_never_booked():
    board = _board()
    assert board.register_tap(_score(t_start=-10.0, t_end=-5.0), now=1000.0) is None


def test_booked_squares_are_reported():
    board = _board()
    board.register_tap(_score(cell_x=11), now=1000.0)
    board.register_tap(_score(cell_x=12), now=1000.0)
    assert board.stats()["booked_squares"] == 2


# --- the invariant counter ------------------------------------------------
def test_record_flags_a_cell_whose_time_and_column_disagree():
    board = _board()
    # t_start says "now", but the cell claims a column far away.
    board.record([_score(cell_x=999_999, t_start=0.0, t_end=5.0)],
                 now=1000.0, vol="LOW", column_s=COLUMN_S)
    assert board.stats()["misaligned_cells"] == 1


def test_a_whole_column_slip_is_caught():
    """The exact bug: cell names column N, timing points at column N+1."""
    board = _board()
    now = 1000.0
    cell_x = int(now // COLUMN_S)
    board.record([_score(cell_x=cell_x, t_start=COLUMN_S, t_end=2 * COLUMN_S)],
                 now=now, vol="LOW", column_s=COLUMN_S)
    assert board.stats()["misaligned_cells"] == 1


def test_record_accepts_a_correctly_aligned_cell():
    board = _board()
    now = 1000.0
    cell_x = int(now // COLUMN_S)
    board.record([_score(cell_x=cell_x, t_start=0.0, t_end=5.0)],
                 now=now, vol="LOW", column_s=COLUMN_S)
    assert board.stats()["misaligned_cells"] == 0
    assert board.stats()["pending"] == 1


def test_transport_jitter_is_not_reported_as_misalignment():
    """A 120ms hop is normal; only a real slip should trip the counter."""
    board = _board()
    now = 1000.0
    cell_x = int(now // COLUMN_S)
    board.record([_score(cell_x=cell_x, t_start=0.12, t_end=5.12)],
                 now=now, vol="LOW", column_s=COLUMN_S)
    assert board.stats()["misaligned_cells"] == 0


def test_a_cell_is_only_checked_once_not_on_every_pass():
    """The counter must measure slips, not how often the loop ran."""
    board = _board()
    now = 1000.0
    bad = _score(cell_x=999_999, t_start=0.0, t_end=5.0)
    for _ in range(20):
        board.record([bad], now=now, vol="LOW", column_s=COLUMN_S)
    assert board.stats()["misaligned_cells"] == 1
