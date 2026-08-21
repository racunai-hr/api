from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from expenses.models import Expense, ExpenseCategory
from expenses.services.partner_resolver import resolve_partner
from fiscal_gateway.models import As4DocumentLink
from ubl.parser.invoice import parse_invoice_ubl


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


def _next_expense_number(tenant, receipt_number: str) -> str:
    base = receipt_number or 'AS4'
    candidate = f'AS4-{base}'[:50]
    if not Expense.all_objects.filter(tenant=tenant, expense_number=candidate).exists():
        return candidate
    suffix = 1
    while True:
        candidate = f'AS4-{base}-{suffix}'[:50]
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
    from accounting.services.tax_projection.locks import lock_open_vat_period_for_source_mutation

    if As4DocumentLink.all_objects.filter(
        tenant=tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        message_id=message_id,
    ).exists():
        return None

    parsed = parse_invoice_ubl(ubl_xml)
    expense_date = parsed.issue_date or timezone.now().date()
    lock_open_vat_period_for_source_mutation(tenant, expense_date)
    user = _system_user()
    if not user:
        raise ValueError('Nema korisnika za kreiranje troška')

    supplier = resolve_partner(
        tenant=tenant,
        oib=parsed.supplier_oib or supplier_oib or f'unknown-{message_id[:8]}',
        name=parsed.supplier_name or '',
    )
    if parsed.supplier_name and supplier.name != parsed.supplier_name:
        supplier.name = parsed.supplier_name
        if parsed.supplier_address and not supplier.address:
            supplier.address = parsed.supplier_address
        supplier.save(update_fields=['name', 'address'])

    expense = Expense.all_objects.create(
        tenant=tenant,
        expense_number=_next_expense_number(tenant, parsed.invoice_number),
        status='draft',
        category=_default_category(tenant),
        supplier=supplier,
        amount=parsed.total_amount,
        tax_amount=parsed.tax_amount,
        currency=parsed.currency or 'EUR',
        expense_date=expense_date,
        due_date=parsed.due_date,
        receipt_number=parsed.invoice_number,
        description='; '.join(parsed.line_descriptions) or f'Ulazni eRačun {parsed.invoice_number}',
        created_by=user,
    )

    As4DocumentLink.all_objects.create(
        tenant=tenant,
        direction=As4DocumentLink.DIRECTION_INBOUND,
        message_id=message_id,
        as4_status=As4DocumentLink.STATUS_SENT,
        ubl_xml=ubl_xml,
        recipient_oib=customer_oib,
        supplier_oib=supplier_oib or parsed.supplier_oib,
        conversation_id=conversation_id,
        from_party_id=from_party_id,
        content_type=ContentType.objects.get_for_model(Expense),
        object_id=expense.pk,
    )
    return expense
