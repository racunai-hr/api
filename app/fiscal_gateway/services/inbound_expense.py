from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from expenses.models import Expense, ExpenseCategory, ExpenseSource
from expenses.services.partner_resolver import resolve_partner
from fiscal_gateway.models import As4DocumentLink
from ubl.parser.invoice import ParsedInvoice, parse_invoice_ubl


def _system_user():
    User = get_user_model()
    return User.objects.filter(is_superuser=True).first() or User.objects.first()


def _default_category(tenant):
    category, _ = ExpenseCategory.all_objects.get_or_create(
        tenant=tenant,
        name='Ostalo',
        defaults={'description': 'Ostali troškovi'},
    )
    return category


def _as_date(value) -> date | None:
    """UBL dates arrive as ISO strings; period locks and reports need real dates."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _next_expense_number(tenant, receipt_number: str, prefix: str) -> str:
    base = receipt_number or prefix
    candidate = f'{prefix}-{base}'[:50]
    if not Expense.all_objects.filter(tenant=tenant, expense_number=candidate).exists():
        return candidate
    suffix = 1
    while True:
        candidate = f'{prefix}-{base}-{suffix}'[:50]
        if not Expense.all_objects.filter(tenant=tenant, expense_number=candidate).exists():
            return candidate
        suffix += 1


def resolve_tenant_for_customer_oib(customer_oib: str):
    from settings.models import CompanySettings

    cleaned = (customer_oib or '').replace('HR', '').strip()
    if not cleaned:
        return None

    company = CompanySettings.all_objects.filter(vat_number=cleaned).first()
    if company:
        return company.tenant

    from fiscal_gateway.models import DirectTenantConfig, FiscalTenantConfig

    direct = DirectTenantConfig.all_objects.filter(oib=cleaned, is_active=True).first()
    if direct:
        return direct.tenant

    fiscal = FiscalTenantConfig.all_objects.filter(oib=cleaned, is_active=True).first()
    if fiscal:
        return fiscal.tenant

    return None


@dataclass(frozen=True)
class InboundExpenseDraft:
    expense: Expense
    parsed: ParsedInvoice


def create_inbound_expense_from_ubl(
    *,
    tenant,
    ubl_xml: str,
    number_prefix: str,
    source: str = ExpenseSource.MANUAL,
    fallback_supplier_oib: str = '',
    unknown_supplier_ref: str = '',
) -> InboundExpenseDraft:
    """Create the draft Expense for an inbound UBL invoice.

    Transport agnostic on purpose: provider provenance (AS4 / SUPER links,
    import metadata) belongs to the caller, not here.
    """
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    parsed = parse_invoice_ubl(ubl_xml)
    expense_date = _as_date(parsed.issue_date) or timezone.now().date()
    lock_open_vat_period_for_source_mutation(tenant, expense_date)
    user = _system_user()
    if not user:
        raise ValueError('Nema korisnika za kreiranje troška')

    supplier = resolve_partner(
        tenant=tenant,
        oib=(
            parsed.supplier_oib
            or fallback_supplier_oib
            or f'unknown-{unknown_supplier_ref[:8]}'
        ),
        name=parsed.supplier_name or '',
    )
    if parsed.supplier_address and not (supplier.address or '').strip():
        supplier.address = parsed.supplier_address
        supplier.save(update_fields=['address'])

    expense = Expense.all_objects.create(
        tenant=tenant,
        expense_number=_next_expense_number(tenant, parsed.invoice_number, number_prefix),
        source=source,
        status='draft',
        category=_default_category(tenant),
        supplier=supplier,
        amount=parsed.total_amount,
        tax_amount=parsed.tax_amount,
        currency=parsed.currency or 'EUR',
        expense_date=expense_date,
        due_date=_as_date(parsed.due_date),
        receipt_number=parsed.invoice_number,
        description='; '.join(parsed.line_descriptions) or f'Ulazni eRačun {parsed.invoice_number}',
        created_by=user,
    )
    return InboundExpenseDraft(expense=expense, parsed=parsed)


@transaction.atomic
def import_inbound_as4_expense(
    *,
    tenant,
    ubl_xml: str,
    message_id: str,
    supplier_oib: str,
    customer_oib: str,
    from_party_id: str = '',
    conversation_id: str = '',
) -> Expense | None:
    if As4DocumentLink.all_objects.filter(
        tenant=tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        message_id=message_id,
    ).exists():
        return None

    draft = create_inbound_expense_from_ubl(
        tenant=tenant,
        ubl_xml=ubl_xml,
        number_prefix='AS4',
        fallback_supplier_oib=supplier_oib,
        unknown_supplier_ref=message_id,
    )

    As4DocumentLink.all_objects.create(
        tenant=tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        message_id=message_id,
        as4_status=As4DocumentLink.STATUS_SENT,
        ubl_xml=ubl_xml,
        recipient_oib=customer_oib,
        supplier_oib=supplier_oib or draft.parsed.supplier_oib,
        conversation_id=conversation_id,
        from_party_id=from_party_id,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=draft.expense.pk,
    )
    return draft.expense
