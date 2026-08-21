"""JWT-only authentication for the document read API (ADR-0020)."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class DocumentJWTAuthentication(JWTAuthentication):
    """Anonymous requests return 401 with WWW-Authenticate: Bearer."""

    def authenticate_header(self, request):
        return 'Bearer'
