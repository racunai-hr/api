"""TZ2 mapping_version rates and Alma 2026 defaults.

Rates live here, not in the UI.
"""

from __future__ import annotations

from decimal import Decimal

from accounting.services.tax_forms.tz2.payload import TZ2_MAPPING_VERSION, money

ALMA_OIB = '20501805574'

TZ2_RATES_V1_2026 = {
    'room_bed_rate': money('5.97'),
    'aux_bed_rate': money('2.99'),
    'camp_rate': money('0.00'),
    'robinson_rate': money('0.00'),
    'opg_room_bed_rate': money('3.98'),
    'opg_aux_bed_rate': money('1.99'),
    'opg_camp_rate': money('0.00'),
    'opg_robinson_rate': money('0.00'),
}


def rates_for_year(tax_year: int, *, mapping_version: int = TZ2_MAPPING_VERSION) -> dict[str, Decimal]:
    if mapping_version != TZ2_MAPPING_VERSION:
        raise ValueError(f'Nepodržana TZ2 mapping_version: {mapping_version}')
    return dict(TZ2_RATES_V1_2026)


def alma_2026_defaults() -> dict:
    return {
        'first_name': 'ALMA',
        'last_name': 'ČIZMIĆ',
        'oib': ALMA_OIB,
        'municipality_code': '500',
        'city': 'VODICE',
        'street': 'ULICA BRIBIRSKIH KNEZOVA',
        'house_number': '9',
        'room_beds': 10,
        'aux_beds': 0,
        'camp_units': 0,
        'robinson_units': 0,
        'opg_room_beds': 0,
        'opg_aux_beds': 0,
        'opg_camp_units': 0,
        'opg_robinson_units': 0,
        'payment_installments': True,
        'ep_receipts': money('17360.00'),
        'discount_group_1': money('0.00'),
        'discount_group_2': money('0.00'),
        'discount_group_3': money('0.00'),
        'discount_group_4': money('0.00'),
    }
