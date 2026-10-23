"""Build an ObrazacPDVispravak from company settings and entered invoice rows."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from accounting.services.tax_forms.pdv.build import accountant_for_tenant
from accounting.services.tax_forms.pdv_ispravak.payload import (
    PdvIspravakPayload,
    PdvIspravakPreparer,
    PdvIspravakRow,
    PdvIspravakTaxpayer,
)
from accounting.services.tax_forms.pdv_ispravak.render import render_pdv_ispravak_xml
from accounting.services.tax_forms.pdv_ispravak.validation import (
    PdvIspravakSchemaError,
    PdvIspravakValidationError,
    validate_pdv_ispravak_payload,
    validate_pdv_ispravak_xml,
)
from settings.models import CompanySettings

_ZAGREB = ZoneInfo('Europe/Zagreb')
_TWO = Decimal('0.01')


class PdvIspravakBuildError(Exception):
    """Raised when the form cannot be built for this tenant."""


def period_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _decimal(value) -> Decimal:
    return Decimal(str(value or '0')).quantize(_TWO)


def row_from_dict(raw: dict) -> PdvIspravakRow:
    invoice_date = date.fromisoformat(str(raw['invoice_date']))
    return PdvIspravakRow(
        invoice_number=str(raw.get('invoice_number') or '').strip(),
        invoice_date=invoice_date,
        identifier_kind=int(raw['identifier_kind']),
        identifier=str(raw.get('identifier') or '').strip(),
        customer_name=str(raw.get('customer_name') or '').strip(),
        base_5=_decimal(raw.get('base_5')),
        vat_5=_decimal(raw.get('vat_5')),
        base_13=_decimal(raw.get('base_13')),
        vat_13=_decimal(raw.get('vat_13')),
        base_25=_decimal(raw.get('base_25')),
        vat_25=_decimal(raw.get('vat_25')),
    )


def build_pdv_ispravak_payload(tenant, *, year: int, month: int, rows: list[dict]) -> PdvIspravakPayload:
    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    if settings is None:
        raise PdvIspravakBuildError('Nedostaju postavke tvrtke.')
    person = accountant_for_tenant(tenant)
    if person is None:
        raise PdvIspravakBuildError('Nedostaje osoba koja sastavlja obrazac.')
    period_from, period_to = period_bounds(year, month)
    payload = PdvIspravakPayload(
        period_from=period_from,
        period_to=period_to,
        taxpayer=PdvIspravakTaxpayer(
            name=settings.company_name,
            oib=settings.vat_number or '',
            city=settings.city,
            street=settings.street,
            house_number=settings.house_number,
            phone=settings.company_phone or '',
            email=settings.company_email or '',
        ),
        preparer=PdvIspravakPreparer(
            first_name=person.first_name,
            last_name=person.last_name,
            place=settings.city or '',
            prepared_on=datetime.now(_ZAGREB).date(),
        ),
        rows=tuple(row_from_dict(row) for row in rows),
    )
    validate_pdv_ispravak_payload(payload)
    return payload


def pdv_ispravak_xml_bytes(tenant, *, year: int, month: int, rows: list[dict]) -> tuple[bytes, str]:
    try:
        payload = build_pdv_ispravak_payload(tenant, year=year, month=month, rows=rows)
        xml_bytes = render_pdv_ispravak_xml(payload)
        validate_pdv_ispravak_xml(xml_bytes)
    except (PdvIspravakBuildError, PdvIspravakValidationError, PdvIspravakSchemaError, ValueError, KeyError) as exc:
        raise PdvIspravakBuildError(str(exc)) from exc
    filename = (
        f'PDV-ispravak_{payload.taxpayer.oib}_'
        f'{payload.period_from:%Y%m%d}-{payload.period_to:%Y%m%d}.xml'
    )
    return xml_bytes, filename
