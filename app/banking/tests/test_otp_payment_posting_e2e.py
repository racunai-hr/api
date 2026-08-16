from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.rrif_import import import_rrif_chart
from banking.provider_models import BankConnection, BankProvider, PaymentOrder
from banking.services.pis_posting_e2e import run_payment_posting_e2e
from events.payment import PaymentExecuted
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant


@pytest.mark.django_db
class TestOtpPaymentPostingE2e:
    @pytest.fixture(autouse=True)
    def setup_chart(self):
        import_rrif_chart(clear=True)

    @pytest.fixture
    def posting_order(self, settings):
        settings.PIS_SANDBOX_ALLOW_POSTING_ON_AUTHORISED = True

        tenant = Tenant.objects.create(slug='posting-e2e', name='Posting E2E')
        provision_tenant_chart(tenant)

        User = get_user_model()
        user = User.objects.create_superuser(
            username='posting-e2e-admin',
            password='test',
            email='posting-e2e@test.com',
        )

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
            payment_number='PAY-POST-E2E',
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
            code='otp-post-e2e',
            name='OTP Posting E2E',
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
        )
        order = PaymentOrder.all_objects.create(
            tenant=tenant,
            connection=connection,
            payment=payment,
            status='authorised',
            debtor_iban='HR6124070001100204771',
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj d.o.o.',
            amount=Decimal('125.00'),
            currency='EUR',
            reference='POST-E2E',
        )
        return order, payment, invoice

    def test_run_payment_posting_e2e_idempotent(self, posting_order):
        order, payment, invoice = posting_order

        report = run_payment_posting_e2e(order)

        assert report.passed, '\n'.join(
            f'{check.name}: {check.detail}' for check in report.checks if not check.passed
        )

        order.refresh_from_db()
        payment.refresh_from_db()
        invoice.refresh_from_db()

        assert order.posting_journal_entry_id is not None
        assert order.posted_at is not None
        payment_ct = ContentType.objects.get_for_model(payment)
        assert JournalEntry.all_objects.filter(
            source_content_type=payment_ct,
            source_object_id=payment.pk,
        ).count() == 1
        assert payment.status == 'completed'
        assert invoice.status == 'paid'

    def test_resolve_posting_e2e_order_by_id(self, posting_order):
        from banking.services.pis_posting_e2e import resolve_posting_e2e_order

        order, _, _ = posting_order
        resolved = resolve_posting_e2e_order(order_id=order.pk)
        assert resolved.pk == order.pk

    def test_build_event_from_authorised_order(self, posting_order):
        from banking.services.payment_order_lifecycle import PaymentOrderLifecycle

        order, payment, _ = posting_order
        event = PaymentOrderLifecycle._build_payment_executed_event(order)

        assert isinstance(event, PaymentExecuted)
        assert event.payment_order_id == order.pk
        assert event.payment_id == payment.pk
