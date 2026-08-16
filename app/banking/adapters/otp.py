from __future__ import annotations

from datetime import date

from banking.adapters.base import BankAdapter
from banking.otp.ais import fetch_transactions
from banking.otp.client import build_client
from banking.provider_models import BankConnection, BankConsent


class OtpBankAdapter:
    def __init__(self, connection: BankConnection, consent: BankConsent):
        self.connection = connection
        self.consent = consent

    def fetch_transactions(
        self,
        account_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        with build_client(
            provider=self.connection.bank_provider,
            tenant=self.connection.tenant,
            correlation_id=self.consent.correlation_id,
        ) as client:
            return fetch_transactions(
                client,
                self.consent,
                account_id,
                date_from=date_from,
                date_to=date_to,
            )


def get_adapter(connection: BankConnection) -> BankAdapter:
    consent = connection.get_active_consent()
    if consent is None:
        raise ValueError('Nema aktivnog consenta.')
    if connection.bank_provider.code.startswith('otp'):
        return OtpBankAdapter(connection, consent)
    raise ValueError(f'Nepodržan provider: {connection.bank_provider.code}')
