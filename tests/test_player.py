"""The operator's real trades reaching the learning system. No network."""
import time

from src.analytics.calibration import Calibrator
from src.analytics.player import PlayerLedger, validate_event
from src.control.room import ControlRoom


def _tap_event(now, **kw):
    base = {
        "event_type": "player-tap", "tap_id": "tap-1", "ts": now * 1000.0,
        "cell_x": 500, "cell_y": 3757, "side": "up", "distance": 1,
        "multiplier": 4.0, "quoted_breakeven": 0.25, "price": 1878.9,
    }
    base.update(kw)
    return base


def _settle_event(now, **kw):
    base = {
        "event_type": "player-settle", "tap_id": "tap-1", "ts": now * 1000.0,
        "join_status": "joined", "join_delay_ms": 4200.0,
        "win": True, "stake": 2.0, "payout": 8.0,
        "cell_x": 500, "cell_y": 3757, "multiplier": 4.0,
    }
    base.update(kw)
    return base


def _score(**kw):
    base = {"p_cal": 0.30, "p_lcb": 0.22, "ev_lcb": 0.2, "verdict": "thin",
            "horizon_s": 10.0, "vol": "MED", "multiplier": 4.0}
    base.update(kw)
    return base


# --- validation -----------------------------------------------------------
def test_a_well_formed_tap_validates():
    now = time.time()
    out = validate_event(_tap_event(now), now=now)
    assert out and out["event_type"] == "player-tap"
    assert out["cell_x"] == 500 and out["side"] == "up"
    assert abs(out["ts"] - now) < 1


def test_events_from_the_wrong_time_are_refused():
    """A replayed or clock-skewed event must not enter a ledger of real bets."""
    now = time.time()
    assert validate_event(_tap_event(now - 600), now=now) is None
    assert validate_event(_tap_event(now + 600), now=now) is None


def test_junk_is_refused():
    now = time.time()
    for bad in (None, "x", {}, {"event_type": "nope"},
                _tap_event(now, tap_id=""), _tap_event(now, tap_id="ab"),
                _tap_event(now, multiplier=0), _tap_event(now, ts=0)):
        assert validate_event(bad, now=now) is None


def test_a_settle_must_be_joined_and_priced():
    now = time.time()
    assert validate_event(_settle_event(now), now=now) is not None
    assert validate_event(_settle_event(now, join_status="unmatched"), now=now) is None
    assert validate_event(_settle_event(now, win="yes"), now=now) is None
    assert validate_event(_settle_event(now, stake=0), now=now) is None
    assert validate_event(_settle_event(now, payout=-1), now=now) is None
    assert validate_event(_settle_event(now, join_delay_ms=99_999), now=now) is None


# --- taps read from the order itself --------------------------------------
def _order_tap(now, **kw):
    """A tap read off the page's outgoing order, not from a click.

    This build's gridState has no screenToCell, so pixel resolution fails and
    every pointer-sourced tap resolved to nothing. The order states the square.
    """
    base = _tap_event(now, capture_source="order", stake_requested=2.0)
    base["pointer"] = {"client_x": None, "client_y": None}
    base.update(kw)
    return base


def test_an_order_sourced_tap_needs_no_pointer():
    now = time.time()
    out = validate_event(_order_tap(now), now=now)
    assert out is not None
    assert out["capture_source"] == "order"
    assert out["stake_requested"] == 2.0


def test_the_ledger_records_where_a_tap_came_from():
    now = time.time()
    led = PlayerLedger()
    tap = led.record_tap(validate_event(_order_tap(now), now=now), _score())
    assert tap.capture_source == "order"
    assert tap.stake_requested == 2.0


def test_a_pointer_tap_still_defaults_to_pointer():
    now = time.time()
    out = validate_event(_tap_event(now), now=now)
    assert out["capture_source"] == "pointer"


def test_an_order_tap_settles_like_any_other():
    now = time.time()
    led, calib = PlayerLedger(), Calibrator()
    led.record_tap(validate_event(_order_tap(now), now=now), _score())
    s = led.record_settle(validate_event(_settle_event(now), now=now), calibrator=calib)
    assert s.delta == 6.0
    assert calib.observed == 1


# --- the ledger -----------------------------------------------------------
def test_a_tap_records_what_our_surface_said_at_the_time():
    now = time.time()
    led = PlayerLedger()
    tap = led.record_tap(validate_event(_tap_event(now), now=now), _score())
    assert tap.matched is True
    assert tap.our_p_cal == 0.30
    assert tap.our_verdict == "thin"


