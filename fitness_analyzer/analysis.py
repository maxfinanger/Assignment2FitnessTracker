"""Standalone calculation, data-quality and classification functions.

These are plain module level functions rather than methods because each one
is a self contained calculation over data passed in: that keeps them easy to
test in isolation and reusable outside a ``TrainingSession``.
"""

from statistics import mean
from typing import Dict, List, Optional

from .models import FitnessObservation, ParticipantProfile, TrainingSession

# --- classification thresholds --------------------------------------------
# Kept as named constants so the rules are visible, adjustable and
# defensible, rather than magic numbers buried in the branching below.

RESTING_HEART_RATE_DELTA = 10.0    # bpm above personal baseline
MODERATE_HEART_RATE_DELTA = 15.0
HIGH_HEART_RATE_DELTA = 45.0

RESTING_ACTIVITY = 0.25            # normalized movement level
MODERATE_ACTIVITY = 0.30
HIGH_ACTIVITY = 0.65

# Recovery: how much heart rate and movement must fall from the start of the
# session to its final windows before we call it "recovering".
RECOVERY_HEART_RATE_DROP = 12.0    # bpm
RECOVERY_ACTIVITY_DROP = 0.10

# Data-quality gate: above this share of unusable windows we refuse to
# classify intensity at all.
MAX_INVALID_FRACTION = 0.30
MIN_AVERAGE_SIGNAL_QUALITY = 0.60
MIN_VALID_OBSERVATIONS = 3


def audit_data_quality(session: TrainingSession) -> Dict:
    """Report what was usable and what was not, and why.

    Two sources of rejected data are combined:

    * rows the loader refused (wrong length, bad identifier, unparseable or
      impossible values). These never became observations, but the session
      remembers them.
    * observations that were built but still fail ``validate()``. The loader
      should prevent these; the check is kept as a second line of defence for
      sessions assembled by hand.

    Nothing is silently discarded: each rejection is returned with its reasons
    so the report can always justify itself.
    """
    rejected = []
    accepted = []

    for record in session.rejections:
        rejected.append(
            {
                "source_file": record.source_file,
                "row_number": record.row_number,
                "timestamp": None,
                "issues": [f"{field}: {reason}" for field, reason in record.issues],
            }
        )

    for observation in session.observations:
        issues = observation.validate()
        if issues:
            rejected.append(
                {
                    "source_file": session.source_file,
                    "row_number": None,
                    "timestamp": observation.timestamp,
                    "issues": issues,
                }
            )
        else:
            accepted.append(observation)

    total = len(accepted) + len(rejected)
    signal_values = [o.signal_quality for o in accepted]
    untrusted = [o.timestamp for o in accepted if not o.is_trusted()]

    return {
        "total_observations": total,
        "usable_observations": len(accepted),
        "rejected_rows": rejected,
        "invalid_fraction": round(len(rejected) / total, 3) if total else 1.0,
        "average_signal_quality": round(mean(signal_values), 3) if signal_values else None,
        "low_confidence_timestamps": untrusted,
        "_accepted": accepted,   # internal handoff, stripped from the final result
    }


def summarize_measurements(observations: List[FitnessObservation]) -> Dict[str, Optional[Dict]]:
    """Average, minimum and maximum for each measured field."""
    return {
        "heart_rate": TrainingSession.summarize([o.heart_rate for o in observations]),
        "skin_response": TrainingSession.summarize([o.skin_response for o in observations]),
        "temperature": TrainingSession.summarize([o.temperature for o in observations]),
        "activity_level": TrainingSession.summarize([o.activity_level for o in observations]),
    }


def compare_with_baseline(
    profile: ParticipantProfile,
    observations: List[FitnessObservation],
) -> Dict[str, Optional[float]]:
    """Express the session relative to the participant's own reference values.

    Absolute numbers say little on their own: 95 bpm is hard work for one
    person and an easy walk for another. Every downstream rule therefore uses
    these deltas rather than raw measurements.
    """
    if not observations:
        return {
            "heart_rate_delta": None,
            "skin_response_delta": None,
            "temperature_delta": None,
            "average_activity_level": None,
        }

    return {
        "heart_rate_delta": round(
            mean(o.heart_rate for o in observations) - profile.baseline_heart_rate, 2
        ),
        "skin_response_delta": round(
            mean(o.skin_response for o in observations) - profile.baseline_skin_response, 2
        ),
        "temperature_delta": round(
            mean(o.temperature for o in observations) - profile.baseline_temperature, 2
        ),
        "average_activity_level": round(mean(o.activity_level for o in observations), 2),
    }


def _peak_block_mean(values: List[float], window: int) -> float:
    """Highest average of any consecutive ``window`` values.

    Using a block average rather than a single maximum reading means one
    noisy spike cannot masquerade as a peak of effort.
    """
    if len(values) < window:
        return mean(values)
    return max(
        mean(values[start:start + window])
        for start in range(len(values) - window + 1)
    )


