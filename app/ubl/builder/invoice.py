from __future__ import annotations

from lxml import etree

from ubl.builder.registry import get_renderer
from ubl.domain.document import UblDocument


def render_invoice_ubl(document: UblDocument, *, profile_version: str = '2025') -> str:
    root = get_renderer(profile_version)(document)
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=True,
    ).decode('utf-8')


def build_invoice_ubl(document: UblDocument, *, profile_version: str = '2025') -> str:
    """Render UblDocument to HR CIUS UBL XML."""
    return render_invoice_ubl(document, profile_version=profile_version)
