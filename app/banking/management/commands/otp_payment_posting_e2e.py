from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.services.pis_posting_e2e import resolve_posting_e2e_order, run_payment_posting_e2e


class Command(BaseCommand):
    help = (
        'E2E provjera PIS knjiženja: publish PaymentExecuted, provjera JournalEntry '
        'i idempotentnog drugog poziva (sandbox: authorised + flag).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--order-id',
            type=int,
            default=None,
            help='PaymentOrder PK (npr. authorised nalog #2 ili #4 u sandboxu)',
        )
        parser.add_argument('--tenant', default=None, help='Tenant slug za odabir zadnjeg naloga')

    def handle(self, *args, **options):
        try:
            order = resolve_posting_e2e_order(
                order_id=options['order_id'],
                tenant_slug=options['tenant'],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            f'PIS posting E2E: order #{order.pk} ({order.tenant.slug}), status={order.status}',
        )
        report = run_payment_posting_e2e(order)
        for check in report.checks:
            if check.passed:
                self.stdout.write(self.style.SUCCESS(f'✔ {check.name}: {check.detail}'))
            else:
                self.stdout.write(self.style.ERROR(f'✘ {check.name}: {check.detail}'))

        if not report.passed:
            raise CommandError('PIS posting E2E provjera nije prošla.')
        self.stdout.write(self.style.SUCCESS('PIS posting E2E OK.'))
