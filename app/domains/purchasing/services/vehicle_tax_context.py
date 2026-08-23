"""Map ExpenseLine persistence facts into TaxEvaluationContext. No tax rules."""

from __future__ import annotations

from domains.tax.vehicle.contracts import (
    DocumentFacts,
    LineFacts,
    TaxEvaluationContext,
    VehicleFacts,
)
from expenses.models import ExpenseLine


def context_from_expense_line(
    line: ExpenseLine,
    *,
    vehicle: VehicleFacts | None = None,
    document: DocumentFacts | None = None,
) -> TaxEvaluationContext:
    """Copy line amounts and kind only. Vehicle/document stay explicit or all-NULL."""
    return TaxEvaluationContext(
        line=LineFacts(
            vehicle_line_kind=line.vehicle_line_kind,
            net_amount=line.net_amount,
            vat_amount=line.vat_amount,
            gross_amount=line.gross_amount,
        ),
        vehicle=vehicle if vehicle is not None else VehicleFacts(),
        document=document if document is not None else DocumentFacts(),
    )
