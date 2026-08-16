from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from banking.services.ais_e2e import AisE2eReport
from banking.services.healthcheck import HealthcheckReport
from banking.services.otp_smoke import OtpSmokeReport, SmokeCheck, run_otp_smoke
from banking.services.pis_e2e import PisE2eReport
from banking.services.pis_posting_e2e import PisPostingE2eReport


def _healthcheck_report(passed: bool = True) -> HealthcheckReport:
    report = HealthcheckReport()
    report.add('check', passed)
    return report


def _ais_report(**checks: tuple[bool, str]) -> AisE2eReport:
    report = AisE2eReport()
    defaults = {
        'oauth_authorize_redirect': (True, 'HTTP 302'),
        'consent_authorized': (True, 'status=valid'),
        'accounts_fetched': (True, 'count=1'),
        'transactions_endpoint': (True, 'HTTP 200'),
    }
    defaults.update(checks)
    for name, (passed, detail) in defaults.items():
        report.add(name, passed, detail)
    return report


def _pis_report(*, payment_passed: bool | None = None) -> PisE2eReport:
    report = PisE2eReport()
    report.add('pis_healthcheck_p12', True)
    if payment_passed is not None:
        report.add('payment_initiated', payment_passed, 'paymentId=abc')
    return report


def _posting_report(passed: bool = True) -> PisPostingE2eReport:
    report = PisPostingE2eReport()
    report.add('payment_linked', passed)
    return report


SUMMARY_LABELS = [
    'Healthcheck',
    'OAuth',
    'Consent',
    'Accounts',
    'Transactions',
    'Payment',
    'Posting',
]


class TestOtpSmokeReport:
    def test_passed_when_all_pass_or_skip(self):
        report = OtpSmokeReport(
            checks=[
                SmokeCheck('Healthcheck', 'pass'),
                SmokeCheck('Payment', 'skip'),
            ],
        )
        assert report.passed
        assert report.pass_count == 1
        assert report.skipped == 1
        assert report.fail_count == 0
        assert report.error_count == 0

    def test_not_passed_on_fail(self):
        report = OtpSmokeReport(checks=[SmokeCheck('OAuth', 'fail')])
        assert not report.passed
        assert report.fail_count == 1

    def test_not_passed_on_error(self):
        report = OtpSmokeReport(checks=[SmokeCheck('OAuth', 'error')])
        assert not report.passed
        assert report.error_count == 1


