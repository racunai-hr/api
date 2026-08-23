"""Assets read API — GET only."""

from __future__ import annotations

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404
from domains.assets.api.authentication import AssetsJWTAuthentication
from domains.assets.api.permissions import TenantAssetsReadPermission
from domains.assets.api.schema import (
    FIXED_ASSET_LIST_PARAMS,
    AssetJournalEntryListSerializer,
    DepreciationScheduleListSerializer,
    FixedAssetDetailSerializer,
    PaginatedFixedAssetsSerializer,
)
from domains.assets.read.filters import parse_fixed_asset_filters
from domains.assets.read.service import (
    get_fixed_asset,
    list_asset_journal_entries,
    list_depreciation_schedule,
    list_fixed_assets,
)


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


class _AssetsReadApiView(APIView):
    authentication_classes = [AssetsJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantAssetsReadPermission]
    http_method_names = ['get', 'head', 'options']

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class FixedAssetListView(_AssetsReadApiView):
    @extend_schema(
        tags=['assets'],
        operation_id='assets_fixed_assets_list',
        parameters=FIXED_ASSET_LIST_PARAMS,
        responses={
            200: PaginatedFixedAssetsSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_fixed_asset_filters(request.query_params)
        except (ValueError, TypeError) as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_fixed_assets(tenant, filters))


class FixedAssetDetailView(_AssetsReadApiView):
    @extend_schema(
        tags=['assets'],
        operation_id='assets_fixed_assets_retrieve',
        responses={
            200: FixedAssetDetailSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk: int):
        return Response(get_fixed_asset(_require_tenant(request), pk))


class FixedAssetDepreciationScheduleView(_AssetsReadApiView):
    @extend_schema(
        tags=['assets'],
        operation_id='assets_fixed_assets_depreciation_schedule',
        responses={
            200: DepreciationScheduleListSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk: int):
        return Response(list_depreciation_schedule(_require_tenant(request), pk))


class FixedAssetJournalEntriesView(_AssetsReadApiView):
    @extend_schema(
        tags=['assets'],
        operation_id='assets_fixed_assets_journal_entries',
        responses={
            200: AssetJournalEntryListSerializer,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def get(self, request, pk: int):
        return Response(list_asset_journal_entries(_require_tenant(request), pk))
