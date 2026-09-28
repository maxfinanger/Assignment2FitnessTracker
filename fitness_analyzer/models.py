"""Domain classes for the Smart Fitness Session Analyzer.

* ``ParticipantProfile`` -- encapsulates a participant's personal reference
  values behind read only properties.
* ``RejectedRecord`` -- an immutable note of one CSV row that was refused:
  which file, which row, which fields and why.
* ``BaseObservation`` -- the generic idea of "one measurement window from a
  wearable sensor": it knows its timestamp and signal quality, and knows how
  to validate and serialize *those* fields.
* ``FitnessObservation`` -- a fitness specific window that adds heart rate,
  skin response, temperature and activity level. It **overrides**
  ``validate_fields()`` and ``to_dict()``, extending the base behavior via
  ``super()`` rather than replacing it.
* ``TrainingSession`` -- **composition**: a session owns one
  ``ParticipantProfile``, a list of accepted ``FitnessObservation`` objects
  and the ``RejectedRecord`` objects for rows that were refused. The
  observations have no meaning without the session that groups them, and the
  session cannot exist without a participant.
"""

import math
from dataclasses import dataclass
from statistics import mean
from typing import Dict, List, Optional, Tuple

from .exceptions import ValidationError

# Plausibility bounds, derived from DATA_DESCRIPTION.md. Slightly wider than
# the documented "normal" ranges so that genuine outliers survive while
# physically impossible values are rejected.
HEART_RATE_BOUNDS = (30.0, 220.0)
TEMPERATURE_BOUNDS = (25.0, 42.0)
UNIT_INTERVAL = (0.0, 1.0)

# Signal-quality rule (documented in the README):
#   * a value outside 0-1 is impossible                 -> row rejected
#   * a value below this threshold is merely weak       -> row kept, flagged
#     "low confidence"
#   * a session whose *average* quality is too low      -> whole session
#     classified "insufficient_data" (see analysis.py)
SIGNAL_QUALITY_TRUST_THRESHOLD = 0.5

# One problem with one field: (field name, reason).
Issue = Tuple[str, str]


