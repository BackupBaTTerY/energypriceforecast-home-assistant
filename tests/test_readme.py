"""Keep the README's entity list in step with the entities that exist.

Adding an entity and forgetting to document it is silent: nothing fails, the
integration works, and users only find the entity by scrolling the device
page. The README is the one place people look before installing, so an entity
missing from it is a real gap - this test makes that gap fail loudly.

Only JSON and Markdown are read, so this runs without the
pytest-homeassistant-custom-component plugin.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPONENT_DIR = REPO_ROOT / "custom_components" / "energypriceforecast"


def _readme_text() -> str:
    """The README with all whitespace runs collapsed.

    Names are wrapped across lines in prose, so a raw substring search would
    miss them for reasons that have nothing to do with the documentation
    being wrong.
    """
    return re.sub(r"\s+", " ", (REPO_ROOT / "README.md").read_text(encoding="utf-8"))


def _entity_names() -> list[tuple[str, str, str]]:
    entity = json.loads(
        (COMPONENT_DIR / "translations" / "en.json").read_text(encoding="utf-8")
    )["entity"]
    return [
        (platform, key, value["name"])
        for platform, entries in entity.items()
        for key, value in entries.items()
    ]


def test_every_entity_is_listed_in_the_readme() -> None:
    readme = _readme_text()

    missing = [
        f"{platform}.{key} ({name!r})"
        for platform, key, name in _entity_names()
        if name not in readme
    ]

    assert not missing, (
        "these entities exist but are not documented in README.md: "
        + ", ".join(sorted(missing))
    )
