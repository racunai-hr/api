"""Company VAT profile helpers. OIB stays on vat_number; vat_id is the PDV ID."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from shared.oib import is_valid_oib, normalize_oib
from shared.vat import eu_vat_format_valid, normalize_vat_number

STATUS_NONE = 'none'
STATUS_VAT_ID = 'vat_id'
STATUS_REGISTERED = 'registered'


def normalize_company_oib(value: str) -> str:
    return normalize_oib(value or '')


def normalize_company_vat_id(value: str) -> str:
    vat = normalize_vat_number(value)
    if vat.isdigit() and len(vat) == 11:
        return f'HR{vat}'
    return vat


def validate_company_vat_profile(*, oib: str, vat_id: str, status: str) -> tuple[str, str]:
    """Return normalized (oib, vat_id) or raise ValidationError."""
    normalized_oib = normalize_company_oib(oib)
    if normalized_oib and not is_valid_oib(normalized_oib):
        raise ValidationError({'vat_number': 'OIB nije valjan (ISO 7064 MOD 11,10).'})

    normalized_vat_id = normalize_company_vat_id(vat_id) if vat_id else ''

    if status == STATUS_NONE:
        if normalized_vat_id:
            raise ValidationError({'vat_id': 'Status "nije u sustavu PDV-a" ne smije imati PDV ID.'})
        return normalized_oib, ''

    if status in {STATUS_VAT_ID, STATUS_REGISTERED}:
        if not normalized_vat_id:
            raise ValidationError({'vat_id': 'PDV identifikacijski broj je obavezan za ovaj status.'})
        if not eu_vat_format_valid(normalized_vat_id):
            raise ValidationError({'vat_id': 'PDV identifikacijski broj nije u valjanom EU formatu.'})
        if normalized_vat_id.startswith('HR'):
            body = normalized_vat_id[2:]
            if not is_valid_oib(body):
                raise ValidationError({'vat_id': 'HR PDV ID mora sadržavati valjani OIB.'})
            if normalized_oib and body != normalized_oib:
                raise ValidationError({'vat_id': 'HR PDV ID mora odgovarati OIB-u tvrtke.'})
        return normalized_oib, normalized_vat_id

    raise ValidationError({'vat_registration_status': f'Nepoznat PDV status: {status}.'})