def test_a_tap_on_a_cell_we_never_scored_is_kept_but_unmatched():
    now = time.time()
    led = PlayerLedger()
    tap = led.record_tap(validate_event(_tap_event(now), now=now), None)
    assert tap.matched is False
    assert tap.our_p_cal is None


def test_a_tap_that_never_resolved_to_a_cell_is_kept_unresolved():
    now = time.time()
    led = PlayerLedger()
    ev = validate_event(_tap_event(now, cell_x=None, cell_y=None), now=now)
    tap = led.record_tap(ev, None)
    assert tap is not None
    assert tap.cell_x is None and tap.square_known is False
    assert tap.join_status == "unresolved"
    assert led.unresolved_taps == 1
    assert led.summary()["hit_rate"] is None


def test_a_settlement_books_real_money():
    now = time.time()
    led = PlayerLedger()
    led.record_tap(validate_event(_tap_event(now), now=now), _score())
    s = led.record_settle(validate_event(_settle_event(now), now=now))
    assert s.delta == 6.0
    summ = led.summary()
    assert summ["trades"] == 1 and summ["wins"] == 1
    assert summ["staked"] == 2.0 and summ["pnl"] == 6.0
    assert summ["expectancy_per_unit"] == 3.0


def test_a_losing_settlement_books_the_stake():
    now = time.time()
    led = PlayerLedger()
    led.record_tap(validate_event(_tap_event(now), now=now), _score())
    s = led.record_settle(validate_event(_settle_event(now, win=False, payout=0.0), now=now))
    assert s.delta == -2.0
    assert led.summary()["losses"] == 1


def test_a_settle_without_its_tap_is_counted_not_dropped_silently():
    """A broken joiner must be distinguishable from 'not trading'."""
    now = time.time()
    led = PlayerLedger()
    assert led.record_settle(validate_event(_settle_event(now), now=now)) is None
    assert led.summary()["unmatched_settles"] == 1


# --- learning -------------------------------------------------------------
def test_real_outcomes_train_their_own_calibrator():
    now = time.time()
    led, calib = PlayerLedger(), Calibrator()
    for i in range(40):
        tid = f"tap-{i}"
        led.record_tap(validate_event(_tap_event(now, tap_id=tid), now=now), _score())
        led.record_settle(
            validate_event(_settle_event(now, tap_id=tid, win=i < 12), now=now),
            calibrator=calib,
        )
    assert calib.observed == 40
    # Our surface said 0.30; reality delivered 12/40 = 0.30.
    out = calib.adjust(0.30, 10.0, "MED")
    assert 0.25 < out.p_cal < 0.36
    assert out.n == 40


def test_agreement_reports_how_our_verdicts_actually_fared():
    now = time.time()
    led = PlayerLedger()
    for i in range(10):                       # cells we called unreachable
        tid = f"unreach-{i}"
        led.record_tap(validate_event(_tap_event(now, tap_id=tid), now=now),
                       _score(verdict="unreachable", p_cal=0.02))
        led.record_settle(validate_event(
            _settle_event(now, tap_id=tid, win=False, payout=0.0), now=now))
    for i in range(6):                        # cells we called tappable
        tid = f"tappable-{i}"
        led.record_tap(validate_event(_tap_event(now, tap_id=tid), now=now),
                       _score(verdict="tap", p_cal=0.6))
        led.record_settle(validate_event(_settle_event(now, tap_id=tid, win=True), now=now))
    ag = led.agreement()
    rows = {r["our_verdict"]: r for r in ag["by_our_verdict"]}
    assert rows["unreachable"]["pnl"] < 0
    assert rows["tap"]["pnl"] > 0
    assert ag["player_brier"] is not None
    assert ag["matched"] == 16


# --- persistence ----------------------------------------------------------
def test_the_ledger_survives_a_restart(tmp_path):
    now = time.time()
    path = tmp_path / "player.json"
    led = PlayerLedger.load(path)
    led.record_tap(validate_event(_tap_event(now), now=now), _score())
    led.record_settle(validate_event(_settle_event(now), now=now))
    led.save()
    again = PlayerLedger.load(path)
    assert again.summary()["trades"] == 1
    assert again.summary()["pnl"] == 6.0


def test_a_corrupt_ledger_does_not_brick_startup(tmp_path):
    path = tmp_path / "player.json"
    path.write_text("{not json")
    assert PlayerLedger.load(path).summary()["trades"] == 0


