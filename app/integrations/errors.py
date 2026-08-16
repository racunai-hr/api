from ubl.domain.errors import UblError


class IntegrationError(Exception):
    """Base error for integration layer."""


class IntegrationValidationError(IntegrationError, UblError):
    """UBL validation error at integration boundary."""


class UnknownIntegrationError(IntegrationError):
    """Raised when no connector is registered for a type/provider pair."""


class InactiveIntegrationError(IntegrationError):
    """Raised when integration config or provider credentials are inactive."""


class MissingIntegrationError(IntegrationError):
    """Raised when no active integration exists for the tenant."""
