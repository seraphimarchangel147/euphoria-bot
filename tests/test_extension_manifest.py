"""Sanity-check the unpacked MV3 extension folder. No browser required."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "extension"


def test_manifest_is_mv3_with_required_hosts():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    hosts = manifest["host_permissions"]
    assert "https://euphoria.finance/*" in hosts
    assert "https://api.mainnet.euphoria.finance/*" in hosts
    assert any(h.startswith("http://127.0.0.1") for h in hosts)
    scripts = manifest["content_scripts"]
    assert scripts
    for cs in scripts:
        assert all(m.startswith("https://euphoria.finance") or m.startswith("https://www.euphoria.finance") for m in cs["matches"])
    assert "background" in manifest
    assert (ROOT / manifest["background"]["service_worker"]).is_file()


def test_deployment_build_marker_matches_manifest():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    content = (ROOT / "content.js").read_text()
    assert manifest["version"] == "0.4.2"
    assert 'const CONTENT_BUILD = "0.4.2-deployment-ready";' in content


def test_page_world_helper_runs_before_the_app_opens_its_socket():
    """inject.js must beat the bundle to window.WebSocket.

    The page opens its quote socket during module init. Injecting from the
    isolated world via a <script> tag at document_idle always lost that race,
    which is why no socket frames were ever captured.
    """
    manifest = json.loads((ROOT / "manifest.json").read_text())
    main = [cs for cs in manifest["content_scripts"] if cs.get("world") == "MAIN"]
    assert main, "no MAIN-world content script declared"
    entry = main[0]
    assert entry["run_at"] == "document_start"
    assert "inject.js" in entry["js"]
    # grid-context must be defined before inject.js reads it.
    assert entry["js"].index("grid-context.js") < entry["js"].index("inject.js")
    for name in entry["js"]:
        assert (ROOT / name).is_file()


def test_extension_docs_the_three_jobs():
    text = (ROOT / "README.md").read_text().lower()
    assert "prices" in text
    assert "session" in text
    assert "overlay" in text
    assert "helper" in text
    assert "logged-in" in text or "logged in" in text
    banned = (ROOT / "background.js").read_text() + (ROOT / "content.js").read_text() + (ROOT / "inject.js").read_text()
    assert "remote-debugging" not in banned
    assert "webdriver" not in banned.lower()
    assert "turnstileToken" not in banned
    assert "solveCaptcha" not in banned
    inject = (ROOT / "inject.js").read_text()
    assert "getCellScreenBounds" in inject
    assert "euphoria-helper-layer" in inject
    assert "pointer-events" in (ROOT / "overlay.css").read_text()
    assert "gridX" in inject
    assert "click()" not in inject
    assert "requestAnimationFrame" in inject
    assert "candidates" in inject
    assert "timeframes" in inject
    assert "tf_lean" in inject
    assert "euphoria-helper-tf-strip" in inject
    assert "euphoria-helper-grade" in inject
    assert "think.why" in inject or "think.grade" in inject
    assert "setup" in inject
    assert "sit" in inject
    css = (ROOT / "overlay.css").read_text()
    assert "euphoria-helper-tf-strip" in css
    assert "euphoria-helper-grade" in css
    content = (ROOT / "content.js").read_text()
    assert "ebo-tf" in content
    assert "ebo-grade" in content
    assert "ebo-grid" in content
    assert "grid hooked" in content
    assert "grid fallback" in content
    assert "no canvas" in content
    assert "helper offline" in content
    assert "indicator" in content
    assert "click()" not in content
    assert "ebo-manual" in content
    assert "ebo-auto" in content
    assert "euphoria-sync" in content
    assert 'type: "control"' in content
    assert 'path: "/status"' in content
    assert "await fetch(" not in content
    assert "fetch(base" not in content
    assert "new EventSource" not in content
    bg = (ROOT / "background.js").read_text()
    assert 'msg.type === "mode"' in bg
    assert "euphoria-sync" in bg
    assert "async function controlCommand" in bg
    assert 'helper("/status")' in bg


def test_start_stop_handlers_request_start_then_status():
    bg = (ROOT / "background.js").read_text()
    start = bg.index("async function controlCommand")
    chunk = bg[start:start + 500]
    assert "helper(path" in chunk
    assert 'method: "POST"' in chunk
    assert 'helper("/status")' in chunk
    content = (ROOT / "content.js").read_text()
    cmd = content.index("async function command")
    body = content[cmd:cmd + 450]
    assert 'type: "control"' in body
    assert "POST" in body
    assert 'path: "/status"' in body
    assert "paintSnapshot" in body


def test_overlay_fallback_and_pink_without_pick():
    inject = (ROOT / "inject.js").read_text()
    assert "function fallbackSnap" in inject
    assert "function fallbackCellBounds" in inject
    assert "ETH_DPL = 0.5" in inject
    assert "BTC_DPL = 10" in inject
    assert "SQUARE_MS = 5000" in inject
    assert "function standAsideFromThink" in inject
    aside = inject[inject.index("function standAsideFromThink"):inject.index("function standAsideFromThink") + 420]
    assert "lesson" not in aside
    # The overlay paints the whole scored grid when /think sends a surface, and
    # still falls back to the original nearby pair when it does not.
    assert "function surfaceTiles" in inject
    assert "function surfaceStyle" in inject
    assert "No surface yet" in inject
    assert "grid-hook" in inject
    assert "pointer-events" in inject


def test_the_side_card_leads_with_a_verdict_and_explains_its_colours():
    """The card used to lead with a bias word that was true even when there was
    nothing worth tapping, and it never explained the four colours painted on
    the live chart."""
    content = (ROOT / "content.js").read_text()
    css = (ROOT / "overlay.css").read_text()
    assert "ebo-verdict" in content and "NO TRADE" in content
    # the grid-walk read the operator actually asked for
    assert "ebo-dwell" in content and "ebo-break" in content
    assert "breaks next" in content
    assert "ebo-updown" in content and "ebo-updown" in css
    # real money, and the board summary
    assert "ebo-money" in content and "USDM" in content
    assert "ebo-board" in content and "unproven" in content
    # a legend for the canvas colours
    assert "ebo-key" in content and "what the colours mean" in content
    assert "unreachable" in content and "no fit" in content
    assert "ebo-key" in css
    # the coil indicator: a move is coming, without claiming a side
    assert "ebo-coil" in content and "ebo-coil" in css
    assert "coil.headline" in content and "coil.score" in content
    # stale two-square-era copy is gone
    assert "tiles on the grid" not in content


def test_the_card_paint_cannot_be_killed_by_one_missing_element():
    """A single unguarded lookup froze the whole card on its initial markup.

    It threw part-way through every paint, so the helper looked disconnected
    while it was in fact running fine -- and nothing said why.
    """
    import re
    content = (ROOT / "content.js").read_text()
    body = content[content.index("function paintSnapshot"):]
    body = body[: body.index("\nfunction ")]
    # No direct property access on a raw getElementById inside the paint.
    assert not re.search(r'getElementById\("[^"]+"\)\.', body), \
        "paintSnapshot must null-guard every element lookup"
    # Elements written by the paint must exist in the markup it renders.
    written = set(re.findall(r'set\("([a-z0-9-]+)"', content))
    for name in written:
        assert f'id="{name}"' in content, f"paint writes #{name} but nothing renders it"
    # And the poll loop survives a paint failure.
    assert "overlay paint failed" in content


def test_the_canvas_loop_survives_a_bad_frame():
    """One throwing frame used to end the overlay for the whole session.

    `loop()` requested the next frame only after `draw()` returned, so any
    exception stopped the chain permanently and the overlay just vanished.
    """
    inject = (ROOT / "inject.js").read_text()
    body = inject[inject.index("function loop()"):]
    body = body[: body.index("}") + 1]
    reschedule = body.index("requestAnimationFrame")
    assert "try" in body and body.index("try") > reschedule, \
        "the next frame must be requested before draw() can throw"
    assert "overlay draw failed" in inject


def test_a_trade_is_read_from_the_order_not_from_pixel_math():
    """gridState on this build has no screenToCell, so inverting the canvas
    geometry resolved every captured tap to nothing. The outgoing order states
    the square outright and survives the next chart redesign."""
    inject = (ROOT / "inject.js").read_text()
    capture = (ROOT / "player-capture.js").read_text()
    content = (ROOT / "content.js").read_text()
    assert "function extractOrder" in inject and "function findOrder" in inject
    assert "cellX" in inject and "quotedGridRefTime" in inject
    # Orders leaving by either transport are read.
    assert inject.count("inspectOrder(") >= 3
    # An order-sourced tap is self-evidencing; the pointer gate is for clicks.
    assert 'capture_source === "order"' in capture
    assert 'capture_source !== "order"' in content


def test_grid_context_reads_a_real_grid_not_just_the_next_two_squares():
    ctx = (ROOT / "grid-context.js").read_text()
    assert "FORWARD_COLUMNS = 6" in ctx
    assert "ROW_RADIUS = 4" in ctx
    assert "getMultiplierForCell" in ctx
    assert "break_even_probability" in ctx
    css = (ROOT / "overlay.css").read_text()
    assert "2147483645" in css
    assert "overflow: visible" in css
    assert "pointer-events: none" in css
