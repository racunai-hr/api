"""OfficialDocument lifecycle (ADR-0028) — evidencija, PDF, veza na postojeću JE."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.http import Http404

from accounting.models import (
    AssetJournalLinkRole,
    FixedAsset,
    FixedAssetJournalLink,
    JournalEntry,
    OfficialDocument,
    OfficialDocumentPostingProfile,
)
from domains.finance.services.cost_centers import cost_center_ref
from domains.finance.services.cost_center_resolver import load_bookable_cost_center
from accounting.services.posting import (
    OFFICIAL_DOCUMENT_POSTED,
    ensure_default_official_document_posting_profiles,
    post_document,
)
from domains.finance.services.subledger import get_subledger_item_for_source
from partners.models import Partner

OFFICIAL_POSTED_MARKER = f'[{OFFICIAL_DOCUMENT_POSTED}]'

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_KINDS = frozenset({OfficialDocument.KIND_TAX_DECISION, OfficialDocument.KIND_OTHER})


class OfficialDocumentConflict(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class OfficialDocumentBadRequest(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise OfficialDocumentBadRequest('invalid_amount', 'Iznos mora biti decimalni broj.') from exc
    if amount <= Decimal('0'):
        raise OfficialDocumentBadRequest('invalid_amount', 'Iznos mora biti veći od nule.')
    return amount.quantize(Decimal('0.01'))


def _parse_date(value, *, field: str, required: bool = True):
    if value in (None, ''):
        if required:
            raise OfficialDocumentBadRequest('invalid_date', f'{field} je obavezan.')
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise OfficialDocumentBadRequest('invalid_date', f'{field} mora biti YYYY-MM-DD.') from exc


def _lock_document(tenant, document_id: int) -> OfficialDocument:
    document = (
        OfficialDocument.all_objects.select_for_update(of=('self',))
        .filter(tenant=tenant, pk=document_id)
        .select_related('issuer', 'related_fixed_asset', 'created_by', 'posting_profile', 'cost_center')
        .first()
    )
    if document is None:
        raise Http404()
    return document


def _issuer(tenant, issuer_id) -> Partner:
    try:
        pk = int(issuer_id)
    except (TypeError, ValueError) as exc:
        raise OfficialDocumentBadRequest('invalid_issuer', 'issuer_id je obavezan.') from exc
    partner = Partner.all_objects.filter(tenant=tenant, pk=pk).first()
    if partner is None:
        raise Http404()
    return partner


def _asset(tenant, asset_id):
    if asset_id in (None, ''):
        return None
    try:
        pk = int(asset_id)
    except (TypeError, ValueError) as exc:
        raise OfficialDocumentBadRequest('invalid_asset', 'related_fixed_asset_id nije valjan.') from exc
    asset = FixedAsset.all_objects.filter(tenant=tenant, pk=pk).first()
    if asset is None:
        raise Http404()
    return asset


def _ensure_capitalize_asset_link(*, tenant, document, entry) -> None:
    """Link capitalize OfficialDocument JE onto the related asset (ADR-0029 §2.5).

    Idempotent. Does not reuse apply_asset_journal_links (backfill audit).
    """
    profile = document.posting_profile
    if profile is None or profile.economic_effect != OfficialDocumentPostingProfile.EFFECT_CAPITALIZE:
        return
    if entry is None or not document.related_fixed_asset_id:
        return

    asset = document.related_fixed_asset
    if asset is None:
        asset = FixedAsset.all_objects.filter(pk=document.related_fixed_asset_id).first()
    if asset is None:
        raise OfficialDocumentBadRequest('invalid_asset', 'Povezana imovina nije pronađena.')
    if asset.tenant_id != tenant.pk or entry.tenant_id != tenant.pk:
        raise OfficialDocumentBadRequest(
            'invalid_asset',
            'Imovina i temeljnica moraju pripadati istom tenantu.',
        )
    if asset.tenant_id != entry.tenant_id:
        raise OfficialDocumentBadRequest(
            'invalid_asset',
            'Imovina i temeljnica moraju pripadati istom tenantu.',
        )

    existing = FixedAssetJournalLink.all_objects.filter(
        tenant=tenant,
        fixed_asset=asset,
        journal_entry=entry,
    ).first()
    if existing is not None:
        return

    link = FixedAssetJournalLink(
        tenant=tenant,
        fixed_asset=asset,
        journal_entry=entry,
        role=AssetJournalLinkRole.DEPENDENT_COST,
    )
    try:
        link.full_clean()
        link.save()
    except ValidationError as exc:
        raise OfficialDocumentBadRequest(
            'invalid_asset',
            'Temeljnicu nije moguće povezati s imovinom.',
        ) from exc
    except IntegrityError:
        return


def _detect_kind(header: bytes) -> str | None:
    if header.startswith(b'%PDF-'):
        return 'pdf'
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    return None


def _validate_upload(upload: UploadedFile) -> tuple[str, bytes]:
    size = getattr(upload, 'size', None) or 0
    if size > MAX_FILE_BYTES:
        raise OfficialDocumentBadRequest(
            'invalid_file',
            f'Datoteka je prevelika ({size} B). Maksimalno {MAX_FILE_BYTES // (1024 * 1024)} MB.',
        )
    header = upload.read(8)
    upload.seek(0)
    kind = _detect_kind(header)
    if kind is None:
        raise OfficialDocumentBadRequest('invalid_file', 'Datoteka mora biti PDF, JPG ili PNG.')
    content = upload.read()
    upload.seek(0)
    return kind, content


def file_is_valid_pdf(document: OfficialDocument) -> bool:
    field = document.original_file
    if not field or not field.name:
        return False
    try:
        if not field.storage.exists(field.name):
            return False
        with field.open('rb') as handle:
            header = handle.read(5)
    except OSError:
        return False
    return header == b'%PDF-'


def _attach_file(document: OfficialDocument, upload: UploadedFile) -> None:
    if not document.pk:
        raise OfficialDocumentBadRequest('invalid_file', 'Dokument mora biti spremljen prije privitka.')
    kind, content = _validate_upload(upload)
    filename = getattr(upload, 'name', None) or 'document.bin'
    document.original_file.save(filename, upload, save=False)
    if str(document.pk) not in (document.original_file.name or ''):
        raise OfficialDocumentBadRequest(
            'invalid_file',
            'Upload path mora sadržavati ID dokumenta.',
        )
    document.original_filename = filename[:255]
    document.content_type = getattr(upload, 'content_type', '') or (
        'application/pdf' if kind == 'pdf' else f'image/{kind}'
    )
    document.file_sha256 = hashlib.sha256(content).hexdigest()
    document.file_size = len(content)
    document.save(
        update_fields=[
            'original_file',
            'original_filename',
            'content_type',
            'file_sha256',
            'file_size',
            'updated_at',
        ]
    )


def _serialize(document: OfficialDocument) -> dict:
    document.refresh_from_db()
    issuer = document.issuer
    asset = document.related_fixed_asset
    profile = document.posting_profile
    return {
        'id': document.pk,
        'official_kind': document.kind,
        'workflow_status': document.status,
        'issuer_id': issuer.pk if issuer else None,
        'issuer_name': issuer.name if issuer else None,
        'document_number': document.document_number,
        'reference': document.reference or '',
        'issue_date': document.issue_date.isoformat() if document.issue_date else None,
        'due_date': document.due_date.isoformat() if document.due_date else None,
        'amount': str(document.amount),
        'currency': document.currency,
        'status': document.status,
        'original_filename': document.original_filename or '',
        'content_type': document.content_type or '',
        'file_sha256': document.file_sha256 or '',
        'file_size': document.file_size,
        'has_file': bool(document.original_file and document.original_file.name),
        'related_fixed_asset_id': asset.pk if asset else None,
        'posting_profile_id': profile.pk if profile else None,
        'posting_profile_code': profile.code if profile else None,
        'posting_profile_name': profile.name if profile else None,
        'cost_center': cost_center_ref(document.cost_center),
        'notes': document.notes or '',
        'created_at': document.created_at.isoformat() if document.created_at else None,
    }


def get_official_document(*, tenant, document_id: int) -> dict:
    document = (
        OfficialDocument.all_objects.filter(tenant=tenant, pk=document_id)
        .select_related('issuer', 'related_fixed_asset', 'posting_profile', 'cost_center')
        .first()
    )
    if document is None:
        raise Http404()
    return _serialize(document)


@transaction.atomic
def create_official_document(*, tenant, data: dict, file=None, user=None) -> dict:
    kind = (data.get('official_kind') or data.get('kind') or OfficialDocument.KIND_TAX_DECISION).strip()
    if kind not in ALLOWED_KINDS:
        raise OfficialDocumentBadRequest('invalid_kind', 'kind mora biti tax_decision ili other.')
    issuer = _issuer(tenant, data.get('issuer_id'))
    document_number = (data.get('document_number') or '').strip()
    if not document_number:
        raise OfficialDocumentBadRequest('invalid_document_number', 'document_number je obavezan.')
    issue_date = _parse_date(data.get('issue_date'), field='issue_date')
    due_date = _parse_date(data.get('due_date'), field='due_date', required=False)
    amount = _money(data.get('amount'))
    currency = (data.get('currency') or 'EUR').strip().upper() or 'EUR'
    if len(currency) != 3:
        raise OfficialDocumentBadRequest('invalid_currency', 'Valuta mora imati 3 znaka.')
    register = str(data.get('register') or '').lower() in {'1', 'true', 'yes'}
    document = OfficialDocument(
        tenant=tenant,
        kind=kind,
        issuer=issuer,
        document_number=document_number,
        reference=(data.get('reference') or '').strip(),
        issue_date=issue_date,
        due_date=due_date,
        amount=amount,
        currency=currency,
        status=OfficialDocument.STATUS_DRAFT,
        related_fixed_asset=_asset(tenant, data.get('related_fixed_asset_id')),
        posting_profile=_optional_profile(tenant, data.get('posting_profile_id'), kind=kind),
        cost_center=_optional_cost_center(tenant, data.get('cost_center_id')),
        notes=(data.get('notes') or '').strip(),
        created_by=user if getattr(user, 'pk', None) else None,
    )
    try:
        document.save()
    except IntegrityError as exc:
        raise OfficialDocumentConflict(
            'duplicate_document',
            'Dokument s istim izdavateljem i brojem već postoji.',
        ) from exc
    if file is not None:
        _attach_file(document, file)
    if register:
        return register_official_document(tenant=tenant, document_id=document.pk)
    return _serialize(document)


@transaction.atomic
def register_official_document(*, tenant, document_id: int, file=None) -> dict:
    document = _lock_document(tenant, document_id)
    if document.status == OfficialDocument.STATUS_CANCELLED:
        raise OfficialDocumentConflict('invalid_status', 'Otkazani dokument se ne može registrirati.')
    if document.status == OfficialDocument.STATUS_REGISTERED:
        return _serialize(document)
    if file is not None:
        _attach_file(document, file)
        document.refresh_from_db()
    if not file_is_valid_pdf(document):
        raise OfficialDocumentBadRequest(
            'missing_pdf',
            'Registracija zahtijeva valjani PDF.',
        )
    if not document.issuer_id or not (document.document_number or '').strip() or not document.issue_date:
        raise OfficialDocumentBadRequest(
            'incomplete',
            'Za registraciju su obavezni izdavatelj, broj i datum dokumenta.',
        )
    if document.amount is None or document.amount <= Decimal('0'):
        raise OfficialDocumentBadRequest('invalid_amount', 'Iznos mora biti veći od nule.')
    document.status = OfficialDocument.STATUS_REGISTERED
    document.save(update_fields=['status', 'updated_at'])
    return _serialize(document)


@transaction.atomic
def cancel_official_document(*, tenant, document_id: int) -> dict:
    document = _lock_document(tenant, document_id)
    if document.status == OfficialDocument.STATUS_CANCELLED:
        return _serialize(document)
    document.status = OfficialDocument.STATUS_CANCELLED
    document.save(update_fields=['status', 'updated_at'])
    return _serialize(document)


@transaction.atomic
def link_official_document_journal(*, tenant, document_id: int, journal_entry_id) -> dict:
    document = _lock_document(tenant, document_id)
    try:
        je_id = int(journal_entry_id)
    except (TypeError, ValueError) as exc:
        raise OfficialDocumentBadRequest('invalid_journal', 'journal_entry_id je obavezan.') from exc
    entry = (
        JournalEntry.all_objects.select_for_update(of=('self',))
        .filter(tenant=tenant, pk=je_id)
        .first()
    )
    if entry is None:
        raise Http404()
    ct = ContentType.objects.get_for_model(OfficialDocument)
    if entry.source_content_type_id and (
        entry.source_content_type_id != ct.pk or entry.source_object_id != document.pk
    ):
        raise OfficialDocumentConflict(
            'source_taken',
            'Temeljnica je već povezana s drugim dokumentom.',
        )
    entry.source_content_type = ct
    entry.source_object_id = document.pk
    entry.save(update_fields=['source_content_type', 'source_object_id'])
    return _serialize(document)


def _source_journal_entries(tenant, document: OfficialDocument):
    ct = ContentType.objects.get_for_model(OfficialDocument)
    return list(
        JournalEntry.all_objects.filter(
            tenant=tenant,
            source_content_type=ct,
            source_object_id=document.pk,
            status__in=['draft', 'posted'],
        ).order_by('id')
    )


def _successful_ab_posting(tenant, document: OfficialDocument):
    jes = _source_journal_entries(tenant, document)
    marker_je = next(
        (je for je in jes if (je.description or '').startswith(OFFICIAL_POSTED_MARKER)),
        None,
    )
    item = get_subledger_item_for_source(tenant, document)
    return jes, marker_je, item


def _optional_cost_center(tenant, cost_center_id):
    if cost_center_id in (None, ''):
        return None
    try:
        return load_bookable_cost_center(tenant, int(cost_center_id))
    except (TypeError, ValueError) as exc:
        raise OfficialDocumentBadRequest('invalid_cost_center', 'cost_center_id nije valjan.') from exc
    except ValidationError as exc:
        detail = next(iter(exc.message_dict.values()))[0] if hasattr(exc, 'message_dict') else str(exc)
        raise OfficialDocumentBadRequest('invalid_cost_center', str(detail)) from exc


def _optional_profile(tenant, profile_id, *, kind: str):
    if profile_id in (None, ''):
        return None
    return _resolve_profile(tenant, profile_id, kind=kind)


def _resolve_profile(tenant, profile_id, *, kind: str) -> OfficialDocumentPostingProfile:
    try:
        pk = int(profile_id)
    except (TypeError, ValueError) as exc:
        raise OfficialDocumentBadRequest('invalid_profile', 'posting_profile_id nije valjan.') from exc
    ensure_default_official_document_posting_profiles(tenant)
    profile = OfficialDocumentPostingProfile.all_objects.filter(tenant=tenant, pk=pk).first()
    if profile is None:
        raise Http404()
    if not profile.is_active:
        raise OfficialDocumentBadRequest('profile_inactive', 'Profil knjiženja nije aktivan.')
    allowed = profile.allowed_kinds or []
    if kind not in allowed:
        raise OfficialDocumentBadRequest(
            'kind_not_allowed',
            f'Profil {profile.code} ne dopušta kind={kind}.',
        )
    return profile


def list_official_document_posting_profiles(*, tenant) -> list[dict]:
    ensure_default_official_document_posting_profiles(tenant)
    rows = OfficialDocumentPostingProfile.all_objects.filter(
        tenant=tenant,
        is_active=True,
    ).order_by('code')
    return [
        {
            'id': row.pk,
            'code': row.code,
            'name': row.name,
            'economic_effect': row.economic_effect,
            'allowed_kinds': list(row.allowed_kinds or []),
            'requires_fixed_asset': row.requires_fixed_asset,
            'is_active': row.is_active,
        }
        for row in rows
    ]


@transaction.atomic
def set_official_document_posting_profile(*, tenant, document_id: int, posting_profile_id) -> dict:
    document = _lock_document(tenant, document_id)
    if document.status == OfficialDocument.STATUS_CANCELLED:
        raise OfficialDocumentConflict('invalid_status', 'Otkazani dokument nema izmjenu profila.')
    _jes, marker_je, item = _successful_ab_posting(tenant, document)
    if marker_je is not None and item is not None:
        raise OfficialDocumentConflict(
            'profile_locked',
            'Profil se ne može mijenjati nakon uspješnog knjiženja.',
        )
    document.posting_profile = _resolve_profile(tenant, posting_profile_id, kind=document.kind)
    document.save(update_fields=['posting_profile', 'updated_at'])
    return _serialize(document)


@transaction.atomic
def post_official_document(*, tenant, document_id: int, user) -> dict:
    document = _lock_document(tenant, document_id)
    if document.status != OfficialDocument.STATUS_REGISTERED:
        raise OfficialDocumentConflict(
            'invalid_status',
            'Knjiži je dopušten samo za registrirani dokument.',
        )
    ensure_default_official_document_posting_profiles(tenant)
    profile = document.posting_profile
    if profile is None:
        raise OfficialDocumentBadRequest('missing_profile', 'Knjiži zahtijeva posting profil.')
    if not profile.is_active:
        raise OfficialDocumentBadRequest('profile_inactive', 'Profil knjiženja nije aktivan.')
    allowed = profile.allowed_kinds or []
    if document.kind not in allowed:
        raise OfficialDocumentBadRequest(
            'kind_not_allowed',
            f'Profil {profile.code} ne dopušta kind={document.kind}.',
        )
    if profile.requires_fixed_asset and not document.related_fixed_asset_id:
        raise OfficialDocumentBadRequest(
            'missing_fixed_asset',
            'Ovaj profil zahtijeva povezanu imovinu.',
        )

    jes, marker_je, item = _successful_ab_posting(tenant, document)
    if jes and item is None:
        raise OfficialDocumentConflict(
            'already_linked_manual_posting',
            'Dokument ima ručno povezanu temeljnicu bez saldakonta.',
        )
    if marker_je is not None and item is not None:
        _ensure_capitalize_asset_link(tenant=tenant, document=document, entry=marker_je)
        return _serialize(document)
    if jes:
        raise OfficialDocumentConflict(
            'already_linked_manual_posting',
            'Dokument ima ručno povezanu temeljnicu bez saldakonta.',
        )

    entry = post_document(
        tenant,
        document,
        OFFICIAL_DOCUMENT_POSTED,
        user,
        entry_date=document.issue_date,
    )
    if entry is None:
        raise OfficialDocumentBadRequest(
            'missing_posting_rule',
            'Nema aktivnog pravila knjiženja za ovaj profil.',
        )
    item = get_subledger_item_for_source(tenant, document)
    if item is None:
        raise OfficialDocumentConflict(
            'posting_incomplete',
            'Knjiženje nije stvorilo stavku saldakonta.',
        )
    _ensure_capitalize_asset_link(tenant=tenant, document=document, entry=entry)
    return _serialize(document)
