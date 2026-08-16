from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from banking.services.ais_e2e import (
    AisE2eReport,
    resolve_e2e_connection,
    run_ais_e2e_checks,
)
from banking.services.healthcheck import HealthcheckReport, run_otp_healthcheck, run_pis_healthcheck
from banking.services.pis_e2e import (
    PisE2eReport,
    resolve_pis_e2e_connection,
    run_pis_e2e_checks,
)
from banking.services.pis_posting_e2e import (
    PisPostingE2eReport,
    resolve_posting_e2e_order,
    run_payment_posting_e2e,
)

FromStep = Literal['healthcheck', 'ais', 'pis', 'posting']
SmokeStatus = Literal['pass', 'fail', 'error', 'skip']

_STEPS_ORDER: list[FromStep] = ['healthcheck', 'ais', 'pis', 'posting']

_AIS_SUMMARY_CHECKS: list[tuple[str, str]] = [
    ('OAuth', 'oauth_authorize_redirect'),
    ('Consent', 'consent_authorized'),
    ('Accounts', 'accounts_fetched'),
    ('Transactions', 'transactions_endpoint'),
]


@dataclass(frozen=True)
class SmokeCheck:
    label: str
    status: SmokeStatus
    detail: str = ''
    duration_ms: int | None = None


@dataclass
class OtpSmokeReport:
    checks: list[SmokeCheck] = field(default_factory=list)
    healthcheck_otp: HealthcheckReport | None = None
    healthcheck_pis: HealthcheckReport | None = None
    ais_report: AisE2eReport | None = None
    pis_report: PisE2eReport | None = None
    posting_report: PisPostingE2eReport | None = None

    @property
    def passed(self) -> bool:
        return all(c.status in ('pass', 'skip') for c in self.checks)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.status == 'pass')

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == 'fail')

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if c.status == 'error')

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.checks if c.status == 'skip')


def run_otp_smoke(
    *,
    tenant: str | None = None,
    connection_id: int | None = None,
    order_id: int | None = None,
    provider: str = 'otp_sandbox',
    min_sync_streak: int = 3,
    submit_test_payment: bool = False,
    run_sync: bool = False,
    skip_posting: bool = False,
    from_step: FromStep = 'healthcheck',
) -> OtpSmokeReport:
    """Orchestrator za OTP sandbox readiness provjeru."""
    report = OtpSmokeReport()

    _run_healthcheck_step(
        report,
        from_step=from_step,
        provider=provider,
        connection_id=connection_id,
        tenant=tenant,
        min_sync_streak=min_sync_streak,
    )
    _run_ais_step(
        report,
        from_step=from_step,
        connection_id=connection_id,
        tenant=tenant,
        run_sync=run_sync,
    )
    _run_pis_step(
        report,
        from_step=from_step,
        connection_id=connection_id,
        tenant=tenant,
        min_sync_streak=min_sync_streak,
        submit_test_payment=submit_test_payment,
    )
    _run_posting_step(
        report,
        from_step=from_step,
        skip_posting=skip_posting,
        order_id=order_id,
        tenant=tenant,
    )

    return report


def _should_run(step: FromStep, from_step: FromStep) -> bool:
    order = {name: index for index, name in enumerate(_STEPS_ORDER)}
    return order[step] >= order[from_step]


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _check_passed(checks, name: str) -> bool | None:
    match = [c for c in checks if c.name == name]
    return match[0].passed if match else None


def _check_detail(checks, name: str) -> str:
    match = [c for c in checks if c.name == name]
    return match[0].detail if match else ''


def _run_healthcheck_step(
    report: OtpSmokeReport,
    *,
    from_step: FromStep,
    provider: str,
    connection_id: int | None,
    tenant: str | None,
    min_sync_streak: int,
) -> None:
    if not _should_run('healthcheck', from_step):
        report.checks.append(SmokeCheck('Healthcheck', 'skip'))
        return

    start = time.perf_counter()
    try:
        otp_report = run_otp_healthcheck(provider=provider)
        pis_report = run_pis_healthcheck(
            connection_id=connection_id,
            tenant=tenant,
            min_sync_streak=min_sync_streak,
        )
        report.healthcheck_otp = otp_report
        report.healthcheck_pis = pis_report
        passed = otp_report.passed and pis_report.passed
        detail = ''
        if not passed:
            failed = [
                item.label
                for item in (*otp_report.checks, *pis_report.checks)
                if not item.passed
            ]
            detail = '; '.join(failed[:3])
        status: SmokeStatus = 'pass' if passed else 'fail'
    except Exception as exc:
        status = 'error'
        detail = str(exc)

    report.checks.append(
        SmokeCheck('Healthcheck', status, detail, _elapsed_ms(start)),
    )


