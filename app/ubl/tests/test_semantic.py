import json
from decimal import Decimal
from pathlib import Path

import pytest

from ubl.domain.document import UblDocument
from ubl.domain.errors import SemanticValidationError
from ubl.validators.semantic import validate_semantic

GOLDEN_DIR = Path(__file__).resolve().parent / 'golden' / 'hrcius2025'


def test_semantic_rejects_invalid_totals():
    payload = json.loads((GOLDEN_DIR / 'fixtures' / 'invoice_minimal.json').read_text())
    document = UblDocument.model_validate(payload)
    bad = document.model_copy(
        update={
            'totals': document.totals.model_copy(update={'payable_amount': Decimal('999')}),
        }
    )
    with pytest.raises(SemanticValidationError):
        validate_semantic(bad)


def test_semantic_accepts_finestar_reference():
    payload = json.loads((GOLDEN_DIR / 'fixtures' / 'invoice_finestar_reference.json').read_text())
    document = UblDocument.model_validate(payload)
    validate_semantic(document)
