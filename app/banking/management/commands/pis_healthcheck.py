from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.services.healthcheck import run_pis_healthcheck


class Command(BaseCommand):
    help = 'Provjera PIS preduvjeta (cert, connected veza, consent, sync streak).'

    def add_arguments(self, parser):
        parser.add_argument('--connection-id', type=int, default=None)
        parser.add_argument('--tenant', default=None)
        parser.add_argument('--min-sync-streak', type=int, default=3)

    def handle(self, *args, **options):
        report = run_pis_healthcheck(
            connection_id=options['connection_id'],
            tenant=options['tenant'],
            min_sync_streak=options['min_sync_streak'],
        )

        for item in report.checks:
            if item.passed:
                self._ok(item.label)
            else:
                self._fail(item.label)

        if not report.passed:
            raise CommandError('PIS healthcheck failed.')
        self.stdout.write(self.style.SUCCESS('PIS healthcheck OK.'))

    def _ok(self, message: str):
        self.stdout.write(self.style.SUCCESS(f'✔ {message}'))

    def _fail(self, message: str):
        self.stdout.write(self.style.ERROR(f'✘ {message}'))
