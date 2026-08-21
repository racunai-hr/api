from __future__ import annotations


class PurchasingConflict(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class IdempotencyKeyReused(PurchasingConflict):
    def __init__(self):
        super().__init__('idempotency_key_reused', 'Idempotency-Key već postoji s drugačijim sadržajem.')


class ImportProcessing(PurchasingConflict):
    def __init__(self):
        super().__init__('processing', 'Uvoz se trenutačno obrađuje.')


class InvalidImportStatus(PurchasingConflict):
    def __init__(self, detail: str = 'Akcija nije dopuštena u trenutnom statusu.'):
        super().__init__('invalid_status', detail)


class HardDuplicate(PurchasingConflict):
    def __init__(self):
        super().__init__('hard_duplicate', 'Datoteka je već potvrđena kao ulazni račun.')


class DuplicateOverrideRequired(PurchasingConflict):
    def __init__(self):
        super().__init__(
            'duplicate_override_required',
            'Mogući duplikat računa. Potrebna je eksplicitna potvrda.',
        )


class PartnerChanged(PurchasingConflict):
    def __init__(self):
        super().__init__('partner_changed', 'Partner je u međuvremenu izmijenjen. Ponovno učitajte razlike.')


class PartnerRequired(PurchasingConflict):
    def __init__(self):
        super().__init__('partner_required', 'Partner mora biti spojen prije potvrde računa.')


class PurchasingBadRequest(Exception):
    """400 with a stable code (ADR-0023 country / MDM write)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail
