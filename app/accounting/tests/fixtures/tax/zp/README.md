# ZP test fixtures — racunAI ERP

```text
Path: accounting/tests/fixtures/tax/zp/
Status: Prepared before ZP implementation (Sprint 3)
Spec: docs/tax/ZP_ARCHITECTURE.md
```

Fixtures za **Obrazac ZP** (Zbirna prijava — izlazne EU isporuke). Koriste se za:

- unit testove agregacije (`aggregate_zp_rows`)
- snapshot testove payloada (`build_zp_payload`)
- cross-form provjere ZP ↔ PDV box **101** / **103**
- validation testove (neispravan PDV ID)
- regresiju nakon implementacije

**Isti `fixtures/tax/{form}/` layout** — vidi [`docs/tax/FORM_IMPLEMENTATION_CONVENTION.md`](../../../../docs/tax/FORM_IMPLEMENTATION_CONVENTION.md).

---

## Scenariji

| ID | Folder | Svrha |
|----|--------|-------|
| `single_eu_partner` | `single_eu_partner/` | Jedan DE primatelj — dobra |
| `multiple_eu_partners` | `multiple_eu_partners/` | DE + FR + IT — miješano dobra/usluge |
| `period_correction` | `period_correction/` | Ispravak razdoblja (v1 → v2 ledger + payload) |
| `empty_period` | `empty_period/` | Nema EU izlaza — prazan payload |
| `invalid_vat_id` | `invalid_vat_id/` | Neispravan EU PDV ID — očekivana validation greška |
| `pdv_mismatch_101_103` | `pdv_mismatch_101_103/` | ZP zbroj ≠ PDV 101/103 — verification test |

Indeks: [`scenarios.yaml`](scenarios.yaml)

---

## Format datoteka

### `ledger_entries.json`

Izvor za agregaciju — pojednostavljeni `VATLedgerEntry` (I-RA):

```json
{
  "period": {"year": 2026, "month": 6},
  "entries": [
    {
      "ledger_type": "I-RA",
      "vat_box": "101",
      "entry_date": "2026-06-15",
      "net_amount": "10000.00",
      "partner_country": "DE",
      "partner_vat_id": "DE123456789"
    }
  ]
}
```

- **`vat_box` `101`** → dobra EU (ZP red: `goods_value`)
- **`vat_box` `103`** → usluge EU (ZP red: `services_value`)

### `expected_payload.json`

Kanonski `ZpPayload` snapshot (`payload.json` SSOT):

```json
{
  "schema_version": "1.0",
  "mapping_version": 1,
  "period_from": "2026-06-01",
  "period_to": "2026-06-30",
  "rows": [
    {
      "country_code": "DE",
      "pdv_id": "123456789",
      "goods_value": "10000.00",
      "services_value": "0.00"
    }
  ]
}
```

### `pdv_cross_check.json`

Očekivani PDV boxovi za usklađenost (Fine Star test tenant):

```json
{
  "fields": {"101": "10000.00", "103": "0.00"},
  "must_match_zp_totals": true
}
```

Za `pdv_mismatch_101_103`: `must_match_zp_totals: false` i namjerno pogrešni PDV iznosi.

### `expected_validation.json`

Za negativne scenarije (`invalid_vat_id`):

```json
{
  "expect_error": true,
  "error_substring": "Neispravan EU PDV ID"
}
```

---

## Korištenje u testovima (nakon implementacije ZP)

```python
from pathlib import Path

ZP_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'tax' / 'zp'

def load_scenario(name: str) -> dict:
    base = ZP_FIXTURES / name
    return {
        'ledger': json.loads((base / 'ledger_entries.json').read_text()),
        'expected': json.loads((base / 'expected_payload.json').read_text()),
        'pdv': json.loads((base / 'pdv_cross_check.json').read_text()),
    }
```

Contract test (bez ZP koda): `test_zp_fixture_contract.py` — validira strukturu svih scenarija.

---

## Dodavanje novog scenarija

1. Nova podmapa u `tax/zp/{scenario_id}/`
2. Unos u `scenarios.yaml`
3. Ažurirati contract test

---

## Reference

- [`docs/tax/ZP_ARCHITECTURE.md`](../../../../docs/tax/ZP_ARCHITECTURE.md)
- [`docs/tax/FORM_REGISTRY.md`](../../../../docs/tax/FORM_REGISTRY.md)
- PDV-S payload uzorak: `accounting/services/tax_forms/pdv_s/payload.py`
