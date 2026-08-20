"""Assemble DocumentSummary / DocumentDetail DTOs from a consistent snapshot."""

from __future__ import annotations

from datetime import date

from domains.reporting.documents.attachments import download_available_field
from domains.reporting.documents.provenance import provenanced
from domains.reporting.documents.projection import (
    bank_block,
    bank_expected,
    build_subledger_block,
    collect_controls,
    money,
    operational_deposit,
    operational_incoming,
    operational_outgoing,
    payment_order_block,
    period_returns_context,
    posting_block,
    vat_lifecycle,
)
from fiscal_gateway.models import As4DocumentLink
from integrations.models import IntegrationOutboxMessage


def _primary_subledger(rows):
    if not rows:
        return None
    active = [row for row in rows if row.status != 'cancelled']
    return active[0] if active else rows[0]


def _primary_posting(rows):
    if not rows:
        return None
    posted = [row for row in rows if row.status == 'posted']
    return posted[0] if posted else rows[0]


def _latest_as4(links):
    if not links:
        return None
    return sorted(links, key=lambda link: link.created_at, reverse=True)[0]


def assemble_row(direction: str, document, rel, as_of_day: date, *, detail: bool) -> dict:
    if direction == 'outgoing':
        partner = rel['partners'].get(document.company_to_id)
        items = rel['items_by_invoice'].get(document.pk, [])
        ledger_rows = rel['vat_out'].get(document.pk, [])
        subledger = _primary_subledger(rel['sub_out'].get(document.pk, []))
        posting = _primary_posting(rel['je_out'].get(document.pk, []))
        as4_links = rel['as4_out'].get(document.pk, [])
        super_links = rel['super_out'].get(document.pk, [])
        payments = rel['payments_by_invoice'].get(document.pk, [])
        attachments = []
        internal_number = document.invoice_number
        source_number = document.invoice_number
        document_date = document.issue_date
        gross = document.total_amount
        net = document.subtotal
        vat = document.tax_amount
        currency = 'EUR'
        source = None
        fiscal_logs = rel['fiscal_by_invoice'].get(document.pk, [])
        outbox = rel['outbox_by_invoice'].get(document.pk, [])
        has_pdf_xml = True  # generated PDF always available for persisted invoice
        if as4_links:
            has_pdf_xml = True
    else:
        partner = rel['partners'].get(document.supplier_id)
        items = []
        ledger_rows = rel['vat_in'].get(document.pk, [])
        subledger = _primary_subledger(rel['sub_in'].get(document.pk, []))
        posting = _primary_posting(rel['je_in'].get(document.pk, []))
        as4_links = rel['as4_in'].get(document.pk, [])
        super_links = rel['super_in'].get(document.pk, [])
        payments = []
        attachments = rel['attachments_by_expense'].get(document.pk, [])
        internal_number = document.expense_number
        source_number = document.receipt_number or document.expense_number
        document_date = document.expense_date
        gross = document.amount
        net = document.amount - document.tax_amount
        vat = document.tax_amount
        currency = document.currency or 'EUR'
        source = document.source
        fiscal_logs = []
        outbox = []
        has_pdf_xml = bool(attachments) or any(link.ubl_xml for link in as4_links) or any(
            link.ubl_xml or link.pdf_path for link in super_links
        )

    as4 = _latest_as4(as4_links)
    as4_status = as4.as4_status if as4 else None
    payment_ids = [p.pk for p in payments]
    bank_txs = []
    for pay in payments:
        bank_txs.extend(rel['bank_by_payment'].get(pay.pk, []))
    if posting is not None:
        bank_txs.extend(rel['bank_by_je'].get(posting.pk, []))
    match_status = None
    if any(tx.match_status == 'matched' for tx in bank_txs):
        match_status = 'matched'
    elif any(tx.match_status == 'suggested' for tx in bank_txs):
        match_status = 'suggested'
    elif bank_txs:
        match_status = 'unmatched'
    expected_bank = bank_expected(
        direction=direction,
        payments=payments,
        expense=document if direction == 'incoming' else None,
    )
    order = None
    for pay in payments:
        if pay.pk in rel['orders_by_payment']:
            order = rel['orders_by_payment'][pay.pk]
            break
    allocations = rel['alloc_by_sub'].get(subledger.pk, []) if subledger else []
    closing_entries = []
    if posting is not None:
        closing_entries.append(posting)
    outbox_incomplete = any(
        msg.status in (
            IntegrationOutboxMessage.STATUS_PENDING,
            IntegrationOutboxMessage.STATUS_RETRYING,
        )
        for msg in outbox
    )
    jir_log = next((log for log in fiscal_logs if log.jir), None)

    period_ids = {row.vat_period_id for row in ledger_rows}
    vat_returns = []
    for period_id in period_ids:
        vat_returns.extend(rel['returns_by_period'].get(period_id, []))
    versions, disclaimer = period_returns_context(vat_returns, rel['events_by_return'])

    disputed = as4_status in {
        As4DocumentLink.STATUS_REJECTED,
        As4DocumentLink.STATUS_FAILED,
    }
    has_receipt = bool(attachments) or bool(source and source != 'manual')
    if direction == 'outgoing':
        operational = operational_outgoing(
            document=document,
            subledger=subledger,
            as4_status=as4_status,
            as_of_day=as_of_day,
        )
    else:
        operational = operational_incoming(
            document=document,
            subledger=subledger,
            disputed=disputed,
            as_of_day=as_of_day,
            has_receipt_evidence=has_receipt,
        )

    alerts, notices = collect_controls({
        'document': document,
        'direction': direction,
        'partner': partner,
        'subledger': subledger,
        'ledger_rows': ledger_rows,
        'as_of_day': as_of_day,
        'as4_status': as4_status,
        'posting_entry': posting,
        'payments': payments,
        'allocations': allocations,
        'bank_matched': match_status == 'matched',
        'closing_entries': closing_entries,
        'payment_order': order,
        'partner_ibans': rel['ibans_by_partner'].get(partner.pk if partner else 0, set()),
        'outbox_incomplete': outbox_incomplete,
        'possible_duplicate': False,
        'has_pdf_xml': has_pdf_xml,
        'items': items,
    })

    boxes = sorted({row.vat_box for row in ledger_rows if row.vat_box})
    ledger_type = ledger_rows[0].ledger_type if ledger_rows else None
    period_label = None
    if ledger_rows:
        period = ledger_rows[0].vat_period
        period_label = f'{period.year:04d}-{period.month:02d}'

    payload = {
        'id': document.pk,
        'kind': 'invoice' if direction == 'outgoing' else 'expense',
        'direction': direction,
        'internal_number': internal_number,
        'source_number': source_number,
        'partner_name': partner.name if partner else None,
        'partner_oib': partner.tax_number if partner else None,
        'partner_vat_number': partner.vat_number if partner else None,
        'document_date': document_date.isoformat() if document_date else None,
        'due_date': document.due_date.isoformat() if document.due_date else None,
        'document_status': provenanced(document.status, source='document_status'),
        'operational_status': operational,
        'posting': posting_block(posting),
        'subledger': build_subledger_block(subledger, as_of_day),
        'bank': bank_block(match_status=match_status, expected=expected_bank),
        'payment_order': payment_order_block(order),
        'vat': {
            'lifecycle': vat_lifecycle(direction=direction, document=document, ledger_rows=ledger_rows),
            'ledger_type': provenanced(ledger_type, source='vat_ledger_entry') if ledger_type else provenanced(None, reason='not_recorded'),
            'period': provenanced(period_label, source='vat_ledger_entry') if period_label else provenanced(None, reason='not_recorded'),
            'boxes': provenanced(boxes, source='vat_ledger_entry') if boxes else provenanced(None, reason='not_recorded'),
            'document_in_submitted_return': provenanced(None, reason='not_provable'),
            'period_returns': versions,
            'disclaimer': disclaimer,
        },
        'eracun': {
            'as4_status': provenanced(as4_status, source='as4_document_link') if as4_status else provenanced(None, reason='not_recorded'),
            'message_id': provenanced(as4.message_id, source='as4_document_link') if as4 else provenanced(None, reason='not_recorded'),
            'source': provenanced(source, source='expense') if source else provenanced(None, reason='not_applicable' if direction == 'outgoing' else 'not_recorded'),
        },
        'fiscal': {
            'jir': provenanced(jir_log.jir, source='fiscal_submission_log') if jir_log else provenanced(None, reason='not_recorded'),
            'zki': provenanced(None, reason='not_recorded'),
        },
        'controls': alerts,
        'notices': notices,
        'amounts': {
            'currency': currency,
            'net': money(net),
            'vat': money(vat),
            'gross': money(gross),
            'fx_rate': provenanced(None, reason='not_recorded'),
        },
    }
    if not detail:
        return payload

    payload['partner_id'] = partner.pk if partner else None
    payload['description'] = document.description
    payload['notes'] = document.notes
    payload['created_at'] = document.created_at.isoformat() if document.created_at else None
    payload['updated_at'] = document.updated_at.isoformat() if document.updated_at else None
    payload['created_by'] = document.created_by.username if document.created_by_id else None
    if direction == 'outgoing':
        payload['items'] = [
            {
                'item_name': item.item_name,
                'quantity': money(item.quantity),
                'unit_price': money(item.unit_price),
                'tax_rate': str(item.tax_rate),
                'vat_procedure': item.vat_procedure,
                'line_total': money(item.line_total),
            }
            for item in items
        ]
        payload['service_date'] = document.service_date.isoformat() if document.service_date else None
    else:
        payload['items'] = []
        payload['service_date'] = None
    payload['journal_lines'] = []
    if posting is not None:
        payload['journal_lines'] = [
            {
                'account_code': line.account.account_code if line.account_id else None,
                'account_name': line.account.account_name if line.account_id else None,
                'debit': money(line.debit_amount),
                'credit': money(line.credit_amount),
                'description': line.description,
            }
            for line in rel['lines_by_je'].get(posting.pk, [])
        ]
    payload['payments'] = [
        {
            'id': pay.pk,
            'payment_number': pay.payment_number,
            'amount': money(pay.amount),
            'payment_date': pay.payment_date.isoformat() if pay.payment_date else None,
            'payment_method': pay.payment_method,
            'status': pay.status,
        }
        for pay in payments
    ]
    payload['allocations'] = [
        {
            'id': alloc.pk,
            'amount': money(alloc.amount),
            'created_at': alloc.created_at.isoformat() if alloc.created_at else None,
        }
        for alloc in allocations
    ]
    payload['ledger_entries'] = [
        {
            'ledger_type': row.ledger_type,
            'vat_box': row.vat_box,
            'vat_rate': str(row.vat_rate),
            'base_amount': money(row.base_amount),
            'vat_amount': money(row.vat_amount),
            'entry_category': row.entry_category,
            'entry_date': row.entry_date.isoformat() if row.entry_date else None,
            'document_number': row.document_number,
        }
        for row in ledger_rows
    ]
    payload['attachments'] = [
        {
            'id': att.pk,
            'original_filename': att.original_filename,
            'created_at': att.created_at.isoformat() if att.created_at else None,
            'uploaded_by': att.uploaded_by.username if att.uploaded_by_id else None,
            'download_available': download_available_field(att),
        }
        for att in attachments
    ]
    if direction == 'incoming':
        payload['ubl_available'] = any(bool(link.ubl_xml) for link in as4_links + super_links)
    else:
        payload['ubl_available'] = any(bool(link.ubl_xml) for link in as4_links + super_links)
    return payload


