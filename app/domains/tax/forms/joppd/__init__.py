"""Obrazac JOPPD — izvješće o primicima, porezu na dohodak i doprinosima.

NAPOMENA (opseg/ograničenje): XML koji ovaj modul generira strukturno slijedi
objavljenu shemu Porezne uprave (Stranica A — zaglavlje/skupni podaci, Stranica
B/C — podaci po stjecatelju), ali NIJE validiran protiv službenog XSD-a (za
razliku od Obrazaca ZP/PDV/PDV-S/TZ2, koji imaju lokalnu kopiju XSD-a u
``accounting/services/schemas/``). Prije produkcijske predaje u ePoreznu
potrebno je:
    1. nabaviti važeću JOPPD XSD shemu i dodati XSD validaciju
       (vidi ``accounting/services/tax_forms/zp/validation.py`` za obrazac), i
    2. da ovlašteni računovođa provjeri generirani XML na testnom primjeru.

Dependency rule: ovaj paket NE smije uvoziti ``payroll.models`` izravno — sve
podatke o zaposlenicima/obračunu dobiva kroz ``domains.hr.facade`` DTO-ove.
"""

MATURITY = 'L1'
