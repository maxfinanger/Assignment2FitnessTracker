"""Shared helpers for the test suite."""

import csv
import tempfile
from contextlib import contextmanager
from pathlib import Path

from data_generator import generate_fitness_data
from fitness_analyzer.analysis import analyze_session
from fitness_analyzer.loader import PROFILE_COLUMNS, SESSION_COLUMNS, load_profiles, load_sessions
from fitness_analyzer.models import FitnessObservation, ParticipantProfile, TrainingSession

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

PROFILE = ParticipantProfile("P001", 70, 1.5, 32.0, name="Test Person")


@contextmanager
def temp_dir():
    with tempfile.TemporaryDirectory() as name:
        yield Path(name)


def write_csv(path, header, rows):
    """Write rows exactly as given (no validation), returning the path."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def write_profiles(directory, rows=None):
    rows = rows if rows is not None else [["P001", "Test Person", 70, 1.5, 32.0]]
    return write_csv(Path(directory) / "profiles.csv", PROFILE_COLUMNS, rows)


def write_sessions(directory, rows, name="sessions.csv"):
    return write_csv(Path(directory) / name, SESSION_COLUMNS, rows)


def good_row(session="FIT-2026-001", participant="P001", t=0, **overrides):
    """One valid session row (as a list of column values), optionally altered."""
    values = dict(
        session_id=session, participant_id=participant, timestamp=t,
        heart_rate=72, skin_response=1.5, temperature=32.0,
        activity_level=0.10, signal_quality=0.90,
    )
    values.update(overrides)
    return [values[column] for column in SESSION_COLUMNS]


def load_one(rows, profiles_rows=None):
    """Write rows to a temp file, load them, and return the SessionLoadResult."""
    with temp_dir() as directory:
        profiles = load_profiles(write_profiles(directory, profiles_rows)).profiles
        return load_sessions(write_sessions(directory, rows), profiles)


def make_observation(**overrides) -> FitnessObservation:
    """A clean, valid observation unless a field is deliberately overridden."""
    defaults = dict(
        timestamp=0, heart_rate=72, skin_response=1.5, temperature=32.0,
        activity_level=0.10, signal_quality=0.90,
    )
    defaults.update(overrides)
    return FitnessObservation(**defaults)


def make_session(observations, profile=PROFILE, session_id="FIT-2026-001") -> TrainingSession:
    session = TrainingSession(profile, session_id)
    for observation in observations:
        session.add_observation(observation)
    return session


def analyze_generated(scenario, seed, windows=12):
    """Generate a scenario, push it through CSV files and the real loader, analyse it.

    Going through files (rather than building objects directly) means the
    scenario tests exercise the whole pipeline, including rejection of the
    generator's deliberately broken poor-quality rows.
    """
    profile, observations = generate_fitness_data("P001", scenario, seed, windows)
    with temp_dir() as directory:
        profile_path = write_csv(
            directory / "profiles.csv", PROFILE_COLUMNS,
            [[profile["participant_id"], "Generated", profile["baseline_heart_rate"],
              profile["baseline_skin_response"], profile["baseline_temperature"]]],
        )
        rows = [
            ["FIT-2026-001", "P001"] + ["" if o[c] is None else o[c] for c in SESSION_COLUMNS[2:]]
            for o in observations
        ]
        session_path = write_sessions(directory, rows)
        profiles = load_profiles(profile_path).profiles
        session = load_sessions(session_path, profiles).sessions[0]
        return analyze_session(session)
