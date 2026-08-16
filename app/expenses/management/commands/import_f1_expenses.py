from django.core.management.base import BaseCommand, CommandError

from expenses.models import ExpensePayer, ExpenseSource, SettlementMethod
from expenses.parsers.f1_csv import F1CsvParser
from expenses.services.f1_supplier_extract import resolve_csv_paths
from expenses.services.import_service import import_expense_rows
from tenants.models import Tenant


class Command(BaseCommand):
    help = 'Uvezi F1 CSV troškove s opcionalnim overrideom podmirenja i statusa.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, required=True, help='Tenant slug (npr. finestar)')
        parser.add_argument(
            '--path',
            type=str,
            required=True,
            help='Putanja do CSV datoteke ili direktorija s F1 CSV-ovima',
        )
        parser.add_argument(
            '--status',
            type=str,
            default='approved',
            choices=['draft', 'submitted', 'approved', 'paid', 'rejected'],
            help='Status novih troškova (default: approved)',
        )
        parser.add_argument(
            '--settlement-method',
            type=str,
            default='',
            choices=['', *SettlementMethod.values],
            help='Način podmirenja u tvrtki (npr. private_card)',
        )
        parser.add_argument(
            '--paid-by-oib',
            type=str,
            default='',
            help='OIB platitelja za privatno podmirenje',
        )
        parser.add_argument(
            '--reimbursement-status',
            type=str,
            default='',
            help='Status nadoknade (default: auto iz settlement_method)',
        )
        parser.add_argument('--dry-run', action='store_true', help='Analiziraj bez spremanja u bazu')
        parser.add_argument(
            '--user',
            type=str,
            default='',
            help='Username korisnika koji uvozi (default: prvi superuser)',
        )

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if not tenant:
            raise CommandError(f'Tenant "{options["tenant"]}" nije pronađen.')

        user = None
        if options['user']:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.filter(username=options['user']).first()
            if not user:
                raise CommandError(f'Korisnik "{options["user"]}" nije pronađen.')
        else:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.filter(is_superuser=True).first()
            if not user:
                raise CommandError('Nema superusera za import.')

        paid_by = None
        if options['paid_by_oib']:
            paid_by = ExpensePayer.all_objects.filter(
                tenant=tenant,
                oib=options['paid_by_oib'],
                is_active=True,
            ).first()
            if not paid_by:
                raise CommandError(f'Platitelj s OIB-om "{options["paid_by_oib"]}" nije pronađen.')

        try:
            csv_paths = resolve_csv_paths(options['path'])
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc

        rows = []
        parse_errors = []
        for csv_path in csv_paths:
            content = csv_path.read_bytes()
            parse_result = F1CsvParser().parse(content, filename=csv_path.name)
            rows.extend(parse_result.rows)
            parse_errors.extend(parse_result.parse_errors)

        for err in parse_errors:
            self.stderr.write(self.style.ERROR(f'Red {err.row_number} — {err.message}'))

        result = import_expense_rows(
            tenant=tenant,
            user=user,
            rows=rows,
            source=ExpenseSource.F1_CSV,
            filename=','.join(path.name for path in csv_paths),
            dry_run=options['dry_run'],
            status=options['status'],
            settlement_method=options['settlement_method'],
            paid_by=paid_by,
            reimbursement_status=options['reimbursement_status'],
        )

        mode = ' (dry-run)' if options['dry_run'] else ''
        self.stdout.write(
            f'{result.rows_total} redaka | {result.created} novih | '
            f'{result.duplicates} duplikata | {result.errors} grešaka{mode}'
        )
        if result.duplicate_receipts:
            self.stdout.write('Duplikati:')
            for receipt in result.duplicate_receipts:
                self.stdout.write(f'  {receipt}')
        for issue in result.error_details:
            self.stderr.write(self.style.ERROR(f'Red {issue.row_number} — {issue.message}'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry-run — nema promjena u bazi.'))
        elif result.created:
            self.stdout.write(self.style.SUCCESS('Import dovršen.'))
