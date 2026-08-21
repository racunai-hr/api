from django.urls import path

from domains.partners.api.views import (
    PartnerBankAccountDetailView,
    PartnerBankAccountListView,
    PartnerContactDetailView,
    PartnerContactListView,
    PartnerDetailView,
    PartnerListView,
)

urlpatterns = [
    path('partners/', PartnerListView.as_view(), name='partner-list'),
    path('partners/<int:pk>/', PartnerDetailView.as_view(), name='partner-detail'),
    path(
        'partners/<int:pk>/contacts/',
        PartnerContactListView.as_view(),
        name='partner-contacts',
    ),
    path(
        'partners/<int:pk>/contacts/<int:contact_id>/',
        PartnerContactDetailView.as_view(),
        name='partner-contact-detail',
    ),
    path(
        'partners/<int:pk>/bank-accounts/',
        PartnerBankAccountListView.as_view(),
        name='partner-bank-accounts',
    ),
    path(
        'partners/<int:pk>/bank-accounts/<int:account_id>/',
        PartnerBankAccountDetailView.as_view(),
        name='partner-bank-account-detail',
    ),
]