def assemble_deposit_row(document, rel, as_of_day: date, *, detail: bool) -> dict:
    partner = rel['partners'].get(document.partner_id)
    subledger = _primary_subledger(rel['sub_dep'].get(document.pk, []))
    posting = _primary_posting(rel['je_dep'].get(document.pk, []))
    allocations = rel['alloc_by_sub'].get(subledger.pk, []) if subledger else []
    operational = operational_deposit(document=document, subledger=subledger)

    payload = {
        'id': document.pk,
        'kind': 'deposit',
        'direction': 'deposit',
        'internal_number': document.number,
        'source_number': document.reference or document.number,
        'partner_name': partner.name if partner else None,
        'partner_oib': partner.tax_number if partner else None,
        'partner_vat_number': partner.vat_number if partner else None,
        'document_date': document.deposit_date.isoformat() if document.deposit_date else None,
        'due_date': document.deposit_date.isoformat() if document.deposit_date else None,
        'document_status': provenanced(document.status, source='document_status'),
        'operational_status': operational,
        'posting': posting_block(posting),
        'subledger': build_subledger_block(subledger, as_of_day),
        'bank': bank_block(match_status=None, expected=False),
        'payment_order': payment_order_block(None),
        'vat': {
            'lifecycle': provenanced('not_tax_active', source='deposit'),
            'ledger_type': provenanced(None, reason='not_applicable'),
            'period': provenanced(None, reason='not_applicable'),
            'boxes': provenanced(None, reason='not_applicable'),
            'document_in_submitted_return': provenanced(None, reason='not_applicable'),
            'period_returns': [],
            'disclaimer': None,
        },
        'eracun': {
            'as4_status': provenanced(None, reason='not_applicable'),
            'message_id': provenanced(None, reason='not_applicable'),
            'source': provenanced(None, reason='not_applicable'),
        },
        'fiscal': {
            'jir': provenanced(None, reason='not_applicable'),
            'zki': provenanced(None, reason='not_applicable'),
        },
        'controls': [],
        'notices': [],
        'amounts': {
            'currency': document.currency or 'EUR',
            'net': money(document.amount),
            'vat': money(0),
            'gross': money(document.amount),
            'fx_rate': provenanced(None, reason='not_applicable'),
        },
    }
    if not detail:
        return payload

    payload['partner_id'] = partner.pk if partner else None
    payload['description'] = document.reference or ''
    payload['notes'] = document.notes or ''
    payload['created_at'] = document.created_at.isoformat() if document.created_at else None
    payload['updated_at'] = document.updated_at.isoformat() if document.updated_at else None
    payload['created_by'] = document.created_by.username if document.created_by_id else None
    payload['items'] = []
    payload['service_date'] = None
    payload['journal_lines'] = []
    if posting is not None:
        payload['journal_lines'] = [
            {
                'account_code': line.account.account_code if line.account_id else None,
                'account_name': line.account.account_name if line.account_id else None,
                'debit': money(line.debit_amount),
                'credit': money(line.credit_amount),
                'description': line.description,
            }
            for line in rel['lines_by_je'].get(posting.pk, [])
        ]
    payload['payments'] = []
    payload['allocations'] = [
        {
            'id': alloc.pk,
            'amount': money(alloc.amount),
            'created_at': alloc.created_at.isoformat() if alloc.created_at else None,
        }
        for alloc in allocations
    ]
    payload['ledger_entries'] = []
    payload['attachments'] = []
    payload['ubl_available'] = False
    return payload


def documents_from_keys(tenant, keys, rel, as_of_day: date, *, detail: bool) -> list[dict]:
    rows = []
    for direction, pk in keys:
        if direction == 'deposit':
            document = rel['deposits'].get(pk)
            if document is None:
                continue
            rows.append(assemble_deposit_row(document, rel, as_of_day, detail=detail))
            continue
        document = (
            rel['invoices'].get(pk) if direction == 'outgoing' else rel['expenses'].get(pk)
        )
        if document is None:
            continue
        rows.append(assemble_row(direction, document, rel, as_of_day, detail=detail))
    return rows
