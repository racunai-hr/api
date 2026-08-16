from __future__ import annotations

import pytest

from banking.otp.pis import map_otp_payment_status, resolve_payment_order_status
from banking.services.pis_e2e import PisE2eReport, run_pis_e2e_checks


class TestOtpPaymentStatusMapping:
    def test_maps_rcvd_to_submitted(self):
        assert map_otp_payment_status('RCVD') == 'submitted'

    def test_maps_acsc_to_executed(self):
        assert map_otp_payment_status('ACSC') == 'executed'

    def test_resolve_keeps_authorised_on_rcvd(self):
        assert resolve_payment_order_status(
            'authorised',
            transaction_status='RCVD',
            sca_status='finalised',
        ) == 'authorised'

    def test_resolve_promotes_to_authorised_after_sca(self):
        assert resolve_payment_order_status(
            'sca_required',
            transaction_status='RCVD',
            sca_status='finalised',
        ) == 'authorised'

    def test_resolve_never_downgrades(self):
        assert resolve_payment_order_status(
            'authorised',
            transaction_status='RCVD',
        ) == 'authorised'


class TestPisE2eReport:
    def test_all_passed(self):
        report = PisE2eReport()
        report.add('a', True)
        assert report.passed


@pytest.mark.otp_pis_integration
@pytest.mark.django_db
class TestOtpPisE2eIntegration:
    def test_pis_e2e_prerequisites(self):
        from banking.services.pis_e2e import resolve_pis_e2e_connection

        try:
            connection = resolve_pis_e2e_connection(tenant_slug='otp-company-no1')
        except Exception:
            pytest.skip('Nema connected veze za otp-company-no1.')

        report = run_pis_e2e_checks(connection, submit_test_payment=False)
        failures = [c for c in report.checks if not c.passed]
        assert not failures, '\n'.join(f'{c.name}: {c.detail}' for c in failures)
