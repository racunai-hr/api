"""Source labels for incoming documents — new eRačun value next to legacy SUPER rows."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from django.test import SimpleTestCase

from domains.reporting.documents.incoming_detail import build_document_meta


def _document():
    return SimpleNamespace(
        expense_date=date(2026, 8, 20),
        due_date=date(2026, 9, 1),
        created_at=datetime(2026, 8, 20, 10, 0, 0),
        currency='EUR',
    )


class IncomingDocumentMetaTests(SimpleTestCase):
    def test_eracun_source_is_labelled_as_ubl_document(self):
        meta = build_document_meta(_document(), None, source='eracun')
        self.assertEqual(meta['source_label'], 'eRačun')
        self.assertEqual(meta['format'], 'UBL')

    def test_legacy_super_source_keeps_its_label(self):
        meta = build_document_meta(_document(), None, source='super')
        self.assertEqual(meta['source_label'], 'SUPER eRačun')
        self.assertEqual(meta['format'], 'UBL')

    def test_ocr_source_has_no_ubl_format(self):
        meta = build_document_meta(_document(), None, source='ocr')
        self.assertEqual(meta['source_label'], 'OCR')
        self.assertIsNone(meta['format'])
