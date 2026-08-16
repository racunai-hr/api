"""Tests for VAT_BOX_REGISTRY invariants and documentation sync."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from accounting.models import VATLedgerEntry
from accounting.services.tax_forms.pdv.boxes import (
    BoxValueSource,
    VATBox,
    VAT_BOX_REGISTRY,
    _box,
    implemented_boxes,
    parse_pdv_mapping_markdown,
    pdv_mapping_doc_path,
    registry_doc_rows,
)
from accounting.services.tax_forms.pdv.mapping import PDV_MAPPING


class VATBoxRegistryTests(SimpleTestCase):
    def test_unique_codes(self):
        codes = [definition.code for definition in VAT_BOX_REGISTRY]
        self.assertEqual(len(codes), len(set(codes)))

    def test_stable_sort_order(self):
        codes = [definition.code for definition in VAT_BOX_REGISTRY]
        self.assertEqual(codes, sorted(codes, key=int))

    def test_enum_sync(self):
        registry_codes = {definition.code for definition in VAT_BOX_REGISTRY}
        enum_codes = set(VATBox.values)
        self.assertEqual(enum_codes, registry_codes)

    def test_reserved_implies_not_implemented(self):
        for definition in VAT_BOX_REGISTRY:
            if definition.reserved:
                self.assertFalse(
                    definition.implemented,
                    msg=f'Box {definition.code} is reserved but marked implemented',
                )

    def test_implemented_implies_active(self):
        for definition in VAT_BOX_REGISTRY:
            if definition.implemented:
                self.assertTrue(
                    definition.active,
                    msg=f'Box {definition.code} is implemented but not active',
                )

    def test_implemented_boxes_have_mapping_rules(self):
        for definition in VAT_BOX_REGISTRY:
            if definition.implemented:
                self.assertTrue(definition.mapping_rule)

    def test_value_source_field_type_consistency(self):
        for definition in VAT_BOX_REGISTRY:
            with self.subTest(box=definition.code):
                if definition.value_source in {
                    BoxValueSource.PAIR,
                    BoxValueSource.MARGIN_PAIR,
                    BoxValueSource.CATEGORY_TOTAL_OUTPUT,
                    BoxValueSource.CATEGORY_TOTAL_INPUT,
                }:
                    self.assertEqual(definition.field_type, 'pair')
                elif definition.value_source in {
                    BoxValueSource.AGGREGATE_BASE,
                    BoxValueSource.AGGREGATE_VAT,
                    BoxValueSource.COMPUTED_VAT_DUE,
                }:
                    self.assertEqual(definition.field_type, 'scalar')
                elif definition.value_source == BoxValueSource.BOOLEAN_NO_OUTPUT:
                    self.assertEqual(definition.field_type, 'bool')
                elif definition.value_source == BoxValueSource.ZERO:
                    self.assertIn(definition.field_type, ('scalar', 'bool'))

    def test_registry_every_box_has_explicit_value_source(self):
        for definition in VAT_BOX_REGISTRY:
            with self.subTest(box=definition.code):
                self.assertIsInstance(definition.value_source, BoxValueSource)

    def test_box_factory_requires_value_source(self):
        with self.assertRaises(TypeError):
            _box('999', 'Test', field_type='scalar', category='other')

    def test_registry_value_sources_are_explicit(self):
        allowed = set(BoxValueSource)
        special_codes = {
            '200': BoxValueSource.CATEGORY_TOTAL_OUTPUT,
            '300': BoxValueSource.CATEGORY_TOTAL_INPUT,
            '400': BoxValueSource.COMPUTED_VAT_DUE,
            '660': BoxValueSource.BOOLEAN_NO_OUTPUT,
            '701': BoxValueSource.MARGIN_PAIR,
            '702': BoxValueSource.MARGIN_PAIR,
            '703': BoxValueSource.MARGIN_PAIR,
            '704': BoxValueSource.MARGIN_PAIR,
            '610': BoxValueSource.AGGREGATE_BASE,
            '612': BoxValueSource.AGGREGATE_BASE,
            '614': BoxValueSource.AGGREGATE_BASE,
            '611': BoxValueSource.AGGREGATE_VAT,
            '613': BoxValueSource.AGGREGATE_VAT,
            '615': BoxValueSource.AGGREGATE_VAT,
        }
        for definition in VAT_BOX_REGISTRY:
            with self.subTest(box=definition.code):
                self.assertIn(definition.value_source, allowed)
                if definition.code in special_codes:
                    self.assertEqual(definition.value_source, special_codes[definition.code])
                elif definition.field_type == 'pair' and definition.code not in {'701', '702', '703', '704'}:
                    self.assertEqual(definition.value_source, BoxValueSource.PAIR)
                elif definition.field_type == 'scalar':
                    self.assertEqual(definition.value_source, BoxValueSource.ZERO)
                elif definition.field_type == 'bool':
                    self.assertEqual(definition.value_source, BoxValueSource.BOOLEAN_NO_OUTPUT)

    def test_eu_goods_boxes_are_implemented(self):
        for code in ('207', '307'):
            definition = next(item for item in VAT_BOX_REGISTRY if item.code == code)
            self.assertTrue(definition.implemented, msg=f'Box {code} must be implemented')
            self.assertTrue(definition.mapping_rule, msg=f'Box {code} requires mapping_rule')

    def test_eu_outbound_boxes_are_implemented(self):
        for code in ('101', '103'):
            definition = next(item for item in VAT_BOX_REGISTRY if item.code == code)
            self.assertTrue(definition.implemented, msg=f'Box {code} must be implemented')
            self.assertTrue(definition.mapping_rule, msg=f'Box {code} requires mapping_rule')

    def test_reverse_charge_boxes_are_implemented(self):
        for code in ('209', '210', '306', '309'):
            definition = next(item for item in VAT_BOX_REGISTRY if item.code == code)
            self.assertTrue(definition.implemented, msg=f'Box {code} must be implemented')
            self.assertTrue(definition.mapping_rule, msg=f'Box {code} requires mapping_rule')

    def test_viii1_boxes_are_implemented(self):
        for code in ('610', '611', '612', '613', '614', '615'):
            definition = next(item for item in VAT_BOX_REGISTRY if item.code == code)
            self.assertTrue(definition.implemented, msg=f'Box {code} must be implemented')
            self.assertTrue(definition.mapping_rule, msg=f'Box {code} requires mapping_rule')

    def test_oss_boxes_are_implemented(self):
        for code in ('204', '214', '215', '308'):
            definition = next(item for item in VAT_BOX_REGISTRY if item.code == code)
            self.assertTrue(definition.implemented, msg=f'Box {code} must be implemented')
            self.assertTrue(definition.mapping_rule, msg=f'Box {code} requires mapping_rule')

    def test_pdv_mapping_doc_path_resolves_in_docker_layout(self):
        rel = Path('docs') / 'accounting' / 'pdv-mapping.md'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'app'
            doc_path = base / rel
            doc_path.parent.mkdir(parents=True)
            doc_path.write_text('# placeholder\n', encoding='utf-8')
            with patch('accounting.services.tax_forms.pdv.boxes._pdv_mapping_doc_candidates') as mock_candidates:
                mock_candidates.return_value = [doc_path, doc_path]
                resolved = pdv_mapping_doc_path()
            self.assertEqual(resolved, doc_path)

    def test_pdv_mapping_doc_path_raises_when_missing(self):
        missing_a = Path('/tmp/nonexistent-pdv-mapping-a.md')
        missing_b = Path('/tmp/nonexistent-pdv-mapping-b.md')
        with patch('accounting.services.tax_forms.pdv.boxes._pdv_mapping_doc_candidates') as mock_candidates:
            mock_candidates.return_value = [missing_a, missing_b, missing_a]
            with self.assertRaises(FileNotFoundError) as ctx:
                pdv_mapping_doc_path()
        message = str(ctx.exception)
        self.assertIn('Unable to locate pdv-mapping.md', message)
        self.assertIn(str(missing_a), message)
        self.assertIn(str(missing_b), message)
        tried_lines = [line.strip() for line in message.splitlines() if line.strip().startswith('- ')]
        self.assertEqual(len(tried_lines), len(set(tried_lines)))

    def test_docs_sync_with_registry(self):
        doc_path = pdv_mapping_doc_path()
        self.assertTrue(doc_path.is_file(), f'Missing documentation: {doc_path}')
        doc_rows = parse_pdv_mapping_markdown(doc_path.read_text(encoding='utf-8'))
        expected_rows = registry_doc_rows()
        self.assertEqual(len(doc_rows), len(expected_rows))
        for doc_row, expected in zip(doc_rows, expected_rows):
            self.assertEqual(doc_row, expected, msg=f"Mismatch for box {expected['code']}")

    def test_implemented_boxes_have_mapping_coverage(self):
        for box in implemented_boxes():
            self.assertIn(box.code, PDV_MAPPING, msg=f'Missing PDV_MAPPING for box {box.code}')

    def test_vat_ledger_entry_exposes_vat_box_field(self):
        field = VATLedgerEntry._meta.get_field('vat_box')
        self.assertEqual(field.max_length, 3)
        self.assertTrue(field.blank)

        category_field = VATLedgerEntry._meta.get_field('entry_category')
        self.assertEqual(category_field.default, 'domestic')

        manual_field = VATLedgerEntry._meta.get_field('is_manual')
        self.assertFalse(manual_field.default)
