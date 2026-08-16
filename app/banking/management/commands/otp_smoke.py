from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.services.otp_smoke import OtpSmokeReport, run_otp_smoke


class Command(BaseCommand):
    help = 'OTP sandbox readiness smoke test (healthcheck → AIS → PIS → posting).'

    def add_arguments(self, parser):
        parser.add_argument('--tenant', default=None)
        parser.add_argument('--connection-id', type=int, default=None)
        parser.add_argument('--order-id', type=int, default=None)
        parser.add_argument('--provider', default='otp_sandbox')
        parser.add_argument('--min-sync-streak', type=int, default=3)
        parser.add_argument(
            '--submit-test-payment',
            action='store_true',
            help='Aktivira korak Payment (read-only smoke default)',
        )
        parser.add_argument(
            '--run-sync',
            action='store_true',
            help='AIS E2E s dva synca',
        )
        parser.add_argument(
            '--skip-posting',
            action='store_true',
            help='Preskoči Posting korak',
        )
        parser.add_argument(
            '--from',
            dest='from_step',
            default='healthcheck',
            choices=['healthcheck', 'ais', 'pis', 'posting'],
            help='Prvi korak koji se izvršava; prethodni se preskaču',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Detaljni check output ispod summaryja',
        )

    def handle(self, *args, **options):
        report = run_otp_smoke(
            tenant=options['tenant'],
            connection_id=options['connection_id'],
            order_id=options['order_id'],
            provider=options['provider'],
            min_sync_streak=options['min_sync_streak'],
            submit_test_payment=options['submit_test_payment'],
            run_sync=options['run_sync'],
            skip_posting=options['skip_posting'],
            from_step=options['from_step'],
        )

        self.stdout.write('')
        self.stdout.write('OTP Sandbox Readiness')
        self.stdout.write('')

        for check in report.checks:
            self._write_check_line(check)

        self.stdout.write('')
        self.stdout.write('Summary')
        self.stdout.write(f'PASS: {report.pass_count}')
        self.stdout.write(f'FAIL: {report.fail_count}')
        self.stdout.write(f'ERROR: {report.error_count}')
        self.stdout.write(f'SKIP: {report.skipped}')
        self.stdout.write('')

        if report.passed:
            self.stdout.write(
                self.style.SUCCESS(
                    f'READY ({report.pass_count} passed, {report.skipped} skipped)',
                ),
            )
        else:
            self.stdout.write(
                self.style.ERROR('NOT READY'),
            )

        if options['verbose']:
            self._write_verbose(report)

        if not report.passed:
            raise CommandError('OTP smoke NOT READY.')

    def _write_check_line(self, check) -> None:
        duration = f'{check.duration_ms} ms' if check.duration_ms is not None else ''
        if check.status == 'pass':
            line = f'✓ {check.label}'
            if duration:
                line = f'{line:<32} {duration}'
            self.stdout.write(self.style.SUCCESS(line))
        elif check.status == 'skip':
            suffix = ' (skipped)'
            self.stdout.write(self.style.WARNING(f'– {check.label}{suffix}'))
        elif check.status == 'fail':
            line = f'✗ {check.label} FAIL'
            if duration:
                line = f'{line:<32} {duration}'
            self.stdout.write(self.style.ERROR(line))
            if check.detail:
                self.stdout.write(f'  {check.detail}')
        else:
            line = f'✗ {check.label} ERROR'
            if duration:
                line = f'{line:<32} {duration}'
            self.stdout.write(self.style.ERROR(line))
            if check.detail:
                self.stdout.write(f'  {check.detail}')

    def _write_verbose(self, report: OtpSmokeReport) -> None:
        self.stdout.write('')
        self.stdout.write('Verbose details')
        self.stdout.write('')

        if report.healthcheck_otp:
            self.stdout.write('OTP healthcheck:')
            for item in report.healthcheck_otp.checks:
                self._write_verbose_item(item.passed, item.label)

        if report.healthcheck_pis:
            self.stdout.write('PIS healthcheck:')
            for item in report.healthcheck_pis.checks:
                self._write_verbose_item(item.passed, item.label)

        if report.ais_report:
            self.stdout.write('AIS E2E:')
            for item in report.ais_report.checks:
                self._write_verbose_item(item.passed, f'{item.name}: {item.detail}')

        if report.pis_report:
            self.stdout.write('PIS E2E:')
            for item in report.pis_report.checks:
                self._write_verbose_item(item.passed, f'{item.name}: {item.detail}')

        if report.posting_report:
            self.stdout.write('Posting E2E:')
            for item in report.posting_report.checks:
                self._write_verbose_item(item.passed, f'{item.name}: {item.detail}')

    def _write_verbose_item(self, passed: bool, text: str) -> None:
        if passed:
            self.stdout.write(self.style.SUCCESS(f'  ✔ {text}'))
        else:
            self.stdout.write(self.style.ERROR(f'  ✘ {text}'))
