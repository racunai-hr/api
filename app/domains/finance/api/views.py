"""Finance partner read API + Deposit write API (ADR-0022 / ADR-0024)."""

from __future__ import annotations

from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.schema_common import ERROR_400, ERROR_401, ERROR_404, ERROR_409
from domains.finance.api.authentication import FinanceJWTAuthentication
from domains.finance.api.permissions import TenantFinanceReadPermission, TenantFinanceWritePermission
from domains.finance.api.schema import (
    CreateDepositSerializer,
    CreatePrivateFundsClaimSerializer,
    DepositConflictSerializer,
    DepositListSerializer,
    DepositSerializer,
    ExpenseApproveResponseSerializer,
    PartnerFinancialSummarySerializer,
    PartnerSubledgerListSerializer,
    PrivateFundsClaimSerializer,
    ReturnDepositSerializer,
)
from domains.finance.services.aging import partner_financial_summary, partner_subledger_items
from domains.finance.services.deposits import (
    DepositBadRequest,
    DepositConflict,
    cancel_deposit,
    create_deposit,
    get_deposit,
    list_deposits,
    post_deposit,
    return_deposit,
    reverse_deposit,
)
from domains.finance.services.expenses import (
    ExpenseApproveBadRequest,
    ExpenseApproveConflict,
    approve_expense_for_posting,
)
from domains.finance.services.private_funds import (
    PrivateFundsBadRequest,
    PrivateFundsConflict,
    create_claim,
    get_claim,
    post_claim,
)
from partners.models import Partner


def _require_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if tenant is None:
        raise Http404()
    return tenant


def _require_partner(tenant, partner_id: int) -> None:
    if not Partner.all_objects.filter(tenant=tenant, pk=partner_id).exists():
        raise Http404()


def _require_idempotency_key(request) -> str:
    key = (request.headers.get('Idempotency-Key') or request.META.get('HTTP_IDEMPOTENCY_KEY') or '').strip()
    if not key:
        raise ValidationError({'Idempotency-Key': 'Header Idempotency-Key je obavezan.'})
    return key


def _conflict(exc: DepositConflict):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)


def _bad_request(exc: DepositBadRequest):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


class _FinanceReadApiView(APIView):
    authentication_classes = [FinanceJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantFinanceReadPermission]
    http_method_names = ['get', 'head', 'options']

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)


class _FinanceWriteApiView(APIView):
    authentication_classes = [FinanceJWTAuthentication]
    permission_classes = [IsAuthenticated, TenantFinanceWritePermission]

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


class DepositListCreateView(APIView):
    authentication_classes = [FinanceJWTAuthentication]

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsAuthenticated(), TenantFinanceReadPermission()]
        return [IsAuthenticated(), TenantFinanceWritePermission()]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            raise Http404()
        super().permission_denied(request, message=message, code=code)

    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_list',
        parameters=[
            OpenApiParameter(name='partner_id', type=int, location=OpenApiParameter.QUERY, required=False),
        ],
        responses={200: DepositListSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request):
        tenant = _require_tenant(request)
        partner_id = request.query_params.get('partner_id')
        pid = int(partner_id) if partner_id else None
        return Response(list_deposits(tenant=tenant, partner_id=pid))

    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_create',
        request=CreateDepositSerializer,
        responses={
            201: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
        },
    )
    def post(self, request):
        try:
            return Response(
                create_deposit(tenant=_require_tenant(request), data=request.data, user=request.user),
                status=status.HTTP_201_CREATED,
            )
        except DepositBadRequest as exc:
            return _bad_request(exc)
        except Http404:
            raise
        except Exception as exc:
            raise ValidationError({'detail': str(exc)}) from exc


class DepositDetailView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_retrieve',
        responses={200: DepositSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_deposit(tenant=_require_tenant(request), deposit_id=pk))


