"""Ovrhe, isplate, NP1/IO1/NO1, audit i zadržavanje isprava."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payroll.models import (
    AnnualLeaveEntitlement,
    EmployeeEnforcement,
    PayrollDocument,
    PayrollPayment,
    Payslip,
    PayslipAdjustmentLine,
    PayslipEarningLine,
)

TWO = Decimal('0.01')
# Početne kvote obustave. Nisu pravna potvrda — source_reference ostaje UNVERIFIED.
DEFAULT_QUOTA = {
    EmployeeEnforcement.MAINTENANCE: Decimal('0.50'),
    EmployeeEnforcement.ORDINARY: Decimal('0.25'),
    EmployeeEnforcement.SPECIAL: Decimal('0.33'),
}
KIND_RANK = {
    EmployeeEnforcement.MAINTENANCE: 0,
    EmployeeEnforcement.SPECIAL: 1,
    EmployeeEnforcement.ORDINARY: 2,
}


def audit(tenant, user, action: str, instance, changes: dict) -> None:
    from accounts.models import AuditLog

    AuditLog.all_objects.create(
        tenant=tenant,
        user=user if getattr(user, 'is_authenticated', False) else None,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(instance.pk),
        changes=changes,
    )


def _round(value: Decimal) -> Decimal:
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


@transaction.atomic
def apply_enforcements(payslip: Payslip, user=None) -> Payslip:
    """Obustave po prioritetu. Zaštićeni dio ostaje radniku, ostatak ide vjerovniku."""
    if payslip.status == Payslip.POSTED:
        raise ValidationError('Knjižena lista se ne dira.')
    net = payslip.net_salary
    claims = list(
        EmployeeEnforcement.all_objects.filter(
            tenant=payslip.tenant, employee=payslip.employee, valid_from__lte=payslip.period.payment_date,
        ).order_by('priority', 'id')
    )
    claims = [c for c in claims if c.valid_to is None or c.valid_to >= payslip.period.payment_date]
    claims.sort(key=lambda c: (KIND_RANK.get(c.kind, 9), c.priority, c.id))
    payslip.adjustment_lines.filter(enforcement__isnull=False).delete()
    seized = Decimal('0.00')
    seized_by_kind: dict[str, Decimal] = {}
    for claim in claims:
        quota = DEFAULT_QUOTA[claim.kind]
        cap = _round(net * quota)
        room = _round(cap - seized_by_kind.get(claim.kind, Decimal('0.00')))
        take = min(claim.amount_remaining, max(room, Decimal('0.00')), _round(net - seized))
        if take <= 0:
            continue
        PayslipAdjustmentLine.all_objects.create(
            tenant=payslip.tenant,
            payslip=payslip,
            name=f'Ovrha {claim.get_kind_display()} — {claim.creditor_name}',
            amount=take,
            is_taxable=False,
            kind=PayslipAdjustmentLine.DEDUCTION,
            enforcement=claim,
            creditor_iban=claim.creditor_iban,
            note=f'priority={claim.priority};quota={quota}',
        )
        claim.amount_remaining = _round(claim.amount_remaining - take)
        claim.save(update_fields=['amount_remaining'])
        seized = _round(seized + take)
        seized_by_kind[claim.kind] = _round(seized_by_kind.get(claim.kind, Decimal('0.00')) + take)
        audit(payslip.tenant, user, 'enforcement_deduction', claim, {'take': str(take), 'payslip': payslip.pk})
    payslip.deductions_total = seized
    payslip.amount_to_pay = _round(payslip.net_salary + payslip.non_taxable_total - seized)
    if seized:
        payslip.protected_total = _round(payslip.amount_to_pay)
        payslip.protected_salary_part = payslip.protected_total
        payslip.regular_account_amount = Decimal('0.00')
    else:
        payslip.protected_total = Decimal('0.00')
        payslip.regular_account_amount = payslip.amount_to_pay
    payslip.save()
    return payslip


def record_payment(payslip: Payslip, amount: Decimal, user=None, connection=None) -> PayrollPayment:
    payment = PayrollPayment.all_objects.create(
        tenant=payslip.tenant, payslip=payslip, kind=PayrollPayment.NET, amount=amount,
    )
    if connection is not None and payslip.employee.iban:
        from banking.services.pis_orders import create_draft_payment_order

        order = create_draft_payment_order(
            connection,
            creditor_iban=payslip.employee.iban,
            creditor_name=payslip.employee.full_name,
            amount=amount,
            reference=f'PAYROLL-{payslip.pk}',
        )
        payment.payment_order = order
        payment.save(update_fields=['payment_order'])
    paid = _round(payslip.paid_amount + amount)
    if paid >= payslip.amount_to_pay and payslip.amount_to_pay > 0:
        payment_status = 'paid'
    elif paid > 0:
        payment_status = 'partially_paid'
    else:
        payment_status = 'due'
    Payslip.all_objects.filter(pk=payslip.pk).update(paid_amount=paid, payment_status=payment_status)
    payslip.paid_amount = paid
    payslip.payment_status = payment_status
    audit(payslip.tenant, user, 'pay', payslip, {'amount': str(amount)})
    return payment


def payment_deadline(period) -> date:
    year = period.year + (1 if period.month == 12 else 0)
    month = 1 if period.month == 12 else period.month + 1
    return date(year, month, 15)


def refresh_payment_compliance(payslip: Payslip, today: date | None = None) -> Payslip:
    today = today or timezone.localdate()
    deadline = payment_deadline(payslip.period)
    if payslip.payment_status == 'paid':
        return payslip
    if today > deadline and payslip.paid_amount <= 0:
        payslip.payment_status = 'unpaid'
    elif today > deadline and payslip.paid_amount < payslip.amount_to_pay:
        payslip.payment_status = 'partially_paid'
    elif today >= payslip.period.payment_date and payslip.payment_status == 'not_due':
        payslip.payment_status = 'due'
    Payslip.all_objects.filter(pk=payslip.pk).update(payment_status=payslip.payment_status)
    return payslip


def _store_document(payslip: Payslip, kind: str, payload: dict) -> PayrollDocument:
    raw = json.dumps(payload, sort_keys=True, default=str)
    return PayrollDocument.all_objects.create(
        tenant=payslip.tenant,
        kind=kind,
        payslip=payslip,
        payload=payload,
        content_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )


def issue_np1(payslip: Payslip, user=None, today: date | None = None) -> PayrollDocument:
    payslip = refresh_payment_compliance(payslip, today)
    if payslip.payment_status not in ('unpaid', 'partially_paid'):
        raise ValidationError('NP1 se izdaje samo za neplaćenu ili djelomično plaćenu plaću nakon roka.')
    unpaid = _round(payslip.amount_to_pay - payslip.paid_amount)
    document = _store_document(payslip, PayrollDocument.NP1, {
        'payslip_id': payslip.pk,
        'unpaid': str(unpaid),
        'deadline': payment_deadline(payslip.period).isoformat(),
    })
    audit(payslip.tenant, user, 'np1', document, {'unpaid': str(unpaid)})
    return document


@transaction.atomic
def build_termination_payslip(payslip: Payslip, *, severance: Decimal, user=None) -> Payslip:
    entitlement = AnnualLeaveEntitlement.all_objects.filter(
        tenant=payslip.tenant, employee=payslip.employee, year=payslip.period.year,
    ).first()
    unused_amount = entitlement.payout_amount if entitlement is not None else Decimal('0.00')
    payslip.kind = 'termination'
    payslip.save(update_fields=['kind'])
    if unused_amount:
        PayslipEarningLine.all_objects.create(
            tenant=payslip.tenant, payslip=payslip, name='Neiskorišteni godišnji odmor',
            gross_amount=unused_amount, source='manual',
        )
    if severance:
        PayslipEarningLine.all_objects.create(
            tenant=payslip.tenant, payslip=payslip, name='Otpremnina',
            gross_amount=severance, source='manual',
        )
    from payroll.services.calculate import calculate_payslip
    payslip = calculate_payslip(payslip)
    kind = PayrollDocument.IO1 if payslip.payment_status == 'paid' else PayrollDocument.NO1
    if payslip.paid_amount <= 0:
        kind = PayrollDocument.NO1
    _store_document(payslip, kind, {
        'payslip_id': payslip.pk,
        'severance': str(severance),
        'unused_leave': str(unused_amount),
        'kind': kind,
    })
    audit(payslip.tenant, user, 'termination', payslip, {'document': kind})
    return payslip


def legal_evidence_gaps(employee) -> list[str]:
    gaps = []
    citizenship = (employee.citizenship or '').strip().upper()
    if citizenship and citizenship not in {'HR', 'HRV', 'RH'} and not (employee.work_permit_number or '').strip():
        gaps.append('work_permit')
    return gaps
