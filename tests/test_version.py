"""Keep the version the API is told in step with the released version.

The User-Agent reported 0.1.0 for ten releases while manifest.json moved on,
so every request from every user was labelled with a version that had not
existed for months - and "which version are you on?" could not be answered
from the server side at all. Nothing failed, nothing warned; the two numbers
simply lived in different files.

Only manifest.json and const.py are read, so this runs without the
pytest-homeassistant-custom-component plugin.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "energypriceforecast"
)


def _manifest_version() -> str:
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text(encoding="utf-8"))
    return manifest["version"]


def _const_version() -> str:
    source = (COMPONENT_DIR / "const.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION:\s*Final\s*=\s*"([^"]+)"', source, re.M)
    assert match, "const.py no longer defines VERSION as a plain string literal"
    return match.group(1)


def test_const_version_matches_the_manifest() -> None:
    assert _const_version() == _manifest_version()


def test_version_looks_like_a_release() -> None:
    """Catch a placeholder before it ships, not ten releases later."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", _manifest_version())
