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
