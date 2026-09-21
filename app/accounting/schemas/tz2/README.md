# Obrazac TZ 2 — XSD sheme

Službene XSD sheme iz ePorezna G2B paketa:

- [ePorezna_Schemas.zip](https://e-porezna.porezna-uprava.hr/Upute/G2B/ePorezna_Schemas.zip) → mapa `TZ 2/`
- Nije mapa `TZ/` ni `TZ 1/`

## Status

**Imported** — `v1-0/` (2026-09-15), byte-identično iz službenog zip arhiva.

| Polje | Vrijednost |
|-------|------------|
| `schema_version` | `1.0` |
| Verzija sheme | `verzijaSheme="1.0"` (fixed atribut na `sObrazacTZ2`) |
| Namespace | `http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacTZ2/v1-0` |
| `Uskladjenost` | `ObrazacTZ2-v1-0` |
| Izvor | [ePorezna_Schemas.zip](https://e-porezna.porezna-uprava.hr/Upute/G2B/ePorezna_Schemas.zip) → mapa `TZ 2/` |
| Datum importa | 2026-09-15 |
| SHA-256 zipa | `5670c921f1393b08b4ea00657f78f2e814253eda3eb855fcabcb50b9467db8ee` (vidi [`docs/porezna/upute/2026/README.md`](../../../../docs/porezna/upute/2026/README.md)) |

## Struktura

```text
accounting/schemas/tz2/
    README.md                          # this file
    v1-0/
        ObrazacTZ2-v1-0.xsd            # root — NE MIJENJATI
        ObrazacTZ2tipovi-v1-0.xsd
        ObrazacTZ2metapodaci-v1-0.xsd
        MetapodaciTipovi-v2-0.xsd
        TemeljniTipovi-v2-1.xsd
        ObrazacTZ2sPotpisom-v1-0.xsd   # potpisni wrapper (vanjski XSD-ovi nisu u zipu)
        examples/
            Primjer.xml                # službeni primjer (unsigned)
```

## Datoteke (`v1-0/`)

| Datoteka | B | SHA-256 | Opis |
|----------|---|---------|------|
| `ObrazacTZ2-v1-0.xsd` | 761 | `626ca43dacea4a998f1d1b74e11dacdfd893dac9f2a42da9d1a9ebc96e475902` | Root shema — element `ObrazacTZ2` |
| `ObrazacTZ2tipovi-v1-0.xsd` | 17 095 | `e48f481b14dab290f8778f68cd0ab2d38ece55f979846432a78ce633afd3838c` | Poslovni tipovi (`Podatak01`–`36`) |
| `ObrazacTZ2metapodaci-v1-0.xsd` | 1 612 | `0b9c95fe00ee3cd348dcad49311c5974a44798a0cb3a16426d25fdc6a7a29ed2` | Metapodaci obrasca |
| `MetapodaciTipovi-v2-0.xsd` | 5 773 | `d0952ed4387557f224c471b04f6d2d6903597b56cab6fea48b361a9d99587564` | Shared metapodaci tipovi |
| `TemeljniTipovi-v2-1.xsd` | 7 249 | `33c83f073b5ef18009a05bb88ed8cd6ae03bd8546f1bfb77180cca87a9ccb42e` | OIB, decimal, šifra općine, … |
| `ObrazacTZ2sPotpisom-v1-0.xsd` | 1 330 | `93e142802c5e03ba2d2ac645280f17de3694a83987ab7432409a6d93f1d0d223` | Potpisni wrapper |
| `examples/Primjer.xml` | 2 183 | `c57313a7135f775a7942e6b1d0001c7a8b907ef9d6d6ca9c0f3f3d7a32ea111d` | Službeni primjer — `Uskladjenost` = `ObrazacTZ2-v1-0` |

## Ovisnosti (unsigned root `ObrazacTZ2-v1-0.xsd`)

```text
ObrazacTZ2-v1-0.xsd
  └─ include → ObrazacTZ2tipovi-v1-0.xsd
       ├─ import → TemeljniTipovi-v2-1.xsd
       └─ import → ObrazacTZ2metapodaci-v1-0.xsd
            └─ include → MetapodaciTipovi-v2-0.xsd
                 └─ import → TemeljniTipovi-v2-1.xsd
```

`ObrazacTZ2sPotpisom-v1-0.xsd` referencira vanjske potpisne sheme — **nisu u zipu**. Lokalna validacija koristi unsigned root.

**Napomena:** `TemeljniTipovi-v2-1.xsd` u `TZ 2/` **nije** dijeliti s `zp/` / `pdv-s/` — svaka mapa drži svoju kopiju iz zipa.

## Pravilo integriteta XSD (obavezno)

1. **Ne uređivati** službene XSD datoteke koje je objavila Porezna uprava.
2. Spremiti **originalnu verziju** (byte-identično iz službenog zip arhiva).
3. Kompatibilnost, workaroundi ili pomoćna logika → **Python** (`validation.py`, `render.py`).
4. UI ePorezne **nije** shema — payload ide 1:1 prema XSD anotacijama.

Ako PU objavi novu verziju sheme → zamijeni cijeli set u novi `vX-X/` direktorij, ne patchaj staru.

## Validacija u kodu

| Funkcija | Datoteka | Što provjerava |
|----------|----------|----------------|
| `validate_tz2_xml()` | `validation.py` | **Strukturna** — lokalni XSD (`v1-0/ObrazacTZ2-v1-0.xsd`) |
| `validate_tz2_payload()` | `validation.py` | OIB, ime/prezime, šifra općine, zbrojevi |

## Reference u repou

- Arhitektura TZ2: [`docs/tax/TZ2_ARCHITECTURE.md`](../../../../docs/tax/TZ2_ARCHITECTURE.md)
- ADR: [`docs/architecture/ADR-0031-tz2-return.md`](../../../../docs/architecture/ADR-0031-tz2-return.md)
- ZP XSD uzorak: `accounting/schemas/zp/`
