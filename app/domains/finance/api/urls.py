from django.urls import path

from domains.finance.api.views import (
    DepositCancelView,
    DepositDetailView,
    DepositListCreateView,
    DepositPostView,
    DepositReturnView,
    DepositReverseView,
    ExpenseApproveView,
    PartnerFinancialSummaryView,
    PartnerSubledgerView,
)

urlpatterns = [
    path(
        'finance/partners/<int:pk>/financial-summary/',
        PartnerFinancialSummaryView.as_view(),
        name='finance-partner-financial-summary',
    ),
    path(
        'finance/partners/<int:pk>/subledger/',
        PartnerSubledgerView.as_view(),
        name='finance-partner-subledger',
    ),
    path(
        'finance/expenses/<int:pk>/approve/',
        ExpenseApproveView.as_view(),
        name='finance-expense-approve',
    ),
    path('finance/deposits/', DepositListCreateView.as_view(), name='finance-deposit-list'),
    path('finance/deposits/<int:pk>/', DepositDetailView.as_view(), name='finance-deposit-detail'),
    path('finance/deposits/<int:pk>/post/', DepositPostView.as_view(), name='finance-deposit-post'),
    path('finance/deposits/<int:pk>/return/', DepositReturnView.as_view(), name='finance-deposit-return'),
    path('finance/deposits/<int:pk>/reverse/', DepositReverseView.as_view(), name='finance-deposit-reverse'),
    path('finance/deposits/<int:pk>/cancel/', DepositCancelView.as_view(), name='finance-deposit-cancel'),
]