def _is_number(value) -> bool:
    """True for finite real numbers, deliberately excluding bool and NaN/inf.

    ``bool`` is a subclass of ``int`` so ``True`` would otherwise pass as 1.
    ``float("nan")`` compares False against everything, so it would slip
    through range checks unless excluded here.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def check_bounded(name: str, value, bounds: Tuple[float, float]) -> List[Issue]:
    """Check one required numeric field against inclusive bounds."""
    low, high = bounds
    if value is None:
        return [(name, "is missing")]
    if not _is_number(value):
        return [(name, f"is not a number ({value!r})")]
    if not low <= value <= high:
        return [(name, f"{value} outside plausible range {low}-{high}")]
    return []


def check_non_negative(name: str, value) -> List[Issue]:
    """Check one required numeric field is present and not negative."""
    if value is None:
        return [(name, "is missing")]
    if not _is_number(value):
        return [(name, f"is not a number ({value!r})")]
    if value < 0:
        return [(name, f"{value} is negative")]
    return []


def _format_issues(issues: List[Issue]) -> List[str]:
    return [f"{field} {reason}" for field, reason in issues]


class ParticipantProfile:
    """A participant and their personal reference measurements.

    Encapsulation: the baselines are stored in protected attributes and
    exposed through read only properties. Baselines are the fixed point that
    every later comparison is made against, so allowing code elsewhere to
    reassign them would silently invalidate an entire analysis.
    """

    def __init__(
        self,
        participant_id: str,
        baseline_heart_rate: float,
        baseline_skin_response: float,
        baseline_temperature: float,
        name: str = "",
    ):
        if not isinstance(participant_id, str) or not participant_id.strip():
            raise ValidationError("participant_id must be a non-empty string")
        for label, value in (
            ("baseline_heart_rate", baseline_heart_rate),
            ("baseline_skin_response", baseline_skin_response),
            ("baseline_temperature", baseline_temperature),
        ):
            if not _is_number(value):
                raise ValidationError(f"{label} must be a number, got {value!r}")
        problems = self.check_baselines(
            baseline_heart_rate, baseline_skin_response, baseline_temperature
        )
        if problems:
            raise ValidationError("; ".join(_format_issues(problems)))

        self._participant_id = participant_id.strip()
        self._name = name.strip() if isinstance(name, str) else ""
        self._baseline_heart_rate = float(baseline_heart_rate)
        self._baseline_skin_response = float(baseline_skin_response)
        self._baseline_temperature = float(baseline_temperature)

    # -- read only properties (encapsulation) ---------------------------

    @property
    def participant_id(self) -> str:
        return self._participant_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def baseline_heart_rate(self) -> float:
        return self._baseline_heart_rate

    @property
    def baseline_skin_response(self) -> float:
        return self._baseline_skin_response

    @property
    def baseline_temperature(self) -> float:
        return self._baseline_temperature

    @staticmethod
    def check_baselines(heart_rate, skin_response, temperature) -> List[Issue]:
        """Report implausible reference values (pure check, no instance needed).

        A staticmethod so the loader can vet a CSV row *before* attempting to
        build a profile, and get field-level reasons for the rejection log.
        """
        issues: List[Issue] = []
        issues.extend(check_bounded("baseline_heart_rate", heart_rate, HEART_RATE_BOUNDS))
        issues.extend(check_non_negative("baseline_skin_response", skin_response))
        issues.extend(check_bounded("baseline_temperature", temperature, TEMPERATURE_BOUNDS))
        return issues

    @classmethod
    def from_dict(cls, data: Dict) -> "ParticipantProfile":
        """Build a profile from a dictionary of (already typed) values."""
        try:
            return cls(
                participant_id=data["participant_id"],
                baseline_heart_rate=data["baseline_heart_rate"],
                baseline_skin_response=data["baseline_skin_response"],
                baseline_temperature=data["baseline_temperature"],
                name=data.get("name", ""),
            )
        except KeyError as missing:
            raise ValidationError(f"profile is missing field {missing}") from missing

    def to_dict(self) -> Dict:
        return {
            "participant_id": self._participant_id,
            "name": self._name,
            "baseline_heart_rate": self._baseline_heart_rate,
            "baseline_skin_response": self._baseline_skin_response,
            "baseline_temperature": self._baseline_temperature,
        }

    def __repr__(self) -> str:
        return f"ParticipantProfile({self._participant_id!r}, hr={self._baseline_heart_rate})"


@dataclass(frozen=True)
class RejectedRecord:
    """One CSV row that was refused, and exactly why.

    Frozen: a rejection is a historical fact about a row and must not be
    edited after the fact. A row can fail for several reasons at once, so
    ``issues`` holds every ``(field, reason)`` pair that was found.
    """

    source_file: str
    row_number: int
    issues: Tuple[Issue, ...]
    raw_text: str = ""

    def to_dict(self) -> Dict:
        return {
            "source_file": self.source_file,
            "row_number": self.row_number,
            "issues": [f"{field}: {reason}" for field, reason in self.issues],
        }


class BaseObservation:
    """One measurement window from any wearable sensor.

    This base class deliberately knows only what *every* sensor window has:
    when it was taken, and how much the device trusted the reading. Fitness
    specific fields live in the subclass.
    """

    def __init__(self, timestamp, signal_quality):
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
            raise ValidationError(f"timestamp must be an integer >= 0, got {timestamp!r}")
        self.timestamp = timestamp
        self.signal_quality = signal_quality

    def validate_fields(self) -> List[Issue]:
        """Return ``(field, reason)`` pairs for every problem (empty == clean).

        Subclasses override this and call ``super().validate_fields()`` so the
        shared signal-quality rule is applied exactly once, in one place.
        """
        return check_bounded("signal_quality", self.signal_quality, UNIT_INTERVAL)

    def validate(self) -> List[str]:
        """Human readable form of :meth:`validate_fields`."""
        return _format_issues(self.validate_fields())

    def is_trusted(self) -> bool:
        """Whether the device itself reported a reliable reading."""
        return (
            _is_number(self.signal_quality)
            and self.signal_quality >= SIGNAL_QUALITY_TRUST_THRESHOLD
        )

    def to_dict(self) -> Dict:
        return {"timestamp": self.timestamp, "signal_quality": self.signal_quality}


class FitnessObservation(BaseObservation):
    """A fitness measurement window: heart rate, skin, temperature, movement.

    Overrides ``validate_fields()`` and ``to_dict()``, extending rather than
    replacing the base implementations.
    """

    def __init__(
        self,
        timestamp,
        heart_rate,
        skin_response,
        temperature,
        activity_level,
        signal_quality,
    ):
        super().__init__(timestamp, signal_quality)
        self.heart_rate = heart_rate
        self.skin_response = skin_response
        self.temperature = temperature
        self.activity_level = activity_level

    # -- overridden methods ---------------------------------------------

    def validate_fields(self) -> List[Issue]:
        """Extend the base checks with the fitness specific field rules."""
        issues = super().validate_fields()   # shared signal-quality rule
        issues.extend(check_bounded("heart_rate", self.heart_rate, HEART_RATE_BOUNDS))
        issues.extend(check_bounded("temperature", self.temperature, TEMPERATURE_BOUNDS))
        issues.extend(check_bounded("activity_level", self.activity_level, UNIT_INTERVAL))
        issues.extend(check_non_negative("skin_response", self.skin_response))
        return issues

    def to_dict(self) -> Dict:
        data = super().to_dict()             # timestamp + signal_quality
        data.update(
            {
                "heart_rate": self.heart_rate,
                "skin_response": self.skin_response,
                "temperature": self.temperature,
                "activity_level": self.activity_level,
            }
        )
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "FitnessObservation":
        """Alternative constructor from a dictionary of (already typed) values.

        Missing *keys* raise (the caller is broken); missing *values* (None)
        are allowed through so ``validate()`` can report them properly.
        """
        try:
            return cls(
                timestamp=data["timestamp"],
                heart_rate=data["heart_rate"],
                skin_response=data["skin_response"],
                temperature=data["temperature"],
                activity_level=data["activity_level"],
                signal_quality=data["signal_quality"],
            )
        except KeyError as missing:
            raise ValidationError(f"observation is missing field {missing}") from missing

    def __repr__(self) -> str:
        return f"FitnessObservation(t={self.timestamp}, hr={self.heart_rate})"


class TrainingSession:
    """A participant's complete training session.

    This is the composition in the design. The session owns its observations
    and its rejected rows (they are created for it and have no independent
    life outside it) and holds the participant profile that gives those
    numbers meaning.

    Both collections are protected and exposed as read only copies, so the
    only way to add data is through ``add_observation()`` and
    ``record_rejection()``, which enforce the type of what goes in.
    """

    def __init__(
        self,
        participant: ParticipantProfile,
        session_id: str = "unspecified",
        source_file: str = "",
    ):
        if not isinstance(participant, ParticipantProfile):
            raise ValidationError("participant must be a ParticipantProfile")
        self._participant = participant
        self._session_id = session_id
        self._source_file = source_file
        self._observations: List[FitnessObservation] = []
        self._rejections: List[RejectedRecord] = []

    # -- encapsulated collections -----------------------------------------

    @property
    def participant(self) -> ParticipantProfile:
        return self._participant

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def source_file(self) -> str:
        return self._source_file

    @property
    def observations(self) -> List[FitnessObservation]:
        """A copy, so callers cannot mutate the session's internal list."""
        return list(self._observations)

    @property
    def rejections(self) -> List[RejectedRecord]:
        """A copy of the rows that were refused for this session."""
        return list(self._rejections)

    @property
    def rows_received(self) -> int:
        """Every row that claimed to belong to this session, good or bad."""
        return len(self._observations) + len(self._rejections)

    def add_observation(self, observation: FitnessObservation) -> None:
        if not isinstance(observation, FitnessObservation):
            raise ValidationError("only FitnessObservation objects can be added")
        self._observations.append(observation)
        self._observations.sort(key=lambda o: o.timestamp)

    def record_rejection(self, record: RejectedRecord) -> None:
        if not isinstance(record, RejectedRecord):
            raise ValidationError("only RejectedRecord objects can be recorded")
        self._rejections.append(record)

    def __len__(self) -> int:
        return len(self._observations)

    # -- summarizing -------------------------------------------------------

    @staticmethod
    def summarize(values: List[float]) -> Optional[Dict[str, float]]:
        """Return average/minimum/maximum for a list of numbers.

        A staticmethod: it is a pure calculation over its argument, useful to
        any caller, and needs nothing from a particular session instance.
        """
        numbers = [v for v in values if _is_number(v)]
        if not numbers:
            return None
        return {
            "average": round(mean(numbers), 2),
            "minimum": round(min(numbers), 2),
            "maximum": round(max(numbers), 2),
        }

    def valid_observations(self) -> List[FitnessObservation]:
        return [o for o in self._observations if not o.validate()]

    def __repr__(self) -> str:
        return (
            f"TrainingSession({self._session_id!r}, "
            f"{self._participant.participant_id!r}, {len(self._observations)} windows)"
        )
