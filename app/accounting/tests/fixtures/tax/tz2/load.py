"""Load TZ2 fixture scenarios for tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TZ2_FIXTURES_ROOT = Path(__file__).resolve().parent

SCENARIO_IDS = (
    'official_example',
    'empty',
    'alma_2026',
    'missing_last_name',
)


def scenario_dir(scenario_id: str) -> Path:
    path = TZ2_FIXTURES_ROOT / scenario_id
    if not path.is_dir():
        raise FileNotFoundError(f'Unknown TZ2 scenario: {scenario_id}')
    return path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def load_scenario(scenario_id: str) -> dict[str, Any]:
    base = scenario_dir(scenario_id)
    result: dict[str, Any] = {'id': scenario_id}
    input_path = base / 'input.json'
    if input_path.exists():
        result['input'] = _load_json(input_path)
    expected_path = base / 'expected_payload.json'
    if expected_path.exists():
        result['expected'] = _load_json(expected_path)
    validation_path = base / 'expected_validation.json'
    if validation_path.exists():
        result['validation'] = _load_json(validation_path)
    return result
