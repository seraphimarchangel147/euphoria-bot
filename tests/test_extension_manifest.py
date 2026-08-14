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
