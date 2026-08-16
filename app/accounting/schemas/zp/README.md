# Obrazac ZP — XSD sheme

Službene XSD sheme preuzete s ePorezna G2B portala:

- [Tehnički preduvjeti](https://testeporezna.porezna-uprava.hr/ePoreznaWebG2B/Pages/TehnickiPreduvjeti.aspx) → Sheme i primjeri
- [Korisničke upute — Obrazac ZP](https://testeporezna.porezna-uprava.hr/ePoreznaWebG2B/Pages/Ousluzi.aspx)

## Status

**Imported** — `v1-0/` (2026-07-10), byte-identično iz službenog zip arhiva.

| Polje | Vrijednost |
|-------|------------|
| `schema_version` | `1.0` |
| Verzija sheme | `verzijaSheme="1.0"` (fixed atribut na `sObrazacZP`) |
| Namespace | `http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacZP/v1-0` |
| Izvor | [ePorezna_Schemas.zip](https://e-porezna.porezna-uprava.hr/Upute/G2B/ePorezna_Schemas.zip) → mapa `ZP/` |
| Datum importa | 2026-07-10 |
| SHA-256 zipa | `5670c921f1393b08b4ea00657f78f2e814253eda3eb855fcabcb50b9467db8ee` (vidi [`docs/porezna/upute/2026/README.md`](../../../../docs/porezna/upute/2026/README.md)) |

## Struktura

```text
accounting/schemas/zp/
    README.md                          # this file
    v1-0/
        ObrazacZP-v1-0.xsd             # root — NE MIJENJATI
        ObrazacZPtipovi-v1-0.xsd
        ObrazacZPmetapodaci-v1-0.xsd
        MetapodaciTipovi-v2-0.xsd
        TemeljniTipovi-v2-1.xsd
        ObrazacZPsPotpisom-v1-0.xsd    # potpisni wrapper (vanjski XSD-ovi nisu u zipu)
        examples/
            Primjer.xml                # službeni primjer (unsigned)
```

## Datoteke (`v1-0/`)

| Datoteka | B | SHA-256 | Opis |
|----------|---|---------|------|
| `ObrazacZP-v1-0.xsd` | 736 | `aa793e18309f43b05f480ed474d6743df8c115b37ef26b30166420368bd387b8` | Root shema — element `ObrazacZP` |
| `ObrazacZPtipovi-v1-0.xsd` | 16 631 | `77c57725c71088c84b702f2ffc833eb254469b62063435c1d80ed150e2f28f09` | Poslovni tipovi (redovi, zbrojevi) |
| `ObrazacZPmetapodaci-v1-0.xsd` | 1 551 | `d3f281f621dcb19f2b59a325a104afcc418ccf6c98f561f7daf0591da30c4aa0` | Metapodaci obrasca |
| `MetapodaciTipovi-v2-0.xsd` | 5 773 | `d0952ed4387557f224c471b04f6d2d6903597b56cab6fea48b361a9d99587564` | Shared metapodaci tipovi |
| `TemeljniTipovi-v2-1.xsd` | 7 249 | `33c83f073b5ef18009a05bb88ed8cd6ae03bd8546f1bfb77180cca87a9ccb42e` | OIB, decimal, ispostava, … |
| `ObrazacZPsPotpisom-v1-0.xsd` | 1 290 | `c5bab5d7db058e02aee9827bd5b592617e25ba385af94bd48f8385fa7599c799` | Potpisni wrapper (nije potreban za unsigned validaciju) |
| `examples/Primjer.xml` | 3 372 | `1b88f37e4ebb9f2b3faedb81efb31886bc7481aa657b15f68c0e10efd4f7ef1c` | Službeni primjer — `Uskladjenost` = `ObrazacZP-v1-0` |

## Ovisnosti (unsigned root `ObrazacZP-v1-0.xsd`)

```text
ObrazacZP-v1-0.xsd
  └─ include → ObrazacZPtipovi-v1-0.xsd
       ├─ import → TemeljniTipovi-v2-1.xsd
       └─ import → ObrazacZPmetapodaci-v1-0.xsd
            └─ include → MetapodaciTipovi-v2-0.xsd
                 └─ import → TemeljniTipovi-v2-1.xsd
```

`ObrazacZPsPotpisom-v1-0.xsd` referencira `xmldsig-core-schema.xsd`, `XAdES.xsd`, `VanjskaOmotnica.xsd` — **nisu u zipu** (potpis = ePorezna infrastruktura). Lokalna validacija koristi unsigned root + strip signature (uzorak PDV-S).

**Napomena:** `TemeljniTipovi-v2-1.xsd` u `ZP/` **nije** identičan kopiji u `pdv-s/v1-0/` — ne dijeliti datoteke između mapa.

## Pravilo integriteta XSD (obavezno)

1. **Ne uređivati** službene XSD datoteke koje je objavila Porezna uprava.
2. Spremiti **originalnu verziju** (byte-identično iz službenog zip arhiva).
3. Kompatibilnost, workaroundi ili pomoćna logika → **Python** (`validation.py`, `render.py`) ili zasebne **pomoćne datoteke izvan** službenog XSD seta (npr. `schemas/zp/helpers/` — samo ako stvarno treba).
4. U repou mora biti jasno: **XSD = službena specifikacija**, Python = implementacijski sloj.

Ako PU objavi novu verziju sheme → zamijeni cijeli set datoteka iz novog zipa u novi `vX-X/` direktorij, ne patchaj staru.

## Validacija u kodu

| Funkcija | Datoteka | Što provjerava |
|----------|----------|----------------|
| `validate_zp_xml()` | `validation.py` | **Strukturna** — lokalni XSD (`v1-0/ObrazacZP-v1-0.xsd`) |
| `validate_zp_payload()` / poslovna | `validation.py` | OIB, razdoblje, PDV ID, zbrojevi — pravila koja XSD ne pokriva |

XSD ostaje isključivo za strukturnu validaciju XML-a.

## Reference u repou

- Arhitektura ZP: [`docs/tax/ZP_ARCHITECTURE.md`](../../../../docs/tax/ZP_ARCHITECTURE.md)
- PDV-S XSD uzorak: `accounting/schemas/pdv-s/v1-0/`
- PDV XSD: `accounting/schemas/pdv/v11-0/`
- Izvorni extract: `docs/porezna/upute/2026/ePorezna_Schemas_extracted/ePorezna_Schemas/ZP/`
