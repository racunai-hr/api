from django.urls import path

from domains.tax.api.views import PdvPeriodListView

urlpatterns = [
    path('tax/pdv/periods/', PdvPeriodListView.as_view(), name='tax-pdv-periods-list'),
]
