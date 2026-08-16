# Obrazac PDV v11.0 — XSD sheme

Lokalne kopije službenih XML shema za validaciju Obrazac PDV v11.0 (ePorezna).

| Datoteka | Opis |
|----------|------|
| `ObrazacPDV-v11-0.xsd` | Root shema (unsigned obrazac) |
| `ObrazacPDVtipovi-v11-0.xsd` | Tipovi zaglavlja, tijela i polja |
| `ObrazacPDVmetapodaci-v11-0.xsd` | Metapodaci bloka obrasca |
| `MetapodaciTipovi-v2-0.xsd` | Zajednički tipovi metapodataka |
| `TemeljniTipovi-v2-1.xsd` | Temeljni tipovi (OIB, decimal, ispostava, …) |
| `xmldsig-core-schema.xsd` | W3C XML Signature (iz `fiscal_gateway/schemas/`) |
| `XAdES.xsd` | ETSI XAdES v1.3.2 (import xmldsig lokalno) |
| `ObrazacPDVsPotpisom-v11-0.xsd` | Potpisani obrazac (referenca; vanjski potpis u v1) |

**Izvor:** [ePorezna_Schemas.zip](https://e-porezna.porezna-uprava.hr/Upute/G2B/ePorezna_Schemas.zip) — mapa `PDV/` (objavljeno na [porezna-uprava.gov.hr](https://porezna-uprava.gov.hr/hr/xml-shema-obrasca-pdv/8229)).

Validacija: `accounting.services.tax_forms.pdv.validation.validate_pdv_obrazac_xml`.
