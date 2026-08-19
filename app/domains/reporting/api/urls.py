from django.urls import path

from domains.reporting.api.views import (
    DocumentAttachmentDownloadView,
    DocumentDetailView,
    DocumentExportView,
    DocumentListView,
    DocumentPdfView,
)

urlpatterns = [
    path('documents/', DocumentListView.as_view(), name='document-list'),
    path('documents/export/', DocumentExportView.as_view(), name='document-export'),
    path('documents/<str:direction>/<int:pk>/', DocumentDetailView.as_view(), name='document-detail'),
    path('documents/<str:direction>/<int:pk>/pdf/', DocumentPdfView.as_view(), name='document-pdf'),
    path(
        'documents/incoming/<int:pk>/attachments/<int:attachment_id>/',
        DocumentAttachmentDownloadView.as_view(),
        name='document-attachment',
    ),
]
