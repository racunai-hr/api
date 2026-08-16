from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.models import JournalEntry
from expenses.models import Expense, ExpensePayer, ReimbursementStatus, SettlementMethod
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Postavi podmirenje troška i ponovno knjiži expense_paid (storno pogrešnih knjiženja).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument('--expense-number', type=str, required=True, help='Broj troška (npr. T-2026-0001)')
        parser.add_argument(
            '--settlement-method',
            type=str,
            required=True,
            choices=SettlementMethod.values,
            help='Način podmirenja u tvrtki',
        )
        parser.add_argument('--paid-by-oib', type=str, default='', help='OIB platitelja (za privatno podmirenje)')
        parser.add_argument(
            '--status',
            type=str,
            default='',
            choices=['', 'draft', 'submitted', 'approved', 'paid', 'rejected'],
            help='Opcionalni novi status troška',
        )
        parser.add_argument('--dry-run', action='store_true', help='Pregled bez promjena u bazi')
        parser.add_argument(
            '--user',
            type=str,
            default='',
            help='Username korisnika za knjiženje (default: prvi superuser)',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if not tenant:
            raise CommandError(f'Tenant "{options["tenant"]}" nije pronađen.')

        expense = Expense.all_objects.filter(
            tenant=tenant,
            expense_number=options['expense_number'],
        ).first()
        if not expense:
            raise CommandError(f'Trošak "{options["expense_number"]}" nije pronađen.')

        paid_by = None
        if options['paid_by_oib']:
            paid_by = ExpensePayer.all_objects.filter(
                tenant=tenant,
                oib=options['paid_by_oib'],
                is_active=True,
            ).first()
            if not paid_by:
                raise CommandError(f'Platitelj s OIB-om "{options["paid_by_oib"]}" nije pronađen.')

        private_methods = {SettlementMethod.PRIVATE_CARD, SettlementMethod.PRIVATE_CASH}
        target_status = options['status'] or expense.status
        if options['settlement_method'] in private_methods and target_status == 'paid' and not paid_by:
            raise CommandError('paid_by_oib je obavezan za privatno podmirenje plaćenog troška.')

        User = get_user_model()
        user = User.objects.filter(username=options['user']).first() if options['user'] else None
        if not user:
            user = User.objects.filter(is_superuser=True).first()
        if not user:
            raise CommandError('Nema korisnika za knjiženje.')

        ct = ContentType.objects.get_for_model(Expense)
        paid_entries = JournalEntry.all_objects.filter(
            tenant=tenant,
            source_content_type=ct,
            source_object_id=expense.pk,
            description__startswith='[expense_paid]',
            status='posted',
        )

        self.stdout.write(
            f'Trošak {expense.expense_number}: settlement={options["settlement_method"]}, '
            f'paid_by={paid_by}, status={target_status}'
        )
        self.stdout.write(f'Pronađeno {paid_entries.count()} expense_paid knjiženja za storno.')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry-run — nema promjena u bazi.'))
            return

        with transaction.atomic():
            for entry in paid_entries:
                entry.reverse(user)
                self.stdout.write(f'  Stornirano: {entry.entry_number}')

            expense.settlement_method = options['settlement_method']
            expense.paid_by = paid_by
            if options['status']:
                expense.status = options['status']
            if options['settlement_method'] in private_methods and expense.status == 'paid':
                expense.reimbursement_status = ReimbursementStatus.PENDING
            else:
                expense.reimbursement_status = ReimbursementStatus.NOT_REQUIRED
            expense.save()

        self.stdout.write(self.style.SUCCESS('Podmirenje i knjiženje ažurirani.'))
