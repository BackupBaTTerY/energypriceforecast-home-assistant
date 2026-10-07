"""The downloadable chart must match the documented copy-paste example."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).parent.parent


def test_chart_is_valid_and_matches_readme():
    example = (ROOT / "examples/price-chart-comparison.yaml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cards = [yaml.safe_load(block) for block in re.findall(r"```yaml\n(.*?)```", readme, re.S)]
    card = yaml.safe_load(example)
    assert card in cards
    assert card["all_series_config"]["extend_to"] is False
    assert len(card["series"]) == 3
    assert card["series"][2]["stroke_dash"] == 5
    assert "raw_forecast_reference" in card["series"][2]["data_generator"]
