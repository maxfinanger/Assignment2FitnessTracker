"""Load participant profiles and fitness sessions from CSV files.

Design in one paragraph
-----------------------
``read_csv_table`` is the only function that touches the file system. It
turns every low level failure (missing file, no permission, bad encoding,
malformed CSV) into one ``DataFileError`` that names the file and the reason.
Everything after that works on plain lists of strings, row by row. Each row is
checked *completely* (all problems collected, not just the first), and a bad
row is recorded as a ``RejectedRecord`` and skipped, so one broken row never
stops the run. Rows that pass are converted to typed domain objects and
grouped into ``TrainingSession`` objects by session identifier.
"""

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from .exceptions import (
    DataFileError,
    InvalidIdentifierError,
    InvalidRecordError,
    ValidationError,
)
from .models import (
    FitnessObservation,
    Issue,
    ParticipantProfile,
    RejectedRecord,
    TrainingSession,
)
from .validators import validate_participant_id, validate_session_id

PathLike = Union[str, Path]

PROFILE_COLUMNS = (
    "participant_id",
    "name",
    "baseline_heart_rate",
    "baseline_skin_response",
    "baseline_temperature",
)
SESSION_COLUMNS = (
    "session_id",
    "participant_id",
    "timestamp",
    "heart_rate",
    "skin_response",
    "temperature",
    "activity_level",
    "signal_quality",
)

# column -> (converter, plain-language description used in error messages)
_INTEGER = (int, "a whole number")
_DECIMAL = (float, "a number")
PROFILE_CONVERTERS = {
    "baseline_heart_rate": _DECIMAL,
    "baseline_skin_response": _DECIMAL,
    "baseline_temperature": _DECIMAL,
}
SESSION_CONVERTERS = {
    "timestamp": _INTEGER,
    "heart_rate": _DECIMAL,
    "skin_response": _DECIMAL,
    "temperature": _DECIMAL,
    "activity_level": _DECIMAL,
    "signal_quality": _DECIMAL,
}


# --- result containers -------------------------------------------------------


@dataclass
class LoadResult:
    """What happened to one input file."""

    source_file: str
    rows_read: int = 0
    rows_accepted: int = 0
    rejected: List[RejectedRecord] = field(default_factory=list)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected)


@dataclass
class ProfileLoadResult(LoadResult):
    profiles: Dict[str, ParticipantProfile] = field(default_factory=dict)


@dataclass
class SessionLoadResult(LoadResult):
    sessions: List[TrainingSession] = field(default_factory=list)
    # Sessions whose rows never named a valid, known participant. Their rows
    # are all in ``rejected``; there is no profile to analyse them against.
    unlinked_sessions: List[str] = field(default_factory=list)


@dataclass
class _SessionBucket:
    """Working storage for one session while its rows are still being read."""

    session_id: str
    profile: Optional[ParticipantProfile] = None
    observations: List[FitnessObservation] = field(default_factory=list)
    rejections: List[RejectedRecord] = field(default_factory=list)

    def has_timestamp(self, timestamp: int) -> bool:
        return any(o.timestamp == timestamp for o in self.observations)


# --- file access -----------------------------------------------------------


def read_csv_table(
    path: PathLike, required_columns: Sequence[str]
) -> Tuple[List[str], List[Tuple[int, List[str]]]]:
    """Read a CSV file and return ``(header, [(line_number, row), ...])``.

    Blank lines are skipped. ``line_number`` is the physical line in the file
    (the header is line 1), which is what a person opening the file in an
    editor will look for.

    Raises ``DataFileError`` when the file as a whole is unusable.
    """
    path = Path(path)
    reader = None
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise DataFileError(path, "file is empty (no header row)") from None

            header = [name.strip() for name in header]
            if header:
                header[0] = header[0].lstrip("\ufeff")   # tolerate a UTF-8 BOM
            missing = [name for name in required_columns if name not in header]
            if missing:
                raise DataFileError(
                    path,
                    f"missing required column(s): {', '.join(missing)} "
                    f"(found: {', '.join(header)})",
                )
            rows = [(reader.line_num, row) for row in reader if row]
    except FileNotFoundError as error:
        raise DataFileError(path, "file not found") from error
    except PermissionError as error:
        raise DataFileError(path, "permission denied") from error
    except IsADirectoryError as error:
        raise DataFileError(path, "is a directory, not a CSV file") from error
    except UnicodeDecodeError as error:
        raise DataFileError(path, f"not valid UTF-8 text ({error.reason})") from error
    except csv.Error as error:
        line = reader.line_num if reader is not None else "?"
        raise DataFileError(path, f"malformed CSV near line {line}: {error}") from error

    return header, rows


