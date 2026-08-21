"""Banking operational read services (ADR-0021 Slice 3)."""

from __future__ import annotations

from django.db.models import Count
from django.http import Http404

from banking.models import BankImportRun, BankStatement, BankSyncRun, BankTransaction
from banking.provider_models import BankConnection, PaymentOrder
from domains.banking.read.dto import (
    bank_account_dto,
    import_run_dto,
    payment_order_dto,
    statement_detail_dto,
    statement_list_dto,
    sync_run_dto,
    transaction_dto,
)
from domains.banking.read.filters import (
    BankAccountListFilters,
    PaymentOrderListFilters,
    StatementListFilters,
    TransactionListFilters,
)
from domains.reporting.documents.snapshot import isoformat, read_snapshot
from payments.models import BankAccount


def _paginate(qs, page: int, page_size: int):
    total = qs.count()
    start = (page - 1) * page_size
    return total, list(qs[start : start + page_size])


def _list_envelope(*, as_of, count, page, page_size, results, **extra) -> dict:
    payload = {
        'as_of': isoformat(as_of),
        'count': count,
        'page': page,
        'page_size': page_size,
        'results': results,
    }
    payload.update(extra)
    return payload


def get_overview(tenant) -> dict:
    with read_snapshot() as as_of:
        accounts = list(
            BankAccount.all_objects.filter(tenant=tenant, is_active=True)
            .order_by('account_name', 'id')
        )
        unmatched = BankTransaction.all_objects.filter(
            tenant=tenant,
            match_status='unmatched',
        ).count()
        suggested = BankTransaction.all_objects.filter(
            tenant=tenant,
            match_status='suggested',
        ).count()
        recent_statements = BankStatement.all_objects.filter(tenant=tenant).count()
        account_rows = [bank_account_dto(a, now=as_of) for a in accounts]
        # Never sum across currencies — group KPI counts only.
        by_currency: dict[str, int] = {}
        for row in account_rows:
            by_currency[row['currency']] = by_currency.get(row['currency'], 0) + 1
        return {
            'as_of': isoformat(as_of),
            'accounts': account_rows,
            'account_count_by_currency': by_currency,
            'unmatched_transaction_count': unmatched,
            'suggested_transaction_count': suggested,
            'statement_count': recent_statements,
        }


def list_bank_accounts(tenant, filters: BankAccountListFilters) -> dict:
    with read_snapshot() as as_of:
        qs = (
            BankAccount.all_objects.filter(tenant=tenant)
            .order_by('account_name', 'id')
        )
        total, page_rows = _paginate(qs, filters.page, filters.page_size)
        return _list_envelope(
            as_of=as_of,
            count=total,
            page=filters.page,
            page_size=filters.page_size,
            results=[bank_account_dto(a, now=as_of) for a in page_rows],
        )


def list_statements(tenant, filters: StatementListFilters) -> dict:
    with read_snapshot() as as_of:
        qs = (
            BankStatement.all_objects.filter(tenant=tenant)
            .select_related('bank_account')
            .annotate(transaction_count=Count('transactions'))
            .order_by('-statement_date', '-id')
        )
        if filters.bank_account_id is not None:
            qs = qs.filter(bank_account_id=filters.bank_account_id)
        if filters.status:
            qs = qs.filter(status=filters.status)
        if filters.date_from:
            qs = qs.filter(statement_date__gte=filters.date_from)
        if filters.date_to:
            qs = qs.filter(statement_date__lte=filters.date_to)
        total, page_rows = _paginate(qs, filters.page, filters.page_size)
        return _list_envelope(
            as_of=as_of,
            count=total,
            page=filters.page,
            page_size=filters.page_size,
            results=[statement_list_dto(s) for s in page_rows],
        )


def get_statement(tenant, statement_id: int) -> dict:
    with read_snapshot() as as_of:
        statement = (
            BankStatement.all_objects.filter(tenant=tenant, pk=statement_id)
            .select_related('bank_account')
            .annotate(transaction_count=Count('transactions'))
            .first()
        )
        if statement is None:
            raise Http404()
        summary_rows = (
            BankTransaction.all_objects.filter(bank_statement=statement)
            .values('match_status')
            .annotate(c=Count('id'))
        )
        match_summary = {row['match_status']: row['c'] for row in summary_rows}
        payload = statement_detail_dto(statement, match_summary=match_summary)
        payload['as_of'] = isoformat(as_of)
        return payload


def list_transactions(tenant, filters: TransactionListFilters) -> dict:
    with read_snapshot() as as_of:
        qs = (
            BankTransaction.all_objects.filter(tenant=tenant)
            .select_related('bank_statement', 'bank_statement__bank_account')
            .order_by('-transaction_date', '-id')
        )
        if filters.bank_account_id is not None:
            qs = qs.filter(bank_statement__bank_account_id=filters.bank_account_id)
        if filters.statement_id is not None:
            qs = qs.filter(bank_statement_id=filters.statement_id)
        if filters.match_status:
            qs = qs.filter(match_status=filters.match_status)
        if filters.transaction_type:
            qs = qs.filter(transaction_type=filters.transaction_type)
        if filters.date_from:
            qs = qs.filter(transaction_date__gte=filters.date_from)
        if filters.date_to:
            qs = qs.filter(transaction_date__lte=filters.date_to)
        total, page_rows = _paginate(qs, filters.page, filters.page_size)
        return _list_envelope(
            as_of=as_of,
            count=total,
            page=filters.page,
            page_size=filters.page_size,
            results=[transaction_dto(tx) for tx in page_rows],
        )


def list_payment_orders(tenant, filters: PaymentOrderListFilters) -> dict:
    with read_snapshot() as as_of:
        qs = (
            PaymentOrder.all_objects.filter(tenant=tenant)
            .order_by('-created_at', '-id')
        )
        if filters.status:
            qs = qs.filter(status=filters.status)
        if filters.date_from:
            qs = qs.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            qs = qs.filter(created_at__date__lte=filters.date_to)
        total, page_rows = _paginate(qs, filters.page, filters.page_size)
        return _list_envelope(
            as_of=as_of,
            count=total,
            page=filters.page,
            page_size=filters.page_size,
            results=[payment_order_dto(o) for o in page_rows],
        )


def get_statement_import(tenant, run_id: int) -> dict:
    with read_snapshot() as as_of:
        run = BankImportRun.all_objects.filter(tenant=tenant, pk=run_id).first()
        if run is None:
            raise Http404()
        payload = import_run_dto(run)
        payload['as_of'] = isoformat(as_of)
        return payload


def get_connection_sync_status(tenant, connection_id: int) -> dict:
    with read_snapshot() as as_of:
        connection = BankConnection.all_objects.filter(
            tenant=tenant,
            pk=connection_id,
        ).first()
        if connection is None:
            raise Http404()
        active = (
            BankSyncRun.all_objects.filter(
                tenant=tenant,
                connection=connection,
                status__in=BankSyncRun.ACTIVE_STATUSES,
            )
            .order_by('-created_at', '-id')
            .first()
        )
        latest = active or (
            BankSyncRun.all_objects.filter(tenant=tenant, connection=connection)
            .order_by('-created_at', '-id')
            .first()
        )
        return {
            'as_of': isoformat(as_of),
            'connection_id': connection.pk,
            'connection_status': connection.status,
            'last_sync_at': isoformat(connection.last_sync_at) if connection.last_sync_at else None,
            'active_sync': sync_run_dto(active) if active else None,
            'latest_sync': sync_run_dto(latest) if latest else None,
        }
