from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from banking.provider_models import (
    BankConnection,
    BankProvider,
    PaymentExecution,
    PaymentExecutionTransition,
    PaymentOrder,
)
from banking.services.payment_execution_lifecycle import (
    ACTOR_OTP_POLLING,
    ACTOR_SUBMIT,
    ExecutionLifecycle,
    InvalidPaymentExecutionTransition,
)
from banking.services.payment_executions import create_payment_execution, resolve_execution_correlation_id
from events.payment import PaymentExecuted
from payments.models import BankAccount
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(slug='exec-test', name='Execution Test')


@pytest.fixture
def bank_setup(tenant):
    provider = BankProvider.objects.create(
        code='otp-exec',
        name='OTP Exec',
        environment='sandbox',
        iam_base='https://iam.example.test',
        api_base='https://api.example.test',
        service_host='api.example.test',
    )
    bank_account = BankAccount.all_objects.create(
        tenant=tenant,
        account_name='Poslovni EUR',
        bank_name='OTP',
        account_number='HR6124070001100204771',
        iban='HR6124070001100204771',
    )
    connection = BankConnection.all_objects.create(
        tenant=tenant,
        bank_provider=provider,
        bank_account=bank_account,
        status='connected',
        sync_success_streak=3,
    )
    return provider, connection


def _create_order(connection, *, status='draft', payment=None):
    return PaymentOrder.all_objects.create(
        tenant=connection.tenant,
        connection=connection,
        payment=payment,
        status=status,
        debtor_iban='HR6124070001100204771',
        creditor_iban='HR9910090071100100001',
        creditor_name='Primatelj d.o.o.',
        amount=Decimal('100.00'),
        currency='EUR',
        reference='TEST-REF',
    )


def _create_execution(order, *, status='draft', attempt=1):
    return PaymentExecution.all_objects.create(
        tenant=order.tenant,
        order=order,
        attempt=attempt,
        status=status,
        parent_order_correlation_id=order.correlation_id,
    )


@pytest.mark.django_db
class TestExecutionLifecycle:
    def test_allowed_transitions_sync_order(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)
        execution = _create_execution(order)

        ExecutionLifecycle.transition(execution, 'submitted', actor=ACTOR_SUBMIT)
        order.refresh_from_db()
        execution.refresh_from_db()
        assert execution.status == 'submitted'
        assert order.status == 'submitted'
        assert execution.transitions.count() == 1
        assert order.transitions.count() == 1

    def test_invalid_transition_raises(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='authorised')
        execution = _create_execution(order, status='authorised')

        with pytest.raises(InvalidPaymentExecutionTransition):
            ExecutionLifecycle.transition(execution, 'submitted', actor=ACTOR_SUBMIT)

    def test_same_status_is_noop(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='authorised')
        execution = _create_execution(order, status='authorised')

        with patch('banking.services.payment_order_lifecycle.publish') as mock_publish:
            result = ExecutionLifecycle.transition(
                execution,
                'authorised',
                actor=ACTOR_OTP_POLLING,
            )

        order.refresh_from_db()
        assert result.pk == execution.pk
        assert execution.transitions.count() == 0
        assert order.transitions.count() == 0
        mock_publish.assert_not_called()

    def test_emit_on_executed_via_order_sync(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='accepted')
        execution = _create_execution(order, status='accepted')

        with patch('banking.services.payment_order_lifecycle.publish') as mock_publish:
            ExecutionLifecycle.transition(execution, 'executed', actor=ACTOR_OTP_POLLING)

        order.refresh_from_db()
        assert order.status == 'executed'
        mock_publish.assert_called_once()
        assert isinstance(mock_publish.call_args[0][0], PaymentExecuted)

    def test_audit_sequence_increments(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)
        execution = _create_execution(order)

        ExecutionLifecycle.transition(execution, 'submitted', actor=ACTOR_SUBMIT)
        ExecutionLifecycle.transition(execution, 'sca_required', actor=ACTOR_SUBMIT)
        ExecutionLifecycle.transition(execution, 'authorised', actor=ACTOR_OTP_POLLING)

        sequences = list(
            PaymentExecutionTransition.objects.filter(execution=execution)
            .order_by('sequence')
            .values_list('sequence', 'from_status', 'to_status', 'actor'),
        )
        assert sequences == [
            (1, 'draft', 'submitted', ACTOR_SUBMIT),
            (2, 'submitted', 'sca_required', ACTOR_SUBMIT),
            (3, 'sca_required', 'authorised', ACTOR_OTP_POLLING),
        ]


@pytest.mark.django_db
class TestPaymentExecutionRetry:
    def test_rejected_execution_allows_new_attempt_without_changing_order_pk(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='rejected')
        first = _create_execution(order, status='rejected', attempt=1)

        original_pk = order.pk
        second = create_payment_execution(order)

        assert second.pk != first.pk
        assert second.attempt == 2
        assert second.status == 'draft'
        assert order.pk == original_pk

        ExecutionLifecycle.transition(second, 'submitted', actor=ACTOR_SUBMIT)
        order.refresh_from_db()
        assert order.status == 'submitted'
        assert order.executions.count() == 2


@pytest.mark.django_db
class TestPaymentExecutionCorrelationId:
    def test_first_attempt_inherits_order_correlation_id(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)
        execution = create_payment_execution(order)

        assert execution.attempt == 1
        assert execution.correlation_id == order.correlation_id
        assert execution.parent_order_correlation_id == order.correlation_id

    def test_retry_gets_new_correlation_id(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)
        first = create_payment_execution(order)
        second = create_payment_execution(order)

        assert second.attempt == 2
        assert second.correlation_id != first.correlation_id
        assert second.correlation_id != order.correlation_id
        assert second.parent_order_correlation_id == order.correlation_id
        assert first.parent_order_correlation_id == order.correlation_id

    def test_resolve_execution_correlation_id_rule(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)

        assert resolve_execution_correlation_id(order, attempt=1) == order.correlation_id
        assert resolve_execution_correlation_id(order, attempt=2) != order.correlation_id


@pytest.mark.django_db
class TestSyncOrderFromExecution:
    def test_sync_is_execution_to_order_only(self, bank_setup):
        from banking.services.payment_executions import sync_order_from_execution

        _, connection = bank_setup
        order = _create_order(connection)
        execution = PaymentExecution.all_objects.create(
            tenant=order.tenant,
            order=order,
            attempt=1,
            status='submitted',
            provider_payment_id='PAY-123',
            authorization_id='AUTH-1',
            correlation_id=order.correlation_id,
            parent_order_correlation_id=order.correlation_id,
        )
        order_correlation = order.correlation_id

        execution.provider_payment_id = 'PAY-456'
        execution.correlation_id = uuid.uuid4()
        execution.save(update_fields=['provider_payment_id', 'correlation_id', 'updated_at'])

        sync_order_from_execution(order, execution)

        order.refresh_from_db()
        assert order.otp_payment_id == 'PAY-456'
        assert order.correlation_id == order_correlation
