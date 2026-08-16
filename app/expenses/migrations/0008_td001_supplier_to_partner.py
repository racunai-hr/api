"""TD-001: migrate expenses.Supplier → partners.Partner."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


def _supplier_tax_number(supplier) -> str:
    tax_number = (supplier.tax_number or '').strip()
    if tax_number:
        return tax_number
    return f'SUP-{supplier.pk:011d}'


def _upgrade_partner_type(partner, Partner) -> None:
    if partner.partner_type == 'customer':
        partner.partner_type = 'both'
        partner.save(update_fields=['partner_type'])
    elif partner.partner_type not in ('supplier', 'both'):
        Partner.objects.filter(pk=partner.pk).update(partner_type='supplier')


def _next_partner_code(Partner, tenant_id: int) -> str:
    last = (
        Partner.objects.filter(tenant_id=tenant_id)
        .exclude(partner_code='')
        .order_by('-id')
        .values_list('partner_code', flat=True)
        .first()
    )
    if last and str(last).isdigit():
        return f'{int(last) + 1:05d}'
    count = Partner.objects.filter(tenant_id=tenant_id).count()
    return f'{count + 1:05d}'


def migrate_suppliers_to_partners(apps, schema_editor):
    Supplier = apps.get_model('expenses', 'Supplier')
    Partner = apps.get_model('partners', 'Partner')
    Expense = apps.get_model('expenses', 'Expense')
    AnalyticAccount = apps.get_model('accounting', 'AnalyticAccount')

    supplier_to_partner: dict[int, int] = {}

    for supplier in Supplier.objects.all().order_by('pk'):
        partner = None
        tax_number = _supplier_tax_number(supplier)

        if supplier.tax_number:
            partner = Partner.objects.filter(
                tenant_id=supplier.tenant_id,
                tax_number=supplier.tax_number,
            ).first()

        if partner is None and supplier.name:
            partner = Partner.objects.filter(
                tenant_id=supplier.tenant_id,
                name=supplier.name,
            ).first()

        if partner is None:
            partner = Partner.objects.create(
                tenant_id=supplier.tenant_id,
                partner_code=_next_partner_code(Partner, supplier.tenant_id),
                name=supplier.name,
                tax_number=tax_number,
                partner_type='supplier',
                status='active' if supplier.is_active else 'inactive',
                address=supplier.address or '',
                city=supplier.city or '',
                postal_code=supplier.postal_code or '',
                country=supplier.country or 'Hrvatska',
                email=supplier.email or '',
                phone=supplier.phone or '',
            )
        else:
            _upgrade_partner_type(partner, Partner)

        supplier_to_partner[supplier.pk] = partner.pk

    for expense in Expense.objects.filter(supplier_id__isnull=False):
        partner_id = supplier_to_partner.get(expense.supplier_id)
        if partner_id:
            expense.partner_supplier_id = partner_id
            expense.save(update_fields=['partner_supplier_id'])

    for analytic in AnalyticAccount.objects.filter(supplier_id__isnull=False):
        partner_id = supplier_to_partner.get(analytic.supplier_id)
        if partner_id and not analytic.partner_id:
            analytic.partner_id = partner_id
            analytic.counterparty_type = 'supplier'
            analytic.save(update_fields=['partner_id', 'counterparty_type'])


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('partners', '0004_tenant_required'),
        ('expenses', '0007_private_settlement'),
        ('accounting', '0015_submissionevent_v1_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='expense',
            name='partner_supplier',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='partners.partner',
                verbose_name='Dobavljač (partner)',
            ),
        ),
        migrations.RunPython(migrate_suppliers_to_partners, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='expense',
            name='supplier',
        ),
        migrations.RenameField(
            model_name='expense',
            old_name='partner_supplier',
            new_name='supplier',
        ),
        migrations.AlterField(
            model_name='expense',
            name='supplier',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='expenses',
                to='partners.partner',
                verbose_name='Dobavljač',
            ),
        ),
    ]
