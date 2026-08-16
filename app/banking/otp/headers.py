from __future__ import annotations

import uuid

from django.conf import settings

from banking.otp.psu import resolve_psu_id
from banking.provider_models import BankConnection


def berlin_group_headers(
    *,
    connection: BankConnection | None = None,
    consent_id: str | None = None,
    psu_ip: str = '127.0.0.1',
    redirect_uri: str | None = None,
    pre_step_auth: bool = False,
) -> dict[str, str]:
    headers = {
        'PSU-IP-Address': psu_ip,
        'Content-Type': 'application/json',
    }
    if consent_id:
        headers['Consent-ID'] = consent_id
    if redirect_uri:
        headers['TPP-Redirect-URI'] = redirect_uri
        headers['TPP-Redirect-Preferred'] = 'true'
    if connection is not None and not pre_step_auth:
        headers['PSU-ID'] = resolve_psu_id(connection.tenant)
        headers['PSU-ID-Type'] = getattr(settings, 'OTP_PSU_ID_TYPE', '222')
    return headers


def new_request_id() -> str:
    return str(uuid.uuid4())
