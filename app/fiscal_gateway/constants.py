"""CIS endpoint URLs — Tablica 130, Tehnička specifikacija Fiskalizacija eRačuna."""

CIS_ENV_DEMO = 'demo'
CIS_ENV_PROD = 'prod'
CIS_ENV_PTS = 'pts'

# Tablica 130 (spec) + PTS upute (port 8511 za test scenarije)
CIS_ENDPOINTS = {
    CIS_ENV_DEMO: 'https://cistest.apis-it.hr:8509/FiskalizacijaServiceEprod',
    CIS_ENV_PROD: 'https://cis.porezna-uprava.hr:8509/FiskalizacijaService',
    CIS_ENV_PTS: 'https://cis.porezna-uprava.hr:8511/FiskalizacijaService',
}

NS_EFISKALIZACIJA = 'http://www.porezna-uprava.gov.hr/fin/2024/types/eFiskalizacija'
NS_SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'

DEFAULT_DEMO_OIB = '36619131370'
