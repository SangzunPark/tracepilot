class DiagnosticError(Exception):
    """Base exception for diagnostic service errors."""


class CaseNotFoundError(DiagnosticError):
    """Raised when an incident case does not exist."""


class UnknownServiceError(DiagnosticError):
    """Raised when a service or provider is not known."""


class InvalidTimeRangeError(DiagnosticError):
    """Raised when a diagnostic time range is invalid."""
