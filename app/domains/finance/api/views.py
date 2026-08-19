"""Finance partner read API views (ADR-0022)."""

from __future__ import annotations

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_401, ERROR_404
from domains.finance.api.authentication import FinanceJWTAuthentication
from domains.finance.api.permissions import TenantFinanceReadPermission
from domains.finance.api.schema import (
    PartnerFinancialSummarySerializer,
    PartnerSubledgerListSerializer,
)
from domains.finance.services.aging import partner_financial_summary, partner_subledger_items
from partners.models import Partner


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _require_partner(tenant, partner_id: int) -> None:
    if not Partner.all_objects.filter(tenant=tenant, pk=partner_id).exists():
        raise Http404()


class _FinanceReadApiView(APIView):
    authentication_classes = [FinanceJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantFinanceReadPermission]
    http_method_names = ['get', 'head', 'options']

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class PartnerFinancialSummaryView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_partner_financial_summary',
        responses={200: PartnerFinancialSummarySerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        tenant = _require_tenant(request)
        _require_partner(tenant, pk)
        return Response(partner_financial_summary(tenant, pk))


class PartnerSubledgerView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_partner_subledger',
        responses={200: PartnerSubledgerListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        tenant = _require_tenant(request)
        _require_partner(tenant, pk)
        return Response(partner_subledger_items(tenant, pk))
