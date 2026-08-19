"""JWT-only authentication for the partners MDM API (ADR-0022)."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class PartnersJWTAuthentication(JWTAuthentication):
    """Anonymous requests return 401 with WWW-Authenticate: Bearer."""

    def authenticate_header(self, request):
        return 'Bearer'
