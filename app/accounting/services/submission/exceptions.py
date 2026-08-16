"""Submission module exceptions."""


class CreateSubmissionEventError(Exception):
    """Raised when a submission event cannot be created."""


class DuplicateExternalIdentifierError(CreateSubmissionEventError):
    """Raised when external_identifier already exists for destination."""


class TransitionSubmissionStateError(Exception):
    """Raised when a state transition is not allowed."""


class AttachConfirmationError(Exception):
    """Raised when a confirmation attachment cannot be stored."""


class SupersedeSubmissionError(Exception):
    """Raised when a superseding submission cannot be created."""
