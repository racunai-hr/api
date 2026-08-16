from __future__ import annotations

import pytest

from banking.services.ais_e2e import AisE2eReport, run_ais_e2e_checks


class TestAisE2eReport:
    def test_report_passed_when_all_checks_ok(self):
        report = AisE2eReport()
        report.add('a', True)
        report.add('b', True)
        assert report.passed

    def test_report_failed_when_any_check_fails(self):
        report = AisE2eReport()
        report.add('a', True)
        report.add('b', False, 'detail')
        assert not report.passed


@pytest.mark.otp_integration
@pytest.mark.django_db
class TestOtpAisE2eIntegration:
    """Live provjera protiv OTP sandboxa — pokreni ručno nakon connect flowa."""

    def test_ais_e2e_on_connected_connection(self):
        from banking.services.ais_e2e import resolve_e2e_connection

        try:
            connection = resolve_e2e_connection(tenant_slug='otp-company-no1')
        except Exception:
            pytest.skip('Nema connected veze za otp-company-no1.')

        report = run_ais_e2e_checks(connection, run_sync=True)
        failures = [c for c in report.checks if not c.passed]
        assert not failures, '\n'.join(f'{c.name}: {c.detail}' for c in failures)
