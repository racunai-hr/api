"""JWT-only authentication for the banking read API (ADR-0021)."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class BankingJWTAuthentication(JWTAuthentication):
    """Anonymous requests return 401 with WWW-Authenticate: Bearer."""

    def authenticate_header(self, request):
        return 'Bearer'
