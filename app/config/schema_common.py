"""Shared OpenAPI error components matching existing DRF runtime shapes."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample, OpenApiResponse
from rest_framework import serializers


class ErrorDetailSerializer(serializers.Serializer):
    """DRF-style error body: ``{"detail": ...}`` where detail may be str or object."""

    detail = serializers.JSONField()


class CodeDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class ExportLimitErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    limit = serializers.IntegerField()
    count = serializers.IntegerField()


ERROR_400 = OpenApiResponse(
    response=ErrorDetailSerializer,
    description='Nevaljani upit / ValidationError',
)
ERROR_401 = OpenApiResponse(
    response=ErrorDetailSerializer,
    description='Nedostaje ili je nevaljan Bearer token',
    examples=[
        OpenApiExample(
            'Unauthorized',
            value={'detail': 'Authentication credentials were not provided.'},
        )
    ],
)
ERROR_404 = OpenApiResponse(
    response=ErrorDetailSerializer,
    description=(
        'Nije pronađeno: missing resource, cross-tenant ID, '
        'ili autenticiran korisnik bez prava (namjerno 404, ne 403)'
    ),
)
ERROR_409 = OpenApiResponse(
    response=ErrorDetailSerializer,
    description='Konflikt (idempotency / match target taken)',
)
ERROR_422 = OpenApiResponse(
    response=ErrorDetailSerializer,
    description='Neprocesibilan zahtjev (validacija write operacije)',
)
ERROR_410 = OpenApiResponse(
    response=CodeDetailSerializer,
    description='Attachment content unavailable',
)
ERROR_503 = OpenApiResponse(
    response=CodeDetailSerializer,
    description='Invoice PDF unavailable',
)
ERROR_EXPORT_LIMIT = OpenApiResponse(
    response=ExportLimitErrorSerializer,
    description='Export limit exceeded',
)


def money_field(**kwargs):
    return serializers.CharField(
        help_text='Decimal as string, e.g. "1100.00"',
        **kwargs,
    )
