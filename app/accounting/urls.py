from django.urls import path

from . import views

app_name = 'accounting'

urlpatterns = [
    path('vat/<int:period_id>/export/', views.vat_export, name='vat_export'),
    path('vat/<int:period_id>/pdv-s.xml', views.pdv_s_xml_export, name='pdv_s_xml_export'),
    path('vat/<int:period_id>/zp.xml', views.zp_xml_export, name='zp_xml_export'),
    path('reports/trial-balance/<int:year>/<int:month>/', views.trial_balance_export, name='trial_balance_export'),
    path('reports/bilanca/<int:year>/<int:month>/', views.bilanca_export, name='bilanca_export'),
    path('reports/rdg/<int:year>/<int:month>/', views.rdg_export, name='rdg_export'),
    path('reports/journal/<int:year>/<int:month>/', views.journal_export, name='journal_export'),
]
