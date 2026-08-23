"""PDV mapping rules derived from VAT_BOX_REGISTRY."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from accounting.models import VATEntryCategory, VATLedgerEntry
from accounting.services.tax_forms.pdv.boxes import implemented_boxes

PDV_MAPPING_VERSION = 11

_OUTPUT_RRIF_BY_RATE: dict[Decimal, str] = {
    Decimal('5.00'): '240010',
    Decimal('13.00'): '240011',
    Decimal('25.00'): '24001',
}

_JOURNAL_OUTPUT_ACCOUNT_TO_BOX: dict[str, str] = {
    '240010': '201',
    '240011': '202',
    '24001': '203',
}

_JOURNAL_INPUT_ACCOUNTS = frozenset({'1400'})

_EU_VAT_NUMBER_PREFIXES = frozenset(
    {
        'AT', 'BE', 'BG', 'CY', 'CZ', 'DE', 'DK', 'EE', 'EL', 'ES', 'FI', 'FR',
        'HR', 'HU', 'IE', 'IT', 'LT', 'LU', 'LV', 'MT', 'NL', 'PL', 'PT', 'RO',
        'SE', 'SI', 'SK', 'XI',
    }
)

_HR_COUNTRY_NAMES = frozenset({'croatia', 'hrvatska', 'hr'})
_CH_COUNTRY_NAMES = frozenset({'switzerland', 'švicarska', 'svicarska', 'schweiz', 'suisse', 'ch'})
_CVH_STP_NAME_RE = re.compile(r'\b(?:cvh|stp)\b', re.IGNORECASE)
_BANK_NAME_RE = re.compile(r'\b(?:banka|bank)\b', re.IGNORECASE)
_INSURANCE_NAME_RE = re.compile(r'\bosiguranje\b', re.IGNORECASE)
_TELECOM_NAME_RE = re.compile(r'\btelecom', re.IGNORECASE)
_VIN_RE = re.compile(r'(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])')

_EU_MEMBER_COUNTRY_NAMES = frozenset(
    {
        'austria', 'belgium', 'bulgaria', 'cyprus', 'czech republic', 'czechia',
        'denmark', 'estonia', 'finland', 'france', 'germany', 'greece', 'hungary',
        'ireland', 'italy', 'latvia', 'lithuania', 'luxembourg', 'malta',
        'netherlands', 'poland', 'portugal', 'romania', 'slovakia', 'slovenia',
        'spain', 'sweden',
        'deutschland', 'njemačka', 'njemacka',
    }
)

EU_GOODS_ASSET_ACCOUNTS = frozenset({'0373'})
EU_GOODS_PRETPOREZ_ACCOUNTS = frozenset({'14020', '14021', '14022'})
EU_GOODS_OBVEZA_ACCOUNTS = frozenset({'24020', '24021', '24022'})
EU_SERVICES_PRETPOREZ_ACCOUNTS = frozenset({'14030', '14031', '14032'})
EU_SERVICES_OBVEZA_ACCOUNTS = frozenset({'24030', '24031', '24032'})
DOMESTIC_RC_PRETPOREZ_ACCOUNTS = frozenset({'1401', '14010', '14011'})
DOMESTIC_RC_OBVEZA_ACCOUNTS = frozenset({'2401', '24010', '24011'})
IOSS_PRETPOREZ_ACCOUNTS = frozenset({'14040', '14041', '14042'})

RC_INPUT_BOX_TO_OUTPUT = {
    '307': '207',
    '309': '209',
    '306': '210',
}
RC_OUTPUT_BOXES = frozenset(RC_INPUT_BOX_TO_OUTPUT.values())
RC_INPUT_BOXES = frozenset(RC_INPUT_BOX_TO_OUTPUT.keys())

VIII1_VAT_AGGREGATE_BOXES = frozenset({'611', '613', '615'})
VIII1_BASE_AGGREGATE_BOXES = frozenset({'612', '614'})
VIII1_SUB_BOXES = frozenset({'611', '612', '613', '614', '615'})

_VIII1_SKIP_ACCOUNTS = (
    EU_GOODS_ASSET_ACCOUNTS
    | EU_GOODS_PRETPOREZ_ACCOUNTS
    | EU_GOODS_OBVEZA_ACCOUNTS
    | EU_SERVICES_PRETPOREZ_ACCOUNTS
    | EU_SERVICES_OBVEZA_ACCOUNTS
    | DOMESTIC_RC_PRETPOREZ_ACCOUNTS
    | DOMESTIC_RC_OBVEZA_ACCOUNTS
    | _JOURNAL_INPUT_ACCOUNTS
    | IOSS_PRETPOREZ_ACCOUNTS
)

_EU_RC_RATE = Decimal('25.00')


class RegistryMappingError(Exception):
    """Raised when registry and _MAPPING_RULES are out of sync."""


@dataclass(frozen=True)
class MappingRule:
    vat_box: str
    ledger_type: str
    entry_category: str
    rrif_vat_account: str


def _output_rule(vat_box: str, rate: Decimal) -> MappingRule:
    return MappingRule(
        vat_box=vat_box,
        ledger_type=VATLedgerEntry.LEDGER_I_RA,
        entry_category=VATEntryCategory.DOMESTIC,
        rrif_vat_account=_OUTPUT_RRIF_BY_RATE[rate],
    )


def _eu_rule(vat_box: str, *, account: str) -> MappingRule:
    return MappingRule(
        vat_box=vat_box,
        ledger_type=VATLedgerEntry.LEDGER_U_RA,
        entry_category=VATEntryCategory.EU_ACQUISITION,
        rrif_vat_account=account,
    )


def map_invoice_output_5() -> MappingRule:
    return _output_rule('201', Decimal('5.00'))


def map_invoice_output_13() -> MappingRule:
    return _output_rule('202', Decimal('13.00'))


def map_invoice_output_25() -> MappingRule:
    return _output_rule('203', Decimal('25.00'))


def _eu_outbound_rule(vat_box: str) -> MappingRule:
    return MappingRule(
        vat_box=vat_box,
        ledger_type=VATLedgerEntry.LEDGER_I_RA,
        entry_category=VATEntryCategory.EU_SUPPLY,
        rrif_vat_account='',
    )


def map_invoice_eu_goods_101() -> MappingRule:
    return _eu_outbound_rule('101')


def map_invoice_eu_services_103() -> MappingRule:
    return _eu_outbound_rule('103')


def _oss_output_rule(vat_box: str) -> MappingRule:
    return MappingRule(
        vat_box=vat_box,
        ledger_type=VATLedgerEntry.LEDGER_I_RA,
        entry_category=VATEntryCategory.OSS_SUPPLY,
        rrif_vat_account='',
    )


def map_invoice_oss_215() -> MappingRule:
    return _oss_output_rule('215')


def map_invoice_eu_distance_204() -> MappingRule:
    return _oss_output_rule('204')


def map_invoice_eu_electronic_214() -> MappingRule:
    return _oss_output_rule('214')


def map_ioss_pretporez_308() -> MappingRule:
    return MappingRule(
        vat_box='308',
        ledger_type=VATLedgerEntry.LEDGER_U_RA,
        entry_category=VATEntryCategory.OSS_SUPPLY,
        rrif_vat_account='14042',
    )


def map_expense_input_25() -> MappingRule:
    return MappingRule(
        vat_box='303',
        ledger_type=VATLedgerEntry.LEDGER_U_RA,
        entry_category=VATEntryCategory.DOMESTIC,
        rrif_vat_account='1400',
    )


def map_eu_goods_base_207() -> MappingRule:
    return _eu_rule('207', account='0373')


def map_eu_goods_obveza_207() -> MappingRule:
    return _eu_rule('207', account='24022')


def map_eu_goods_pretporez_307() -> MappingRule:
    return _eu_rule('307', account='14022')


def _reverse_charge_rule(vat_box: str, *, account: str, category: str) -> MappingRule:
    return MappingRule(
        vat_box=vat_box,
        ledger_type=VATLedgerEntry.LEDGER_U_RA if vat_box in RC_INPUT_BOXES else VATLedgerEntry.LEDGER_I_RA,
        entry_category=category,
        rrif_vat_account=account,
    )


def map_domestic_rc_obveza_209() -> MappingRule:
    return _reverse_charge_rule(
        '209',
        account='2401',
        category=VATEntryCategory.DOMESTIC,
    )


def map_domestic_rc_pretporez_309() -> MappingRule:
    return _reverse_charge_rule(
        '309',
        account='1401',
        category=VATEntryCategory.DOMESTIC,
    )


def map_eu_services_obveza_210() -> MappingRule:
    return _reverse_charge_rule(
        '210',
        account='24032',
        category=VATEntryCategory.EU_ACQUISITION,
    )


def map_eu_services_pretporez_306() -> MappingRule:
    return _reverse_charge_rule(
        '306',
        account='14032',
        category=VATEntryCategory.EU_ACQUISITION,
    )


def _viii1_rule(vat_box: str, *, account: str) -> MappingRule:
    return MappingRule(
        vat_box=vat_box,
        ledger_type=VATLedgerEntry.LEDGER_U_RA,
        entry_category=VATEntryCategory.ADJUSTMENT,
        rrif_vat_account=account,
    )


def map_viii1_total_610() -> MappingRule:
    return _viii1_rule('610', account='')


def map_viii1_real_estate_611() -> MappingRule:
    return _viii1_rule('611', account='05')


def map_viii1_cars_acquisition_612() -> MappingRule:
    return _viii1_rule('612', account='032')


def map_viii1_cars_disposal_613() -> MappingRule:
    return _viii1_rule('613', account='032')


def map_viii1_other_di_acquisition_614() -> MappingRule:
    return _viii1_rule('614', account='03')


def map_viii1_other_di_disposal_615() -> MappingRule:
    return _viii1_rule('615', account='03')


def map_eu_expense_viii1_614() -> MappingRule:
    return MappingRule(
        vat_box='614',
        ledger_type=VATLedgerEntry.LEDGER_U_RA,
        entry_category=VATEntryCategory.EU_ACQUISITION,
        rrif_vat_account='',
    )


_MAPPING_RULES: dict[str, Callable[[], MappingRule]] = {
    '101': map_invoice_eu_goods_101,
    '103': map_invoice_eu_services_103,
    '204': map_invoice_eu_distance_204,
    '214': map_invoice_eu_electronic_214,
    '215': map_invoice_oss_215,
    '201': map_invoice_output_5,
    '202': map_invoice_output_13,
    '203': map_invoice_output_25,
    '303': map_expense_input_25,
    '207': map_eu_goods_base_207,
    '209': map_domestic_rc_obveza_209,
    '210': map_eu_services_obveza_210,
    '306': map_eu_services_pretporez_306,
    '307': map_eu_goods_pretporez_307,
    '309': map_domestic_rc_pretporez_309,
    '308': map_ioss_pretporez_308,
    '610': map_viii1_total_610,
    '611': map_viii1_real_estate_611,
    '612': map_viii1_cars_acquisition_612,
    '613': map_viii1_cars_disposal_613,
    '614': map_viii1_other_di_acquisition_614,
    '615': map_viii1_other_di_disposal_615,
}


def build_mapping_from_registry() -> dict[str, MappingRule]:
    rules: dict[str, MappingRule] = {}
    for box in implemented_boxes():
        factory = _MAPPING_RULES.get(box.code)
        if factory is None:
            raise RegistryMappingError(
                f'Box {box.code} implemented=True ali nema pravila u _MAPPING_RULES'
            )
        rules[box.code] = factory()
    return rules


PDV_MAPPING = build_mapping_from_registry()


def normalize_vat_rate(rate: Decimal | int | float | str) -> Decimal:
    return Decimal(str(rate)).quantize(Decimal('0.01'))


def _compact_vat_identifier(value: str) -> str:
    return (value or '').strip().replace(' ', '').replace('.', '').replace('-', '').upper()


def _vat_id_prefix(value: str) -> str:
    compact = _compact_vat_identifier(value)
    if len(compact) >= 2 and compact[:2].isalpha():
        return compact[:2]
    return ''


def _has_vin(text: str) -> bool:
    if not text:
        return False
    return bool(_VIN_RE.search(text.upper()))


def is_eu_supplier(supplier) -> bool:
    """True when supplier is in EU (not Croatia), using country name or VAT ID prefix.

    Live partners often store the EU VAT ID on `vat_id` / `vat_number` while
    `tax_number` is empty and `country` is a Croatian exonym (e.g. Njemačka).
    """
    if supplier is None:
        return False

    tax_number = _compact_vat_identifier(getattr(supplier, 'tax_number', '') or '')
    if len(tax_number) == 11 and tax_number.isdigit():
        return False

    vat_id = _compact_vat_identifier(
        getattr(supplier, 'vat_id', '') or getattr(supplier, 'vat_number', '') or ''
    )
    for candidate in (tax_number, vat_id):
        prefix = _vat_id_prefix(candidate)
        if prefix == 'HR':
            return False
        if prefix in _EU_VAT_NUMBER_PREFIXES:
            return True

    country = (getattr(supplier, 'country', '') or '').strip().lower()
    if country in _HR_COUNTRY_NAMES:
        return False
    return country in _EU_MEMBER_COUNTRY_NAMES


def is_eu_goods_acquisition(
    supplier,
    *,
    vat_amount: Decimal,
    base_amount: Decimal,
    description: str = '',
    supply_kind: str = '',
) -> bool:
    """Intra-community goods acquisition (PDV II.7 / III.7) — not a foreign-expense fallback.

    Requires an EU supplier, zero charged VAT, positive net, and a goods signal:
    explicit `supply_kind='goods'` or a VIN in the description. Swiss / other
    third-country 0% expenses do not qualify.
    """
    if not is_eu_supplier(supplier):
        return False
    if Decimal(vat_amount or 0) != 0:
        return False
    if Decimal(base_amount or 0) <= 0:
        return False
    if (supply_kind or '').strip().lower() == 'goods':
        return True
    return _has_vin(description)


def is_hr_supplier(supplier) -> bool:
    """True when supplier is Croatian (OIB or HR country / VAT prefix)."""
    if supplier is None:
        return False
    tax_number = (getattr(supplier, 'tax_number', '') or '').strip().replace(' ', '')
    if len(tax_number) == 11 and tax_number.isdigit():
        return True
    country = (getattr(supplier, 'country', '') or '').strip().lower()
    if country in _HR_COUNTRY_NAMES:
        return True
    if len(tax_number) >= 2 and tax_number[:2].upper() == 'HR':
        return True
    vat_id = (getattr(supplier, 'vat_id', '') or getattr(supplier, 'vat_number', '') or '').strip()
    return len(vat_id) >= 2 and vat_id[:2].upper() == 'HR'


def is_cvh_stp_supplier(supplier) -> bool:
    """True when the partner name identifies a CVH / STP inspection station."""
    if supplier is None or not is_hr_supplier(supplier):
        return False
    name = getattr(supplier, 'name', '') or ''
    return bool(_CVH_STP_NAME_RE.search(name))


def is_hr_bank_supplier(supplier) -> bool:
    if supplier is None or not is_hr_supplier(supplier):
        return False
    name = getattr(supplier, 'name', '') or ''
    return bool(_BANK_NAME_RE.search(name))


def is_hr_insurance_supplier(supplier) -> bool:
    if supplier is None or not is_hr_supplier(supplier):
        return False
    name = getattr(supplier, 'name', '') or ''
    return bool(_INSURANCE_NAME_RE.search(name))


def is_ch_supplier(supplier) -> bool:
    """True when supplier is Swiss (country / country_code / CHE UID) and not HR/EU."""
    if supplier is None or is_hr_supplier(supplier) or is_eu_supplier(supplier):
        return False
    country = (getattr(supplier, 'country', '') or '').strip().lower()
    if country in _CH_COUNTRY_NAMES:
        return True
    country_code = (getattr(supplier, 'country_code', '') or '').strip().upper()
    if country_code == 'CH':
        return True
    for raw in (
        getattr(supplier, 'tax_number', '') or '',
        getattr(supplier, 'vat_id', '') or '',
        getattr(supplier, 'vat_number', '') or '',
    ):
        if _compact_vat_identifier(raw).startswith('CHE'):
            return True
    return False


def is_ch_telecom_supplier(supplier) -> bool:
    """Swiss telecom identity (e.g. Telecom26 AG). Not a generic third-country fallback."""
    if not is_ch_supplier(supplier):
        return False
    name = getattr(supplier, 'name', '') or ''
    return bool(_TELECOM_NAME_RE.search(name))


def cvh_mixed_25_amounts(
    *,
    base_amount: Decimal,
    vat_amount: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """Reconstruct 25% pretporez from header tax when the rest of net is 0%.

    Uses net `base_amount` (gross − tax). Not a generic unknown-rate dump:
    callers must also prove CVH/STP identity.
    """
    base = Decimal(base_amount or 0)
    vat = Decimal(vat_amount or 0)
    if vat <= 0 or base <= 0:
        return None
    rate_25 = Decimal('25.00')
    taxable_base = pretporez_base_from_vat(vat, rate_25)
    if taxable_base <= 0 or taxable_base > base:
        return None
    if rc_vat_from_base(taxable_base, rate_25) != vat:
        return None
    if taxable_base == base:
        return None
    return taxable_base, vat


def is_eu_customer(customer) -> bool:
    """True when invoice recipient is in EU (not Croatia)."""
    return is_eu_supplier(customer)


def partner_eu_vat_id(partner) -> str:
    """EU PDV ID for ledger/ZP — prefers vat_number, falls back to tax_number."""
    vat = (getattr(partner, 'vat_number', '') or '').strip().replace(' ', '')
    if vat:
        return vat
    return (getattr(partner, 'tax_number', '') or '').strip().replace(' ', '')


def invoice_eu_outbound_box(*, has_service_date: bool) -> str:
    """Map EU outbound invoice to box 101 (goods) or 103 (services)."""
    return '103' if has_service_date else '101'


def is_eu_outbound_invoice_line(*, partner, tax_rate: Decimal | int | float | str) -> bool:
    """True when invoice line is 0% supply to an EU B2B customer."""
    if not is_eu_customer(partner):
        return False
    if normalize_vat_rate(tax_rate) != Decimal('0.00'):
        return False
    vat_id = partner_eu_vat_id(partner)
    if len(vat_id) < 3 or not vat_id[:2].isalpha():
        return False
    prefix = vat_id[:2].upper()
    if prefix == 'HR':
        return False
    return prefix in _EU_VAT_NUMBER_PREFIXES or prefix == 'EL'


def invoice_rate_to_box(rate: Decimal | int | float | str) -> str | None:
    normalized = normalize_vat_rate(rate)
    if normalized == Decimal('5.00'):
        return '201'
    if normalized == Decimal('13.00'):
        return '202'
    if normalized == Decimal('25.00'):
        return '203'
    return None


def expense_rate_to_box(rate: Decimal | int | float | str) -> str | None:
    """Domestic pretporez boxes. 301/302 are not implemented — only 25% → 303."""
    if normalize_vat_rate(rate) == Decimal('25.00'):
        return '303'
    return None


def _is_viii1_real_estate_account(account_code: str) -> bool:
    return account_code.startswith('05') or account_code == '026'


def _is_viii1_car_active_account(account_code: str) -> bool:
    return account_code.startswith('032')


def _is_viii1_other_di_account(account_code: str) -> bool:
    if account_code in _VIII1_SKIP_ACCOUNTS:
        return False
    if account_code.startswith('037') or account_code.startswith('039'):
        return False
    return account_code.startswith(('030', '031', '033', '034', '035', '036', '038'))


def journal_line_to_viii1_box(
    *,
    account_code: str,
    debit_amount: Decimal,
    credit_amount: Decimal,
) -> str | None:
    """Map JE line to VIII.1 pretporez ispravak box (611–615). Skips EU RC / 0373."""
    if account_code in _VIII1_SKIP_ACCOUNTS:
        return None
    if debit_amount > 0:
        if _is_viii1_real_estate_account(account_code):
            return '611'
        if _is_viii1_car_active_account(account_code):
            return '612'
        if _is_viii1_other_di_account(account_code):
            return '614'
    elif credit_amount > 0:
        if _is_viii1_car_active_account(account_code):
            return '613'
        if _is_viii1_other_di_account(account_code) or _is_viii1_real_estate_account(account_code):
            return '615'
    return None


def viii1_box_amounts(box_code: str, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Return (base_amount, vat_amount) for a VIII.1 sub-box ledger entry."""
    if box_code in VIII1_VAT_AGGREGATE_BOXES:
        return Decimal('0.00'), amount
    return amount, Decimal('0.00')


