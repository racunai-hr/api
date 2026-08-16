from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Migrira BankPaymentInitiation zapise u PaymentOrder + PaymentExecution.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Samo ispiši koliko bi zapisa bilo migrirano.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            BankPaymentInitiation = apps.get_model('banking', 'BankPaymentInitiation')
        except LookupError:
            self.stdout.write(
                self.style.SUCCESS('BankPaymentInitiation je već uklonjen (migracija primijenjena).'),
            )
            return

        from banking.services.migrate_legacy_initiations import migrate_bank_payment_initiation

        initiations = list(
            BankPaymentInitiation.all_objects
            .select_related('connection', 'connection__bank_account', 'payment')
            .order_by('pk'),
        )
        if not initiations:
            self.stdout.write(self.style.SUCCESS('Nema legacy BankPaymentInitiation zapisa.'))
            return

        if options['dry_run']:
            self.stdout.write(f'Pronađeno {len(initiations)} legacy inicijacija za migraciju.')
            return

        migrated = 0
        skipped = 0
        for initiation in initiations:
            order = migrate_bank_payment_initiation(initiation)
            if order is None:
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'Preskočeno #{initiation.pk}: nedostaje debtor/creditor IBAN.',
                    ),
                )
                continue
            migrated += 1
            self.stdout.write(f'#{initiation.pk} → PaymentOrder #{order.pk}')

        self.stdout.write(
            self.style.SUCCESS(
                f'Migrirano {migrated} inicijacija, preskočeno {skipped}.',
            ),
        )
