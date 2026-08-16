from __future__ import annotations

from decimal import Decimal

from ubl.domain.document import UblDocument
from ubl.domain.errors import SemanticValidationError
from ubl.hrcius.constants import CUSTOMIZATION_ID, ENDPOINT_SCHEME_ID


def _validate_oib(oib: str, field: str, errors: list[str]) -> None:
    cleaned = (oib or '').replace('HR', '').strip()
    if len(cleaned) != 11 or not cleaned.isdigit():
        errors.append(f'{field}: OIB mora imati 11 znamenki')
        return
    if cleaned in {'00000000000', '11111111111', '99999999999'}:
        errors.append(f'{field}: OIB ne smije biti placeholder')


def _oib_checksum(oib: str) -> bool:
    digits = [int(c) for c in oib]
    check = 10
    for d in digits[:10]:
        check = (check + d) % 10
        if check == 0:
            check = 10
        check = (check * 2) % 11
    control = 11 - check
    if control == 10:
        control = 0
    return control == digits[10]


def validate_semantic(document: UblDocument) -> None:
    errors: list[str] = []

    if document.customization_id != CUSTOMIZATION_ID:
        errors.append('customization_id: mora biti HR CIUS 2025 profil')

    if document.profile_id != 'P1' and document.invoice_type_code == '380':
        errors.append('profile_id: standardni račun zahtijeva P1')

    _validate_oib(document.supplier.oib, 'supplier.oib', errors)
    _validate_oib(document.customer.oib, 'customer.oib', errors)

    if document.supplier.oib and len(document.supplier.oib.replace('HR', '')) == 11:
        cleaned_supplier = document.supplier.oib.replace('HR', '')
        if not cleaned_supplier.startswith('99999') and not _oib_checksum(cleaned_supplier):
            errors.append('supplier.oib: neispravna kontrolna znamenka')

    if not document.lines:
        errors.append('lines: račun mora imati barem jednu stavku')

    line_net = sum((line.line_extension_amount for line in document.lines), Decimal('0'))
    if line_net != document.totals.line_extension_amount:
        errors.append('totals.line_extension_amount: ne odgovara zbroju stavki')
    if line_net != document.totals.tax_exclusive_amount:
        errors.append('totals.tax_exclusive_amount: ne odgovara zbroju stavki')

    tax_from_lines: dict[Decimal, Decimal] = {}
    for line in document.lines:
        rate = line.tax_percent
        tax_from_lines[rate] = tax_from_lines.get(rate, Decimal('0')) + (
            line.line_extension_amount * rate / Decimal('100')
        ).quantize(Decimal('0.01'))

    subtotal_tax = sum((s.tax_amount for s in document.tax_subtotals), Decimal('0'))
    if subtotal_tax != document.totals.tax_inclusive_amount - document.totals.tax_exclusive_amount:
        errors.append('tax_subtotals: ukupan PDV ne odgovara razlici iznosa')

    for sub in document.tax_subtotals:
        expected = tax_from_lines.get(sub.percent, Decimal('-1'))
        if expected >= 0 and abs(expected - sub.tax_amount) > Decimal('0.02'):
            errors.append(f'tax_subtotals[{sub.percent}%]: iznos PDV ne odgovara stavkama')

    payable = document.totals.tax_exclusive_amount + subtotal_tax
    if document.invoice_type_code == '381':
        if document.totals.payable_amount > Decimal('0'):
            errors.append('credit_note: očekivan negativan ili nulti iznos')
    elif payable != document.totals.payable_amount:
        errors.append('totals.payable_amount: ne odgovara osnovici + PDV')

    if document.due_date and document.due_date < document.issue_date:
        errors.append('due_date: mora biti >= issue_date')

    # Reverse charge lines use category AE
    if any(line.tax_category == 'AE' for line in document.lines):
        if not all(line.tax_category == 'AE' for line in document.lines):
            errors.append('reverse_charge: sve stavke moraju imati kategoriju AE')

    if errors:
        raise SemanticValidationError(errors)
