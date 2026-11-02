"""Business validation for JoppdPayload.

Službeni ePorezna XSD nije u repou. Spremljena je samo skica
``accounting/services/schemas/joppd-v1-0-draft.xsd``. Dok je shema skica,
provjera traži korijen ObrazacJOPPD i ne zamjenjuje službenu validaciju.
Slanje ostaje isključeno (JOPPD_SUBMIT_ENABLED).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from domains.tax.forms.joppd.payload import JoppdPayload

_OIB_RE = re.compile(r'^\d{11}$')
_MUNICIPALITY_RE = re.compile(r'^\d{5}$')


class JoppdValidationError(Exception):
    """Raised when JoppdPayload fails business validation."""


_SCHEMA_DIR = Path(__file__).resolve().parents[4] / 'accounting' / 'services' / 'schemas'


def validate_joppd_xml(xml_bytes: bytes) -> None:
    """Provjeri XML prema spremljenoj shemi.

    Skica dopušta samo korijen ObrazacJOPPD. Službena shema, kad je datoteka
    bez 'draft' u imenu, mora proći lxml XMLSchema.
    """
    schema_path = next(_SCHEMA_DIR.glob('joppd*.xsd'), None)
    if schema_path is None:
        raise JoppdValidationError('Nema spremljene JOPPD XSD sheme.')
    root = ET.fromstring(xml_bytes)
    local = root.tag.split('}', 1)[-1]
    if local != 'ObrazacJOPPD':
        raise JoppdValidationError(f'Korijen JOPPD XML-a je {local!r}, očekuje se ObrazacJOPPD.')
    if 'draft' in schema_path.name:
        return
    from lxml import etree

    schema = etree.XMLSchema(etree.parse(str(schema_path)))
    if not schema.validate(etree.fromstring(xml_bytes)):
        raise JoppdValidationError('JOPPD XML ne prolazi službenu XSD shemu.')


def validate_joppd_payload(payload: JoppdPayload) -> None:
    """Validate business rules for an Obrazac JOPPD payload."""
    if not _OIB_RE.match(payload.employer.oib or ''):
        raise JoppdValidationError(f'Neispravan OIB poslodavca: {payload.employer.oib!r}')

    if not 1 <= payload.period_month <= 12:
        raise JoppdValidationError(f'Neispravan mjesec razdoblja: {payload.period_month!r}')

    if not payload.rows:
        raise JoppdValidationError(
            f'Nema stjecatelja (zaposlenika) za razdoblje {payload.period_month:02d}/{payload.period_year}.'
        )

    if len(payload.prepared_by.first_name.strip()) < 2 or len(payload.prepared_by.last_name.strip()) < 2:
        raise JoppdValidationError('Ime i prezime sastavljača moraju imati najmanje 2 znaka.')

    seen_oibs: set[str] = set()
    gross_total = Decimal('0.00')
    net_total = Decimal('0.00')

    for index, row in enumerate(payload.rows, start=1):
        if not _OIB_RE.match(row.recipient_oib or ''):
            raise JoppdValidationError(f'Red {index}: neispravan OIB stjecatelja {row.recipient_oib!r}')
        if row.recipient_oib in seen_oibs:
            raise JoppdValidationError(
                f'Red {index}: duplicirani stjecatelj (OIB {row.recipient_oib}) u istom razdoblju.'
            )
        seen_oibs.add(row.recipient_oib)

        if row.municipality_code and not _MUNICIPALITY_RE.match(row.municipality_code):
            raise JoppdValidationError(
                f'Red {index}: neispravna šifra JLS prebivališta {row.municipality_code!r}'
            )

        if row.date_from > row.date_to:
            raise JoppdValidationError(f'Red {index}: datum "od" je nakon datuma "do".')

        if row.gross_salary < 0 or row.net_salary < 0:
            raise JoppdValidationError(f'Red {index}: negativan iznos bruto/neto plaće.')

        gross_total += row.gross_salary
        net_total += row.net_salary

    if gross_total != payload.total_gross or net_total != payload.total_net:
        raise JoppdValidationError('Zbrojevi redova ne odgovaraju ukupnim vrijednostima payloada.')
