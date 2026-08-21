"""OpenAPI auth extensions for domain JWT authenticators (SimpleJWT subclasses)."""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class _BearerJWTScheme(OpenApiAuthenticationExtension):
    name = 'bearerAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
        }


class BankingJWTAuthenticationExtension(_BearerJWTScheme):
    target_class = 'domains.banking.api.authentication.BankingJWTAuthentication'


class DocumentJWTAuthenticationExtension(_BearerJWTScheme):
    target_class = 'domains.reporting.api.authentication.DocumentJWTAuthentication'


class PartnersJWTAuthenticationExtension(_BearerJWTScheme):
    target_class = 'domains.partners.api.authentication.PartnersJWTAuthentication'


class FinanceJWTAuthenticationExtension(_BearerJWTScheme):
    target_class = 'domains.finance.api.authentication.FinanceJWTAuthentication'


class PurchasingJWTAuthenticationExtension(_BearerJWTScheme):
    target_class = 'domains.purchasing.api.authentication.PurchasingJWTAuthentication'


class SimpleJWTAuthenticationExtension(_BearerJWTScheme):
    target_class = 'rest_framework_simplejwt.authentication.JWTAuthentication'
