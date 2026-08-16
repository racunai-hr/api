from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from banking.provider_models import BankConnection, BankProvider, PaymentOrder, PaymentOrderTransition
from banking.services.payment_order_lifecycle import ACTOR_OTP_POLLING, ACTOR_SUBMIT, PaymentOrderLifecycle
from events.dispatcher import publish as real_publish
from events.payment import PaymentExecuted
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant


@pytest.mark.django_db
class TestPisSandboxPostingIntegration:
    """Regresijski test: polling istog statusa ne duplira audit ni knjiženje."""

    @pytest.fixture(autouse=True)
    def setup_chart(self):
        import_rrif_chart(clear=True)

    @pytest.fixture
    def pis_flow_setup(self, settings):
        settings.PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED = True

        tenant = Tenant.objects.create(slug='pis-int', name='PIS Integration')
        provision_tenant_chart(tenant)

        User = get_user_model()
        user = User.objects.create_superuser(username='pis-admin', password='test', email='pis@test.com')

        partner = Partner.all_objects.create(
            tenant=tenant,
            name='Kupac d.o.o.',
            tax_number='12345678901',
            partner_type='customer',
            status='active',
            address='Ulica 1',
            city='Šibenik',
            postal_code='22000',
        )
        invoice = Invoice.all_objects.create(
            tenant=tenant,
            company_to=partner,
            issue_date=date(2026, 6, 1),
            due_date=date(2026, 6, 15),
            status='sent',
            subtotal=Decimal('100.00'),
            tax_amount=Decimal('25.00'),
            total_amount=Decimal('125.00'),
            created_by=user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('100.00'),
            tax_rate=Decimal('25.00'),
        )

        bank_account = BankAccount.all_objects.create(
            tenant=tenant,
            account_name='Poslovni EUR',
            bank_name='OTP',
            account_number='HR6124070001100204771',
            iban='HR6124070001100204771',
        )
        payment = Payment.all_objects.create(
            tenant=tenant,
            payment_number='PAY-PIS-INT',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='pending',
            amount=Decimal('125.00'),
            bank_account=bank_account,
            related_invoice=invoice,
            payment_date=date(2026, 6, 10),
            created_by=user,
        )

        provider = BankProvider.objects.create(
            code='otp-int',
            name='OTP Integration',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=bank_account,
            status='connected',
            sync_success_streak=3,
        )
        order = PaymentOrder.all_objects.create(
            tenant=tenant,
            connection=connection,
            payment=payment,
            status='draft',
            debtor_iban='HR6124070001100204771',
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj d.o.o.',
            amount=Decimal('125.00'),
            currency='EUR',
            reference='PIS-INT',
        )

        return order, payment, invoice

    def test_sandbox_poll_flow_three_audits_one_event_one_journal(self, pis_flow_setup):
        """
        draft → submitted → sca_required → authorised → authorised → authorised

        Tri stvarne promjene (submit + dva polling koraka), dva no-op polla.
        Očekivanje: 3 audit zapisa, 1 PaymentExecuted, 1 JournalEntry.
        """
        order, payment, invoice = pis_flow_setup
        published: list[PaymentExecuted] = []

        def counting_publish(event: PaymentExecuted) -> None:
            published.append(event)
            real_publish(event)

        with patch('banking.services.payment_order_lifecycle.publish', side_effect=counting_publish):
            PaymentOrderLifecycle.transition(order, 'submitted', actor=ACTOR_SUBMIT)
            PaymentOrderLifecycle.transition(order, 'sca_required', actor=ACTOR_SUBMIT)
            PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)
            PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)
            PaymentOrderLifecycle.transition(order, 'authorised', actor=ACTOR_OTP_POLLING)

        order.refresh_from_db()
        payment.refresh_from_db()
        invoice.refresh_from_db()

        assert order.status == 'authorised'
        assert PaymentOrderTransition.objects.filter(order=order).count() == 3
        assert list(
            PaymentOrderTransition.objects.filter(order=order)
            .order_by('sequence')
            .values_list('from_status', 'to_status', 'actor'),
        ) == [
            ('draft', 'submitted', ACTOR_SUBMIT),
            ('submitted', 'sca_required', ACTOR_SUBMIT),
            ('sca_required', 'authorised', ACTOR_OTP_POLLING),
        ]
        assert len(published) == 1
        assert isinstance(published[0], PaymentExecuted)
        assert published[0].payment_order_id == order.pk
        payment_ct = ContentType.objects.get_for_model(payment)
        assert JournalEntry.all_objects.filter(
            source_content_type=payment_ct,
            source_object_id=payment.pk,
        ).count() == 1
        assert payment.status == 'completed'
        assert invoice.status == 'paid'
        assert order.posting_journal_entry_id is not None
        assert order.posted_at is not None
