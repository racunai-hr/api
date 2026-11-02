"""JWT-only authentication for the Assets read API."""

from rest_framework_simplejwt.authentication import JWTAuthentication


class AssetsJWTAuthentication(JWTAuthentication):
    def authenticate_header(self, request):
        return 'Bearer'
