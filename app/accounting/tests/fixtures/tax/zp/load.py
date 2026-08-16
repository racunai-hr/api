"""Load ZP fixture scenarios for tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ZP_FIXTURES_ROOT = Path(__file__).resolve().parent

SCENARIO_IDS = (
    'single_eu_partner',
    'multiple_eu_partners',
    'period_correction',
    'empty_period',
    'invalid_vat_id',
    'pdv_mismatch_101_103',
)


def scenario_dir(scenario_id: str) -> Path:
    path = ZP_FIXTURES_ROOT / scenario_id
    if not path.is_dir():
        raise FileNotFoundError(f'Unknown ZP scenario: {scenario_id}')
    return path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def load_scenario(scenario_id: str) -> dict[str, Any]:
    """Return ledger, expected payload, and optional pdv/validation sidecars."""
    base = scenario_dir(scenario_id)
    result: dict[str, Any] = {'id': scenario_id}

    if scenario_id == 'period_correction':
        result['ledger_v1'] = _load_json(base / 'ledger_entries_v1.json')
        result['expected_v1'] = _load_json(base / 'expected_payload_v1.json')
        result['ledger_v2'] = _load_json(base / 'ledger_entries_v2.json')
        result['expected_v2'] = _load_json(base / 'expected_payload_v2.json')
        result['pdv'] = _load_json(base / 'pdv_cross_check.json')
        result['submission'] = _load_json(base / 'submission_chain.json')
        return result

    result['ledger'] = _load_json(base / 'ledger_entries.json')

    expected_path = base / 'expected_payload.json'
    if expected_path.exists():
        result['expected'] = _load_json(expected_path)

    pdv_path = base / 'pdv_cross_check.json'
    if pdv_path.exists():
        result['pdv'] = _load_json(pdv_path)

    validation_path = base / 'expected_validation.json'
    if validation_path.exists():
        result['validation'] = _load_json(validation_path)

    return result
