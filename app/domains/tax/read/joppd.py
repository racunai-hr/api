"""JOPPD period read-model."""

from __future__ import annotations

from accounting.services.submission.events import get_submission_events
from accounting.services.submission.service import SubmissionService
from domains.tax.forms.joppd.aggregate import aggregate_joppd_rows
from domains.tax.forms.joppd.joppd_returns import get_latest_joppd_return
from domains.tax.forms.joppd.payload import JOPPD_SCHEMA_VERSION
from domains.tax.read.dto import money2, pdv_s_submission_dto


def joppd_period_dto(tenant, year: int, month: int) -> dict:
    payload = aggregate_joppd_rows(tenant, year, month)
    event_uuid = None
    current_submission = None
    submissions: list[dict] = []
    joppd_return = get_latest_joppd_return(tenant, year, month)
    if joppd_return is not None:
        submissions = [pdv_s_submission_dto(event) for event in get_submission_events(joppd_return)]
        current = SubmissionService.current_submission(joppd_return)
        if current is not None:
            current_submission = pdv_s_submission_dto(current)
            event_uuid = current_submission['event_uuid']

    rows = [
        {
            'recipient_oib': row.recipient_oib,
            'first_name': row.first_name,
            'last_name': row.last_name,
            'recipient_code': row.recipient_code,
            'income_type_code': row.income_type_code,
            'gross_salary': money2(row.gross_salary),
            'mio1_amount': money2(row.mio1_amount),
            'mio2_amount': money2(row.mio2_amount),
            'income_tax_amount': money2(row.income_tax_amount),
            'zo_amount': money2(row.zo_amount),
            'net_salary': money2(row.net_salary),
        }
        for row in payload.rows
    ]
    return {
        'period': f'{year:04d}-{month:02d}',
        'schema_version': JOPPD_SCHEMA_VERSION,
        'payment_date': payload.payment_date.isoformat(),
        'row_count': len(rows),
        'total_gross': money2(payload.total_gross),
        'total_mio1': money2(payload.total_mio1),
        'total_mio2': money2(payload.total_mio2),
        'total_income_tax': money2(payload.total_income_tax),
        'total_zo': money2(payload.total_zo),
        'total_net': money2(payload.total_net),
        'return_version': joppd_return.version if joppd_return is not None else None,
        'rows': rows,
        'event_uuid': event_uuid,
        'current_submission': current_submission,
        'submissions': submissions,
    }
