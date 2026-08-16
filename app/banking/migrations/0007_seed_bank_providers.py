from django.db import migrations


def seed_bank_providers(apps, schema_editor):
    BankProvider = apps.get_model('banking', 'BankProvider')
    providers = [
        {
            'code': 'otp_sandbox',
            'name': 'OTP Sandbox',
            'environment': 'sandbox',
            'iam_base': 'https://iam.sandbox.otpbanka.hr',
            'api_base': 'https://api.sandbox.otpbanka.hr',
            'service_host': 'otp-sbx.racunai.hr',
            'is_active': True,
        },
        {
            'code': 'otp_production',
            'name': 'OTP Production',
            'environment': 'production',
            'iam_base': 'https://iam.otpbanka.hr',
            'api_base': 'https://api.otpbanka.hr',
            'service_host': 'otp.racunai.hr',
            'is_active': True,
        },
    ]
    for data in providers:
        BankProvider.objects.update_or_create(code=data['code'], defaults=data)


def unseed_bank_providers(apps, schema_editor):
    BankProvider = apps.get_model('banking', 'BankProvider')
    BankProvider.objects.filter(code__in=['otp_sandbox', 'otp_production']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('banking', '0006_otp_banking_foundation'),
    ]

    operations = [
        migrations.RunPython(seed_bank_providers, unseed_bank_providers),
    ]