# --- conversion helpers ------------------------------------------------------


def _cell(row: List[str], index: int) -> str:
    """The stripped text of a column, or '' if the row is too short to have it."""
    return row[index].strip() if index < len(row) else ""


def _convert_fields(
    raw: Dict[str, str], converters: Dict[str, Tuple[Callable, str]]
) -> Tuple[Dict[str, Optional[float]], List[Issue]]:
    """Convert text to numbers; return the typed values and the problems found.

    A field that cannot be converted is stored as ``None`` (so later checks
    can skip it) and reported as an issue. Nothing is raised here: the caller
    wants *all* problems in the row, not just the first one.
    """
    typed: Dict[str, Optional[float]] = {}
    issues: List[Issue] = []
    for name, (convert, description) in converters.items():
        text = raw[name].strip()
        if text == "":
            typed[name] = None
            issues.append((name, "is missing (empty value)"))
            continue
        try:
            value = convert(text)
        except ValueError:
            typed[name] = None
            issues.append((name, f"{text!r} cannot be converted to {description}"))
            continue
        if isinstance(value, float) and not math.isfinite(value):
            typed[name] = None
            issues.append((name, f"{text!r} is not a finite number"))
            continue
        typed[name] = value
    return typed, issues


def _identifier_or_none(text: str, validator: Callable, issues: List[Issue], field_name: str):
    """Validate an identifier; on failure note the issue and return None."""
    try:
        return validator(text, field_name)
    except InvalidIdentifierError as error:
        issues.append((field_name, str(error)))
        return None


def _raw_line(row: List[str]) -> str:
    return ",".join(row)


# --- profiles ------------------------------------------------------------------


def _parse_profile_row(
    raw: Dict[str, str], seen_ids: Dict[str, int]
) -> ParticipantProfile:
    """Turn one profile row into a ``ParticipantProfile`` or raise.

    Raises ``InvalidRecordError`` carrying every field-level problem.
    """
    issues: List[Issue] = []
    participant_id = _identifier_or_none(
        raw["participant_id"].strip(), validate_participant_id, issues, "participant_id"
    )
    if participant_id is not None and participant_id in seen_ids:
        issues.append(
            ("participant_id", f"duplicate of {participant_id} already defined on row {seen_ids[participant_id]}")
        )

    name = raw["name"].strip()
    if not name:
        issues.append(("name", "is missing (empty value)"))

    typed, convert_issues = _convert_fields(raw, PROFILE_CONVERTERS)
    issues.extend(convert_issues)

    failed = {name_ for name_, _ in convert_issues}
    for field_name, reason in ParticipantProfile.check_baselines(
        typed["baseline_heart_rate"],
        typed["baseline_skin_response"],
        typed["baseline_temperature"],
    ):
        if field_name not in failed:
            issues.append((field_name, reason))

    if issues:
        raise InvalidRecordError(issues)

    try:
        return ParticipantProfile.from_dict({"participant_id": participant_id, "name": name, **typed})
    except ValidationError as error:   # defensive: the checks above should prevent this
        raise InvalidRecordError([("row", str(error))]) from error


def load_profiles(path: PathLike) -> ProfileLoadResult:
    """Load participants. Bad rows are recorded and skipped.

    Raises ``DataFileError`` if the file itself cannot be used.
    """
    header, rows = read_csv_table(path, PROFILE_COLUMNS)
    index = {name: header.index(name) for name in PROFILE_COLUMNS}
    result = ProfileLoadResult(source_file=str(path))
    first_seen: Dict[str, int] = {}

    for line_number, row in rows:
        result.rows_read += 1
        try:
            if len(row) != len(header):
                raise InvalidRecordError(
                    [("row", f"expected {len(header)} fields but found {len(row)}")]
                )
            raw = {name: row[i] for name, i in index.items()}
            profile = _parse_profile_row(raw, first_seen)
        except InvalidRecordError as error:
            result.rejected.append(
                RejectedRecord(str(path), line_number, tuple(error.issues), _raw_line(row))
            )
            continue
        first_seen[profile.participant_id] = line_number
        result.profiles[profile.participant_id] = profile
        result.rows_accepted += 1

    return result


