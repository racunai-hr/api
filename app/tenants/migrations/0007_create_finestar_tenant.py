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
    ('accounting', 'AnalyticAccount'),
    ('accounting', 'FiscalPeriod'),
    ('accounting', 'JournalEntry'),
    ('accounting', 'PostingRule'),
    ('accounting', 'VATPeriod'),
    ('accounting', 'VATLedgerEntry'),
    ('banking', 'BankStatement'),
    ('banking', 'BankTransaction'),
    ('settings', 'TaxRate'),
    ('settings', 'CompanySettings'),
    ('settings', 'SystemParameter'),
    ('dashboard', 'Notification'),
    ('dashboard', 'KPIMetric'),
    ('accounts', 'AuditLog'),
]


def create_finestar_and_move_data(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    TenantMembership = apps.get_model('tenants', 'TenantMembership')
    TenantInvitation = apps.get_model('tenants', 'TenantInvitation')

    demo, _ = Tenant.objects.get_or_create(
        slug='demo',
        defaults={'name': 'Demo d.o.o.', 'is_active': True},
    )
    demo.name = 'Demo d.o.o.'
    demo.is_active = True
    demo.save(update_fields=['name', 'is_active'])

    finestar, created = Tenant.objects.get_or_create(
        slug='finestar',
        defaults={'name': 'Fine Star d.o.o.', 'is_active': True},
    )
    if not created:
        finestar.name = 'Fine Star d.o.o.'
        finestar.is_active = True
        finestar.save(update_fields=['name', 'is_active'])

    for app_label, model_name in TENANT_MODELS:
        Model = apps.get_model(app_label, model_name)
        Model.objects.filter(tenant=demo).update(tenant=finestar)

    TenantMembership.objects.filter(tenant=demo).update(tenant=finestar)
    TenantInvitation.objects.filter(tenant=demo).update(tenant=finestar)


def move_data_back_to_demo(apps, schema_editor):
    Tenant = apps.get_model('tenants', 'Tenant')
    TenantMembership = apps.get_model('tenants', 'TenantMembership')
    TenantInvitation = apps.get_model('tenants', 'TenantInvitation')

    demo = Tenant.objects.filter(slug='demo').first()
    finestar = Tenant.objects.filter(slug='finestar').first()
    if not demo or not finestar:
        return

    for app_label, model_name in TENANT_MODELS:
        Model = apps.get_model(app_label, model_name)
        Model.objects.filter(tenant=finestar).update(tenant=demo)

    TenantMembership.objects.filter(tenant=finestar).update(tenant=demo)
    TenantInvitation.objects.filter(tenant=finestar).update(tenant=demo)
    finestar.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0006_rename_finestar_to_demo'),
        ('partners', '0004_tenant_required'),
        ('invoices', '0010_tenant_required'),
        ('payments', '0004_eur_default_currency'),
        ('expenses', '0004_expense_category_default_account'),
        ('accounting', '0004_accounting_module_expansion'),
        ('banking', '0005_banktransaction_tenant_required'),
        ('settings', '0009_tenant_required'),
        ('dashboard', '0004_tenant_required'),
        ('accounts', '0005_tenant_required'),
    ]

    operations = [
        migrations.RunPython(create_finestar_and_move_data, move_data_back_to_demo),
    ]
