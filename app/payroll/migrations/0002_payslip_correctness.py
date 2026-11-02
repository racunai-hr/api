from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def backfill_adjustment_kind_and_amount_to_pay(apps, schema_editor):
    Adjustment = apps.get_model('payroll', 'PayslipAdjustmentLine')
    Payslip = apps.get_model('payroll', 'Payslip')
    for line in Adjustment.objects.all():
        if line.amount < 0:
            line.kind = 'deduction'
            line.amount = abs(line.amount)
        elif line.is_taxable:
            line.kind = 'taxable_addition'
        else:
            line.kind = 'non_taxable_receipt'
        line.save(update_fields=['kind', 'amount'])

    for payslip in Payslip.objects.all():
        flags = list(payslip.compliance_flags or [])
        has_adjustments = Adjustment.objects.filter(payslip_id=payslip.id).exists()
        if not has_adjustments:
            payslip.amount_to_pay = payslip.net_salary
        if payslip.status == 'posted' and Adjustment.objects.filter(
            payslip_id=payslip.id, kind='taxable_addition',
        ).exists():
            flags.append({'code': 'historical_adjustment_review'})
        payslip.compliance_flags = flags
        payslip.employee_contributions_total = (payslip.mio1_amount or Decimal('0')) + (payslip.mio2_amount or Decimal('0'))
        payslip.calculation_version = 1
        payslip.save(update_fields=[
            'amount_to_pay', 'compliance_flags', 'employee_contributions_total', 'calculation_version',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='payslip',
            name='amount_to_pay',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15, verbose_name='Iznos za isplatu'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='calculation_version',
            field=models.PositiveSmallIntegerField(default=1, verbose_name='Verzija obračuna'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='compliance_flags',
            field=models.JSONField(blank=True, default=list, verbose_name='Oznake usklađenosti'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='deductions_total',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15, verbose_name='Obustave'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='employee_contributions_total',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15, verbose_name='Doprinosi iz plaće'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='non_taxable_total',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15, verbose_name='Neoporezivi primitci'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='payout_index',
            field=models.PositiveSmallIntegerField(default=1, help_text='Više od 1 kad isti radnik ima više isplata u istom obračunskom mjesecu.', verbose_name='Redni broj isplate u mjesecu'),
        ),
        migrations.AddField(
            model_name='payslipadjustmentline',
            name='kind',
            field=models.CharField(
                choices=[
                    ('taxable_addition', 'Oporezivi dodatak'),
                    ('non_taxable_receipt', 'Neoporezivi primitak'),
                    ('deduction', 'Obustava'),
                ],
                default='taxable_addition',
                max_length=24,
                verbose_name='Vrsta',
            ),
        ),
        migrations.AlterField(
            model_name='payslip',
            name='employee',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payslips', to='payroll.employee', verbose_name='Zaposlenik'),
        ),
        migrations.AlterField(
            model_name='payslip',
            name='period',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payslips', to='payroll.payrollperiod', verbose_name='Period'),
        ),
        migrations.RemoveConstraint(
            model_name='payslip',
            name='unique_payslip_per_period_employee',
        ),
        migrations.AddConstraint(
            model_name='payslip',
            constraint=models.UniqueConstraint(fields=('tenant', 'period', 'employee', 'payout_index'), name='unique_payslip_payout_per_period_employee'),
        ),
        migrations.RunPython(backfill_adjustment_kind_and_amount_to_pay, migrations.RunPython.noop),
    ]
