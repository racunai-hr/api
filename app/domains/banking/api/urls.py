from django.urls import path

from domains.banking.api.views import (
    BankAccountListView,
    BankingOverviewView,
    ConnectionSyncEnqueueView,
    ConnectionSyncStatusView,
    PaymentOrderListView,
    StatementDetailView,
    StatementImportCreateView,
    StatementImportDetailView,
    StatementListView,
    TransactionListView,
    TransactionMatchView,
    TransactionUnmatchView,
)

urlpatterns = [
    path('banking/overview/', BankingOverviewView.as_view(), name='banking-overview'),
    path('banking/bank-accounts/', BankAccountListView.as_view(), name='banking-bank-accounts'),
    path('banking/statements/', StatementListView.as_view(), name='banking-statements'),
    path('banking/statements/<int:pk>/', StatementDetailView.as_view(), name='banking-statement-detail'),
    path('banking/transactions/', TransactionListView.as_view(), name='banking-transactions'),
    path(
        'banking/transactions/<int:pk>/match/',
        TransactionMatchView.as_view(),
        name='banking-transaction-match',
    ),
    path(
        'banking/transactions/<int:pk>/unmatch/',
        TransactionUnmatchView.as_view(),
        name='banking-transaction-unmatch',
    ),
    path('banking/payment-orders/', PaymentOrderListView.as_view(), name='banking-payment-orders'),
    path(
        'banking/statement-imports/',
        StatementImportCreateView.as_view(),
        name='banking-statement-import-create',
    ),
    path(
        'banking/statement-imports/<int:pk>/',
        StatementImportDetailView.as_view(),
        name='banking-statement-import-detail',
    ),
    path(
        'banking/connections/<int:pk>/sync/',
        ConnectionSyncEnqueueView.as_view(),
        name='banking-connection-sync',
    ),
    path(
        'banking/connections/<int:pk>/sync-status/',
        ConnectionSyncStatusView.as_view(),
        name='banking-connection-sync-status',
    ),
]
