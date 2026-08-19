from django.urls import path

from domains.banking.api.views import (
    BankAccountListView,
    BankingOverviewView,
    ConnectionSyncStatusView,
    PaymentOrderListView,
    StatementDetailView,
    StatementImportDetailView,
    StatementListView,
    TransactionListView,
)

urlpatterns = [
    path('banking/overview/', BankingOverviewView.as_view(), name='banking-overview'),
    path('banking/bank-accounts/', BankAccountListView.as_view(), name='banking-bank-accounts'),
    path('banking/statements/', StatementListView.as_view(), name='banking-statements'),
    path('banking/statements/<int:pk>/', StatementDetailView.as_view(), name='banking-statement-detail'),
    path('banking/transactions/', TransactionListView.as_view(), name='banking-transactions'),
    path('banking/payment-orders/', PaymentOrderListView.as_view(), name='banking-payment-orders'),
    path(
        'banking/statement-imports/<int:pk>/',
        StatementImportDetailView.as_view(),
        name='banking-statement-import-detail',
    ),
    path(
        'banking/connections/<int:pk>/sync-status/',
        ConnectionSyncStatusView.as_view(),
        name='banking-connection-sync-status',
    ),
]
