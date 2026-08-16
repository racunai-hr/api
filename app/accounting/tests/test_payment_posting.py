from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from accounting.models import JournalEntry
from accounting.services.chart import provision_tenant_chart
from accounting.services.payment_posting import handle_payment_executed
from accounting.services.rrif_import import import_rrif_chart
from events.payment import PaymentExecuted
from invoices.models import Invoice, InvoiceItem
from partners.models import Partner
from payments.models import BankAccount, Payment
from tenants.models import Tenant


def _payment_journal_entries(payment):
    payment_ct = ContentType.objects.get_for_model(payment)
    return JournalEntry.all_objects.filter(
        source_content_type=payment_ct,
        source_object_id=payment.pk,
    )


@pytest.mark.django_db
class TestPaymentPostingService:
    @pytest.fixture(autouse=True)
    def setup_chart(self):
        import_rrif_chart(clear=True)

    @pytest.fixture
    def tenant(self):
        return Tenant.objects.create(slug='posting-test', name='Posting Test')

    @pytest.fixture
    def user(self):
        User = get_user_model()
        return User.objects.create_superuser(username='admin', password='test', email='admin@test.com')

    @pytest.fixture
    def invoice_and_payment(self, tenant, user):
        provision_tenant_chart(tenant)
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
            subtotal=Decimal('200.00'),
            tax_amount=Decimal('50.00'),
            total_amount=Decimal('250.00'),
            created_by=user,
        )
        InvoiceItem.objects.create(
            invoice=invoice,
            item_name='Usluga',
            quantity=1,
            unit_price=Decimal('200.00'),
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
            payment_number='PAY-POST-001',
            payment_type='incoming',
            payment_method='bank_transfer',
            status='pending',
            amount=Decimal('250.00'),
            bank_account=bank_account,
            related_invoice=invoice,
            payment_date=date(2026, 6, 10),
            created_by=user,
        )
        return invoice, payment

    def test_handle_payment_executed_creates_journal_entry(
        self,
        tenant,
        invoice_and_payment,
    ):
        from banking.provider_models import BankConnection, BankProvider, PaymentOrder

        invoice, payment = invoice_and_payment
        provider = BankProvider.objects.create(
            code='otp-post',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=payment.bank_account,
            status='connected',
        )
        order = PaymentOrder.all_objects.create(
            tenant=tenant,
            connection=connection,
            payment=payment,
            status='executed',
            debtor_iban='HR6124070001100204771',
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj',
            amount=Decimal('250.00'),
            currency='EUR',
        )

        event = PaymentExecuted(
            tenant_id=tenant.id,
            payment_order_id=order.pk,
            payment_id=payment.pk,
            bank_connection_id=connection.pk,
            provider_code='otp-post',
            amount=order.amount,
            currency='EUR',
            execution_date=date(2026, 6, 10),
            bank_reference='ref-1',
            correlation_id=uuid4(),
        )

        entry = handle_payment_executed(event)

        assert entry is not None
        assert entry.total_debit == Decimal('250.00')
        payment.refresh_from_db()
        invoice.refresh_from_db()
        order.refresh_from_db()
        assert payment.status == 'completed'
        assert invoice.status == 'paid'
        assert order.posting_journal_entry_id == entry.pk
        assert order.posted_at is not None

    def test_second_payment_executed_is_idempotent(
        self,
        tenant,
        invoice_and_payment,
    ):
        from banking.provider_models import BankConnection, BankProvider, PaymentOrder

        invoice, payment = invoice_and_payment
        provider = BankProvider.objects.create(
            code='otp-idem',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=payment.bank_account,
            status='connected',
        )
        order = PaymentOrder.all_objects.create(
            tenant=tenant,
            connection=connection,
            payment=payment,
            status='executed',
            debtor_iban='HR6124070001100204771',
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj',
            amount=Decimal('250.00'),
            currency='EUR',
        )

        event = PaymentExecuted(
            tenant_id=tenant.id,
            payment_order_id=order.pk,
            payment_id=payment.pk,
            bank_connection_id=connection.pk,
            provider_code='otp-idem',
            amount=order.amount,
            currency='EUR',
            execution_date=date(2026, 6, 10),
            bank_reference='ref-idem',
            correlation_id=uuid4(),
        )

        first_entry = handle_payment_executed(event)
        order.refresh_from_db()
        posted_at = order.posted_at
        journal_count = _payment_journal_entries(payment).count()

        second_entry = handle_payment_executed(event)
        order.refresh_from_db()

        assert second_entry == first_entry
        assert order.posted_at == posted_at
        assert _payment_journal_entries(payment).count() == journal_count

    def test_posting_failure_leaves_order_unmarked(
        self,
        tenant,
        invoice_and_payment,
        monkeypatch,
    ):
        from banking.provider_models import BankConnection, BankProvider, PaymentOrder

        invoice, payment = invoice_and_payment
        provider = BankProvider.objects.create(
            code='otp-fail',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=payment.bank_account,
            status='connected',
        )
        order = PaymentOrder.all_objects.create(
            tenant=tenant,
            connection=connection,
            payment=payment,
            status='executed',
            debtor_iban='HR6124070001100204771',
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj',
            amount=Decimal('250.00'),
            currency='EUR',
        )

        def raise_posting(*args, **kwargs):
            raise RuntimeError('posting failed')

        monkeypatch.setattr(
            'accounting.services.payment_posting.post_invoice_payment',
            raise_posting,
        )

        event = PaymentExecuted(
            tenant_id=tenant.id,
            payment_order_id=order.pk,
            payment_id=payment.pk,
            bank_connection_id=connection.pk,
            provider_code='otp-fail',
            amount=order.amount,
            currency='EUR',
            execution_date=date(2026, 6, 10),
            bank_reference='ref-fail',
            correlation_id=uuid4(),
        )

        with pytest.raises(RuntimeError, match='posting failed'):
            handle_payment_executed(event)

        order.refresh_from_db()
        assert order.posting_journal_entry_id is None
        assert order.posted_at is None

    def test_publish_triggers_handler(self, tenant, invoice_and_payment):
        from banking.provider_models import BankConnection, BankProvider, PaymentOrder
        from events.dispatcher import publish

        invoice, payment = invoice_and_payment
        provider = BankProvider.objects.create(
            code='otp-pub',
            name='OTP',
            environment='sandbox',
            iam_base='https://iam.example.test',
            api_base='https://api.example.test',
            service_host='api.example.test',
        )
        connection = BankConnection.all_objects.create(
            tenant=tenant,
            bank_provider=provider,
            bank_account=payment.bank_account,
            status='connected',
        )
        order = PaymentOrder.all_objects.create(
            tenant=tenant,
            connection=connection,
            payment=payment,
            status='executed',
            debtor_iban='HR6124070001100204771',
            creditor_iban='HR9910090071100100001',
            creditor_name='Primatelj',
            amount=Decimal('250.00'),
            currency='EUR',
        )

        event = PaymentExecuted(
            tenant_id=tenant.id,
            payment_order_id=order.pk,
            payment_id=payment.pk,
            bank_connection_id=connection.pk,
            provider_code='otp-pub',
            amount=order.amount,
            currency='EUR',
            execution_date=date(2026, 6, 10),
            bank_reference='ref-2',
            correlation_id=uuid4(),
        )

        publish(event)

        assert _payment_journal_entries(payment).exists()
        payment.refresh_from_db()
        assert payment.status == 'completed'
