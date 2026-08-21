"""Map canonical UBL parse + partner MDM into PR A incoming detail blocks."""

from __future__ import annotations

from domains.reporting.documents.projection import money
from lxml import etree
from ubl.parser.invoice import PARSER_VERSION, ParsedInvoice, UblMonetaryError, parse_invoice_ubl
from super_integration.portal import build_super_external_view_url


def _dec_str(value) -> str | None:
    """Preserve Decimal as string without inventing scale (not JS float)."""
    if value is None:
        return None
    return format(value, 'f')


def _money_str(value) -> str | None:
    if value is None:
        return None
    return money(value)


def _pick_ubl_xml(super_links, as4_links) -> tuple[str | None, str | None]:
    """Prefer SUPER non-empty ubl_xml, else AS4. Returns (xml, source)."""
    for link in super_links or []:
        xml = (link.ubl_xml or '').strip()
        if xml:
            return xml, 'super'
    for link in as4_links or []:
        xml = (link.ubl_xml or '').strip()
        if xml:
            return xml, 'as4'
    return None, None


def try_parse_incoming_ubl(super_links, as4_links) -> ParsedInvoice | None:
    xml, _source = _pick_ubl_xml(super_links, as4_links)
    if not xml:
        return None
    try:
        return parse_invoice_ubl(xml)
    except (UblMonetaryError, ValueError, etree.XMLSyntaxError):
        return None


def _primary_iban(rel, partner) -> str | None:
    if partner is None:
        return None
    primary = (rel or {}).get('primary_iban_by_partner', {}).get(partner.pk)
    if primary:
        return primary
    ibans = (rel or {}).get('ibans_by_partner', {}).get(partner.pk) or set()
    if not ibans:
        return None
    return sorted(ibans)[0]


def build_supplier_block(partner, parsed: ParsedInvoice | None, rel) -> dict:
    if partner is not None:
        return {
            'id': partner.pk,
            'name': partner.name,
            'country_code': partner.country_code or None,
            'address': {
                'street': partner.address or None,
                'postal_code': partner.postal_code or None,
                'city': partner.city or None,
            },
            'oib': partner.tax_number or None,
            'vat_id': partner.vat_number or None,
            'tax_id': partner.tax_number or None,
            'primary_iban': _primary_iban(rel, partner),
        }
    if parsed is None:
        return {
            'id': None,
            'name': None,
            'country_code': None,
            'address': {'street': None, 'postal_code': None, 'city': None},
            'oib': None,
            'vat_id': None,
            'tax_id': None,
            'primary_iban': None,
        }
    return {
        'id': None,
        'name': parsed.supplier_name or None,
        'country_code': parsed.supplier_country_code,
        'address': {
            'street': parsed.supplier_street,
            'postal_code': parsed.supplier_postal_code,
            'city': parsed.supplier_city,
        },
        'oib': parsed.supplier_oib or None,
        'vat_id': parsed.supplier_vat_id,
        'tax_id': parsed.supplier_oib or None,
        'primary_iban': None,
    }


def build_document_meta(document, parsed: ParsedInvoice | None, *, source: str | None) -> dict:
    source_label = None
    fmt = None
    if source == 'super':
        source_label = 'SUPER eRačun'
        fmt = 'UBL'
    elif source == 'as4':
        source_label = 'AS4 / Fiskal Platform'
        fmt = 'UBL'
    elif source == 'ocr':
        source_label = 'OCR'
    elif source:
        source_label = source

    primary_ref = None
    if parsed and parsed.references:
        for ref in parsed.references:
            if ref.type == 'originator':
                primary_ref = ref.value
                break
        if primary_ref is None:
            primary_ref = parsed.references[0].value

    return {
        'issue_date': (
            parsed.issue_date
            if parsed and parsed.issue_date
            else (document.expense_date.isoformat() if document.expense_date else None)
        ),
        'delivery_date': parsed.delivery_date if parsed else None,
        'due_date': (
            parsed.due_date
            if parsed and parsed.due_date
            else (document.due_date.isoformat() if document.due_date else None)
        ),
        'received_at': document.created_at.isoformat() if document.created_at else None,
        'currency': (
            parsed.currency
            if parsed and parsed.currency
            else (document.currency or 'EUR')
        ),
        'business_process': parsed.profile_id if parsed else None,
        'source_label': source_label,
        'format': fmt,
        'primary_reference': primary_ref,
    }


