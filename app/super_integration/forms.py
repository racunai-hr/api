from django import forms

from .models import SuperTenantConfig


class SuperTenantConfigForm(forms.ModelForm):
    password = forms.CharField(
        label='SUPER lozinka',
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text='Ostavite prazno pri uređivanju da zadržite postojeću lozinku.',
    )

    class Meta:
        model = SuperTenantConfig
        fields = (
            'username',
            'password',
            'company_guid',
            'is_active',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_password = self.instance.password if self.instance.pk else None
        if self.instance.pk:
            self.fields['password'].required = False
        else:
            self.fields['password'].required = True

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        if not self.instance.pk and not password:
            self.add_error('password', 'Lozinka je obavezna pri kreiranju konfiguracije.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            instance.password = password
        elif self._original_password:
            instance.password = self._original_password
        if commit:
            instance.save()
        return instance
