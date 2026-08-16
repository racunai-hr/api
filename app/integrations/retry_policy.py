from __future__ import annotations

from fiscal_gateway.client.as4_client import As4SendError
from fiscal_gateway.client.cis_client import CISClientError
from ubl.domain.errors import (
    SchematronValidationError,
    SemanticValidationError,
    UblError,
    XsdValidationError,
)


def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, (As4SendError, CISClientError)):
        return True
    if isinstance(exc, (SemanticValidationError, XsdValidationError, SchematronValidationError)):
        return False
    if isinstance(exc, UblError):
        return False
    msg = str(exc).lower()
    if any(token in msg for token in ('timeout', 'connection', '503', '502', '504', 'network')):
        return True
    return False
