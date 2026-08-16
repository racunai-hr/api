"""Import RRIF platform predloška."""

from __future__ import annotations

from django.db import transaction

from accounting.models import RRIFChartEntry
from accounting.rrif import DEFAULT_CSV_PATH, enrich_rrif_rows, load_rrif_rows


@transaction.atomic
def import_rrif_chart(csv_path=None, *, clear: bool = False) -> tuple[int, int]:
    path = csv_path or DEFAULT_CSV_PATH
    rows = enrich_rrif_rows(load_rrif_rows(path))

    if clear:
        RRIFChartEntry.objects.all().delete()

    created = 0
    updated = 0
    for row in rows:
        obj, was_created = RRIFChartEntry.objects.update_or_create(
            code=row['code'],
            defaults={
                'name': row['name'],
                'parent_code': row['parent_code'] or '',
                'account_class': row['account_class'],
                'account_type_name': row['account_type_name'],
                'level': row['level'],
                'is_synthetic': row['is_synthetic'],
                'is_postable': row['is_postable'],
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return created, updated
