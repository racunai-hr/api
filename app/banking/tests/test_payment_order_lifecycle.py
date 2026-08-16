from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from banking.provider_models import BankConnection, BankProvider, PaymentOrder, PaymentOrderTransition
from banking.services.payment_order_lifecycle import (
    ACTOR_OTP_POLLING,
    ACTOR_SUBMIT,
    InvalidPaymentOrderTransition,
    PaymentOrderLifecycle,
)
from events.payment import PaymentExecuted
from payments.models import BankAccount
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(slug='pis-test', name='PIS Test')


@pytest.fixture
def bank_setup(tenant):
    provider = BankProvider.objects.create(
        code='otp-test',
        name='OTP Test',
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


@pytest.mark.django_db
class TestPaymentOrderLifecycle:
    def test_allowed_transitions(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)

        PaymentOrderLifecycle.transition(order, 'submitted', actor=ACTOR_SUBMIT)
        order.refresh_from_db()
        assert order.status == 'submitted'
        assert order.transitions.count() == 1

        PaymentOrderLifecycle.transition(order, 'sca_required', actor=ACTOR_SUBMIT)
        PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)
        PaymentOrderLifecycle.transition(order, 'accepted', actor=ACTOR_OTP_POLLING)
        PaymentOrderLifecycle.transition(order, 'executed', actor=ACTOR_OTP_POLLING)

        order.refresh_from_db()
        assert order.status == 'executed'
        assert order.transitions.count() == 5

    def test_invalid_transition_raises(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='authorised')

        with pytest.raises(InvalidPaymentOrderTransition):
            PaymentOrderLifecycle.transition(order, 'submitted', actor=ACTOR_SUBMIT)

    def test_same_status_is_noop(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='authorised')

        with patch('banking.services.payment_order_lifecycle.publish') as mock_publish:
            result = PaymentOrderLifecycle.transition(
                order,
                'authorised',
                actor=ACTOR_OTP_POLLING,
            )

        order.refresh_from_db()
        assert result.pk == order.pk
        assert order.status == 'authorised'
        assert order.transitions.count() == 0
        mock_publish.assert_not_called()

    def test_repeated_poll_does_not_add_transitions(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='authorised')

        PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)
        PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)

        assert order.transitions.count() == 0

    def test_emit_on_executed_transition(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='accepted')

        with patch('banking.services.payment_order_lifecycle.publish') as mock_publish:
            PaymentOrderLifecycle.transition(order, 'executed', actor=ACTOR_OTP_POLLING)

        mock_publish.assert_called_once()
        event = mock_publish.call_args[0][0]
        assert isinstance(event, PaymentExecuted)
        assert event.payment_order_id == order.pk
        assert event.tenant_id == order.tenant_id

    @pytest.mark.parametrize('flag', [True, False])
    def test_sandbox_flag_emit_on_authorised(self, bank_setup, settings, flag):
        _, connection = bank_setup
        settings.PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED = flag
        order = _create_order(connection, status='sca_required')

        with patch('banking.services.payment_order_lifecycle.publish') as mock_publish:
            PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)

        if flag:
            mock_publish.assert_called_once()
            assert isinstance(mock_publish.call_args[0][0], PaymentExecuted)
        else:
            mock_publish.assert_not_called()

    def test_audit_sequence_increments(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection)

        PaymentOrderLifecycle.transition(order, 'submitted', actor=ACTOR_SUBMIT)
        PaymentOrderLifecycle.transition(order, 'sca_required', actor=ACTOR_SUBMIT)
        PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)

        sequences = list(
            PaymentOrderTransition.objects.filter(order=order)
            .order_by('sequence')
            .values_list('sequence', 'from_status', 'to_status', 'actor'),
        )
        assert sequences == [
            (1, 'draft', 'submitted', ACTOR_SUBMIT),
            (2, 'submitted', 'sca_required', ACTOR_SUBMIT),
            (3, 'sca_required', 'authorised', ACTOR_OTP_POLLING),
        ]

    def test_authorised_to_executed_skip_accepted(self, bank_setup):
        _, connection = bank_setup
        order = _create_order(connection, status='authorised')

        with patch('banking.services.payment_order_lifecycle.publish') as mock_publish:
            PaymentOrderLifecycle.transition(order, 'executed', actor=ACTOR_OTP_POLLING)

        order.refresh_from_db()
        assert order.status == 'executed'
        mock_publish.assert_called_once()
