# HR CIUS Schematron sheme

## Trenutno stanje

- Vendored subset: `hrcius2025.sch` (CustomizationID, ProfileID, stranke, totali)
- Validator: `ubl/validators/schematron.py`

## Pun HRUBLSchematron ZIP (PU)

Kad Porezna dostavi `HRUBLSchematron…zip`:

1. Kopiraj ZIP u ovu mapu: `ubl/schematron/HRUBLSchematron.zip`
2. Validator automatski ekstrahira `.sch` i `.xsl` datoteke iz ZIP-a pri prvom pokretanju
3. Pokreni golden testove: `pytest ubl/tests/test_golden.py -v`
4. Ažuriraj interoperability matricu

Alternativno: ručno raspakiraj `.sch`/`.xsl` u ovu mapu (subset datoteka se može zamijeniti).

## Provjera dostupnosti

```python
from ubl.validators.schematron import schematron_available
assert schematron_available()
```
