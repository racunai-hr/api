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
    ExpenseCloseCandidatesView,
    ExpenseCloseFromBankView,
    TransactionSupplierPrepaymentView,
    TransactionListView,
    TransactionMatchView,
    TransactionOpenItemCandidatesView,
    TransactionReconcileOpenItemView,
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
    path(
        'banking/transactions/<int:pk>/open-item-candidates/',
        TransactionOpenItemCandidatesView.as_view(),
        name='banking-transaction-open-item-candidates',
    ),
    path(
        'banking/transactions/<int:pk>/reconcile-open-item/',
        TransactionReconcileOpenItemView.as_view(),
        name='banking-transaction-reconcile-open-item',
    ),
    path(
        'banking/transactions/<int:pk>/supplier-prepayment/',
        TransactionSupplierPrepaymentView.as_view(),
        name='banking-transaction-supplier-prepayment',
    ),
    path(
        'banking/expenses/<int:pk>/close-candidates/',
        ExpenseCloseCandidatesView.as_view(),
        name='banking-expense-close-candidates',
    ),
    path(
        'banking/expenses/<int:pk>/close-from-bank/',
        ExpenseCloseFromBankView.as_view(),
        name='banking-expense-close-from-bank',
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
