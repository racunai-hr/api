from django.urls import path

from . import views

urlpatterns = [
    path('banking/connect/', views.connect_view, name='banking_connect'),
    path('oauth/callback/', views.oauth_callback_view, name='banking_oauth_callback'),
    path('oauth/payment-callback/', views.payment_callback_view, name='banking_payment_callback'),
    path('banking/accounts/', views.accounts_view, name='banking_accounts'),
    path('banking/payments/<int:payment_id>/initiate/', views.initiate_payment_view, name='banking_payment_initiate'),
]
