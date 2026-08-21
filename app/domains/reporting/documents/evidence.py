"""Tenant-scoped document evidence (SUPER/AS4 PDF + UBL).

Shared by DocumentDetail flags and download endpoints so availability cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from expenses.models import Expense
from fiscal_gateway.models import As4DocumentLink
from super_integration.models import SuperDocumentLink

DOCUMENT_PDF_CONTENT_UNAVAILABLE = {
    'code': 'document_pdf_content_unavailable',
    'detail': 'Document PDF content is unavailable.',
}


class EvidenceAbsent(Exception):
    """No evidence record for this document → HTTP 404."""


class EvidenceGone(Exception):
    """Evidence metadata exists but stored bytes are missing → HTTP 410."""


@dataclass(frozen=True)
class PdfFileEvidence:
    abs_path: Path
    filename: str


@dataclass(frozen=True)
class UblEvidence:
    xml: str
    filename: str
    source: str  # 'super' | 'as4'


def resolve_media_relative_path(relative: str) -> Path:
    """Resolve a MEDIA_ROOT-relative path; reject traversal and empty values.

    Raises:
        EvidenceAbsent: empty / unsafe / outside MEDIA_ROOT
        EvidenceGone: path resolves under MEDIA_ROOT but file is missing
    """
    raw = (relative or '').strip()
    if not raw:
        raise EvidenceAbsent
    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        raise EvidenceAbsent
    # Disallow any parent-segment before join (Windows + POSIX).
    if '..' in candidate.parts or candidate.parts[:1] == ('/',):
        raise EvidenceAbsent
    abs_path = (media_root / candidate).resolve()
    try:
        abs_path.relative_to(media_root)
    except ValueError as exc:
        raise EvidenceAbsent from exc
    if not abs_path.is_file():
        raise EvidenceGone
    return abs_path


def _expense_ct():
    return ContentType.objects.get_for_model(Expense)


def _inbound_super_links_qs(tenant, expense_id: int):
    return SuperDocumentLink.all_objects.filter(
        tenant=tenant,
        direction=SuperDocumentLink.DIRECTION_INBOUND,
        content_type=_expense_ct(),
        object_id=expense_id,
    ).order_by('pk')


def _inbound_as4_links_qs(tenant, expense_id: int):
    return As4DocumentLink.all_objects.filter(
        tenant=tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        content_type=_expense_ct(),
        object_id=expense_id,
    ).order_by('pk')


def _pick_super_pdf_link(links) -> SuperDocumentLink | None:
    """Stable pick: lowest pk among inbound links with non-empty pdf_path."""
    for link in links:
        if (link.pdf_path or '').strip():
            return link
    return None


def get_incoming_pdf_evidence(
    *,
    tenant,
    expense_id: int,
    super_links=None,
) -> PdfFileEvidence:
    """Resolve downloadable SUPER inbound PDF for an expense.

    Raises EvidenceAbsent (404) or EvidenceGone (410).
    """
    if not Expense.all_objects.filter(tenant=tenant, pk=expense_id).exists():
        raise EvidenceAbsent

    links = list(super_links) if super_links is not None else list(
        _inbound_super_links_qs(tenant, expense_id)
    )
    links = sorted(links, key=lambda link: link.pk)
    link = _pick_super_pdf_link(links)
    if link is None:
        raise EvidenceAbsent

    abs_path = resolve_media_relative_path(link.pdf_path)
    filename = abs_path.name or f'expense-{expense_id}.pdf'
    return PdfFileEvidence(abs_path=abs_path, filename=filename)


def incoming_pdf_available(*, tenant, expense_id: int, super_links=None) -> bool:
    """True only when GET .../pdf/ would return 200 for this incoming expense."""
    try:
        get_incoming_pdf_evidence(
            tenant=tenant,
            expense_id=expense_id,
            super_links=super_links,
        )
        return True
    except (EvidenceAbsent, EvidenceGone):
        return False


def get_incoming_ubl_evidence(
    *,
    tenant,
    expense_id: int,
    super_links=None,
    as4_links=None,
) -> UblEvidence:
    """Super non-empty UBL, else AS4 non-empty UBL, else EvidenceAbsent.

    Priority is explicit; never queryset-order between providers.
    Within a provider, lowest pk wins.
    """
    if not Expense.all_objects.filter(tenant=tenant, pk=expense_id).exists():
        raise EvidenceAbsent

    super_list = list(super_links) if super_links is not None else list(
        _inbound_super_links_qs(tenant, expense_id)
    )
    super_list = sorted(super_list, key=lambda link: link.pk)
    for link in super_list:
        xml = (link.ubl_xml or '').strip()
        if xml:
            return UblEvidence(
                xml=link.ubl_xml,
                filename=f'expense-{expense_id}-super.xml',
                source='super',
            )

    as4_list = list(as4_links) if as4_links is not None else list(
        _inbound_as4_links_qs(tenant, expense_id)
    )
    as4_list = sorted(as4_list, key=lambda link: link.pk)
    for link in as4_list:
        xml = (link.ubl_xml or '').strip()
        if xml:
            return UblEvidence(
                xml=link.ubl_xml,
                filename=f'expense-{expense_id}-as4.xml',
                source='as4',
            )

    raise EvidenceAbsent


def incoming_ubl_available(
    *,
    tenant,
    expense_id: int,
    super_links=None,
    as4_links=None,
) -> bool:
    try:
        get_incoming_ubl_evidence(
            tenant=tenant,
            expense_id=expense_id,
            super_links=super_links,
            as4_links=as4_links,
        )
        return True
    except EvidenceAbsent:
        return False