def journal_line_to_box(*, account_code: str, debit_amount: Decimal, credit_amount: Decimal) -> str | None:
    if debit_amount > 0 and account_code in _JOURNAL_INPUT_ACCOUNTS:
        return '303'
    if debit_amount > 0 and account_code in IOSS_PRETPOREZ_ACCOUNTS:
        return '308'
    if credit_amount > 0 and account_code in _JOURNAL_OUTPUT_ACCOUNT_TO_BOX:
        return _JOURNAL_OUTPUT_ACCOUNT_TO_BOX[account_code]
    if debit_amount > 0 and account_code in EU_GOODS_PRETPOREZ_ACCOUNTS:
        return '307'
    if credit_amount > 0 and account_code in EU_GOODS_OBVEZA_ACCOUNTS:
        return '207'
    if debit_amount > 0 and account_code in DOMESTIC_RC_PRETPOREZ_ACCOUNTS:
        return '309'
    if credit_amount > 0 and account_code in DOMESTIC_RC_OBVEZA_ACCOUNTS:
        return '209'
    if debit_amount > 0 and account_code in EU_SERVICES_PRETPOREZ_ACCOUNTS:
        return '306'
    if credit_amount > 0 and account_code in EU_SERVICES_OBVEZA_ACCOUNTS:
        return '210'
    return None


