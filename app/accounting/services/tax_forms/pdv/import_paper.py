"""Archive a scanned Obrazac PDV that has no v11 XML.

Fine Star filed nil returns: two quarters in H1 2014, then monthly filings,
plus the 2014 annual return. Amounts on the paper form are HRK. The book
amount stays on the ledger (empty → 0 EUR). A month and a longer filing that
starts in that month are separate rows.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.db import transaction

from accounting.models import (
    SubmissionDestination,
    SubmissionEvent,
    SubmissionSource,
    VATLedgerEntry,
    VATPeriod,
    VATReturn,
    VATReturnSource,
    VATReturnStatus,
)
from accounting.services.submission.service import SubmissionService
from accounting.services.tax_forms.pdv.versions import next_vat_return_version
from settings.models import CompanySettings

PAPER_OIB = '36619131370'
PAPER_SCHEMA = '2014-paper'
ZAGREB = ZoneInfo('Europe/Zagreb')

_TAXPAYER = {
    'city': 'ZAGREB',
    'house_number': '46',
    'name': 'FINE STAR d.o.o.',
    'oib': PAPER_OIB,
    'postal_code': '',
    'street': 'A.T. MIMARE',
}

_TAXPAYER_SIBENIK = {
    'city': 'Šibenik',
    'house_number': '58',
    'name': 'FINE STAR d.o.o.',
    'oib': PAPER_OIB,
    'postal_code': '22000',
    'street': 'Bana Josipa Jelačića',
}


class PaperPdvImportError(ValueError):
    """Raised when a paper PDV archive cannot be recorded."""


@dataclass(frozen=True)
class PaperPdvQuarter:
    year: int
    month: int
    period_to: date | None
    received_at: datetime
    external_identifier: UUID
    form_identifier: UUID | None = None
    pdf_name: str = ''
    keep_ledger: bool = False
    document_vat_due: str = '0.00'
    tax_office: str | None = None


FINESTAR_2014_H1 = (
    PaperPdvQuarter(
        year=2014,
        month=1,
        period_to=date(2014, 3, 31),
        received_at=datetime(2014, 4, 29, 12, 27, 58, tzinfo=ZAGREB),
        external_identifier=UUID('63765b90-3ffd-4cb7-9afa-185d2d125632'),
        form_identifier=UUID('39f286bb-2695-4ffd-947f-d6f2a73a6dfa'),
    ),
    PaperPdvQuarter(
        year=2014,
        month=4,
        period_to=date(2014, 6, 30),
        received_at=datetime(2014, 7, 17, 15, 53, 43, tzinfo=ZAGREB),
        external_identifier=UUID('595681a5-977b-4cc3-bcb5-b53b6d9ad324'),
        form_identifier=UUID('faa3e18c-0a88-4a79-a6f5-5b1014ff65b6'),
    ),
)


def _month(
    year: int,
    month: int,
    received_at: datetime,
    external_identifier: str,
    pdf_name: str,
    *,
    due: str = '0.00',
    keep_ledger: bool = False,
) -> PaperPdvQuarter:
    return PaperPdvQuarter(
        year=year,
        month=month,
        period_to=None,
        received_at=received_at,
        external_identifier=UUID(external_identifier),
        pdf_name=pdf_name,
        keep_ledger=keep_ledger,
        document_vat_due=due,
    )


def _zagreb(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=ZAGREB)


# Nil monthly filings and the 2014 annual return. Quarters stay in FINESTAR_2014_H1.
FINESTAR_PAPER_FILINGS = (
    _month(2014, 1, _zagreb(2014, 7, 21, 10, 37, 12), '2c7f77cc-a46f-417c-968e-f1886d280302', 'pdv 2014 x2.pdf'),
    _month(2014, 2, _zagreb(2014, 7, 21, 10, 39, 22), '35575825-aa29-4d4b-8142-7da9e1fc9159', 'pdv 2014 x2.pdf'),
    _month(2014, 3, _zagreb(2014, 7, 21, 10, 40, 27), '09f9fb6a-71be-476a-8bf1-4a8eca04d604', 'pdv 2014 x 3.pdf'),
    _month(2014, 4, _zagreb(2014, 7, 21, 10, 42, 34), 'd18d1c0f-e178-4abd-9073-8d13d8f4e231', 'pdv 2014 x 3.pdf'),
    _month(2014, 5, _zagreb(2014, 7, 21, 10, 43, 59), 'ad9f09c2-f32a-4eca-a6c5-f9245032540a', 'pdv 2014 x 3.pdf'),
    _month(2014, 6, _zagreb(2014, 7, 21, 10, 46, 53), 'f3fe8bb4-fd88-4401-b0a7-b33f5c4d122d', 'pdv 2014 x 3.pdf'),
    _month(2014, 7, _zagreb(2014, 8, 19, 15, 48, 17), 'e3656656-5f1c-4a69-8bdb-603961868bb6', 'pdv 2014 3.pdf'),
    _month(2014, 8, _zagreb(2014, 9, 22, 15, 2, 8), '2ea52ccf-4265-4b7f-8967-92bd06b2edba', '2014 4.pdf'),
    _month(2014, 9, _zagreb(2014, 10, 21, 12, 58, 47), '9ace4121-2b8b-4aa4-b31e-e21c2573c42a', '2014 2015.pdf'),
    _month(2014, 10, _zagreb(2014, 11, 20, 15, 16, 51), 'eeaae1d5-efc0-40b5-8c2a-8f7df23804c6', '2014 2015.pdf'),
    _month(2014, 11, _zagreb(2014, 12, 17, 15, 11, 40), 'eb311e11-f886-43e4-8363-c51bbc77fddc', '2014 2015.pdf'),
    _month(2014, 12, _zagreb(2015, 2, 4, 16, 34, 8), 'e703010f-0be1-4c0a-8877-86960bdb8120', '2014 2015.pdf'),
    PaperPdvQuarter(
        year=2014,
        month=1,
        period_to=date(2014, 12, 31),
        received_at=_zagreb(2015, 2, 22, 10, 27, 58),
        external_identifier=UUID('35b4ef04-53b8-4d94-a278-edbad00cc2cd'),
        pdf_name='pdv 2015.pdf',
    ),
    _month(2015, 1, _zagreb(2015, 2, 27, 15, 14, 51), '0e70c1cc-9c4e-4b0b-895d-8da03359eb59', 'pdv 2015 x 2.pdf'),
    _month(2015, 2, _zagreb(2015, 3, 23, 10, 48, 51), 'd4b632a6-0815-405b-8efe-1c701e1aaa9e', 'pdv 2015 x 2.pdf'),
    _month(2015, 3, _zagreb(2015, 4, 23, 13, 50, 42), '418a0c55-a8c1-4cd4-96e2-967d7a3c4415', 'pdv 2015 x 2.pdf'),
    _month(2015, 4, _zagreb(2015, 5, 27, 15, 18, 55), 'af67b062-76c1-40d8-b00d-28d58795ac61', 'pdv 2015.pdf'),
    _month(2015, 5, _zagreb(2015, 5, 24, 10, 17, 35), '54e629b2-3ff0-40b7-8607-69c4c070b11f', 'pdv 2015.pdf'),
    _month(2015, 6, _zagreb(2015, 7, 24, 8, 30, 0), 'eea1553b-fa9d-4a79-88ca-4ab21dee9404', 'pdv 2015 3.pdf'),
    _month(2015, 7, _zagreb(2015, 9, 28, 11, 5, 16), 'df4d4257-c26e-4066-8c9b-58d03331bfab', 'pdv 2015 3.pdf'),
    _month(2015, 8, _zagreb(2015, 9, 28, 11, 7, 36), 'a8c2203f-eda0-4fb1-a58c-73cbc0d09d99', 'pdv 2015 3.pdf'),
    _month(2015, 9, _zagreb(2015, 11, 7, 10, 30, 58), 'b5cff094-33dc-410f-8e7b-ca40784bfeb9', 'pdv 2015 5.pdf'),
    _month(2015, 10, _zagreb(2015, 12, 10, 9, 48, 42), '4fc79725-cffc-4ba2-a09b-8fefaea779a4', 'pdv 2015 5.pdf'),
    _month(2015, 11, _zagreb(2016, 1, 25, 8, 56, 13), '84557ca9-2002-4263-a888-0bce1ae188ec', 'pdv 2015 5.pdf'),
    _month(2015, 12, _zagreb(2016, 3, 1, 14, 46, 1), 'd861ef44-79c9-4ae7-98fb-e384aa3033d7', 'pdv 2016 3.pdf'),
    _month(2016, 1, _zagreb(2016, 3, 1, 14, 48, 27), 'eed25156-9e15-49fc-9520-672872b424af', 'pdv 2016 3.pdf'),
    _month(2016, 2, _zagreb(2016, 3, 22, 10, 53, 56), 'b117cb99-e257-4468-b5b8-f5ef3fddab85', 'pdv 2016 2.pdf'),
    PaperPdvQuarter(
        year=2016,
        month=3,
        period_to=None,
        received_at=_zagreb(2016, 4, 25, 12, 6, 34),
        external_identifier=UUID('90cff042-1482-43bd-813f-e0e2dd6e2ed5'),
        pdf_name='pdv 2016 2.pdf',
        keep_ledger=True,
    ),
    _month(2016, 4, _zagreb(2016, 5, 20, 11, 7, 13), 'a45fe0f1-6b24-40f2-828c-fe79cb5e8afd', 'pdv 2016 2.pdf'),
    _month(2016, 5, _zagreb(2016, 6, 24, 9, 35, 57), '451b2bd3-417f-4f93-b6d0-682d4811cce8', 'pdv 2016 2.pdf'),
    _month(2016, 6, _zagreb(2016, 7, 19, 12, 39, 40), '78047719-ea91-4bd6-bdda-a0e339605cc1', 'pdv 2016 1.pdf'),
    _month(2016, 7, _zagreb(2016, 8, 23, 15, 18, 39), '18b4a171-f0ac-40f0-94fa-6ebb0276710e', 'pdv 2016 1.pdf'),
    _month(2016, 8, _zagreb(2016, 9, 20, 11, 29, 11), '570ef099-0f84-4dc0-9fc8-953a5851a94c', 'pdv 2016 1.pdf'),
    _month(2016, 9, _zagreb(2016, 10, 20, 12, 3, 16), '6d0f143d-5272-4c4e-9620-39cc72d6beda', 'pdv 2016.pdf'),
    _month(2016, 10, _zagreb(2016, 11, 21, 11, 27, 44), '16c3137a-9ff4-4b5c-82b4-020535b7f43e', 'pdv 2016.pdf'),
    _month(2016, 11, _zagreb(2016, 12, 19, 13, 12, 18), '6dda920f-8edc-4d2a-b122-4910b8130904', 'pdv 2016.pdf'),
    _month(2016, 12, _zagreb(2017, 1, 30, 10, 28, 24), 'fcc33524-446e-4460-98ee-84107bd71199', 'pdv 2017.pdf'),
    _month(2017, 1, _zagreb(2017, 2, 22, 9, 20, 0), '90bc8635-5dbe-4b4d-976c-1eec3b8ec320', 'pdv 2017.pdf'),
    _month(2017, 2, _zagreb(2017, 3, 23, 9, 19, 0), '94c0e39b-1cc2-4ee4-9616-5e400de97d15', 'pdv 2017.pdf'),
    _month(
        2017, 3, _zagreb(2017, 4, 21, 11, 34, 39), '9887d1b2-9418-4645-ae19-6e70bc4c9828', 'pdv 2017.pdf',
        keep_ledger=True,
    ),
    _month(2017, 4, _zagreb(2017, 5, 15, 8, 37, 6), 'ed911681-5f02-4477-b122-cd57d759f3ee', 'pdv 2017.pdf'),
    _month(2017, 5, _zagreb(2017, 6, 20, 12, 38, 11), '12f41da7-c96f-4dc8-a9d5-7c548bbb2c07', 'pdv 2017 2.pdf'),
    _month(2017, 6, _zagreb(2017, 7, 18, 12, 32, 14), 'f880e00f-978e-4ef7-aef6-045a9fcaf692', 'pdv 2017 2.pdf'),
    _month(2017, 7, _zagreb(2017, 8, 22, 9, 31, 30), 'da553df6-441c-4e6f-88b4-6312bf2b4979', 'pdv 2017 2.pdf'),
    _month(2017, 8, _zagreb(2017, 9, 20, 13, 8, 18), '3ee097e1-9626-4937-a68a-df555a45cdac', 'pdv 2017 2.pdf'),
    _month(2017, 9, _zagreb(2017, 10, 20, 13, 55, 48), 'ebdf9f76-6c13-4cf8-a4eb-f2866a23c67c', 'pdv 2017 3.pdf'),
    _month(
        2017, 10, _zagreb(2017, 11, 20, 9, 2, 37), '8693209d-4342-4331-9fa8-fb6594ef91c4', 'pdv 2017 3.pdf',
        due='-198.75',
        keep_ledger=True,
    ),
    _month(2017, 11, _zagreb(2017, 12, 20, 11, 32, 33), '64368efa-83ef-4e29-8a3b-87d4c31c52b4', 'pdv 2017 3.pdf'),
    _month(2017, 12, _zagreb(2018, 1, 25, 11, 44, 48), 'e03c8c28-327e-43ce-b7d0-49013daf6a22', 'pdv 2017 3.pdf'),
    _month(2018, 1, _zagreb(2018, 2, 22, 13, 57, 0), '36caf1db-8ff0-4526-8f08-f9a33b582d58', 'pdv 2017 4.pdf'),
    _month(2018, 2, _zagreb(2018, 3, 22, 8, 28, 32), 'bbaeb15a-8155-4319-9625-0398d2293792', 'pdv 2017 4.pdf'),
    _month(2018, 3, _zagreb(2018, 4, 20, 9, 43, 37), '71fa3848-527f-47b2-9569-b94276ff3ea7', 'pdv 2017 4.pdf'),
    _month(2018, 4, _zagreb(2018, 5, 21, 11, 32, 0), '96f46ca0-4b22-49a9-823e-2b65cb20071c', 'pdv 2017 4.pdf'),
    _month(2018, 5, _zagreb(2018, 6, 20, 15, 36, 21), '320929b6-d958-4824-be75-4b81b708c07a', 'pdv 2018.pdf'),
    _month(2018, 7, _zagreb(2018, 8, 20, 15, 51, 3), '229c1bba-09eb-407c-8eef-919c2310c892', 'pdv 2018.pdf'),
    _month(2018, 8, _zagreb(2018, 9, 20, 13, 16, 0), 'e7628a8a-6e4c-4f48-82b3-60bd4915c0fb', 'pdv 2018.pdf'),
    _month(2018, 9, _zagreb(2018, 10, 22, 12, 53, 37), '04959011-8981-40e8-8c6c-4e2bf20f1919', 'pdv 2018.pdf'),
    _month(2018, 10, _zagreb(2018, 11, 21, 9, 40, 33), '82c3eaa3-7124-4fe2-9271-4262e92c4b50', 'pdv 2018.pdf'),
    _month(2018, 11, _zagreb(2018, 12, 21, 9, 29, 10), 'faa8f869-ded6-4a21-89c1-6c7095204442', 'pdv 2018 2.pdf'),
    _month(2018, 12, _zagreb(2019, 1, 22, 8, 29, 39), 'e8168899-fc8e-4938-beb0-e4d3a0c263ef', 'pdv 2018 2.pdf'),
    _month(2019, 1, _zagreb(2019, 2, 21, 12, 38, 33), '4a7337db-0392-48fe-877d-423f89d65afd', 'pdv 2018 2.pdf'),
    _month(2019, 1, _zagreb(2019, 2, 21, 12, 39, 13), '8279bb39-f7f6-479a-97b0-c49f7ba909d0', 'pdv 2018 2.pdf'),
    _month(2019, 2, _zagreb(2019, 3, 21, 14, 26, 40), 'd056f898-9802-426c-9ea4-b87e1aa5e668', 'pdv 2018 2.pdf'),
    _month(2019, 2, _zagreb(2019, 3, 21, 14, 29, 51), '0bbecb49-dd47-407c-82fe-88603e4fce46', 'pdv 2018 2.pdf'),
    _month(2019, 4, _zagreb(2019, 6, 21, 9, 2, 12), 'e5935b75-15bd-4a1e-addb-4dd7c7bd6687', 'pdv 2018 2.pdf'),
    _month(2019, 4, _zagreb(2019, 6, 21, 9, 12, 50), '2a8eedd9-9357-4666-94aa-89d9e3a5a223', 'pdv 2018 2.pdf'),
    _month(2019, 5, _zagreb(2019, 6, 21, 9, 16, 4), '8ced5e3b-b144-4714-b7fc-85b5c45b6a8d', 'pdv 2019.pdf'),
    _month(2019, 5, _zagreb(2019, 6, 21, 9, 17, 32), 'ad07258a-7815-4605-86fd-95b4027cfb91', 'pdv 2019.pdf'),
    _month(2019, 6, _zagreb(2019, 7, 19, 14, 58, 36), '4b3e2b40-2203-4be1-b53a-07dd4bc5da24', 'pdv 2019.pdf'),
    _month(2019, 6, _zagreb(2019, 7, 19, 15, 0, 20), '61e2d399-b541-42d7-abde-5accd49d474e', 'pdv 2019.pdf'),
    _month(2019, 7, _zagreb(2019, 8, 22, 13, 31, 17), '9ff0492b-8156-4ab3-a404-9c7612a2da44', 'pdv 2019.pdf'),
    _month(2019, 7, _zagreb(2019, 8, 22, 13, 32, 35), '4ec20257-b0ae-49e9-9176-fca06b205c12', 'pdv 2019.pdf'),
    _month(2019, 8, _zagreb(2019, 9, 23, 11, 16, 8), 'e9a1c5e8-246b-4092-af2d-a911b0584371', 'pdv 2019.pdf'),
    _month(2019, 8, _zagreb(2019, 9, 23, 11, 18, 26), '7a817fb8-ae9b-489a-b195-2b76a2f3013b', 'pdv 2019.pdf'),
    _month(2019, 9, _zagreb(2019, 10, 22, 10, 56, 30), 'f12cd75e-8c55-4397-b248-5804f92e1d47', 'pdv 2019.pdf'),
    _month(2019, 9, _zagreb(2019, 10, 22, 10, 57, 55), 'd43b3941-1ef5-4683-af54-49ac53243483', 'pdv 2019.pdf'),
    _month(2019, 10, _zagreb(2019, 12, 9, 13, 59, 40), '6846d2e7-a69d-4234-aa4d-1766ae700911', 'pdv 2019.pdf'),
    _month(2019, 11, _zagreb(2019, 12, 20, 15, 0, 35), 'f269fdd6-b7c7-444b-89bd-91a0cf30707a', 'pdv 2020.pdf'),
    _month(2020, 1, _zagreb(2020, 2, 19, 13, 45, 24), '9a681756-4809-4dec-9537-139fc5d7b9e2', 'pdv 2020.pdf'),
    _month(2020, 2, _zagreb(2020, 3, 25, 15, 21, 0), '2e43ce45-6462-4741-95d2-79830421a1ff', 'pdv 2020.pdf'),
    _month(2020, 3, _zagreb(2020, 4, 20, 14, 15, 37), 'c87f256c-533d-4918-8378-fa6473d99480', 'pdv 2020.pdf'),
    _month(2020, 4, _zagreb(2020, 5, 20, 10, 18, 1), '24615829-ae73-44db-a34c-0d4081c10023', 'pdv 2020.pdf'),
    _month(2020, 5, _zagreb(2020, 6, 24, 12, 10, 29), '14c77ecc-cb3f-4742-8e52-b81028d4621b', 'pdv 2020.pdf'),
    _month(2020, 6, _zagreb(2020, 7, 20, 13, 48, 0), '3cb852ee-bf32-40e5-821f-92849835728d', 'pdv 2020.pdf'),
    _month(2020, 7, _zagreb(2020, 8, 20, 13, 58, 0), '4562af33-73d0-44ba-9b24-4c50e856beeb', 'pdv 2020.pdf'),
    _month(2020, 8, _zagreb(2020, 9, 21, 10, 52, 0), '8bc7df35-3176-4eeb-bd20-55a9a03cf166', 'pdv 2020.pdf'),
    _month(2020, 9, _zagreb(2020, 10, 22, 12, 58, 47), 'e9e7474a-c7f4-4ff1-9b0d-8cf6f1a1e258', 'pdv 2020.pdf'),
    _month(2020, 10, _zagreb(2020, 11, 24, 14, 43, 59), '57278266-231a-421a-b5da-28af41039b83', 'pdv 2020.pdf'),
    _month(2020, 11, _zagreb(2020, 12, 22, 14, 15, 10), '13b6ff12-d2b3-4d16-9fef-4d7e1328febd', 'pdv 2020 2021.pdf'),
    _month(
        2020, 12, _zagreb(2021, 1, 21, 12, 26, 57), '573f39c7-cf2f-4067-ac9e-0038040f879d', 'pdv 2020 2021.pdf',
        keep_ledger=True,
    ),
    _month(
        2020, 12, _zagreb(2021, 4, 12, 8, 8, 0), 'bb71b40d-ed68-4ea0-bb62-d2926fb16f50', 'pdv 2020 2021.pdf',
        keep_ledger=True,
    ),
    _month(2021, 1, _zagreb(2021, 2, 23, 10, 49, 0), '5c0b9571-7cdc-444f-9bbf-5af37528de28', 'pdv 2020 2021.pdf'),
    _month(2021, 2, _zagreb(2021, 3, 23, 12, 49, 0), 'ff90eada-1e45-497d-ad7b-5d38aaf755e9', 'pdv 2020 2021.pdf'),
    _month(2021, 3, _zagreb(2021, 4, 22, 9, 25, 51), 'a39716fa-a480-4b85-b1eb-2601450dd75f', 'pdv 2020 2021.pdf'),
    _month(
        2021, 4, _zagreb(2021, 5, 20, 15, 0, 20), '534c9731-87b7-497c-963b-83832561f8c1', 'pdv 2020 2021.pdf',
        due='-360.00',
    ),
    _month(
        2021, 4, _zagreb(2021, 5, 20, 15, 1, 42), '2739449b-4a0d-4451-8815-1ea6bc14eaa6', 'pdv 2020 2021.pdf',
        due='-360.00',
    ),
    _month(2021, 5, _zagreb(2021, 6, 21, 10, 27, 41), '13775765-e96b-488a-8ff1-8ca561453707', 'pdv 2020 2021.pdf'),
    _month(2021, 6, _zagreb(2021, 7, 20, 8, 22, 0), '4306e787-9f4c-4cfb-a22b-cebacee431e1', 'pdv 2020 2021.pdf'),
    _month(2021, 8, _zagreb(2021, 9, 21, 9, 31, 15), 'c2be5eb9-6cd4-4b20-8aad-c23d54a41b8f', 'pdv 2020 2021.pdf'),
    _month(2021, 9, _zagreb(2021, 10, 20, 15, 32, 57), 'd5f9f39e-83eb-4b83-b225-3e1f22dd7a25', 'pdv 2020 2021.pdf'),
    _month(2021, 10, _zagreb(2021, 11, 24, 8, 53, 29), 'b7c891f8-0bfe-487c-8ec2-a5c27b3dbcf6', 'pdv 2020 2021.pdf'),
    PaperPdvQuarter(
        year=2022,
        month=1,
        period_to=None,
        received_at=_zagreb(2022, 2, 21, 13, 44, 2),
        external_identifier=UUID('a40d5d45-c33b-442e-b7c1-b33fb24de69a'),
        pdf_name='pdv 2022-02.pdf',
        document_vat_due='-5.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=1,
        period_to=None,
        received_at=_zagreb(2023, 4, 13, 8, 3, 21),
        external_identifier=UUID('16da6473-f4a6-4cf6-9b8b-b536bb3e3197'),
        form_identifier=UUID('b914be90-5c11-4424-a1d7-71da63f25824'),
        pdf_name='pdv 2022 01 ispravci.pdf',
        document_vat_due='-5.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=1,
        period_to=None,
        received_at=_zagreb(2023, 4, 14, 13, 47, 41),
        external_identifier=UUID('58502262-4a97-4243-aea1-2ec2b47d1c4a'),
        form_identifier=UUID('ad824092-8e75-4f60-aab6-001fe17d52e3'),
        pdf_name='pdv 2022 01 ispravci.pdf',
        document_vat_due='-5.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=2,
        period_to=None,
        received_at=_zagreb(2022, 3, 21, 14, 0, 0),
        external_identifier=UUID('e8e81041-bc1f-480d-be7c-f664f58257bb'),
        form_identifier=UUID('d24f17b6-09d0-4498-86ab-7413962e28f7'),
        pdf_name='pdv 2022-02.pdf',
        document_vat_due='-23758.05',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=2,
        period_to=None,
        received_at=_zagreb(2022, 3, 25, 9, 6, 6),
        external_identifier=UUID('407294af-15c8-418a-81f4-2803db588e28'),
        form_identifier=UUID('b3aa43e0-5776-4da5-8611-5634812c7853'),
        pdf_name='pdv 2022-02.pdf',
        document_vat_due='-26740.53',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=3,
        period_to=None,
        received_at=_zagreb(2022, 4, 20, 14, 54, 54),
        external_identifier=UUID('5daf7624-9948-4bfd-94b8-709b6660e1e4'),
        form_identifier=UUID('cbe6d1bd-6bde-48c4-bb25-66dd123f9b0d'),
        pdf_name='pdv 2022 03.pdf',
        document_vat_due='-25462.16',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=4,
        period_to=None,
        received_at=_zagreb(2022, 5, 20, 13, 43, 39),
        external_identifier=UUID('5f4b4f41-1ef8-4d62-bd68-47fb3beea620'),
        form_identifier=UUID('c9856e84-6874-4f05-957d-6d9a7a0f0031'),
        pdf_name='pdv 2022 04.pdf',
        document_vat_due='0.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=5,
        period_to=None,
        received_at=_zagreb(2022, 6, 20, 14, 4, 7),
        external_identifier=UUID('b3afe864-e600-4da7-9c28-c41b5135f846'),
        form_identifier=UUID('d311a4db-abed-48c9-ab52-e49979826878'),
        pdf_name='pdv 2022 05 2.pdf',
        document_vat_due='-4761.71',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=5,
        period_to=None,
        received_at=_zagreb(2022, 6, 21, 13, 56, 44),
        external_identifier=UUID('1d727673-b5c8-4e76-a156-f63414b33748'),
        form_identifier=UUID('fc151f29-b0e4-4ede-9272-75b30a9f49f1'),
        pdf_name='pdv 2022 05.pdf',
        document_vat_due='-57574.15',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=7,
        period_to=None,
        received_at=_zagreb(2022, 8, 18, 8, 47, 49),
        external_identifier=UUID('ec620ecc-3d38-4b2c-8482-5e2a5d0bfc06'),
        form_identifier=UUID('622c25b6-9a2c-44ac-961b-f73dabe1ded6'),
        pdf_name='pdv 2022 07.pdf',
        document_vat_due='-5011.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=8,
        period_to=None,
        received_at=_zagreb(2022, 9, 20, 12, 9, 26),
        external_identifier=UUID('4eae7245-eee4-42ae-be45-77001c10ab56'),
        form_identifier=UUID('1c2b3cac-63c1-4d6e-b48a-b983db06399c'),
        pdf_name='pdv 2022 08.pdf',
        document_vat_due='0.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=9,
        period_to=None,
        received_at=_zagreb(2022, 10, 18, 7, 44, 18),
        external_identifier=UUID('3844c2be-b18e-4e69-b05c-b99c9114a20e'),
        form_identifier=UUID('8932736a-935c-404b-a6d7-92aa66f56ac7'),
        pdf_name='pdv 2022 09.pdf',
        document_vat_due='0.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=10,
        period_to=None,
        received_at=_zagreb(2022, 11, 15, 11, 21, 33),
        external_identifier=UUID('bb98b9dd-7ad1-4f7b-939d-94c1c055f4ed'),
        form_identifier=UUID('6732026d-6c6e-4d73-93bd-0b512cbad8a2'),
        pdf_name='pdv 2022 10.pdf',
        document_vat_due='-600.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=10,
        period_to=None,
        received_at=_zagreb(2022, 12, 1, 13, 5, 52),
        external_identifier=UUID('7460a418-dd41-4af9-a17b-455dd7a29953'),
        form_identifier=UUID('66490717-67c1-4d68-bced-a4036fd460c9'),
        pdf_name='pdv 2022 10 i preknjiženje.pdf',
        document_vat_due='-11095.44',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=10,
        period_to=None,
        received_at=_zagreb(2022, 12, 1, 13, 24, 52),
        external_identifier=UUID('75363826-85ae-4328-b3f2-61ca13393028'),
        form_identifier=UUID('baf44868-affa-42a6-9316-c058dc2139df'),
        pdf_name='pdv 2022 10 i preknjiženje.pdf',
        document_vat_due='-11095.44',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=11,
        period_to=None,
        received_at=_zagreb(2022, 12, 19, 8, 4, 44),
        external_identifier=UUID('c339b489-3b08-40b9-a9a7-b16b5a1da516'),
        form_identifier=UUID('1a76912d-49d7-406e-b6a1-b2b6e62f6896'),
        pdf_name='pdv 2022 11.pdf',
        document_vat_due='0.00',
        tax_office='Šibenik',
    ),
    PaperPdvQuarter(
        year=2022,
        month=12,
        period_to=None,
        received_at=_zagreb(2023, 1, 19, 11, 45, 53),
        external_identifier=UUID('14c6cd9a-7e6d-48a0-9aaa-1760f78cda57'),
        form_identifier=UUID('18a1fd32-bc83-4dec-b3c2-b908ae2ad3a2'),
        pdf_name='pdv 2022 12.pdf',
        document_vat_due='0.00',
        tax_office='Šibenik',
    ),
)


def _filing_end(quarter: PaperPdvQuarter) -> date:
    if quarter.period_to is not None:
        return quarter.period_to
    last_day = calendar.monthrange(quarter.year, quarter.month)[1]
    return date(quarter.year, quarter.month, last_day)


def filing_label(quarter: PaperPdvQuarter) -> str:
    start = f'{quarter.year:04d}-{quarter.month:02d}'
    end = _filing_end(quarter)
    if (end.year, end.month) == (quarter.year, quarter.month):
        return start
    return f'{start}..{end.year:04d}-{end.month:02d}'


def _paper_snapshot(quarter: PaperPdvQuarter) -> dict:
    start = date(quarter.year, quarter.month, 1)
    due = quarter.document_vat_due
    sibenik = quarter.tax_office == 'Šibenik'
    snapshot = {
        'document_currency': 'HRK',
        'document_vat_due': due,
        'fields': {},
        'form_identifier': str(quarter.form_identifier) if quarter.form_identifier else None,
        'kind': 'paper-pdv-2014',
        'mapping_version': 0,
        'nil_return': Decimal(due) == 0,
        'period_from': start.isoformat(),
        'period_to': _filing_end(quarter).isoformat(),
        'schema_version': PAPER_SCHEMA,
        'tax_office': quarter.tax_office or 'SUSEDGRAD',
        'taxpayer': _TAXPAYER_SIBENIK if sibenik else _TAXPAYER,
    }
    if snapshot['form_identifier'] is None:
        del snapshot['form_identifier']
    return snapshot


def _canonical(snapshot: dict) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(',', ':'))


def _assert_tenant_oib(tenant) -> None:
    settings = CompanySettings.all_objects.filter(tenant=tenant).first()
    if settings is None:
        return
    digits = ''.join(ch for ch in (settings.vat_number or '') if ch.isdigit())
    if digits and digits != PAPER_OIB:
        raise PaperPdvImportError(
            f'OIB tvrtke {digits} nije {PAPER_OIB}. Ove prijave su Fine Star 2014.',
        )


def _period_for(tenant, quarter: PaperPdvQuarter) -> VATPeriod:
    lookup = VATPeriod.all_objects.filter(
        tenant=tenant,
        year=quarter.year,
        month=quarter.month,
    )
    if quarter.period_to is None:
        period = lookup.filter(period_to__isnull=True).first()
    else:
        period = lookup.filter(period_to=quarter.period_to).first()
    if period is None:
        return VATPeriod.objects.create(
            tenant=tenant,
            year=quarter.year,
            month=quarter.month,
            period_to=quarter.period_to,
            status='open',
        )
    if (
        not quarter.keep_ledger
        and VATLedgerEntry.all_objects.filter(vat_period=period).exists()
    ):
        raise PaperPdvImportError(
            f'PDV {quarter.month:02d}/{quarter.year} ima knjigu i nije papirnata arhiva.',
        )
    other = period.returns.exclude(
        source=VATReturnSource.IMPORTED,
        status=VATReturnStatus.IMPORTED,
    )
    if other.exists():
        raise PaperPdvImportError(
            f'PDV {quarter.month:02d}/{quarter.year} već ima obrazac koji nije arhiva.',
        )
    return period


def _create_return(period: VATPeriod, quarter: PaperPdvQuarter) -> VATReturn:
    for existing in period.returns.filter(
        source=VATReturnSource.IMPORTED,
        status=VATReturnStatus.IMPORTED,
    ).order_by('version'):
        due = (existing.payload_snapshot or {}).get('document_vat_due')
        if due == quarter.document_vat_due:
            return existing
    snapshot = _paper_snapshot(quarter)
    canonical = _canonical(snapshot)
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    version = next_vat_return_version(period)
    return VATReturn.objects.create(
        tenant=period.tenant,
        vat_period=period,
        version=version,
        status=VATReturnStatus.IMPORTED,
        source=VATReturnSource.IMPORTED,
        schema_version=PAPER_SCHEMA,
        mapping_version=0,
        payload_snapshot=snapshot,
        payload_hash=digest,
        payload_json=ContentFile(canonical.encode('utf-8'), name='payload.json'),
    )


@transaction.atomic
def _import_quarter(tenant, quarter: PaperPdvQuarter, pdf_bytes: bytes, pdf_name: str) -> str:
    if SubmissionEvent.all_objects.filter(
        tenant=tenant,
        destination=SubmissionDestination.EPOREZNA,
        external_identifier=quarter.external_identifier,
    ).exists():
        return 'skipped'
    period = _period_for(tenant, quarter)
    vat_return = _create_return(period, quarter)
    SubmissionService.create_event(
        vat_return,
        destination=SubmissionDestination.EPOREZNA,
        external_identifier=quarter.external_identifier,
        submitted_at=quarter.received_at,
        submitted_by=None,
        source=SubmissionSource.IMPORT,
        version_confirmed=True,
        confirmation_attachment=ContentFile(pdf_bytes, name=pdf_name),
        book_period=True,
    )
    return 'imported'


def import_finestar_pdv_2014_h1(tenant, confirmation_path: Path) -> list[tuple[str, str]]:
    """Record the two nil quarterly filings. Safe to run again."""
    path = Path(confirmation_path)
    if not path.is_file():
        raise PaperPdvImportError(f'Potvrda ne postoji: {path}')
    _assert_tenant_oib(tenant)
    pdf_bytes = path.read_bytes()
    pdf_name = path.name
    results = []
    for quarter in FINESTAR_2014_H1:
        outcome = _import_quarter(tenant, quarter, pdf_bytes, pdf_name)
        results.append((filing_label(quarter), outcome))
    return results


def import_finestar_paper_filings(tenant, directory: Path) -> list[tuple[str, str]]:
    """Record the monthly nil filings and the 2014 annual return. Safe to run again."""
    root = Path(directory)
    if not root.is_dir():
        raise PaperPdvImportError(f'Direktorij ne postoji: {root}')
    _assert_tenant_oib(tenant)
    missing = sorted({item.pdf_name for item in FINESTAR_PAPER_FILINGS if not (root / item.pdf_name).is_file()})
    if missing:
        raise PaperPdvImportError('Nedostaje PDF: ' + ', '.join(missing))
    cache: dict[str, bytes] = {}
    results = []
    for quarter in FINESTAR_PAPER_FILINGS:
        if quarter.pdf_name not in cache:
            cache[quarter.pdf_name] = (root / quarter.pdf_name).read_bytes()
        try:
            outcome = _import_quarter(tenant, quarter, cache[quarter.pdf_name], quarter.pdf_name)
        except PaperPdvImportError as exc:
            outcome = f'preskočeno: {exc}'
        results.append((filing_label(quarter), outcome))
    return results
