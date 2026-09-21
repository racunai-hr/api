"""Verification — TZ2 arithmetic identities from official primjer.xml."""

from __future__ import annotations

from accounting.services.tax_forms.tz2.payload import Tz2Payload, money


def verify_tz2_arithmetic(payload: Tz2Payload) -> list[str]:
    """Return field mismatches; empty list means identities hold."""
    errors: list[str] = []
    expected_25 = money(
        payload.room_bed_total
        + payload.aux_bed_total
        + payload.camp_total
        + payload.robinson_total
        + payload.opg_room_bed_total
        + payload.opg_aux_bed_total
        + payload.opg_camp_total
        + payload.opg_robinson_total
    )
    if payload.total_assessed != expected_25:
        errors.append(f'Podatak25 {payload.total_assessed} != {expected_25}')

    expected_30 = money(
        payload.discount_group_1
        + payload.discount_group_2
        + payload.discount_group_3
        + payload.discount_group_4
    )
    if payload.total_discount != expected_30:
        errors.append(f'Podatak30 {payload.total_discount} != {expected_30}')

    expected_31 = money(payload.total_assessed - payload.total_discount)
    if payload.amount_after_discount != expected_31:
        errors.append(f'Podatak31 {payload.amount_after_discount} != {expected_31}')

    if payload.payment_installments:
        if payload.lump_sum_flag != '0' or payload.installment_flag != '1':
            errors.append('Obročno plaćanje zahtijeva Podatak32=0 i Podatak34=1.')
        expected_rata = money(payload.amount_after_discount / 3)
        if payload.installment_amount != expected_rata:
            errors.append(f'Podatak35 {payload.installment_amount} != {expected_rata}')
        if payload.lump_sum_amount != money('0.00'):
            errors.append('Podatak33 mora biti 0.00 pri obročnom plaćanju.')
    else:
        if payload.lump_sum_flag != '1' or payload.installment_flag != '0':
            errors.append('Jednokratna uplata zahtijeva Podatak32=1 i Podatak34=0.')
        if payload.lump_sum_amount != payload.amount_after_discount:
            errors.append(f'Podatak33 {payload.lump_sum_amount} != {payload.amount_after_discount}')
        if payload.installment_amount != money('0.00'):
            errors.append('Podatak35 mora biti 0.00 pri jednokratnoj uplati.')
    return errors
