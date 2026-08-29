"""Key-parity checks between strings.json and every translations/*.json.

A missing key falls back to the English default at runtime, which is easy
to miss when adding a new config-flow field or entity without updating
every language file. This test only touches JSON files, not
homeassistant, so it does not need the pytest-homeassistant-custom-component
plugin.
"""
from __future__ import annotations

import json
from pathlib import Path

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "energypriceforecast"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"

# These sections are required to match exactly: they drive the config-flow
# UI and entity names, so a missing key is a visible bug. data_description
# is included too - it was once excluded as "supplementary", which is how
# the reconfigure step silently shipped with no help text at all while the
# user step had it for every field.
REQUIRED_SECTIONS = (
    ("config", "step", "user", "data"),
    ("config", "step", "user", "data_description"),
    ("config", "step", "reconfigure", "data"),
    ("config", "step", "reconfigure", "data_description"),
    ("config", "error"),
    ("config", "abort"),
    ("entity", "sensor"),
    ("entity", "binary_sensor"),
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _get(data: dict, path: tuple[str, ...]) -> dict:
    for part in path:
        data = data[part]
    return data


def _translation_files() -> list[Path]:
    return sorted(TRANSLATIONS_DIR.glob("*.json"))


def test_at_least_one_translation_file_exists() -> None:
    assert _translation_files()


def test_every_translation_file_is_valid_json() -> None:
    for path in _translation_files():
        _load(path)  # raises if invalid


def test_translations_have_every_required_key_from_strings_json() -> None:
    strings = _load(COMPONENT_DIR / "strings.json")

    for translation_path in _translation_files():
        translation = _load(translation_path)
        for section in REQUIRED_SECTIONS:
            expected_keys = set(_get(strings, section).keys())
            actual_keys = set(_get(translation, section).keys())
            missing = expected_keys - actual_keys
            assert not missing, (
                f"{translation_path.name} is missing keys {sorted(missing)} "
                f"under {'.'.join(section)}"
            )


# Fields whose meaning is not self-evident from the label alone: the
# block/plan settings interact with each other (N must fit inside X) and
# have non-obvious behaviour (plans lock in, no partial plans). Shipping
# either config-flow step without help text for these leaves users
# guessing, which is what happened once already - see REQUIRED_SECTIONS.
FIELDS_NEEDING_HELP_TEXT = (
    "cheapest_hours_count",
    "cheapest_hours_window_hours",
    "cheapest_hours_start_hour",
    "weekend_hours_count",
)


def test_plan_settings_are_explained_in_both_config_steps() -> None:
    for path in [COMPONENT_DIR / "strings.json", *_translation_files()]:
        data = _load(path)
        for step in ("user", "reconfigure"):
            descriptions = _get(data, ("config", "step", step, "data_description"))
            for field in FIELDS_NEEDING_HELP_TEXT:
                assert descriptions.get(field), (
                    f"{path.name}: no help text for {field} in the {step} step"
                )


def test_translations_do_not_have_unknown_extra_keys() -> None:
    """Catch typos: a key that exists nowhere in strings.json is dead weight."""
    strings = _load(COMPONENT_DIR / "strings.json")

    for translation_path in _translation_files():
        translation = _load(translation_path)
        for section in REQUIRED_SECTIONS:
            expected_keys = set(_get(strings, section).keys())
            actual_keys = set(_get(translation, section).keys())
            extra = actual_keys - expected_keys
            assert not extra, (
                f"{translation_path.name} has keys {sorted(extra)} under "
                f"{'.'.join(section)} that do not exist in strings.json"
            )
