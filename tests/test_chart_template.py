"""The downloadable chart and card must match the documented copy-paste examples."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).parent.parent


def test_chart_is_valid_and_matches_the_docs():
    example = (ROOT / "examples/price-chart-comparison.yaml").read_text(encoding="utf-8")
    readme = (ROOT / "docs" / "dashboard.md").read_text(encoding="utf-8")
    cards = [yaml.safe_load(block) for block in re.findall(r"```yaml\n(.*?)```", readme, re.S)]
    card = yaml.safe_load(example)
    assert card in cards
    assert card["all_series_config"]["extend_to"] is False
    assert len(card["series"]) == 3
    assert card["series"][2]["stroke_dash"] == 5
    assert "raw_forecast_reference" in card["series"][2]["data_generator"]


def test_card_is_valid_and_matches_the_docs():
    example = (ROOT / "examples/price-card.yaml").read_text(encoding="utf-8")
    readme = (ROOT / "docs" / "dashboard.md").read_text(encoding="utf-8")
    cards = [yaml.safe_load(block) for block in re.findall(r"```yaml\n(.*?)```", readme, re.S)]
    card = yaml.safe_load(example)

    assert card in cards
    assert card["type"] == "sections"
    # Price, plan, chart and the measured figures - the whole answer on one view.
    assert len(card["sections"]) == 5


def test_the_card_draws_the_comparable_forecast_and_no_fixed_currency():
    example = (ROOT / "examples/price-card.yaml").read_text(encoding="utf-8")
    card = yaml.safe_load(example)
    chart = card["sections"][3]["cards"][1]
    by_name = {series["name"]: series for series in chart["series"]}
    reference = by_name["Forecast before publication"]

    # The line that is held against the published quality figures.
    assert reference["name"] == "Forecast before publication"
    assert "final_value" in reference["data_generator"]
    # Cents only where the market settles in euro; everyone else keeps theirs.
    assert "unit_of_measurement" in example
    assert example.count("u[:3] == 'EUR'") >= 5
    # Every tile says something when a figure is not there yet.
    assert "still learning" in example


def test_the_card_offers_the_longer_horizon_only_to_who_lacks_it():
    example = (ROOT / "examples/price-card.yaml").read_text(encoding="utf-8")
    footer = yaml.safe_load(example)["sections"][-1]["cards"][-1]["content"]

    assert "energypriceforecast.eu/en/private-pro/" in footer
    assert "14 days free" in footer
    # Found by its icon rather than its entity ID, which follows the HA language.
    assert "'mdi:clock-check-outline'" in footer
    # Anyone who already has 120 hours does not see an advert.
    assert "float < 120" in footer


def test_the_card_bands_the_evenings_marked_as_possible_spikes():
    example = (ROOT / "examples/price-card.yaml").read_text(encoding="utf-8")
    chart = yaml.safe_load(example)["sections"][3]["cards"][1]
    spike = {series["name"]: series for series in chart["series"]}["Price spike possible"]

    # A band on the hidden 0..1 axis, like the cheap hours - never a price.
    assert spike["yaxis_id"] == "plan"
    assert spike["type"] == "area"
    assert "spike_possible === true" in spike["data_generator"]
    assert "raw_forecast" in spike["data_generator"]
    # The caption owns up to what it is.
    assert "often a false alarm" in example
