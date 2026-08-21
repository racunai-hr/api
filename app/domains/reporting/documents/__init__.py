"""ADR-0020 document read model — read-only projection of incoming/outgoing invoices."""

from domains.reporting.documents.service import (
    export_documents,
    get_document_detail,
    list_documents,
)

__all__ = [
    'export_documents',
    'get_document_detail',
    'list_documents',
]
