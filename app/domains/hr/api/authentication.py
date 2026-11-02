"""JWT-only authentication for the HR/payroll API."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class HrJWTAuthentication(JWTAuthentication):
    """Anonymous requests return 401 with WWW-Authenticate: Bearer."""

    def authenticate_header(self, request):
        return 'Bearer'
