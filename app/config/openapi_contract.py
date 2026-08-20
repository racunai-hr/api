"""Deterministic OpenAPI contract inventory for /api/ DRF routes.

Source of truth for *which* routes belong in the contract. Schema fields live
in schema serializers + @extend_schema; openapi.yaml is a generated artifact.
"""

from __future__ import annotations

# (METHOD, path) — path templates as emitted by spectacular (trailing slash).
CONTRACT_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ('post', '/api/auth/token/'),
        ('post', '/api/auth/token/refresh/'),
        ('get', '/api/auth/me/'),
        ('get', '/api/documents/'),
        ('get', '/api/documents/export/'),
        ('get', '/api/documents/{direction}/{id}/'),
        ('get', '/api/documents/{direction}/{id}/pdf/'),
        ('get', '/api/documents/incoming/{id}/attachments/{attachment_id}/'),
        ('get', '/api/banking/overview/'),
        ('get', '/api/banking/bank-accounts/'),
        ('get', '/api/banking/statements/'),
        ('get', '/api/banking/statements/{id}/'),
        ('get', '/api/banking/transactions/'),
        ('post', '/api/banking/transactions/{id}/match/'),
        ('post', '/api/banking/transactions/{id}/unmatch/'),
        ('get', '/api/banking/payment-orders/'),
        ('post', '/api/banking/statement-imports/'),
        ('get', '/api/banking/statement-imports/{id}/'),
        ('post', '/api/banking/connections/{id}/sync/'),
        ('get', '/api/banking/connections/{id}/sync-status/'),
        ('get', '/api/partners/'),
        ('post', '/api/partners/'),
        ('get', '/api/partners/{id}/'),
        ('patch', '/api/partners/{id}/'),
        ('get', '/api/partners/{id}/contacts/'),
        ('post', '/api/partners/{id}/contacts/'),
        ('patch', '/api/partners/{id}/contacts/{contact_id}/'),
        ('delete', '/api/partners/{id}/contacts/{contact_id}/'),
        ('get', '/api/partners/{id}/bank-accounts/'),
        ('post', '/api/partners/{id}/bank-accounts/'),
        ('patch', '/api/partners/{id}/bank-accounts/{account_id}/'),
        ('delete', '/api/partners/{id}/bank-accounts/{account_id}/'),
        ('get', '/api/finance/partners/{id}/financial-summary/'),
        ('get', '/api/finance/partners/{id}/subledger/'),
        ('post', '/api/purchasing/invoices/import/'),
        ('get', '/api/purchasing/invoices/import/{id}/'),
        ('post', '/api/purchasing/invoices/import/{id}/retry/'),
        ('post', '/api/purchasing/invoices/import/{id}/create-partner/'),
        ('post', '/api/purchasing/invoices/import/{id}/apply-partner-updates/'),
        ('post', '/api/purchasing/invoices/import/{id}/confirm/'),
        ('post', '/api/purchasing/invoices/import/{id}/discard/'),
    }
)

# Django URL name → expected OpenAPI path (for resolver cross-check).
# Paths use spectacular's {id} style for int pk converters.
URL_NAME_TO_OPERATION: dict[str, tuple[str, str]] = {
    'token_obtain_pair': ('post', '/api/auth/token/'),
    'token_refresh': ('post', '/api/auth/token/refresh/'),
    'auth_me': ('get', '/api/auth/me/'),
    'document-list': ('get', '/api/documents/'),
    'document-export': ('get', '/api/documents/export/'),
    'document-detail': ('get', '/api/documents/{direction}/{id}/'),
    'document-pdf': ('get', '/api/documents/{direction}/{id}/pdf/'),
    'document-attachment': ('get', '/api/documents/incoming/{id}/attachments/{attachment_id}/'),
    'banking-overview': ('get', '/api/banking/overview/'),
    'banking-bank-accounts': ('get', '/api/banking/bank-accounts/'),
    'banking-statements': ('get', '/api/banking/statements/'),
    'banking-statement-detail': ('get', '/api/banking/statements/{id}/'),
    'banking-transactions': ('get', '/api/banking/transactions/'),
    'banking-transaction-match': ('post', '/api/banking/transactions/{id}/match/'),
    'banking-transaction-unmatch': ('post', '/api/banking/transactions/{id}/unmatch/'),
    'banking-payment-orders': ('get', '/api/banking/payment-orders/'),
    'banking-statement-import-create': ('post', '/api/banking/statement-imports/'),
    'banking-statement-import-detail': ('get', '/api/banking/statement-imports/{id}/'),
    'banking-connection-sync': ('post', '/api/banking/connections/{id}/sync/'),
    'banking-connection-sync-status': ('get', '/api/banking/connections/{id}/sync-status/'),
    'partner-list': ('get', '/api/partners/'),
    'partner-detail': ('get', '/api/partners/{id}/'),
    'partner-contacts': ('get', '/api/partners/{id}/contacts/'),
    'partner-contact-detail': ('patch', '/api/partners/{id}/contacts/{contact_id}/'),
    'partner-bank-accounts': ('get', '/api/partners/{id}/bank-accounts/'),
    'partner-bank-account-detail': ('patch', '/api/partners/{id}/bank-accounts/{account_id}/'),
    'finance-partner-financial-summary': ('get', '/api/finance/partners/{id}/financial-summary/'),
    'finance-partner-subledger': ('get', '/api/finance/partners/{id}/subledger/'),
    'purchasing-invoice-import-create': ('post', '/api/purchasing/invoices/import/'),
    'purchasing-invoice-import-detail': ('get', '/api/purchasing/invoices/import/{id}/'),
    'purchasing-invoice-import-retry': ('post', '/api/purchasing/invoices/import/{id}/retry/'),
    'purchasing-invoice-import-create-partner': (
        'post',
        '/api/purchasing/invoices/import/{id}/create-partner/',
    ),
    'purchasing-invoice-import-apply-partner-updates': (
        'post',
        '/api/purchasing/invoices/import/{id}/apply-partner-updates/',
    ),
    'purchasing-invoice-import-confirm': ('post', '/api/purchasing/invoices/import/{id}/confirm/'),
    'purchasing-invoice-import-discard': ('post', '/api/purchasing/invoices/import/{id}/discard/'),
}

EXCLUDED_URL_NAMES = frozenset(
    {
        'api_ready',
        'api-schema',
        'api-schema-swagger-ui',
        'api-schema-redoc',
        'fiscal_as4_inbound',
        'login',
        'logout',
        'api-root',
    }
)
