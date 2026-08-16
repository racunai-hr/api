# Obrazac PDV-S v1.0 — XSD sheme

Lokalne kopije službenih XML shema za validaciju Obrazac PDV-S (ePorezna).

| Datoteka | Opis |
|----------|------|
| `ObrazacPDVS-v1-0.xsd` | Root shema |
| `ObrazacPDVStipovi-v1-0.xsd` | Tipovi (Isporuka, IsporukeUkupno, …) |
| `ObrazacPDVSmetapodaci-v1-0.xsd` | Metapodaci |
| `TemeljniTipovi-v2-1.xsd` | OIB, ispostava, decimal, … |
| `ObrazacPDVS-primjer.xml` | Službeni primjer (sve EU države) |

**Izvor:** [ePorezna_Schemas.zip](https://e-porezna.porezna-uprava.hr/Upute/G2B/ePorezna_Schemas.zip) — mapa `PDV-S/`.

Validacija: `accounting.services.tax_forms.pdv_s.validation.validate_pdv_s_xml`.

Generiranje: `python manage.py generate_pdv_s_xml --tenant finestar --year YYYY --month MM --output /path/to/file.xml`
