"""OTP sandbox PSU identifiers per test tenant slug."""

from __future__ import annotations

from functools import lru_cache

OTP_TEST_PSU_SUFFIXES: dict[str, str] = {
    'otp-company-no1': 'company.no1',
    'otp-company-no2': 'company.no2',
    'otp-co1-emp1': 'co1.emp1',
    'otp-co1-emp2': 'co1.emp2',
    'otp-co2-emp1': 'co2.emp1',
    'otp-co2-emp2': 'co2.emp2',
    'otp-retail1': 'retail1',
    'otp-retail2': 'retail2',
}

DEFAULT_OTP_TEST_TENANTS = ('otp-company-no1', 'otp-company-no2')


def sandbox_psu_prefix() -> str:
    from django.conf import settings

    client_id = getattr(settings, 'OTP_CLIENT_ID', '')
    marker = 'PSD-SANDBOX-ID'
    if marker in client_id:
        return client_id.split(marker, 1)[1]
    return getattr(settings, 'OTP_TPP_ID', '164')


@lru_cache(maxsize=1)
def otp_test_psu_by_slug() -> dict[str, str]:
    prefix = sandbox_psu_prefix()
    return {slug: f'{prefix}.{suffix}' for slug, suffix in OTP_TEST_PSU_SUFFIXES.items()}


ALL_OTP_TEST_TENANTS = tuple(OTP_TEST_PSU_SUFFIXES.keys())


def resolve_psu_id(tenant) -> str:
    slug = getattr(tenant, 'slug', str(tenant))
    return otp_test_psu_by_slug().get(slug, slug)
