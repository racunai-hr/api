from __future__ import annotations


class UblError(Exception):
    """Base error for UBL document pipeline."""

    messages: list[str]

    def __init__(self, messages: list[str] | None = None, *, message: str | None = None):
        if messages is None:
            messages = [message] if message else []
        self.messages = messages
        super().__init__('; '.join(messages) if messages else (message or ''))


class SemanticValidationError(UblError):
    """Poslovna pravila HR CIUS — field-level poruke."""


class XsdValidationError(UblError):
    """UBL Invoice XSD validation failed."""


class SchematronValidationError(UblError):
    """HR CIUS Schematron validation failed."""


class RenderingError(UblError):
    """UBL XML rendering failed."""


class DocumentBuildError(UblError):
    """Document could not be built from source data (e.g. missing CompanySettings)."""
