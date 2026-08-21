"""JWT partners MDM API views (ADR-0022)."""

from __future__ import annotations

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404
from domains.partners.api.authentication import PartnersJWTAuthentication
from domains.partners.api.permissions import (
    TenantPartnersReadPermission,
    TenantPartnersWritePermission,
)
from domains.partners.api.schema import (
    PARTNER_LIST_PARAMS,
    ContactListSerializer,
    ContactSerializer,
    ContactWriteSerializer,
    PaginatedPartnersSerializer,
    PartnerBankAccountListSerializer,
    PartnerBankAccountSerializer,
    PartnerBankAccountWriteSerializer,
    PartnerConflictSerializer,
    PartnerSerializer,
    PartnerWriteSerializer,
)
from domains.partners.services.partners import (
    PartnerFieldError,
    PartnerIbanConflict,
    PartnerTaxNumberConflict,
    PartnerVatNumberConflict,
    create_bank_account,
    create_contact,
    create_partner,
    delete_bank_account,
    delete_contact,
    get_partner,
    list_bank_accounts,
    list_contacts,
    list_partners,
    parse_partner_list_filters,
    update_bank_account,
    update_contact,
    update_partner,
)


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _conflict(code: str, field: str):
    return Response({'code': code, 'field': field}, status=status.HTTP_409_CONFLICT)


def _field_error(exc: PartnerFieldError):
    return Response(
        {'code': exc.code, 'field': exc.field, 'detail': exc.detail},
        status=status.HTTP_400_BAD_REQUEST,
    )


class _PartnersApiView(APIView):
    authentication_classes = [PartnersJWTAuthentication]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class _PartnersReadWriteApiView(_PartnersApiView):
    """GET uses read role; mutating methods use write role."""

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated(), TenantPartnersReadPermission()]
        return [IsAuthenticated(), TenantPartnersWritePermission()]


class _PartnersWriteApiView(_PartnersApiView):
    permission_classes = [IsAuthenticated, TenantPartnersWritePermission]


class PartnerListView(_PartnersReadWriteApiView):
    @extend_schema(
        tags=['partners'],
        operation_id='partners_list',
        parameters=PARTNER_LIST_PARAMS,
        responses={200: PaginatedPartnersSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        tenant = _require_tenant(request)
        try:
            filters = parse_partner_list_filters(request.query_params)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(list_partners(tenant, filters))

    @extend_schema(
        tags=['partners'],
        operation_id='partners_create',
        request=PartnerWriteSerializer,
        responses={
            201: PartnerSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PartnerConflictSerializer,
        },
    )
    def post(self, request):
        tenant = _require_tenant(request)
        try:
            payload = create_partner(tenant, request.data, user=request.user)
        except PartnerTaxNumberConflict:
            return _conflict('partner_tax_number_conflict', 'tax_number')
        except PartnerVatNumberConflict:
            return _conflict('partner_vat_number_conflict', 'vat_number')
        except PartnerFieldError as exc:
            return _field_error(exc)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(payload, status=status.HTTP_201_CREATED)


class PartnerDetailView(_PartnersReadWriteApiView):
    http_method_names = ['get', 'patch', 'head', 'options']

    @extend_schema(
        tags=['partners'],
        operation_id='partners_retrieve',
        responses={200: PartnerSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_partner(_require_tenant(request), pk))

    @extend_schema(
        tags=['partners'],
        operation_id='partners_partial_update',
        request=PartnerWriteSerializer,
        responses={
            200: PartnerSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PartnerConflictSerializer,
        },
    )
    def patch(self, request, pk: int):
        tenant = _require_tenant(request)
        try:
            payload = update_partner(tenant, pk, request.data)
        except PartnerTaxNumberConflict:
            return _conflict('partner_tax_number_conflict', 'tax_number')
        except PartnerVatNumberConflict:
            return _conflict('partner_vat_number_conflict', 'vat_number')
        except PartnerFieldError as exc:
            return _field_error(exc)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(payload)


class PartnerContactListView(_PartnersReadWriteApiView):
    @extend_schema(
        tags=['partners'],
        operation_id='partners_contacts_list',
        responses={200: ContactListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(list_contacts(_require_tenant(request), pk))

    @extend_schema(
        tags=['partners'],
        operation_id='partners_contacts_create',
        request=ContactWriteSerializer,
        responses={201: ContactSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def post(self, request, pk: int):
        tenant = _require_tenant(request)
        try:
            payload = create_contact(tenant, pk, request.data)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(payload, status=status.HTTP_201_CREATED)


class PartnerContactDetailView(_PartnersWriteApiView):
    http_method_names = ['patch', 'delete', 'head', 'options']

    @extend_schema(
        tags=['partners'],
        operation_id='partners_contacts_partial_update',
        request=ContactWriteSerializer,
        responses={200: ContactSerializer, 400: ERROR_400, 401: ERROR_401, 404: ERROR_404},
    )
    def patch(self, request, pk: int, contact_id: int):
        tenant = _require_tenant(request)
        try:
            payload = update_contact(tenant, pk, contact_id, request.data)
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(payload)

    @extend_schema(
        tags=['partners'],
        operation_id='partners_contacts_destroy',
        responses={204: None, 401: ERROR_401, 404: ERROR_404},
    )
    def delete(self, request, pk: int, contact_id: int):
        delete_contact(_require_tenant(request), pk, contact_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PartnerBankAccountListView(_PartnersReadWriteApiView):
    @extend_schema(
        tags=['partners'],
        operation_id='partners_bank_accounts_list',
        responses={200: PartnerBankAccountListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(list_bank_accounts(_require_tenant(request), pk))

    @extend_schema(
        tags=['partners'],
        operation_id='partners_bank_accounts_create',
        request=PartnerBankAccountWriteSerializer,
        responses={
            201: PartnerBankAccountSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PartnerConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        tenant = _require_tenant(request)
        try:
            payload = create_bank_account(tenant, pk, request.data)
        except PartnerIbanConflict:
            return _conflict('partner_iban_conflict', 'iban')
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(payload, status=status.HTTP_201_CREATED)


class PartnerBankAccountDetailView(_PartnersWriteApiView):
    http_method_names = ['patch', 'delete', 'head', 'options']

    @extend_schema(
        tags=['partners'],
        operation_id='partners_bank_accounts_partial_update',
        request=PartnerBankAccountWriteSerializer,
        responses={
            200: PartnerBankAccountSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: PartnerConflictSerializer,
        },
    )
    def patch(self, request, pk: int, account_id: int):
        tenant = _require_tenant(request)
        try:
            payload = update_bank_account(tenant, pk, account_id, request.data)
        except PartnerIbanConflict:
            return _conflict('partner_iban_conflict', 'iban')
        except ValueError as exc:
            raise ValidationError({'detail': str(exc)}) from exc
        return Response(payload)

    @extend_schema(
        tags=['partners'],
        operation_id='partners_bank_accounts_destroy',
        responses={204: None, 401: ERROR_401, 404: ERROR_404},
    )
    def delete(self, request, pk: int, account_id: int):
        delete_bank_account(_require_tenant(request), pk, account_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
