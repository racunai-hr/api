"""Audit duplicate BankTransaction match targets before/after 0017 constraints."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from banking.models import BankTransaction
from banking.services.match_duplicates import (
    duplicate_matched_journal_groups,
    duplicate_matched_payment_groups,
)


class Command(BaseCommand):
    help = (
        'Prikaži BankTransaction redove gdje isti Payment ili JournalEntry '
        'ima više od jedne povezane transakcije (preflight za 0017 unique).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            type=str,
            default='',
            help='Opcionalni tenant slug; bez vrijednosti skenira sve tenante.',
        )

    def handle(self, *args, **options):
        qs = BankTransaction.all_objects.all()
        tenant_slug = (options.get('tenant') or '').strip()
        if tenant_slug:
            qs = qs.filter(tenant__slug=tenant_slug)

        payment_groups = duplicate_matched_payment_groups(qs)
        journal_groups = duplicate_matched_journal_groups(qs)

        if not payment_groups and not journal_groups:
            self.stdout.write(self.style.SUCCESS('Nema duplikata matched_payment ni matched_journal_entry.'))
            return

        if payment_groups:
            self.stdout.write(self.style.WARNING(f'Duplikati matched_payment: {len(payment_groups)} grupa'))
            for row in payment_groups:
                payment_id = row['matched_payment_id']
                tx_ids = list(
                    qs.filter(matched_payment_id=payment_id)
                    .order_by('id')
                    .values_list('id', flat=True)
                )
                self.stdout.write(f'  payment_id={payment_id} tx_ids={tx_ids}')

        if journal_groups:
            self.stdout.write(
                self.style.WARNING(f'Duplikati matched_journal_entry: {len(journal_groups)} grupa')
            )
            for row in journal_groups:
                je_id = row['matched_journal_entry_id']
                tx_ids = list(
                    qs.filter(matched_journal_entry_id=je_id)
                    .order_by('id')
                    .values_list('id', flat=True)
                )
                self.stdout.write(f'  journal_entry_id={je_id} tx_ids={tx_ids}')

        self.stdout.write(
            self.style.ERROR(
                'Razrijesi duplikate prije migracije 0017 (fail-closed unique constraints).'
            )
        )
