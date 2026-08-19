"""Audit duplicate BankTransaction match targets before 0017 constraints."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from banking.models import BankTransaction


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

        payment_dupes = (
            qs.filter(matched_payment_id__isnull=False)
            .values('matched_payment_id')
            .annotate(c=Count('id'))
            .filter(c__gt=1)
            .order_by('matched_payment_id')
        )
        journal_dupes = (
            qs.filter(matched_journal_entry_id__isnull=False)
            .values('matched_journal_entry_id')
            .annotate(c=Count('id'))
            .filter(c__gt=1)
            .order_by('matched_journal_entry_id')
        )

        payment_groups = list(payment_dupes)
        journal_groups = list(journal_dupes)

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