def _run_ais_step(
    report: OtpSmokeReport,
    *,
    from_step: FromStep,
    connection_id: int | None,
    tenant: str | None,
    run_sync: bool,
) -> None:
    if not _should_run('ais', from_step):
        for label, _ in _AIS_SUMMARY_CHECKS:
            report.checks.append(SmokeCheck(label, 'skip'))
        return

    start = time.perf_counter()
    try:
        connection = resolve_e2e_connection(
            connection_id=connection_id,
            tenant_slug=tenant,
        )
        ais_report = run_ais_e2e_checks(connection, run_sync=run_sync)
        report.ais_report = ais_report
        duration = _elapsed_ms(start)
        for label, check_name in _AIS_SUMMARY_CHECKS:
            passed = _check_passed(ais_report.checks, check_name)
            if passed is None:
                status: SmokeStatus = 'fail'
                detail = f'{check_name} not found'
            elif passed:
                status = 'pass'
                detail = _check_detail(ais_report.checks, check_name)
            else:
                status = 'fail'
                detail = _check_detail(ais_report.checks, check_name)
            report.checks.append(SmokeCheck(label, status, detail, duration))
    except Exception as exc:
        duration = _elapsed_ms(start)
        detail = str(exc)
        for label, _ in _AIS_SUMMARY_CHECKS:
            report.checks.append(SmokeCheck(label, 'error', detail, duration))


def _run_pis_step(
    report: OtpSmokeReport,
    *,
    from_step: FromStep,
    connection_id: int | None,
    tenant: str | None,
    min_sync_streak: int,
    submit_test_payment: bool,
) -> None:
    run_pis = _should_run('pis', from_step)
    evaluate_payment = submit_test_payment and run_pis

    if not run_pis:
        report.checks.append(SmokeCheck('Payment', 'skip'))
        return

    start = time.perf_counter()
    try:
        connection = resolve_pis_e2e_connection(
            connection_id=connection_id,
            tenant_slug=tenant,
        )
        pis_report = run_pis_e2e_checks(
            connection,
            submit_test_payment=submit_test_payment,
            min_sync_streak=min_sync_streak,
        )
        report.pis_report = pis_report
        duration = _elapsed_ms(start)

        if evaluate_payment:
            passed = _check_passed(pis_report.checks, 'payment_initiated')
            if passed is None:
                status: SmokeStatus = 'fail'
                detail = 'payment_initiated not found'
            elif passed:
                status = 'pass'
                detail = _check_detail(pis_report.checks, 'payment_initiated')
            else:
                status = 'fail'
                detail = _check_detail(pis_report.checks, 'payment_initiated')
            report.checks.append(SmokeCheck('Payment', status, detail, duration))
        else:
            report.checks.append(SmokeCheck('Payment', 'skip'))
    except Exception as exc:
        duration = _elapsed_ms(start)
        if evaluate_payment:
            report.checks.append(SmokeCheck('Payment', 'error', str(exc), duration))
        else:
            report.checks.append(SmokeCheck('Payment', 'skip'))


def _run_posting_step(
    report: OtpSmokeReport,
    *,
    from_step: FromStep,
    skip_posting: bool,
    order_id: int | None,
    tenant: str | None,
) -> None:
    if skip_posting or not _should_run('posting', from_step):
        report.checks.append(SmokeCheck('Posting', 'skip'))
        return

    start = time.perf_counter()
    try:
        order = resolve_posting_e2e_order(order_id=order_id, tenant_slug=tenant)
        posting_report = run_payment_posting_e2e(order)
        report.posting_report = posting_report
        duration = _elapsed_ms(start)
        status: SmokeStatus = 'pass' if posting_report.passed else 'fail'
        detail = ''
        if not posting_report.passed:
            failed = [c.name for c in posting_report.checks if not c.passed]
            detail = '; '.join(failed[:3])
        report.checks.append(SmokeCheck('Posting', status, detail, duration))
    except Exception as exc:
        report.checks.append(
            SmokeCheck('Posting', 'error', str(exc), _elapsed_ms(start)),
        )