# --- through the room -----------------------------------------------------
def _room(tmp_path, **kw):
    kw.setdefault("enable_oracle", False)
    kw.setdefault("enable_wallet", False)
    kw.setdefault("dry_run", True)
    for name, fn in (("session_path", "s"), ("token_path", "t"), ("calibration_path", "c"),
                     ("bankroll_path", "b"), ("traversal_path", "tr"), ("pnl_path", "p"),
                     ("player_path", "pl"), ("player_calibration_path", "pc")):
        kw.setdefault(name, tmp_path / f"{fn}.json")
    return ControlRoom(**kw)


def test_the_room_accepts_a_tap_and_a_settlement(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    assert room.ingest_player_event(_tap_event(now))["ok"] is True
    out = room.ingest_player_event(_settle_event(now))
    assert out["ok"] is True and out["matched"] is True
    report = room.player_view()
    assert report["summary"]["trades"] == 1
    assert report["summary"]["pnl"] == 6.0
    room.close()
    assert (tmp_path / "pl.json").is_file()


def test_the_room_refuses_a_forged_event(tmp_path):
    room = _room(tmp_path)
    assert room.ingest_player_event({"event_type": "player-tap"})["ok"] is False
    assert room.ingest_player_event({"nope": 1})["ok"] is False
    assert room.player.rejected >= 2
    room.close()


def test_a_replayed_tap_is_accepted_once(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    assert room.ingest_player_event(_tap_event(now))["ok"] is True
    assert room.ingest_player_event(_tap_event(now))["ok"] is False
    room.close()


def test_real_money_never_touches_the_paper_bankroll(tmp_path):
    """The paper roll answers 'would the policy have made money'. Mixing in
    discretionary human taps would destroy the only clean answer to that."""
    room = _room(tmp_path)
    now = time.time()
    before = room.bankroll.balance
    room.ingest_player_event(_tap_event(now))
    room.ingest_player_event(_settle_event(now))
    assert room.bankroll.balance == before
    assert room.bankroll.trades == 0
    assert room.player.summary()["trades"] == 1
    room.close()


def test_real_outcomes_do_not_contaminate_the_free_label_buckets(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    before = room.calibrator.observed
    room.ingest_player_event(_tap_event(now))
    room.ingest_player_event(_settle_event(now))
    assert room.calibrator.observed == before      # the grid's own labels, untouched
    room.close()

# --- window-settle / square-unknown ---------------------------------------
def test_a_cell_less_tap_joins_its_closed_window_as_square_unknown():
    now = time.time()
    led = PlayerLedger()
    ev = validate_event(_tap_event(now, cell_x=None, cell_y=None, tap_id="orphan-1"), now=now)
    tap = led.record_tap(ev, None)
    assert tap.join_status == "unresolved"
    joined = led.join_window(now - 1.0, now + 4.0, outcome="hit")
    assert len(joined) == 1
    assert joined[0].join_status == "window-settled"
    assert joined[0].join_tag == "square-unknown"
    assert joined[0].square_known is False
    assert joined[0].window_outcome == "hit"  # helper grade, not player square
    assert led.unresolved_taps == 0
    assert led.window_settled == 1
    summ = led.summary()
    assert summ["trades"] == 0
    assert summ["hit_rate"] is None
    assert summ["staked"] == 0.0
    assert summ["pnl"] == 0.0


def test_window_join_does_not_invent_stake_or_pnl():
    now = time.time()
    led = PlayerLedger()
    ev = validate_event(
        _tap_event(now, cell_x=None, cell_y=None, tap_id="staked-orphan",
                   stake_requested=0.10),
        now=now,
    )
    # validate_event only copies stake_requested on the event; tap_event helper
    # may not include it unless we pass it through the payload.
    if ev is not None:
        ev["stake_requested"] = 0.10
    tap = led.record_tap(ev, None)
    assert tap.stake_requested == 0.10
    led.join_window(now - 0.5, now + 4.5, outcome="miss")
    summ = led.summary()
    assert summ["staked"] == 0.0 and summ["returned"] == 0.0 and summ["pnl"] == 0.0
    assert summ["trades"] == 0 and summ["hit_rate"] is None
    assert tap.join_status == "window-settled"
    assert tap.stake_requested == 0.10  # kept on the tap, never booked


def test_window_join_skips_taps_outside_the_window():
    now = time.time()
    led = PlayerLedger()
    led.record_tap(validate_event(_tap_event(now - 30, cell_x=None, cell_y=None,
                                            tap_id="old"), now=now - 30), None)
    # clock skew may refuse the old event; plant it directly if so
    if "old" not in led.taps:
        from src.analytics.player import PlayerTap
        led.taps["old"] = PlayerTap(
            tap_id="old", ts=now - 30, cell_x=None, cell_y=None, side="",
            distance=None, multiplier=None, quoted_breakeven=None, price=None,
        )
        led.unresolved_taps += 1
    led.record_tap(validate_event(_tap_event(now, cell_x=None, cell_y=None,
                                            tap_id="now"), now=now), None)
    joined = led.join_window(now - 1.0, now + 4.0, outcome="sit")
    assert [t.tap_id for t in joined] == ["now"]
    assert led.taps["old"].join_status == "unresolved"


def test_window_join_does_not_assume_the_blue_pin():
    """A tap with a known cell is not the helper pick. Leave it for real settle."""
    now = time.time()
    led = PlayerLedger()
    led.record_tap(validate_event(_tap_event(now), now=now), _score())
    assert led.join_window(now - 1.0, now + 4.0, outcome="hit") == []
    tap = next(iter(led.taps.values()))
    assert tap.join_status == "unresolved"
    assert led.summary()["hit_rate"] is None


def test_backfill_joins_orphans_against_remembered_windows():
    now = time.time()
    led = PlayerLedger()
    led.remember_window(now - 2.0, now + 3.0, outcome="sit")
    led.record_tap(validate_event(_tap_event(now, cell_x=None, cell_y=None,
                                            tap_id="late"), now=now), None)
    assert led.unresolved_taps == 1
    assert led.backfill() == 1
    assert led.unresolved_taps == 0
    assert led.taps["late"].join_tag == "square-unknown"


def test_window_settled_taps_survive_a_restart(tmp_path):
    now = time.time()
    path = tmp_path / "player.json"
    led = PlayerLedger.load(path)
    led.record_tap(validate_event(_tap_event(now, cell_x=None, cell_y=None), now=now), None)
    led.remember_window(now - 1.0, now + 4.0, outcome="miss")
    led.backfill()
    led.save()
    again = PlayerLedger.load(path)
    again.backfill()
    assert again.window_settled == 1
    assert again.unresolved_taps == 0
    tap = next(iter(again.taps.values()))
    assert tap.join_status == "window-settled"
    assert tap.join_tag == "square-unknown"
    assert again.summary()["hit_rate"] is None


def test_historical_counter_orphans_cannot_join_without_timestamps(tmp_path):
    """The 81 live orphans were counted and dropped — no ts to overlap a window."""
    path = tmp_path / "player.json"
    path.write_text('{"version":1,"settles":[],"staked":0,"returned":0,'
                    '"wins":0,"losses":0,"unmatched_settles":0,'
                    '"unresolved_taps":81,"rejected":0}')
    led = PlayerLedger.load(path)
    led.remember_window(time.time() - 5, time.time(), outcome="hit")
    assert led.backfill() == 0
    assert led.unresolved_taps == 81
    assert led.window_settled == 0


def test_room_window_flip_joins_a_cell_less_tap(tmp_path):
    room = _room(tmp_path)
    now = time.time()
    assert room.ingest_player_event(_tap_event(now, cell_x=None, cell_y=None))["ok"] is True
    assert room.player.unresolved_taps == 1
    # Mid-window so t0 = wall_now - 2.5s covers the tap we just ingested.
    room.grid = {"square_duration": 5000, "now_ms": 1_002_500}
    body = {"grade": {"last": {"outcome": "stood-out"}}}
    room._tape_view(body)
    room.grid["now_ms"] = 1_007_500
    room._tape_view(body)
    assert room.player.unresolved_taps == 0
    tap = next(iter(room.player.taps.values()))
    assert tap.join_status == "window-settled"
    assert tap.join_tag == "square-unknown"
    edge = room.player_edge_view()
    assert edge["unresolved"] == 0
    assert edge["window_settled"] == 1
    assert edge["hit_rate"] is None
    assert "square unknown" in edge["line"]
    room.close()


def test_room_player_edge_stays_null_after_window_join(tmp_path):
    room = _room(tmp_path)
    room.player.unresolved_taps = 77
    edge = room.player_edge_view()
    assert edge["hit_rate"] is None
    assert edge["line"] == "no proven edge"
    room.close()