# --- sessions ------------------------------------------------------------------


def _parse_observation(raw: Dict[str, str]) -> Tuple[Optional[FitnessObservation], List[Issue]]:
    """Convert and range-check the measurement columns of one row.

    Returns ``(observation, [])`` for a good row, or ``(None, issues)``.
    """
    typed, issues = _convert_fields(raw, SESSION_CONVERTERS)
    failed = {name for name, _ in issues}

    timestamp = typed["timestamp"]
    if timestamp is not None and timestamp < 0:
        issues.append(("timestamp", f"{timestamp} is negative"))
        failed.add("timestamp")

    # If the timestamp is unusable a placeholder is used only so the other
    # fields can still be range checked; the timestamp problem is already
    # recorded and the observation is discarded because ``issues`` is non-empty.
    observation = FitnessObservation.from_dict(
        {**typed, "timestamp": typed["timestamp"] if "timestamp" not in failed else 0}
    )
    for field_name, reason in observation.validate_fields():
        if field_name not in failed:       # don't repeat a conversion failure
            issues.append((field_name, reason))

    return (observation if not issues else None), issues


def load_sessions(path: PathLike, profiles: Dict[str, ParticipantProfile]) -> SessionLoadResult:
    """Load one session file, validate every row and group rows into sessions.

    A row is rejected (and the run continues) for: wrong number of fields,
    malformed identifiers, unknown participant, a participant that contradicts
    the rest of its session, unparseable or out-of-range measurements, or a
    repeated timestamp within a session.

    Rows are attributed to a session as soon as their session identifier is
    valid, even if the rest of the row is bad, so a session whose every row was
    rejected still appears in the results (as ``insufficient_data``) instead of
    vanishing.

    Raises ``DataFileError`` if the file itself cannot be used.
    """
    header, rows = read_csv_table(path, SESSION_COLUMNS)
    index = {name: header.index(name) for name in SESSION_COLUMNS}
    result = SessionLoadResult(source_file=str(path))
    buckets: Dict[str, _SessionBucket] = {}

    for line_number, row in rows:
        result.rows_read += 1
        issues: List[Issue] = []

        # 1. Identity: checked on every row, even ones that are broken elsewhere.
        session_id = _identifier_or_none(
            _cell(row, index["session_id"]), validate_session_id, issues, "session_id"
        )
        participant_id = _identifier_or_none(
            _cell(row, index["participant_id"]), validate_participant_id, issues, "participant_id"
        )
        profile = None
        if participant_id is not None:
            try:
                profile = profiles[participant_id]
            except KeyError:
                issues.append(
                    ("participant_id", f"unknown participant {participant_id!r} (not in the profiles file)")
                )

        bucket = None
        if session_id is not None:
            bucket = buckets.setdefault(session_id, _SessionBucket(session_id))
            if profile is not None:
                if bucket.profile is None:
                    bucket.profile = profile
                elif bucket.profile.participant_id != profile.participant_id:
                    issues.append(
                        (
                            "participant_id",
                            f"session {session_id} belongs to {bucket.profile.participant_id}, "
                            f"not {profile.participant_id}",
                        )
                    )

        # 2. Shape and measurements.
        observation = None
        if len(row) != len(header):
            issues.append(("row", f"expected {len(header)} fields but found {len(row)}"))
        else:
            raw = {name: row[i] for name, i in index.items()}
            observation, measurement_issues = _parse_observation(raw)
            issues.extend(measurement_issues)
            if (
                observation is not None
                and bucket is not None
                and bucket.has_timestamp(observation.timestamp)
            ):
                issues.append(("timestamp", f"duplicate timestamp {observation.timestamp} in {session_id}"))

        # 3. Accept or reject.
        if issues:
            record = RejectedRecord(str(path), line_number, tuple(issues), _raw_line(row))
            result.rejected.append(record)
            if bucket is not None:
                bucket.rejections.append(record)
        else:
            bucket.observations.append(observation)
            result.rows_accepted += 1

    # 4. Assemble sessions in the order they first appeared in the file.
    for bucket in buckets.values():
        if bucket.profile is None:
            result.unlinked_sessions.append(bucket.session_id)
            continue
        session = TrainingSession(bucket.profile, bucket.session_id, str(path))
        for observation in bucket.observations:
            session.add_observation(observation)
        for record in bucket.rejections:
            session.record_rejection(record)
        result.sessions.append(session)

    return result
