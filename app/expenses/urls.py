from django.urls import path

from . import views

app_name = 'expenses'

urlpatterns = [
    path('attachments/<int:pk>/download/', views.attachment_download, name='attachment_download'),
    path('<int:pk>/as4-ubl/', views.expense_as4_ubl, name='expense_as4_ubl'),
    path('<int:pk>/super-pdf/', views.expense_super_pdf, name='expense_super_pdf'),
]
