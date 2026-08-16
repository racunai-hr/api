import os

from django.contrib.auth.models import User
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from expenses.models import Expense, ExpenseAttachment
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Povezuje PDF datoteku s troškom kao ExpenseAttachment.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--expense-number', type=str, required=True, help='Broj troška (npr. T-2026-0001)')
        parser.add_argument('--file', type=str, required=True, help='Putanja do PDF datoteke')
        parser.add_argument('--user', type=str, default='', help='Username koji je učitao (default: prvi superuser)')

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if not tenant:
            raise CommandError(f'Tenant "{options["tenant"]}" nije pronađen.')

        expense = Expense.all_objects.filter(
            tenant=tenant,
            expense_number=options['expense_number'],
        ).first()
        if not expense:
            raise CommandError(
                f'Trošak "{options["expense_number"]}" nije pronađen za tenant {tenant.slug}.'
            )

        source = os.path.abspath(options['file'])
        if not os.path.isfile(source):
            raise CommandError(f'Datoteka ne postoji: {source}')
        if not source.lower().endswith('.pdf'):
            raise CommandError('Dozvoljen je samo PDF format.')

        user = None
        if options['user']:
            user = User.objects.filter(username=options['user']).first()
            if not user:
                raise CommandError(f'Korisnik "{options["user"]}" nije pronađen.')
        else:
            user = User.objects.filter(is_superuser=True).order_by('id').first()
        if not user:
            raise CommandError('Nema korisnika za uploaded_by.')

        original_filename = os.path.basename(source)
        with open(source, 'rb') as handle:
            attachment = ExpenseAttachment(
                tenant=tenant,
                expense=expense,
                original_filename=original_filename,
                uploaded_by=user,
            )
            attachment.file.save(original_filename, File(handle), save=True)

        self.stdout.write(
            self.style.SUCCESS(
                f'Prilog kreiran: {attachment} → trošak {expense.expense_number} (id={attachment.pk})'
            )
        )
