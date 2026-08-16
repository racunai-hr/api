from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from banking.provider_models import BankProvider


@dataclass(frozen=True)
class OtpCredentials:
    client_id: str
    client_secret: str
    cert_path: str
    cert_password: str
    redirect_uri: str


def get_active_otp_provider() -> BankProvider | None:
    env = getattr(settings, 'OTP_ENV', 'sandbox')
    return BankProvider.objects.filter(environment=env, is_active=True).first()


def resolve_otp_credentials() -> OtpCredentials:
    return OtpCredentials(
        client_id=settings.OTP_CLIENT_ID,
        client_secret=settings.OTP_CLIENT_SECRET,
        cert_path=settings.OTP_CERT_PATH,
        cert_password=settings.OTP_CERT_PASSWORD,
        redirect_uri=settings.OTP_REDIRECT_URI,
    )
