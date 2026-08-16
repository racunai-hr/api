import json
import time
from pathlib import Path

import pytest

from ubl.builder.invoice import render_invoice_ubl
from ubl.domain.document import UblDocument
from ubl.validators.semantic import validate_semantic
from ubl.validators.xsd import _get_schema, validate_xsd

GOLDEN_DIR = Path(__file__).resolve().parent / 'golden' / 'hrcius2025'
FIXTURE_NAME = 'invoice_finestar_reference'
ITERATIONS = 100
SOAK_ITERATIONS = int(__import__('os').environ.get('UBL_BENCHMARK_ITERATIONS', '100'))
THRESHOLD_SECONDS = 30
SOAK_THRESHOLD_SECONDS = 300


def _load_document(name: str) -> UblDocument:
    payload = json.loads((GOLDEN_DIR / 'fixtures' / f'{name}.json').read_text())
    return UblDocument.model_validate(payload)


@pytest.mark.benchmark
def test_invoice_pipeline_benchmark():
    """100× finestar reference: validate → render → XSD (regression guard)."""
    _get_schema()
    document = _load_document(FIXTURE_NAME)

    started = time.perf_counter()
    for _ in range(ITERATIONS):
        validate_semantic(document)
        xml = render_invoice_ubl(document)
        validate_xsd(xml)
    elapsed = time.perf_counter() - started

    per_doc_ms = (elapsed / ITERATIONS) * 1000
    assert elapsed < THRESHOLD_SECONDS, (
        f'Benchmark prekoračen: {elapsed:.2f}s za {ITERATIONS} dok ({per_doc_ms:.1f}ms/doc)'
    )


@pytest.mark.benchmark
@pytest.mark.soak
def test_invoice_pipeline_soak():
    """Soak profil: 1000× (ili UBL_BENCHMARK_ITERATIONS) za load baseline."""
    _get_schema()
    document = _load_document(FIXTURE_NAME)
    iterations = SOAK_ITERATIONS

    started = time.perf_counter()
    for _ in range(iterations):
        validate_semantic(document)
        xml = render_invoice_ubl(document)
        validate_xsd(xml)
    elapsed = time.perf_counter() - started

    per_doc_ms = (elapsed / iterations) * 1000
    assert elapsed < SOAK_THRESHOLD_SECONDS, (
        f'Soak benchmark prekoračen: {elapsed:.2f}s za {iterations} dok ({per_doc_ms:.1f}ms/doc)'
    )