def build_lifecycle_status(
    document,
    *,
    integration_source: str | None,
    posting=None,
    subledger=None,
    ledger_rows=None,
    match_status: str | None = None,
) -> dict:
    status = document.status or ''
    document_status = {
        'draft': 'received',
        'approved': 'received',
        'paid': 'received',
        'cancelled': 'cancelled',
        'rejected': 'rejected',
    }.get(status, status or None)
    workflow = {
        'draft': 'pending',
        'approved': 'approved',
        'paid': 'approved',
        'cancelled': 'cancelled',
        'rejected': 'rejected',
    }.get(status, status or None)
    integration = 'received' if integration_source in ('super', 'as4') else None

    if posting is None:
        posting_status = 'unposted'
    elif posting.status == 'posted':
        posting_status = 'posted'
    else:
        posting_status = posting.status or 'unposted'

    vat_status = 'recorded' if ledger_rows else 'absent'
    subledger_status = subledger.status if subledger is not None else None
    if match_status in ('matched', 'suggested', 'unmatched'):
        payment_status = match_status
    else:
        payment_status = None

    return {
        'document': document_status,
        'workflow': workflow,
        'integration': integration,
        'posting': posting_status,
        'vat': vat_status,
        'subledger': subledger_status,
        'payment': payment_status,
    }


def build_lines_block(parsed: ParsedInvoice | None) -> list[dict]:
    if not parsed:
        return []
    rows = []
    for line in parsed.lines:
        classif = None
        if line.classification and (line.classification.code or line.classification.scheme):
            classif = {
                'scheme': line.classification.scheme,
                'code': line.classification.code,
            }
        rows.append(
            {
                'position': line.position,
                'classification': classif,
                'name': line.name,
                'description': line.description,
                'unit': line.unit_code,
                'quantity': _dec_str(line.quantity),
                'unit_price': _money_str(line.unit_price),
                'vat_rate': _dec_str(line.vat_rate),
                # Line-level VAT/gross amounts are not invented when absent from UBL.
                'net_amount': _money_str(line.line_extension_amount),
                'vat_amount': None,
                'gross_amount': None,
            }
        )
    return rows


def build_charges_block(parsed: ParsedInvoice | None) -> list[dict]:
    if not parsed:
        return []
    rows = []
    for charge in parsed.charges:
        # Skip pure allowances from the "charges" list when indicator is false.
        if charge.charge_indicator is False:
            continue
        rows.append(
            {
                'code': charge.code,
                'type': 'charge' if charge.charge_indicator else None,
                'description': charge.description,
                'amount': _money_str(charge.amount),
                'vat_rate': _dec_str(charge.vat_rate),
            }
        )
    return rows


def build_tax_summary_block(parsed: ParsedInvoice | None) -> list[dict]:
    if not parsed:
        return []
    return [
        {
            'rate': _dec_str(row.rate),
            'base': _money_str(row.base),
            'vat': _money_str(row.vat),
        }
        for row in parsed.tax_summary
    ]


def build_totals_block(document, parsed: ParsedInvoice | None) -> dict:
    currency = document.currency or 'EUR'
    if parsed is None:
        return {
            'line_net': None,
            'charges_total': None,
            'allowances_total': None,
            'vat_total': money(document.tax_amount) if document.tax_amount is not None else None,
            'grand_total': money(document.amount) if document.amount is not None else None,
            'prepaid': None,
            'payable': None,
            'currency': currency,
        }
    return {
        'line_net': _money_str(parsed.line_extension_amount),
        'charges_total': _money_str(parsed.charge_total_amount),
        'allowances_total': _money_str(parsed.allowance_total_amount),
        'vat_total': _money_str(parsed.tax_amount),
        'grand_total': _money_str(parsed.total_amount),
        'prepaid': _money_str(parsed.prepaid_amount),
        'payable': _money_str(parsed.payable_amount),
        'currency': parsed.currency or currency,
    }


