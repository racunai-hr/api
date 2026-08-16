"""Invariant guard: PaymentOrder.status samo preko PaymentOrderLifecycle.

Dva sloja:
- Runtime: PaymentOrder.save() + ContextVar u lifecycle_status_change()
- Source: AST lint (lifecycle_guard paket)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from banking.provider_models import BankConnection, BankProvider, PaymentExecution, PaymentOrder
from banking.services.payment_execution_lifecycle import ACTOR_SUBMIT, ExecutionLifecycle
from banking.services.payment_execution_status_guard import PaymentExecutionStatusGuardError
from banking.services.payment_order_lifecycle import ACTOR_SUBMIT as ORDER_ACTOR_SUBMIT, PaymentOrderLifecycle
from banking.services.payment_order_status_guard import PaymentOrderStatusGuardError
from lifecycle_guard import LIFECYCLE_PROTECTED_MODELS, scan_for_violations
from payments.models import BankAccount
from tenants.models import Tenant


def test_payment_order_status_never_assigned_outside_lifecycle_in_source():
    """Lint guard: order.status = ... samo u payment_order_lifecycle.py."""
    violations = scan_for_violations(LIFECYCLE_PROTECTED_MODELS)
    assert not violations, (
        'Pronađene izravne promjene statusa izvan lifecycle servisa:\n'
        + '\n'.join(f'{v.path}:{v.line}: {v.model}.{v.field} — {v.snippet}' for v in violations)
    )


@pytest.mark.django_db
def test_payment_order_status_never_changes_without_transition():
    """Runtime guard: order.status = ...; order.save() mora baciti grešku."""
    tenant = Tenant.objects.create(slug='guard-test', name='Guard Test')
    provider = BankProvider.objects.create(
        code='guard-prov',
        name='Guard',
        environment='sandbox',
        iam_base='https://iam.example.test',
        api_base='https://api.example.test',
        service_host='api.example.test',
    )
    bank_account = BankAccount.all_objects.create(
        tenant=tenant,
        account_name='EUR',
        bank_name='OTP',
        account_number='HR123',
        iban='HR123',
    )
    connection = BankConnection.all_objects.create(
        tenant=tenant,
        bank_provider=provider,
        bank_account=bank_account,
        status='connected',
    )
    order = PaymentOrder.all_objects.create(
        tenant=tenant,
        connection=connection,
        status='draft',
        debtor_iban='HR123',
        creditor_iban='HR456',
        creditor_name='Primatelj',
        amount=Decimal('10.00'),
        currency='EUR',
    )

    order.status = 'executed'
    with pytest.raises(PaymentOrderStatusGuardError, match='PaymentOrderLifecycle.transition'):
        order.save(update_fields=['status', 'updated_at'])

    order.refresh_from_db()
    assert order.status == 'draft'


@pytest.mark.django_db
def test_payment_order_status_changes_through_lifecycle():
    tenant = Tenant.objects.create(slug='guard-lc', name='Guard LC')
    provider = BankProvider.objects.create(
        code='guard-lc',
        name='Guard LC',
        environment='sandbox',
        iam_base='https://iam.example.test',
        api_base='https://api.example.test',
        service_host='api.example.test',
    )
    bank_account = BankAccount.all_objects.create(
        tenant=tenant,
        account_name='EUR',
        bank_name='OTP',
        account_number='HR123',
        iban='HR123',
    )
    connection = BankConnection.all_objects.create(
        tenant=tenant,
        bank_provider=provider,
        bank_account=bank_account,
        status='connected',
    )
    order = PaymentOrder.all_objects.create(
        tenant=tenant,
        connection=connection,
        status='draft',
        debtor_iban='HR123',
        creditor_iban='HR456',
        creditor_name='Primatelj',
        amount=Decimal('10.00'),
        currency='EUR',
    )

    PaymentOrderLifecycle.transition(order, 'submitted', actor=ORDER_ACTOR_SUBMIT)

    order.refresh_from_db()
    assert order.status == 'submitted'


@pytest.mark.django_db
def test_payment_execution_status_never_changes_without_transition():
    tenant = Tenant.objects.create(slug='exec-guard', name='Exec Guard')
    provider = BankProvider.objects.create(
        code='exec-guard',
        name='Exec Guard',
        environment='sandbox',
        iam_base='https://iam.example.test',
        api_base='https://api.example.test',
        service_host='api.example.test',
    )
    bank_account = BankAccount.all_objects.create(
        tenant=tenant,
        account_name='EUR',
        bank_name='OTP',
        account_number='HR123',
        iban='HR123',
    )
    connection = BankConnection.all_objects.create(
        tenant=tenant,
        bank_provider=provider,
        bank_account=bank_account,
        status='connected',
    )
    order = PaymentOrder.all_objects.create(
        tenant=tenant,
        connection=connection,
        status='draft',
        debtor_iban='HR123',
        creditor_iban='HR456',
        creditor_name='Primatelj',
        amount=Decimal('10.00'),
        currency='EUR',
    )
    execution = PaymentExecution.all_objects.create(
        tenant=tenant,
        order=order,
        attempt=1,
        status='draft',
        parent_order_correlation_id=order.correlation_id,
    )

    execution.status = 'submitted'
    with pytest.raises(PaymentExecutionStatusGuardError, match='ExecutionLifecycle.transition'):
        execution.save(update_fields=['status', 'updated_at'])

    execution.refresh_from_db()
    assert execution.status == 'draft'


@pytest.mark.django_db
def test_payment_execution_status_changes_through_lifecycle():
    tenant = Tenant.objects.create(slug='exec-guard-lc', name='Exec Guard LC')
    provider = BankProvider.objects.create(
        code='exec-guard-lc',
        name='Exec Guard LC',
        environment='sandbox',
        iam_base='https://iam.example.test',
        api_base='https://api.example.test',
        service_host='api.example.test',
    )
    bank_account = BankAccount.all_objects.create(
        tenant=tenant,
        account_name='EUR',
        bank_name='OTP',
        account_number='HR123',
        iban='HR123',
    )
    connection = BankConnection.all_objects.create(
        tenant=tenant,
        bank_provider=provider,
        bank_account=bank_account,
        status='connected',
    )
    order = PaymentOrder.all_objects.create(
        tenant=tenant,
        connection=connection,
        status='draft',
        debtor_iban='HR123',
        creditor_iban='HR456',
        creditor_name='Primatelj',
        amount=Decimal('10.00'),
        currency='EUR',
    )
    execution = PaymentExecution.all_objects.create(
        tenant=tenant,
        order=order,
        attempt=1,
        status='draft',
        parent_order_correlation_id=order.correlation_id,
    )

    ExecutionLifecycle.transition(execution, 'submitted', actor=ACTOR_SUBMIT)

    execution.refresh_from_db()
    assert execution.status == 'submitted'
