"""Proknjiži i uskladi kunske izvode Fine Star (temeljni kapital, ATM, naknade, kamate)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from banking.services.hrk_statement_booking import (
    FINASTAR_SLUG,
    HRK_ACCOUNT_IBAN,
    HrkLineBookingError,
    book_hrk_statement_lines,
)
from payments.models import BankAccount
from tenants.models import Tenant


class Command(BaseCommand):
    help = (
        'Knjiži stavke kunskog računa Fine Star u glavnu knjigu i usklađuje izvode. '
        'Idempotentno: postojeća temeljnica s oznakom [hrk-izvod:btx-…] se ne duplicira.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--tenant', type=str, default=FINASTAR_SLUG)

    def handle(self, *args, **options):
        slug = options['tenant']
        if slug != FINASTAR_SLUG:
            raise CommandError(f'Naredba je samo za tenant {FINASTAR_SLUG}.')
        try:
            tenant = Tenant.objects.get(slug=slug)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f'Tenant ne postoji: {slug}') from exc

        account = BankAccount.all_objects.filter(tenant=tenant, iban=HRK_ACCOUNT_IBAN).first()
        if account is None:
            raise CommandError(f'Nema kunskog računa {HRK_ACCOUNT_IBAN}.')

        User = get_user_model()
        user = User.objects.filter(is_superuser=True, is_active=True).order_by('id').first()
        if user is None:
            raise CommandError('Nema aktivnog superusera za knjiženje.')

        try:
            outcomes = book_hrk_statement_lines(tenant, user, account)
        except HrkLineBookingError as exc:
            raise CommandError(str(exc)) from exc

        for line in outcomes:
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS(f'Gotovo: {len(outcomes)} stavki.'))
