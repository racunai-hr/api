"""JWT-only authentication for finance partner read API (ADR-0022)."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class FinanceJWTAuthentication(JWTAuthentication):
    def authenticate_header(self, request):
        return 'Bearer'
