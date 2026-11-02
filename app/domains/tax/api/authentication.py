"""JWT-only authentication for tax read API."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class TaxJWTAuthentication(JWTAuthentication):
    def authenticate_header(self, request):
        return 'Bearer'
