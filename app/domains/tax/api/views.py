"""Tax JWT read API — PDV Razdoblja."""

from __future__ import annotations

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_401, ERROR_404
from domains.tax.api.authentication import TaxJWTAuthentication
from domains.tax.api.permissions import TenantTaxReadPermission
from domains.tax.api.schema import PdvPeriodListSerializer
from domains.tax.read.service import list_pdv_periods


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


class PdvPeriodListView(APIView):
    authentication_classes = [TaxJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantTaxReadPermission]
    http_method_names = ['get', 'head', 'options']

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)

    @extend_schema(
        tags=['tax'],
        operation_id='tax_pdv_periods_list',
        responses={
            200: PdvPeriodListSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        return Response(list_pdv_periods(_require_tenant(request)))
