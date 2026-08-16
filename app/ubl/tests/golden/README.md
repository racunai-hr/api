# HR CIUS 2025 golden master tests

## Struktura

- `fixtures/*.json` — kanonski `UblDocument` payloadi (6 scenarija)
- `*.xml` — očekivani UBL XML output (generiran iz buildera)

## Golden master

`invoice_finestar_reference.xml` je službeni golden master za Fine Star referentni račun.
Svaka promjena u `ubl/builder/hrcius2025.py` mora proći structural diff na ovom fajlu.

## Scenariji

| Fixture | Golden XML | Validacija |
|---------|------------|------------|
| `invoice_finestar_reference` | ✅ structural diff | semantic + XSD + Schematron |
| `invoice_minimal` | ✅ structural diff | semantic + XSD + Schematron |
| `invoice_standard` | ✅ structural diff | semantic + XSD + Schematron |
| `credit_note` | ✅ structural diff | semantic + XSD + Schematron |
| `export_invoice` | ✅ structural diff | semantic + XSD + Schematron |
| `reverse_charge` | ✅ structural diff | semantic + XSD + Schematron |

## Regeneracija golden XML-ova

```bash
cd erp/erp/app
python -c "
import json
from pathlib import Path
from ubl.builder.invoice import render_invoice_ubl
from ubl.domain.document import UblDocument

golden = Path('ubl/tests/golden/hrcius2025')
for fixture in golden.glob('fixtures/*.json'):
    doc = UblDocument.model_validate(json.loads(fixture.read_text()))
    xml = render_invoice_ubl(doc)
    (golden / f'{fixture.stem}.xml').write_text(xml)
"
```

## Schematron

HR CIUS Schematron sheme su u `ubl/schematron/hrcius2025.sch` (vendored subset).
Kad PU objavi puni `HRUBLSchematron…` ZIP, zamijeniti/ proširiti sadržaj te mape.
