"""Copy inbound UBL monetary and tax facts onto Expense. Does not post or classify."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType

from expenses.models import Expense


def tax_facts_from_summary(rows) -> dict[str, str]:
    """Keep a category only when every non-empty id is the same one.

    Exemption reason is kept only when every row of that category agrees.
    """
    empty = {
        'ubl_tax_category': '',
        'ubl_tax_exemption_reason_code': '',
        'ubl_tax_exemption_reason_text': '',
    }
    ids = {(row.category_id or '').strip().upper() for row in rows if (row.category_id or '').strip()}
    if len(ids) != 1:
        return empty
    category = next(iter(ids))
    matching = [row for row in rows if (row.category_id or '').strip().upper() == category]
    codes = {(row.exemption_reason_code or '').strip() for row in matching}
    texts = {(row.exemption_reason_text or '').strip() for row in matching}
    return {
        'ubl_tax_category': category,
        'ubl_tax_exemption_reason_code': next(iter(codes)) if len(codes) == 1 else '',
        'ubl_tax_exemption_reason_text': next(iter(texts)) if len(texts) == 1 else '',
    }


def monetary_facts_from_parsed(parsed) -> dict[str, Decimal | None]:
    return {
        'ubl_prepaid_amount': Decimal(parsed.prepaid_amount or 0),
        'ubl_payable_amount': Decimal(parsed.payable_amount or 0),
    }


def apply_parsed_ubl_facts(expense: Expense, parsed) -> None:
    facts = {
        **monetary_facts_from_parsed(parsed),
        **tax_facts_from_summary(parsed.tax_summary),
    }
    for field, value in facts.items():
        setattr(expense, field, value)
    expense.save(update_fields=[*facts.keys(), 'updated_at'])


def _single_inbound_xml(expense: Expense) -> str | None:
    from fiscal_gateway.models import As4DocumentLink
    from super_integration.models import SuperDocumentLink

    content_type = ContentType.objects.get_for_model(Expense)
    blobs = []
    for model, direction in (
        (SuperDocumentLink, SuperDocumentLink.DIRECTION_INBOUND),
        (As4DocumentLink, As4DocumentLink.DIRECTION_INBOUND),
    ):
        for xml in model.all_objects.filter(
            tenant_id=expense.tenant_id,
            content_type=content_type,
            object_id=expense.pk,
            direction=direction,
        ).values_list('ubl_xml', flat=True):
            if (xml or '').strip():
                blobs.append(xml)
    if len(blobs) != 1:
        return None
    return blobs[0]


def backfill_expense_ubl_facts(expense: Expense) -> bool:
    """Re-read the one stored inbound UBL. Two links, or none, leave the expense unchanged."""
    from ubl.parser.invoice import parse_invoice_ubl

    xml = _single_inbound_xml(expense)
    if xml is None:
        return False
    apply_parsed_ubl_facts(expense, parse_invoice_ubl(xml))
    return True