class DepositPostView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_post',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=None,
        responses={
            200: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(post_deposit(tenant=_require_tenant(request), deposit_id=pk, user=request.user))
        except DepositConflict as exc:
            return _conflict(exc)
        except DepositBadRequest as exc:
            return _bad_request(exc)


class DepositReturnView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_return',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=ReturnDepositSerializer,
        responses={
            200: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(
                return_deposit(
                    tenant=_require_tenant(request),
                    deposit_id=pk,
                    user=request.user,
                    data=request.data,
                )
            )
        except DepositConflict as exc:
            return _conflict(exc)
        except DepositBadRequest as exc:
            return _bad_request(exc)


class DepositReverseView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_reverse',
        parameters=[
            OpenApiParameter(
                name='Idempotency-Key',
                type=str,
                location=OpenApiParameter.HEADER,
                required=True,
            ),
        ],
        request=None,
        responses={
            200: DepositSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(reverse_deposit(tenant=_require_tenant(request), deposit_id=pk, user=request.user))
        except DepositConflict as exc:
            return _conflict(exc)
        except DepositBadRequest as exc:
            return _bad_request(exc)


class DepositCancelView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_deposits_cancel',
        request=None,
        responses={
            200: DepositSerializer,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(cancel_deposit(tenant=_require_tenant(request), deposit_id=pk))
        except DepositConflict as exc:
            return _conflict(exc)


class ExpenseApproveView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_expenses_approve',
        request=None,
        responses={
            200: ExpenseApproveResponseSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        try:
            return Response(
                approve_expense_for_posting(
                    tenant=_require_tenant(request),
                    expense_id=pk,
                    user=request.user,
                )
            )
        except ExpenseApproveConflict as exc:
            return Response(
                {'code': exc.code, 'detail': exc.detail},
                status=status.HTTP_409_CONFLICT,
            )
        except ExpenseApproveBadRequest as exc:
            return Response(
                {'code': exc.code, 'detail': exc.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )


def _pf_conflict(exc: PrivateFundsConflict):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_409_CONFLICT)


def _pf_bad_request(exc: PrivateFundsBadRequest):
    return Response({'code': exc.code, 'detail': exc.detail}, status=status.HTTP_400_BAD_REQUEST)


class PrivateFundsClaimDetailView(_FinanceReadApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_private_funds_claim_detail',
        responses={200: PrivateFundsClaimSerializer, 401: ERROR_401, 404: ERROR_404},
    )
    def get(self, request, pk: int):
        return Response(get_claim(tenant=_require_tenant(request), claim_id=pk))


class PrivateFundsClaimCreateView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_private_funds_claim_create',
        request=CreatePrivateFundsClaimSerializer,
        responses={
            201: PrivateFundsClaimSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request):
        _require_idempotency_key(request)
        ser = CreatePrivateFundsClaimSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            dto = create_claim(
                tenant=_require_tenant(request),
                data=ser.validated_data,
                user=request.user,
            )
            return Response(dto, status=status.HTTP_201_CREATED)
        except PrivateFundsConflict as exc:
            return _pf_conflict(exc)
        except PrivateFundsBadRequest as exc:
            return _pf_bad_request(exc)


class PrivateFundsClaimPostView(_FinanceWriteApiView):
    @extend_schema(
        tags=['finance'],
        operation_id='finance_private_funds_claim_post',
        request=None,
        responses={
            200: PrivateFundsClaimSerializer,
            400: ERROR_400,
            401: ERROR_401,
            404: ERROR_404,
            409: DepositConflictSerializer,
        },
    )
    def post(self, request, pk: int):
        _require_idempotency_key(request)
        try:
            return Response(
                post_claim(
                    tenant=_require_tenant(request),
                    claim_id=pk,
                    user=request.user,
                )
            )
        except PrivateFundsConflict as exc:
            return _pf_conflict(exc)
        except PrivateFundsBadRequest as exc:
            return _pf_bad_request(exc)
