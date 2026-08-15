"""Sanity-check the unpacked MV3 extension folder. No browser required."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "extension"


def test_manifest_is_mv3_with_required_hosts():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert manifest["version"] == "0.4.0"
    assert "player" in manifest["description"].lower()
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
    assert "Always draw pink" in inject
    assert "grid-hook" in inject
    assert "pointer-events" in inject
    css = (ROOT / "overlay.css").read_text()
    assert "2147483645" in css
    assert "overflow: visible" in css
    assert "pointer-events: none" in css