def detect_recovery(observations: List[FitnessObservation]) -> Dict:
    """Check whether heart rate and movement decline towards the session end.

    The assignment asks specifically about the *end* of a session, so this
    compares the closing block of windows against the highest sustained block
    that came before it.

    Comparing the closing block against the *opening* block instead would be
    the obvious approach, but it misses the most typical recovery shape of
    all: a session that starts calm, climbs to a peak, and then falls away.
    There the first and last thirds can have identical averages while a clear
    recovery sits in between. Measuring the fall from the peak captures that,
    and still catches a session that simply declines throughout.
    """
    usable = len(observations)
    if usable < 3:
        return {
            "is_recovering": False,
            "reason": "too few usable windows to judge a trend",
            "heart_rate_drop": None,
            "activity_drop": None,
        }

    window = max(1, usable // 3)
    closing = observations[-window:]
    earlier = observations[:-window]

    if not earlier:
        return {
            "is_recovering": False,
            "reason": "no windows before the closing block to compare against",
            "heart_rate_drop": None,
            "activity_drop": None,
        }

    heart_rate_peak = _peak_block_mean([o.heart_rate for o in earlier], window)
    activity_peak = _peak_block_mean([o.activity_level for o in earlier], window)

    closing_activity = mean(o.activity_level for o in closing)
    heart_rate_drop = round(heart_rate_peak - mean(o.heart_rate for o in closing), 2)
    activity_drop = round(activity_peak - closing_activity, 2)

    # All three conditions must hold. The last one matters: a hard session
    # that merely dips slightly at the end is still a hard session, so the
    # participant must have genuinely eased off before we call it recovery.
    is_recovering = (
        heart_rate_drop >= RECOVERY_HEART_RATE_DROP
        and activity_drop >= RECOVERY_ACTIVITY_DROP
        and closing_activity < HIGH_ACTIVITY
    )

    return {
        "is_recovering": is_recovering,
        "heart_rate_drop": heart_rate_drop,
        "activity_drop": activity_drop,
        "comparison_window": window,
        "reason": (
            f"heart rate fell {heart_rate_drop} bpm and movement fell {activity_drop} "
            f"from the session peak to the final {window} window(s)"
        ),
    }


def classify_session(quality: Dict, comparison: Dict, recovery: Dict) -> Dict[str, str]:
    """Apply the classification rules and explain the outcome.

    Order matters. Data quality is checked first, because an intensity label
    computed from mostly-unusable windows would look authoritative while
    resting on nothing. Recovery is checked next, since a recovering session
    can sit at almost any average intensity and would otherwise be
    mislabelled by the level-based rules below it.
    """
    if (
        quality["usable_observations"] < MIN_VALID_OBSERVATIONS
        or quality["invalid_fraction"] > MAX_INVALID_FRACTION
        or (
            quality["average_signal_quality"] is not None
            and quality["average_signal_quality"] < MIN_AVERAGE_SIGNAL_QUALITY
        )
    ):
        quality_text = (
            f"{quality['average_signal_quality']}"
            if quality["average_signal_quality"] is not None
            else "unknown"
        )
        return {
            "label": "insufficient_data",
            "explanation": (
                f"Only {quality['usable_observations']} of {quality['total_observations']} "
                f"rows were usable and average signal quality was {quality_text}. "
                "The session cannot be classified reliably."
            ),
        }

    heart_rate_delta = comparison["heart_rate_delta"]
    activity = comparison["average_activity_level"]

    if recovery["is_recovering"]:
        return {
            "label": "recovering",
            "explanation": (
                f"Measurements decline towards the end of the session: {recovery['reason']}. "
                f"Average heart rate was {heart_rate_delta:+.1f} bpm relative to baseline."
            ),
        }

    if heart_rate_delta <= RESTING_HEART_RATE_DELTA and activity <= RESTING_ACTIVITY:
        return {
            "label": "resting",
            "explanation": (
                f"Heart rate stayed close to baseline ({heart_rate_delta:+.1f} bpm) "
                f"with little movement ({activity:.2f})."
            ),
        }

    if heart_rate_delta >= HIGH_HEART_RATE_DELTA and activity >= HIGH_ACTIVITY:
        return {
            "label": "high_activity",
            "explanation": (
                f"Heart rate was far above baseline ({heart_rate_delta:+.1f} bpm) "
                f"with sustained high movement ({activity:.2f})."
            ),
        }

    if heart_rate_delta >= MODERATE_HEART_RATE_DELTA and activity >= MODERATE_ACTIVITY:
        return {
            "label": "moderate_activity",
            "explanation": (
                f"Heart rate was moderately raised ({heart_rate_delta:+.1f} bpm) "
                f"with steady movement ({activity:.2f})."
            ),
        }

    return {
        "label": "uncertain",
        "explanation": (
            f"Heart rate ({heart_rate_delta:+.1f} bpm from baseline) and movement "
            f"({activity:.2f}) do not match any defined intensity pattern."
        ),
    }


def analyze_session(session: TrainingSession) -> Dict:
    """Run the full pipeline and return one structured result dictionary.

    A dictionary (rather than an object) is the deliverable here because the
    assignment asks for a structured result: it is easy to print, to compare
    between sessions, and to serialize if the fitness centre ever wants to
    store or transmit it.
    """
    quality = audit_data_quality(session)
    usable = quality.pop("_accepted")

    comparison = compare_with_baseline(session.participant, usable)
    recovery = detect_recovery(usable)
    classification = classify_session(quality, comparison, recovery)

    return {
        "session_id": session.session_id,
        "source_file": session.source_file,
        "participant": session.participant.to_dict(),
        "data_quality": quality,
        "summaries": summarize_measurements(usable),
        "baseline_comparison": comparison,
        "recovery": recovery,
        "classification": classification,
    }