def journal_line_vat_rate(account_code: str) -> Decimal:
    if account_code == '240010':
        return Decimal('5.00')
    if account_code == '240011':
        return Decimal('13.00')
    if account_code in DOMESTIC_RC_PRETPOREZ_ACCOUNTS | DOMESTIC_RC_OBVEZA_ACCOUNTS:
        return _EU_RC_RATE
    if account_code in EU_GOODS_PRETPOREZ_ACCOUNTS | EU_GOODS_OBVEZA_ACCOUNTS:
        return _EU_RC_RATE
    if account_code in EU_SERVICES_PRETPOREZ_ACCOUNTS | EU_SERVICES_OBVEZA_ACCOUNTS:
        if account_code.endswith('0'):
            return Decimal('5.00')
        if account_code.endswith('1'):
            return Decimal('13.00')
        return _EU_RC_RATE
    if account_code in IOSS_PRETPOREZ_ACCOUNTS:
        if account_code.endswith('0'):
            return Decimal('5.00')
        if account_code.endswith('1'):
            return Decimal('13.00')
        return Decimal('25.00')
    return Decimal('25.00')


def pretporez_base_from_vat(vat_amount: Decimal, rate: Decimal = Decimal('25.00')) -> Decimal:
    if rate <= 0:
        return Decimal('0.00')
    return (vat_amount * Decimal('100') / rate).quantize(Decimal('0.01'))


