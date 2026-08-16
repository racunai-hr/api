"""Validation and sync tests for pdv-eu-source-matrix.yaml."""

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from accounting.services.tax_forms.pdv.boxes import implemented_boxes
from accounting.services.tax_forms.pdv.source_matrix import (
    GENERATED_MD_BANNER,
    SOURCE_MATRIX_MD_PATH,
    SOURCE_MATRIX_YAML_PATH,
    SourceMatrixValidationError,
    eu_box_codes_from_matrix,
    load_source_matrix,
    render_source_matrix_markdown,
    validate_source_matrix,
    write_source_matrix_markdown,
)

_EU_IMPLEMENTED_BOXES = frozenset({'204', '207', '214', '215', '307', '308'})


class PdvEuSourceMatrixTests(SimpleTestCase):
    def test_yaml_exists_and_validates(self):
        matrix = load_source_matrix()
        validate_source_matrix(matrix)
        self.assertEqual(matrix.metadata.version, '1.2.0')
        self.assertIn(matrix.metadata.status, {'Draft', 'Reviewed', 'Locked'})

    def test_event_ids_unique(self):
        matrix = load_source_matrix()
        event_ids = [event.event_id for event in matrix.events]
        self.assertEqual(len(event_ids), len(set(event_ids)))

    def test_eu_events_cover_boxes_207_and_307(self):
        matrix = load_source_matrix()
        self.assertEqual(eu_box_codes_from_matrix(matrix), _EU_IMPLEMENTED_BOXES)

    def test_reviewed_status_has_last_reviewed(self):
        matrix = load_source_matrix()
        if matrix.metadata.status in {'Reviewed', 'Locked'}:
            self.assertIsNotNone(matrix.metadata.last_reviewed)
        if matrix.metadata.status == 'Draft':
            self.assertIsNone(matrix.metadata.last_reviewed)

    def test_markdown_sync_with_yaml(self):
        matrix = load_source_matrix()
        validate_source_matrix(matrix)
        expected = render_source_matrix_markdown(matrix)
        actual = SOURCE_MATRIX_MD_PATH.read_text(encoding='utf-8')
        self.assertEqual(actual, expected)

    def test_markdown_has_generated_banner(self):
        content = SOURCE_MATRIX_MD_PATH.read_text(encoding='utf-8')
        self.assertTrue(content.startswith(GENERATED_MD_BANNER))

    def test_yaml_path_under_docs_accounting(self):
        self.assertEqual(
            SOURCE_MATRIX_YAML_PATH.name,
            'pdv-eu-source-matrix.yaml',
        )
        self.assertEqual(SOURCE_MATRIX_YAML_PATH.parent.name, 'accounting')
        self.assertEqual(SOURCE_MATRIX_MD_PATH.name, 'pdv-eu-source-matrix.md')

    def test_locked_required_before_implemented_eu_boxes(self):
        matrix = load_source_matrix()
        implemented = {box.code for box in implemented_boxes()}
        eu_implemented = implemented & _EU_IMPLEMENTED_BOXES
        if eu_implemented:
            self.assertEqual(
                matrix.metadata.status,
                'Locked',
                msg=(
                    f'EU boxes {sorted(eu_implemented)} are implemented in registry '
                    f'but source matrix status is {matrix.metadata.status!r}; '
                    'expected Locked.'
                ),
            )

    def test_write_source_matrix_markdown_is_idempotent(self):
        matrix = load_source_matrix()
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / 'pdv-eu-source-matrix.md'
            write_source_matrix_markdown(matrix, path=path)
            first = path.read_text(encoding='utf-8')
            write_source_matrix_markdown(matrix, path=path)
            second = path.read_text(encoding='utf-8')
            self.assertEqual(first, second)

    def test_draft_status_rejects_last_reviewed(self):
        matrix = load_source_matrix()
        if matrix.metadata.status != 'Draft':
            return
        from accounting.services.tax_forms.pdv import source_matrix as sm

        broken = sm.SourceMatrix(
            metadata=sm.SourceMatrixMetadata(
                version=matrix.metadata.version,
                status='Draft',
                owner=matrix.metadata.owner,
                last_reviewed='2026-07-05',
            ),
            events=matrix.events,
            invariants=matrix.invariants,
            dedup=matrix.dedup,
            account_ranges=matrix.account_ranges,
            references=matrix.references,
            raw=matrix.raw,
        )
        with self.assertRaises(SourceMatrixValidationError):
            validate_source_matrix(broken)
