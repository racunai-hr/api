from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from banking.parsers.types import ParsedBankStatement


@dataclass(frozen=True)
class BankStatementImportResult:
    import_id: str
    format: str
    statements_processed: int
    statements_created: int
    statements_updated: int
    transactions_processed: int
    transactions_created: int
    transactions_skipped: int
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    duration_ms: int


@dataclass
class ImportResultBuilder:
    statements_processed: int = 0
    statements_created: int = 0
    statements_updated: int = 0
    transactions_processed: int = 0
    transactions_created: int = 0
    transactions_skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def build(self, *, format: str, duration_ms: int, import_id: str) -> BankStatementImportResult:
        return BankStatementImportResult(
            import_id=import_id,
            format=format,
            statements_processed=self.statements_processed,
            statements_created=self.statements_created,
            statements_updated=self.statements_updated,
            transactions_processed=self.transactions_processed,
            transactions_created=self.transactions_created,
            transactions_skipped=self.transactions_skipped,
            warnings=tuple(self.warnings),
            errors=tuple(self.errors),
            duration_ms=duration_ms,
        )


class BankStatementImporter(Protocol):
    format_key: str

    def import_parsed(
        self,
        *,
        tenant,
        user,
        statements: list[ParsedBankStatement],
        import_id: str,
    ) -> ImportResultBuilder:
        ...