def build_references_block(parsed: ParsedInvoice | None) -> list[dict]:
    if not parsed:
        return []
    return [{'type': ref.type, 'value': ref.value} for ref in parsed.references]


def build_integration_block(
    *,
    source: str | None,
    super_links,
    as4_links,
    document,
) -> dict:
    external_id = None
    received_at = None
    external_view_url = None
    status = None

    if super_links:
        link = super_links[0]
        external_id = (link.super_guid or '').strip() or None
        received_at = link.created_at.isoformat() if link.created_at else None
        status = 'received'
        if source == 'super' or external_id:
            external_view_url = build_super_external_view_url(external_id)
    elif as4_links:
        link = as4_links[0]
        external_id = (link.message_id or '').strip() or None
        received_at = link.created_at.isoformat() if link.created_at else None
        status = 'received'

    if received_at is None and document.created_at:
        received_at = document.created_at.isoformat()

    return {
        'source': source,
        'status': status,
        'received_at': received_at,
        'external_id': external_id,
        'external_view_url': external_view_url,
    }


def build_technical_block(*, as4_links, parsed: ParsedInvoice | None, document) -> dict:
    message_id = None
    if as4_links:
        message_id = (as4_links[0].message_id or '').strip() or None
    return {
        'message_id': message_id,
        'parser_version': parsed.parser_version if parsed else PARSER_VERSION,
        'imported_at': document.created_at.isoformat() if document.created_at else None,
        'source_hash': None,
    }


def build_accounting_block(*, posting, journal_lines) -> dict | None:
    if posting is None:
        return {
            'journal_entry_id': None,
            'entry_number': None,
            'entry_date': None,
            'status': 'unposted',
            'debit_total': None,
            'credit_total': None,
            'lines': [],
        }
    debit_total = sum((line.debit_amount or 0) for line in journal_lines)
    credit_total = sum((line.credit_amount or 0) for line in journal_lines)
    return {
        'journal_entry_id': posting.pk,
        'entry_number': posting.entry_number or None,
        'entry_date': posting.entry_date.isoformat() if posting.entry_date else None,
        'status': posting.status or 'unposted',
        'debit_total': _money_str(debit_total),
        'credit_total': _money_str(credit_total),
        'lines': [
            {
                'account_code': line.account.account_code if line.account_id else None,
                'account_name': line.account.account_name if line.account_id else None,
                'partner_name': None,
                'debit': _money_str(line.debit_amount),
                'credit': _money_str(line.credit_amount),
                'description': line.description or '',
            }
            for line in journal_lines
        ],
    }


def build_vat_context(*, ledger_rows, period_label: str | None) -> dict:
    rates_map: dict[str, dict] = {}
    total_base = None
    total_vat = None
    for row in ledger_rows or []:
        rate_key = str(row.vat_rate) if row.vat_rate is not None else ''
        bucket = rates_map.setdefault(
            rate_key,
            {'rate': _dec_str(row.vat_rate), 'base': 0, 'vat': 0},
        )
        bucket['base'] += row.base_amount or 0
        bucket['vat'] += row.vat_amount or 0
        total_base = (total_base or 0) + (row.base_amount or 0)
        total_vat = (total_vat or 0) + (row.vat_amount or 0)
    return {
        'period': period_label,
        'recorded': bool(ledger_rows),
        'deductible': None,
        'total_base': _money_str(total_base),
        'total_vat': _money_str(total_vat),
        'rates': [
            {
                'rate': row['rate'],
                'base': _money_str(row['base']),
                'vat': _money_str(row['vat']),
            }
            for row in rates_map.values()
        ],
    }


