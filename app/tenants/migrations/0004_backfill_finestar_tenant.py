from django.db import migrations


TENANT_MODELS = [
    ('partners', 'Partner'),
    ('partners', 'PartnerType'),
    ('invoices', 'Invoice'),
    ('payments', 'BankAccount'),
    ('payments', 'Payment'),
    ('expenses', 'ExpenseCategory'),
    ('expenses', 'Supplier'),
    ('expenses', 'Expense'),
    ('accounting', 'AccountType'),
    ('accounting', 'ChartOfAccounts'),
    ('accounting', 'JournalEntry'),
    ('banking', 'BankStatement'),
    ('settings', 'TaxRate'),
    ('settings', 'CompanySettings'),
    ('settings', 'SystemParameter'),
    ('dashboard', 'Notification'),
    ('dashboard', 'KPIMetric'),
    ('accounts', 'AuditLog'),
]


def backfill_finestar_tenant(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    tenant, _ = Tenant.objects.get_or_create(
        slug='finestar',
        defaults={'name': 'Fine Star d.o.o.', 'is_active': True},
    )
    for app_label, model_name in TENANT_MODELS:
        Model = apps.get_model(app_label, model_name)
        Model.objects.filter(tenant__isnull=True).update(tenant=tenant)


def clear_tenant_assignments(apps, schema_editor):
    for app_label, model_name in TENANT_MODELS:
        Model = apps.get_model(app_label, model_name)
        Model.objects.all().update(tenant=None)


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0003_add_tenant_fields'),
        ('partners', '0003_add_tenant_fields'),
        ('invoices', '0009_add_tenant_fields'),
        ('payments', '0002_add_tenant_fields'),
        ('expenses', '0002_add_tenant_fields'),
        ('accounting', '0002_add_tenant_fields'),
        ('banking', '0002_add_tenant_fields'),
        ('settings', '0008_add_tenant_fields'),
        ('dashboard', '0003_add_tenant_fields'),
        ('accounts', '0004_add_tenant_fields'),
    ]

    operations = [
        migrations.RunPython(backfill_finestar_tenant, clear_tenant_assignments),
    ]
