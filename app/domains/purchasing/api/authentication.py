"""Purchasing domain JWT authentication."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class PurchasingJWTAuthentication(JWTAuthentication):
    def authenticate_header(self, request):
        return 'Bearer'
