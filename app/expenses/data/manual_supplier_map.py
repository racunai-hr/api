"""Ručno uneseni dobavljači izvan F1 (strani, uvoz vozila, …)."""

MANUAL_SUPPLIER_MAP = {
    'DE229674882': {
        'name': 'Automobile Hadžić',
        'address': 'Ückendorfer Str. 2–8',
        'city': 'Gelsenkirchen',
        'postal_code': '45886',
        'country': 'Germany',
        'email': 'info@automobile-hadzic.de',
        'phone': '+49 209 167990',
        'notes': 'Njemački prodavatelj rabljenih vozila; T-Cross račun 70025237, Golf 70025249.',
    },
    'DE355497142': {
        'name': 'SaM Automobile',
        'address': 'Sägmühlweg 4',
        'city': 'Sinsheim',
        'postal_code': '74889',
        'country': 'Germany',
        'email': 'info@sam-automobile.de',
        'phone': '+49 7261 6590055',
        'notes': (
            'Njemački prodavatelj rabljenih vozila (Sahin Mustafa); '
            'Audi A8 račun 2026-213 (VIN WAUZZZF86RN003268).'
        ),
    },
    '18683136487': {
        'name': 'Ministarstvo financija — Carinska uprava, CU Šibenik',
        'address': 'Carinski ured Šibenik (Područni carinski ured Split)',
        'city': 'Šibenik',
        'postal_code': '22000',
        'country': 'Croatia',
        'email': '',
        'phone': '',
        'notes': (
            'PPMV — rješenje i prijava. Uplata na Državni proračun RH: '
            'IBAN HR1210010051863000160, model HR68, poziv 1147-{OIB tvrtke}. '
            'Na izvodu: DRŽAVNI PRORAČUN REPUBLIKE HRVATSKE.'
        ),
    },
}
