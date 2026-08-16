import pytest

from ubl.domain.errors import (
    DocumentBuildError,
    RenderingError,
    SchematronValidationError,
    SemanticValidationError,
    UblError,
    XsdValidationError,
)


def test_ubl_error_hierarchy():
    assert issubclass(SemanticValidationError, UblError)
    assert issubclass(XsdValidationError, UblError)
    assert issubclass(SchematronValidationError, UblError)
    assert issubclass(RenderingError, UblError)
    assert issubclass(DocumentBuildError, UblError)


def test_ubl_error_messages():
    exc = SemanticValidationError(['OIB nevaljan', 'Nedostaje total'])
    assert exc.messages == ['OIB nevaljan', 'Nedostaje total']
    assert 'OIB nevaljan' in str(exc)


def test_integration_validation_error_is_both():
    from integrations.errors import IntegrationError, IntegrationValidationError

    exc = IntegrationValidationError(['validation failed'])
    assert isinstance(exc, IntegrationError)
    assert isinstance(exc, UblError)