def eu_goods_vat_from_base(base_amount: Decimal, rate: Decimal = _EU_RC_RATE) -> Decimal:
    return (base_amount * rate / Decimal('100')).quantize(Decimal('0.01'))


def eu_rc_base_from_vat(vat_amount: Decimal, rate: Decimal = _EU_RC_RATE) -> Decimal:
    """Osnovica za RC par II.7/III.7 — preferira forward-kompatibilnu bazu (izbjegava 0,01 drift)."""
    base = pretporez_base_from_vat(vat_amount, rate)
    if rate != _EU_RC_RATE:
        return base
    alt_base = (base - Decimal('0.01')).quantize(Decimal('0.01'))
    if alt_base > 0 and eu_goods_vat_from_base(alt_base, rate) == vat_amount:
        return alt_base
    return base


def rc_output_box_for_input(input_box: str) -> str | None:
    return RC_INPUT_BOX_TO_OUTPUT.get(input_box)


def rc_vat_from_base(base_amount: Decimal, rate: Decimal) -> Decimal:
    return eu_goods_vat_from_base(base_amount, rate)


def derived_expense_vat_rate(*, base_amount: Decimal, vat_amount: Decimal) -> Decimal | None:
    """Implied VAT rate from expense net/tax. None when net is not positive."""
    base = Decimal(base_amount or 0)
    vat = Decimal(vat_amount or 0)
    if base <= 0:
        return None
    return (vat * Decimal('100') / base).quantize(Decimal('0.01'))


def expense_matches_domestic_25(
    *,
    base_amount: Decimal,
    vat_amount: Decimal,
    vat_rate: Decimal | None = None,
) -> bool:
    """True when amounts are domestic 25% pretporez, including 1-cent invoice rounding.

    Does not dump unknown or mixed header rates into 303.
    """
    base = Decimal(base_amount or 0)
    vat = Decimal(vat_amount or 0)
    if vat <= 0:
        return False
    rate_25 = Decimal('25.00')
    if base > 0:
        forward = rc_vat_from_base(base, rate_25)
        if forward == vat:
            return True
        # Invoice 25% can land 0.01 away from banker's ROUND_HALF_EVEN
        # (e.g. 40.50 × 0.25 = 10.125 → 10.12, račun 10.13).
        if abs(forward - vat) == Decimal('0.01'):
            return True
        if pretporez_base_from_vat(vat, rate_25) == base:
            return True
    rate = vat_rate
    if rate is None and base > 0:
        rate = derived_expense_vat_rate(base_amount=base, vat_amount=vat)
    if rate is None:
        return False
    return normalize_vat_rate(rate) == rate_25