@patch('banking.services.otp_smoke.run_payment_posting_e2e')
@patch('banking.services.otp_smoke.resolve_posting_e2e_order')
@patch('banking.services.otp_smoke.run_pis_e2e_checks')
@patch('banking.services.otp_smoke.resolve_pis_e2e_connection')
@patch('banking.services.otp_smoke.run_ais_e2e_checks')
@patch('banking.services.otp_smoke.resolve_e2e_connection')
@patch('banking.services.otp_smoke.run_pis_healthcheck')
@patch('banking.services.otp_smoke.run_otp_healthcheck')
class TestRunOtpSmoke:
    def _setup_all_pass(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_otp_hc.return_value = _healthcheck_report(True)
        mock_pis_hc.return_value = _healthcheck_report(True)
        mock_resolve_ais.return_value = MagicMock()
        mock_ais.return_value = _ais_report()
        mock_resolve_pis.return_value = MagicMock()
        mock_pis.return_value = _pis_report()
        mock_resolve_posting.return_value = MagicMock()
        mock_posting.return_value = _posting_report(True)

    def test_full_smoke_all_pass(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        self._setup_all_pass(
            mock_otp_hc,
            mock_pis_hc,
            mock_resolve_ais,
            mock_ais,
            mock_resolve_pis,
            mock_pis,
            mock_resolve_posting,
            mock_posting,
        )

        report = run_otp_smoke(tenant='otp-company-no1')

        assert report.passed
        assert [c.label for c in report.checks] == SUMMARY_LABELS
        assert report.pass_count == 6
        assert report.skipped == 1  # Payment without --submit-test-payment
        assert all(c.duration_ms is not None for c in report.checks if c.status != 'skip')

    def test_payment_skip_without_submit_flag(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        self._setup_all_pass(
            mock_otp_hc,
            mock_pis_hc,
            mock_resolve_ais,
            mock_ais,
            mock_resolve_pis,
            mock_pis,
            mock_resolve_posting,
            mock_posting,
        )

        report = run_otp_smoke(tenant='otp-company-no1')

        payment = next(c for c in report.checks if c.label == 'Payment')
        assert payment.status == 'skip'
        mock_pis.assert_called_once()
        assert mock_pis.call_args.kwargs['submit_test_payment'] is False

    def test_payment_pass_with_submit_flag(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        self._setup_all_pass(
            mock_otp_hc,
            mock_pis_hc,
            mock_resolve_ais,
            mock_ais,
            mock_resolve_pis,
            mock_pis,
            mock_resolve_posting,
            mock_posting,
        )
        mock_pis.return_value = _pis_report(payment_passed=True)

        report = run_otp_smoke(tenant='otp-company-no1', submit_test_payment=True)

        payment = next(c for c in report.checks if c.label == 'Payment')
        assert payment.status == 'pass'
        assert report.skipped == 0

    def test_ais_fail_maps_to_fail_not_error(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_otp_hc.return_value = _healthcheck_report(True)
        mock_pis_hc.return_value = _healthcheck_report(True)
        mock_resolve_ais.return_value = MagicMock()
        mock_ais.return_value = _ais_report(
            oauth_authorize_redirect=(False, 'HTTP 401'),
        )
        mock_resolve_pis.return_value = MagicMock()
        mock_pis.return_value = _pis_report()
        mock_resolve_posting.return_value = MagicMock()
        mock_posting.return_value = _posting_report(True)

        report = run_otp_smoke(tenant='otp-company-no1')

        oauth = next(c for c in report.checks if c.label == 'OAuth')
        assert oauth.status == 'fail'
        assert 'HTTP 401' in oauth.detail
        assert not report.passed
        mock_pis.assert_called_once()
        mock_posting.assert_called_once()

    def test_ais_resolve_exception_maps_to_error(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_otp_hc.return_value = _healthcheck_report(True)
        mock_pis_hc.return_value = _healthcheck_report(True)
        mock_resolve_ais.side_effect = ConnectionError('Connection refused')
        mock_resolve_pis.return_value = MagicMock()
        mock_pis.return_value = _pis_report()
        mock_resolve_posting.return_value = MagicMock()
        mock_posting.return_value = _posting_report(True)

        report = run_otp_smoke(tenant='otp-company-no1')

        for label in ['OAuth', 'Consent', 'Accounts', 'Transactions']:
            check = next(c for c in report.checks if c.label == label)
            assert check.status == 'error'
            assert 'Connection refused' in check.detail
        assert report.error_count == 4
        mock_ais.assert_not_called()
        mock_pis.assert_called_once()

    def test_continue_on_error_runs_all_steps(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_otp_hc.return_value = _healthcheck_report(False)
        mock_pis_hc.return_value = _healthcheck_report(True)
        mock_resolve_ais.return_value = MagicMock()
        mock_ais.return_value = _ais_report()
        mock_resolve_pis.return_value = MagicMock()
        mock_pis.return_value = _pis_report()
        mock_resolve_posting.return_value = MagicMock()
        mock_posting.return_value = _posting_report(True)

        report = run_otp_smoke(tenant='otp-company-no1')

        healthcheck = next(c for c in report.checks if c.label == 'Healthcheck')
        assert healthcheck.status == 'fail'
        mock_ais.assert_called_once()
        mock_pis.assert_called_once()
        mock_posting.assert_called_once()
        assert len(report.checks) == 7

    def test_from_posting_skips_previous_steps(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_resolve_posting.return_value = MagicMock()
        mock_posting.return_value = _posting_report(True)

        report = run_otp_smoke(
            tenant='otp-company-no1',
            from_step='posting',
            order_id=2,
        )

        mock_otp_hc.assert_not_called()
        mock_pis_hc.assert_not_called()
        mock_ais.assert_not_called()
        mock_pis.assert_not_called()
        mock_posting.assert_called_once()

        skipped = [c for c in report.checks if c.status == 'skip']
        assert len(skipped) == 6
        posting = next(c for c in report.checks if c.label == 'Posting')
        assert posting.status == 'pass'

    def test_from_ais_skips_healthcheck_only(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        self._setup_all_pass(
            mock_otp_hc,
            mock_pis_hc,
            mock_resolve_ais,
            mock_ais,
            mock_resolve_pis,
            mock_pis,
            mock_resolve_posting,
            mock_posting,
        )

        report = run_otp_smoke(tenant='otp-company-no1', from_step='ais')

        mock_otp_hc.assert_not_called()
        mock_pis_hc.assert_not_called()
        healthcheck = next(c for c in report.checks if c.label == 'Healthcheck')
        assert healthcheck.status == 'skip'
        mock_ais.assert_called_once()

    def test_from_pis_skips_healthcheck_and_ais_labels(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_resolve_pis.return_value = MagicMock()
        mock_pis.return_value = _pis_report()
        mock_resolve_posting.return_value = MagicMock()
        mock_posting.return_value = _posting_report(True)

        report = run_otp_smoke(tenant='otp-company-no1', from_step='pis')

        mock_ais.assert_not_called()
        for label in ['Healthcheck', 'OAuth', 'Consent', 'Accounts', 'Transactions']:
            check = next(c for c in report.checks if c.label == label)
            assert check.status == 'skip'
        mock_pis.assert_called_once()

    def test_skip_posting(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        self._setup_all_pass(
            mock_otp_hc,
            mock_pis_hc,
            mock_resolve_ais,
            mock_ais,
            mock_resolve_pis,
            mock_pis,
            mock_resolve_posting,
            mock_posting,
        )

        report = run_otp_smoke(tenant='otp-company-no1', skip_posting=True)

        posting = next(c for c in report.checks if c.label == 'Posting')
        assert posting.status == 'skip'
        mock_posting.assert_not_called()

    def test_duration_ms_set(
        self,
        mock_otp_hc,
        mock_pis_hc,
        mock_resolve_ais,
        mock_ais,
        mock_resolve_pis,
        mock_pis,
        mock_resolve_posting,
        mock_posting,
    ):
        mock_otp_hc.return_value = _healthcheck_report(True)
        mock_pis_hc.return_value = _healthcheck_report(True)
        mock_resolve_ais.return_value = MagicMock()
        mock_ais.return_value = _ais_report()
        mock_resolve_pis.return_value = MagicMock()
        mock_pis.return_value = _pis_report()
        mock_resolve_posting.return_value = _posting_report(True)

        tick = iter([0.0, 0.05, 0.05, 0.1, 0.1, 0.15, 0.15, 0.2, 0.2, 0.25])
        with patch(
            'banking.services.otp_smoke.time.perf_counter',
            side_effect=lambda: next(tick),
        ):
            report = run_otp_smoke(tenant='otp-company-no1', skip_posting=True)

        healthcheck = next(c for c in report.checks if c.label == 'Healthcheck')
        assert healthcheck.duration_ms == 50
        ran_checks = [c for c in report.checks if c.duration_ms is not None]
        assert len(ran_checks) >= 2
