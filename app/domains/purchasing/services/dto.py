from __future__ import annotations

from domains.purchasing.services.matching import parse_iso_date, parse_money, supplier_from_payload
from domains.reporting.documents.snapshot import isoformat


def _partner_dto(run) -> dict:
    partner = run.matched_partner
    return {
        'match': run.partner_match,
        'partner_id': partner.pk if partner else None,
        'candidate_id': run.partner_candidate_id,
        'name': partner.name if partner else '',
        'tax_number': partner.tax_number if partner else '',
        'diff': list(run.partner_diff or []),
    }


def _duplicate_dto(run) -> dict:
    expense = run.duplicate_expense
    label = ''
    if expense is not None:
        partner_name = expense.supplier.name if expense.supplier_id else ''
        label = (
            f'{partner_name} · račun {expense.receipt_number} · '
            f'{expense.amount} {expense.currency}'
        ).strip(' ·')
    return {
        'kind': run.duplicate_kind,
        'expense_id': expense.pk if expense else None,
        'label': label,
        'detail': dict(run.duplicate_detail or {}),
    }


def extracted_view(payload: dict) -> dict:
    supplier = supplier_from_payload(payload or {})
    return {
        'supplier': supplier,
        'invoice_number': str((payload or {}).get('invoice_number') or '').strip(),
        'issue_date': str((payload or {}).get('issue_date') or '').strip(),
        'due_date': (payload or {}).get('due_date'),
        'currency': str((payload or {}).get('currency') or 'EUR').strip() or 'EUR',
        'net_amount': str((payload or {}).get('net_amount') or ''),
        'tax_amount': str((payload or {}).get('tax_amount') or ''),
        'total_amount': str((payload or {}).get('total_amount') or ''),
        'iban': supplier.get('iban') or str((payload or {}).get('iban') or ''),
        'vat_breakdown': list((payload or {}).get('vat_breakdown') or []),
        'line_items': list((payload or {}).get('line_items') or []),
    }


def confirm_values(payload: dict, overrides: dict | None = None) -> dict:
    view = extracted_view(payload)
    data = dict(overrides or {})
    invoice_number = str(data.get('invoice_number') or view['invoice_number']).strip()
    issue_date = parse_iso_date(data.get('issue_date') or view['issue_date'])
    due_raw = data.get('due_date') if 'due_date' in data else view['due_date']
    due_date = parse_iso_date(due_raw) if due_raw else None
    total = parse_money(data.get('total_amount') or view['total_amount'])
    tax = parse_money(data.get('tax_amount') or view['tax_amount']) or 0
    net = parse_money(data.get('net_amount') or view['net_amount'])
    currency = str(data.get('currency') or view['currency'] or 'EUR').strip().upper()[:3]
    description = str(data.get('description') or '').strip()
    if not description:
        items = view.get('line_items') or []
        names = [str(item.get('description') or '').strip() for item in items if isinstance(item, dict)]
        names = [name for name in names if name]
        description = ', '.join(names[:3]) or f"Ulazni račun {invoice_number}".strip()
    return {
        'invoice_number': invoice_number,
        'issue_date': issue_date,
        'due_date': due_date,
        'amount': total,
        'tax_amount': tax,
        'net_amount': net,
        'currency': currency or 'EUR',
        'description': description,
        'iban': str(data.get('iban') or view['iban'] or ''),
    }


def import_dto(run, *, created: bool | None = None) -> dict:
    payload = {
        'id': run.pk,
        'status': run.status,
        'original_filename': run.original_filename,
        'content_type': run.content_type,
        'file_sha256': run.file_sha256,
        'file_size': run.file_size,
        'ocr_provider': run.ocr_provider,
        'ocr_model': run.ocr_model,
        'ocr_schema_version': run.ocr_schema_version,
        'ocr_extracted_at': isoformat(run.ocr_extracted_at) if run.ocr_extracted_at else None,
        'extracted': extracted_view(run.extracted_payload or {}),
        'warnings': list(run.warnings or []),
        'partner': _partner_dto(run),
        'duplicate': _duplicate_dto(run),
        'confirmed_expense_id': run.confirmed_expense_id,
        'last_error': run.last_error,
        'created_at': isoformat(run.created_at) if run.created_at else None,
        'started_at': isoformat(run.started_at) if run.started_at else None,
        'finished_at': isoformat(run.finished_at) if run.finished_at else None,
    }
    if created is not None:
        payload['created'] = created
    return payload
