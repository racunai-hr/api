from django.urls import path

from domains.purchasing.api.views import (
    ExpenseEracunRejectionView,
    InvoiceImportApplyPartnerUpdatesView,
    InvoiceImportConfirmView,
    InvoiceImportCreatePartnerView,
    InvoiceImportCreateView,
    InvoiceImportDetailView,
    InvoiceImportDiscardView,
    InvoiceImportRetryView,
)

urlpatterns = [
    path(
        'purchasing/invoices/import/',
        InvoiceImportCreateView.as_view(),
        name='purchasing-invoice-import-create',
    ),
    path(
        'purchasing/invoices/import/<int:pk>/',
        InvoiceImportDetailView.as_view(),
        name='purchasing-invoice-import-detail',
    ),
    path(
        'purchasing/invoices/import/<int:pk>/retry/',
        InvoiceImportRetryView.as_view(),
        name='purchasing-invoice-import-retry',
    ),
    path(
        'purchasing/invoices/import/<int:pk>/create-partner/',
        InvoiceImportCreatePartnerView.as_view(),
        name='purchasing-invoice-import-create-partner',
    ),
    path(
        'purchasing/invoices/import/<int:pk>/apply-partner-updates/',
        InvoiceImportApplyPartnerUpdatesView.as_view(),
        name='purchasing-invoice-import-apply-partner-updates',
    ),
    path(
        'purchasing/invoices/import/<int:pk>/confirm/',
        InvoiceImportConfirmView.as_view(),
        name='purchasing-invoice-import-confirm',
    ),
    path(
        'purchasing/invoices/import/<int:pk>/discard/',
        InvoiceImportDiscardView.as_view(),
        name='purchasing-invoice-import-discard',
    ),
    path(
        'purchasing/expenses/<int:pk>/eracun-rejection/',
        ExpenseEracunRejectionView.as_view(),
        name='purchasing-expense-eracun-rejection',
    ),
]
