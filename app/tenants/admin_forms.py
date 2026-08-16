from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from tenants.turnstile import turnstile_required_for_request, verify_turnstile


class TurnstileAdminAuthenticationForm(AdminAuthenticationForm):
    def clean(self):
        if turnstile_required_for_request(self.request):
            token = self.data.get('cf-turnstile-response')
            if not token:
                raise ValidationError(
                    _('CAPTCHA je obavezna.'),
                    code='turnstile_required',
                )

            forwarded = self.request.META.get('HTTP_X_FORWARDED_FOR', '')
            remote_ip = (
                forwarded.split(',')[0].strip()
                or self.request.META.get('REMOTE_ADDR')
            )
            if not verify_turnstile(token, remote_ip):
                raise ValidationError(
                    _('CAPTCHA provjera nije uspjela. Pokušajte ponovno.'),
                    code='turnstile_failed',
                )

        return super().clean()
