from django.urls import path
from . import views

app_name = "invoices"

urlpatterns = [
    path("<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
]
