"""HR CIUS 2025 constants."""

CUSTOMIZATION_ID = (
    'urn:cen.eu:en16931:2017#compliant#urn:mfin.gov.hr:cius-2025:1.0'
    '#conformant#urn:mfin.gov.hr:ext-2025:1.0'
)
ENDPOINT_SCHEME_ID = '9934'
DEFAULT_PROFILE_ID = 'P1'
DEFAULT_INVOICE_TYPE_CODE = '380'
CREDIT_NOTE_TYPE_CODE = '381'
DEFAULT_UNIT_CODE = 'H87'
DEFAULT_CURRENCY = 'EUR'
DEFAULT_PAYMENT_MEANS_CODE = '30'
DEFAULT_CLASSIFICATION_SCHEME = 'CG'

HR_VAT_NAMES = {
    '25': 'HR:PDV25',
    '13': 'HR:PDV13',
    '5': 'HR:PDV5',
    '0': 'HR:PDV0',
}


def hr_vat_name(percent) -> str:
    key = str(int(percent)) if float(percent) == int(float(percent)) else str(percent)
    return HR_VAT_NAMES.get(key, f'HR:PDV{key}')
