"""OpenAPI schema serializers for auth endpoints (allowlist, not models)."""

from __future__ import annotations

from rest_framework import serializers


class TokenObtainRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
    turnstile_token = serializers.CharField(required=False, allow_blank=True, write_only=True)


class TokenPairResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class AuthMeUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    is_superuser = serializers.BooleanField()


class AuthMeTenantSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    role = serializers.CharField()
    is_default = serializers.BooleanField()
    admin_url = serializers.CharField()


class AuthMeResponseSerializer(serializers.Serializer):
    user = AuthMeUserSerializer()
    tenants = AuthMeTenantSerializer(many=True)
    platform_admin_url = serializers.CharField()
