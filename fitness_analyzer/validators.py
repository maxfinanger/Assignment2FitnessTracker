"""Regular-expression validation of identifiers.

Only *format* checks use regular expressions. Numeric range checks (heart
rate, activity level ...) are ordinary comparisons in ``models.py``.

Two details worth knowing:

* ``fullmatch`` is used, so the whole string must match. ``^...$`` alone is
  not enough: ``$`` also matches just before a trailing newline, which would
  let ``"P001\\n"`` through.
* ``re.ASCII`` makes ``\\d`` mean only 0-9. Without it Python would also
  accept digits from other scripts (for example Arabic-Indic digits).
"""

import re

from .exceptions import InvalidIdentifierError

PARTICIPANT_ID_PATTERN = re.compile(r"^P\d{3}$", re.ASCII)
SESSION_ID_PATTERN = re.compile(r"^FIT-\d{4}-\d{3}$", re.ASCII)


def validate_identifier(value, pattern, field: str, expected: str) -> str:
    """Return ``value`` if it fully matches ``pattern``, else raise."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise InvalidIdentifierError(field, value, expected)
    return value


def validate_participant_id(value, field: str = "participant_id") -> str:
    return validate_identifier(
        value, PARTICIPANT_ID_PATTERN, field, "P followed by 3 digits, e.g. P001"
    )


def validate_session_id(value, field: str = "session_id") -> str:
    return validate_identifier(
        value, SESSION_ID_PATTERN, field, "FIT-YYYY-NNN, e.g. FIT-2026-001"
    )
