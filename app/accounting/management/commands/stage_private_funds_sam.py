"""Stage SaM private-funds events (ADR-0026) on finestar tenant."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.models import Deposit, PrivateFundsClaim, SubledgerItem
from domains.finance.services.private_funds import (
    create_claim,
    ensure_partner_ante_vrcan,
    post_claim,
)
from domains.finance.services.subledger import get_subledger_item_for_source
from expenses.models import Expense
from tenants.models import Tenant, TenantMembership


class Command(BaseCommand):
    help = (
        'Ensure Partner Ante + post PrivateFundsClaim for SaM remainder 9900 '
        'and deposit funding 5000 (finestar staging).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', default='finestar')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(slug=options['tenant']).first()
        if tenant is None:
            raise CommandError(f'Tenant {options["tenant"]} not found.')

        User = get_user_model()
        membership = (
            TenantMembership.objects.filter(tenant=tenant, role='owner')
            .select_related('user')
            .first()
        )
        user = membership.user if membership else User.objects.filter(is_superuser=True).first()
        if user is None:
            raise CommandError('Nema owner/superuser za created_by na JE.')

        with transaction.atomic():
            ante = ensure_partner_ante_vrcan(tenant=tenant, user=user)
            self.stdout.write(f'Partner Ante id={ante.pk} OIB={ante.tax_number}')

            expense = Expense.all_objects.filter(
                tenant=tenant, expense_number='T-2026-0011',
            ).first()
            if expense is None:
                raise CommandError('Expense T-2026-0011 not found.')
            ap = get_subledger_item_for_source(tenant, expense)
            if ap is None:
                raise CommandError('No SubledgerItem for T-2026-0011.')

            deposit = Deposit.all_objects.filter(
                tenant=tenant, number='KAU-202608-0001',
            ).first()
            if deposit is None:
                raise CommandError('Deposit KAU-202608-0001 not found.')

            if options['dry_run']:
                self.stdout.write(
                    f'dry-run: AP open={ap.open_amount} status={ap.status}; '
                    f'deposit={deposit.status}; ante={ante.pk}'
                )
                transaction.set_rollback(True)
                return

            # supplier_payment for remaining AP
            if ap.open_amount > 0 and ap.status in ('open', 'partial'):
                existing_pay = PrivateFundsClaim.all_objects.filter(
                    tenant=tenant,
                    claim_type=PrivateFundsClaim.CLAIM_SUPPLIER_PAYMENT,
                    related_object_id=expense.pk,
                    status=PrivateFundsClaim.STATUS_POSTED,
                ).first()
                if existing_pay is None:
                    dto = create_claim(
                        tenant=tenant,
                        user=user,
                        data={
                            'partner_id': ante.pk,
                            'claim_type': 'supplier_payment',
                            'amount': str(ap.open_amount),
                            'claim_date': date(2026, 7, 30),
                            'related_type': 'expense',
                            'related_id': expense.pk,
                            'reference': 'SaM T-2026-0011 — Ante privatno 9900',
                        },
                    )
                    post_claim(tenant=tenant, claim_id=dto['id'], user=user)
                    self.stdout.write(self.style.SUCCESS(f'supplier_payment posted {dto["number"]}'))
                else:
                    self.stdout.write(f'supplier_payment already posted {existing_pay.number}')
            else:
                self.stdout.write(f'AP already closed open={ap.open_amount}')

            existing_fund = PrivateFundsClaim.all_objects.filter(
                tenant=tenant,
                claim_type=PrivateFundsClaim.CLAIM_DEPOSIT_FUNDING,
                related_object_id=deposit.pk,
                status=PrivateFundsClaim.STATUS_POSTED,
            ).first()
            if existing_fund is None:
                dto = create_claim(
                    tenant=tenant,
                    user=user,
                    data={
                        'partner_id': ante.pk,
                        'claim_type': 'deposit_funding',
                        'amount': str(deposit.amount),
                        'claim_date': deposit.deposit_date,
                        'related_type': 'deposit',
                        'related_id': deposit.pk,
                        'reference': 'SaM kaucija — Ante financirao 5000',
                    },
                )
                post_claim(tenant=tenant, claim_id=dto['id'], user=user)
                self.stdout.write(self.style.SUCCESS(f'deposit_funding posted {dto["number"]}'))
            else:
                self.stdout.write(f'deposit_funding already posted {existing_fund.number}')

            expense.refresh_from_db()
            ap = get_subledger_item_for_source(tenant, expense)
            ante_open = SubledgerItem.all_objects.filter(
                tenant=tenant,
                partner=ante,
                status__in=('open', 'partial'),
            )
            total = sum((i.open_amount for i in ante_open), Decimal('0'))
            self.stdout.write(
                f'Acceptance: expense={expense.status} AP={ap.open_amount if ap else None} '
                f'deposit={deposit.status} ante_open={total}'
            )
            if expense.status != 'paid' or (ap and ap.open_amount != 0) or total != Decimal('14900.00'):
                raise CommandError(
                    f'Acceptance failed: expense={expense.status} AP={ap} ante_open={total}'
                )
            self.stdout.write(self.style.SUCCESS('SaM private-funds acceptance OK (14.900)'))
