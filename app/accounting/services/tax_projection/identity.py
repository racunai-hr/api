"""Row identity for projection DTOs. Does not change classify()."""

from __future__ import annotations

from accounting.services.tax_forms.pdv.mapping import RC_INPUT_BOXES, RC_OUTPUT_BOXES
from accounting.services.tax_projection.contracts import ProjectedLedgerRow
from domains.tax.classification.contracts import EventKind, ProposedLedgerRow


def source_line_kind(row: ProposedLedgerRow) -> str:
    if row.source_kind == 'invoice_item':
        return 'invoice_item'
    if row.source_kind == 'journal_line' and row.source_line_id:
        return 'journal_line'
    return 'none'


def row_role(row: ProposedLedgerRow) -> str:
    if row.rule_code == 'PERIOD_VIII1_610' or (
        row.box == '610' and row.source_kind == 'vat_period'
    ):
        return 'period_610'
    if row.source_kind == 'manual_ledger':
        return f'manual:{row.box}'
    if row.event_kind == EventKind.REVERSAL:
        return f'reversal:{row.box}'
    if row.box in RC_INPUT_BOXES:
        return 'rc_input'
    if row.box in RC_OUTPUT_BOXES:
        if row.base_amount > 0 and row.tax_amount == 0:
            return 'rc_acquisition'
        return 'rc_output'
    return f'standard:{row.box}'


def to_projected(row: ProposedLedgerRow) -> ProjectedLedgerRow:
    return ProjectedLedgerRow(
        source_kind=row.source_kind,
        source_id=row.source_document_id,
        source_line_kind=source_line_kind(row),
        source_line_id=row.source_line_id,
        row_role=row_role(row),
        event_kind=row.event_kind.value,
        box=row.box,
        category=row.category,
        direction=row.direction.value,
        ledger_type=row.ledger_type,
        base_amount=row.base_amount,
        tax_amount=row.tax_amount,
        sign=row.sign,
        rule_code=row.rule_code,
        mapping_version=row.mapping_version,
    )


def identity_of(row: ProjectedLedgerRow) -> tuple[str, int, str, int, str]:
    return (
        row.source_kind,
        row.source_id,
        row.source_line_kind,
        row.source_line_id if row.source_line_id is not None else 0,
        row.row_role,
    )