def build_subledger_context(*, subledger, allocations) -> dict:
    if subledger is None:
        return {
            'item_id': None,
            'state': None,
            'original_amount': None,
            'allocated_amount': None,
            'open_amount': None,
            'due_date': None,
            'allocations': [],
        }
    allocated = None
    if subledger.original_amount is not None and subledger.open_amount is not None:
        allocated = subledger.original_amount - subledger.open_amount
    return {
        'item_id': subledger.pk,
        'state': subledger.status,
        'original_amount': _money_str(subledger.original_amount),
        'allocated_amount': _money_str(allocated),
        'open_amount': _money_str(subledger.open_amount),
        'due_date': subledger.due_date.isoformat() if subledger.due_date else None,
        'allocations': [
            {
                'id': alloc.pk,
                'amount': _money_str(alloc.amount),
                'created_at': alloc.created_at.isoformat() if alloc.created_at else None,
                'journal_entry_id': alloc.journal_entry_id,
                'status': 'allocated',
            }
            for alloc in allocations or []
        ],
    }


def build_payment_block(*, bank_txs, match_status: str | None) -> dict:
    primary = bank_txs[0] if bank_txs else None
    account_mask = None
    if primary is not None:
        statement = getattr(primary, 'bank_statement', None)
        account = getattr(statement, 'bank_account', None) if statement else None
        iban = (getattr(account, 'iban', None) or '').replace(' ', '')
        if len(iban) >= 4:
            account_mask = f'…{iban[-4:]}'
    return {
        'matched': match_status == 'matched',
        'date': primary.transaction_date.isoformat() if primary and primary.transaction_date else None,
        'amount': _money_str(primary.amount) if primary else None,
        'account_mask': account_mask,
        'reference': (primary.reference or None) if primary else None,
        'reconcile_status': match_status,
        'bank_transaction_id': primary.pk if primary else None,
    }


def enrich_incoming_detail_payload(
    payload: dict,
    *,
    document,
    partner,
    rel,
    super_links,
    as4_links,
    posting=None,
    subledger=None,
    ledger_rows=None,
    journal_lines=None,
    allocations=None,
    bank_txs=None,
    match_status: str | None = None,
    period_label: str | None = None,
) -> dict:
    """Attach PR A + PR C blocks onto an incoming DocumentDetail payload."""
    source = document.source
    parsed = try_parse_incoming_ubl(super_links, as4_links)
    journal_lines = journal_lines or []
    allocations = allocations or []
    bank_txs = bank_txs or []
    ledger_rows = ledger_rows or []

    payload['number'] = document.receipt_number or document.expense_number
    payload['supplier'] = build_supplier_block(partner, parsed, rel)
    payload['document'] = build_document_meta(document, parsed, source=source)
    payload['status'] = build_lifecycle_status(
        document,
        integration_source='super' if super_links else ('as4' if as4_links else source),
        posting=posting,
        subledger=subledger,
        ledger_rows=ledger_rows,
        match_status=match_status,
    )
    payload['lines'] = build_lines_block(parsed)
    payload['charges'] = build_charges_block(parsed)
    payload['tax_summary'] = build_tax_summary_block(parsed)
    payload['totals'] = build_totals_block(document, parsed)
    payload['references'] = build_references_block(parsed)
    payload['integration'] = build_integration_block(
        source=source,
        super_links=super_links,
        as4_links=as4_links,
        document=document,
    )
    payload['technical'] = build_technical_block(
        as4_links=as4_links,
        parsed=parsed,
        document=document,
    )
    payload['accounting'] = build_accounting_block(posting=posting, journal_lines=journal_lines)
    payload['vat_context'] = build_vat_context(ledger_rows=ledger_rows, period_label=period_label)
    payload['subledger_context'] = build_subledger_context(
        subledger=subledger,
        allocations=allocations,
    )
    payload['payment'] = build_payment_block(bank_txs=bank_txs, match_status=match_status)
    if parsed and parsed.notes and not (payload.get('notes') or '').strip():
        payload['notes'] = '\n'.join(parsed.notes)
    return payload
