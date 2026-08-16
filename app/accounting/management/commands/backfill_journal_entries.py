from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounting.services.posting import post_document
from expenses.models import Expense
from invoices.models import Invoice
from payments.models import Payment
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Retroaktivno knjiženje dokumenata za razdoblje.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True)
        parser.add_argument('--from', dest='date_from', type=str, default='2026-01-01')
        parser.add_argument('--to', dest='date_to', type=str, default='2026-05-31')
        parser.add_argument('--user', type=str, default='admin')

    def handle(self, *args, **options):
        tenant = Tenant.objects.get(slug=options['tenant'])
        date_from = datetime.strptime(options['date_from'], '%Y-%m-%d').date()
        date_to = datetime.strptime(options['date_to'], '%Y-%m-%d').date()
        User = get_user_model()
        user = User.objects.filter(username=options['user']).first() or User.objects.filter(is_superuser=True).first()
        if not user:
            self.stderr.write('Nema korisnika za knjiženje.')
            return

        counts = {'invoice_issued': 0, 'invoice_paid': 0, 'expense_approved': 0, 'expense_paid': 0}

        for invoice in Invoice.all_objects.filter(
            tenant=tenant,
            issue_date__gte=date_from,
            issue_date__lte=date_to,
            status__in=['sent', 'paid', 'overdue'],
        ).order_by('issue_date'):
            if post_document(tenant, invoice, 'invoice_issued', user):
                counts['invoice_issued'] += 1
            if invoice.status == 'paid':
                if post_document(tenant, invoice, 'invoice_paid', user):
                    counts['invoice_paid'] += 1

        for expense in Expense.all_objects.filter(
            tenant=tenant,
            expense_date__gte=date_from,
            expense_date__lte=date_to,
            status__in=['approved', 'paid'],
        ).order_by('expense_date'):
            if post_document(tenant, expense, 'expense_approved', user):
                counts['expense_approved'] += 1
            if expense.status == 'paid':
                if post_document(tenant, expense, 'expense_paid', user):
                    counts['expense_paid'] += 1

        for payment in Payment.all_objects.filter(
            tenant=tenant,
            payment_date__gte=date_from,
            payment_date__lte=date_to,
            status='completed',
            related_invoice__isnull=False,
        ):
            invoice = payment.related_invoice
            if invoice and invoice.status == 'paid':
                if post_document(tenant, invoice, 'invoice_paid', user):
                    counts['invoice_paid'] += 1

        self.stdout.write(self.style.SUCCESS(f'Backfill završen: {counts}'))
