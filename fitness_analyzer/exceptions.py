"""Custom exception classes for the Smart Fitness Session Analyzer.

Each class marks a *different kind* of failure so the code that catches it can
react appropriately:

* ``InvalidIdentifierError`` -- an ID does not match its required format.
* ``InvalidRecordError``     -- a CSV row cannot be accepted (carries every
  field-level problem found in that row).
* ``DataFileError``          -- a whole file cannot be read or written; wraps
  the low level ``OSError`` / ``csv.Error`` with the path and a plain reason.
* ``ValidationError``        -- an object (profile, observation, session)
  refused to be constructed from bad data.
"""

from typing import Iterable, Tuple


class ValidationError(ValueError):
    """Raised when data is too malformed to build a domain object from."""


class InvalidIdentifierError(ValueError):
    """Raised when an identifier has an invalid format."""

    def __init__(self, field: str, value, expected: str):
        self.field = field
        self.value = value
        self.expected = expected
        super().__init__(f"{value!r} is not a valid {field} (expected {expected})")


class InvalidRecordError(ValueError):
    """Raised when a CSV record cannot be accepted.

    ``issues`` is a list of ``(field, reason)`` pairs: a row is checked
    completely, so one rejection can report several problems at once.
    """

    def __init__(self, issues: Iterable[Tuple[str, str]]):
        self.issues = list(issues)
        text = "; ".join(f"{field}: {reason}" for field, reason in self.issues)
        super().__init__(text or "record rejected")


class DataFileError(Exception):
    """Raised when an input or output file cannot be used as a whole."""

    def __init__(self, path, reason: str):
        self.path = str(path)
        self.reason = reason
        super().__init__(f"{self.path}: {reason}")
