from django.urls import path

from domains.finance.api.views import PartnerFinancialSummaryView, PartnerSubledgerView

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
]
