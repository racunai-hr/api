from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from tenants.turnstile import verify_turnstile


class TurnstileTokenObtainPairSerializer(TokenObtainPairSerializer):
    turnstile_token = serializers.CharField(required=False, write_only=True)

    def validate(self, attrs):
        from django.conf import settings

        if getattr(settings, 'TURNSTILE_VERIFY_ENABLED', False):
            token = attrs.pop('turnstile_token', None) or self.initial_data.get('turnstile_token')
            if not token:
                raise serializers.ValidationError({
                    'turnstile_token': 'CAPTCHA je obavezna.',
                })

            request = self.context.get('request')
            remote_ip = None
            if request is not None:
                forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
                remote_ip = forwarded.split(',')[0].strip() or request.META.get('REMOTE_ADDR')

            if not verify_turnstile(token, remote_ip):
                raise serializers.ValidationError({
                    'turnstile_token': 'CAPTCHA provjera nije uspjela. Pokušajte ponovno.',
                })
        else:
            attrs.pop('turnstile_token', None)

        return super().validate(attrs)
