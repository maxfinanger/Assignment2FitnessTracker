"""Command line front end: wire the loader, analysis and writers together."""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .analysis import analyze_session
from .exceptions import DataFileError
from .loader import LoadResult, load_profiles, load_sessions
from .reporting import build_analysis_report, build_rejected_report, format_summary_table
from .writers import prepare_output_directory, write_summary_csv, write_text

DEFAULT_PROFILES = Path("data") / "participants.csv"
DEFAULT_SESSIONS = (
    Path("data") / "fitness_sessions.csv",
    Path("data") / "fitness_sessions_invalid.csv",
)
DEFAULT_OUTPUT = Path("output")

EXIT_OK = 0
EXIT_PARTIAL = 1    # finished, but at least one session file could not be read
EXIT_FATAL = 2      # could not do the job at all


@dataclass
class RunOutcome:
    """Everything a run produced, kept together so it is easy to test."""

    load_results: List[LoadResult] = field(default_factory=list)
    results: List[Dict] = field(default_factory=list)
    file_errors: List[str] = field(default_factory=list)
    unlinked_sessions: List[str] = field(default_factory=list)
    created_files: List[Path] = field(default_factory=list)

    @property
    def rows_accepted(self) -> int:
        return sum(r.rows_accepted for r in self.load_results)

    @property
    def rows_rejected(self) -> int:
        return sum(r.rows_rejected for r in self.load_results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Analyse wearable fitness sessions relative to participant baselines.",
    )
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES,
                        help="participants CSV (default: %(default)s)")
    parser.add_argument("--sessions", type=Path, nargs="+", default=list(DEFAULT_SESSIONS),
                        help="one or more session CSV files (default: the valid and invalid official files)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="output directory, created if missing (default: %(default)s)")
    return parser


def run(profiles_path: Path, session_paths: Sequence[Path], output_dir: Path) -> RunOutcome:
    """Load, validate, analyse and write. Returns what happened.

    A profiles file that cannot be read is fatal (nothing can be analysed
    without baselines) and raises ``DataFileError``. A session file that cannot
    be read is recorded and skipped so the remaining files are still processed.
    """
    outcome = RunOutcome()

    profile_result = load_profiles(profiles_path)          # may raise DataFileError
    outcome.load_results.append(profile_result)

    for path in session_paths:
        try:
            session_result = load_sessions(path, profile_result.profiles)
        except DataFileError as error:
            outcome.file_errors.append(str(error))
            continue
        outcome.load_results.append(session_result)
        outcome.unlinked_sessions.extend(session_result.unlinked_sessions)
        for session in session_result.sessions:
            outcome.results.append(analyze_session(session))

    directory = prepare_output_directory(output_dir)
    outcome.created_files = [
        write_summary_csv(directory / "analysis_summary.csv", outcome.results),
        write_text(directory / "analysis_report.txt", build_analysis_report(outcome.results)),
        write_text(
            directory / "rejected_records.txt",
            build_rejected_report(outcome.load_results, outcome.file_errors, outcome.unlinked_sessions),
        ),
    ]
    return outcome


def print_completion_summary(outcome: RunOutcome) -> None:
    print("Smart Fitness Session Analyzer - run complete")
    print()
    print(f"{'file':<44}{'read':>6}{'accepted':>10}{'rejected':>10}")
    for load_result in outcome.load_results:
        print(
            f"{load_result.source_file:<44}{load_result.rows_read:>6}"
            f"{load_result.rows_accepted:>10}{load_result.rows_rejected:>10}"
        )
    print(f"{'total':<44}{sum(r.rows_read for r in outcome.load_results):>6}"
          f"{outcome.rows_accepted:>10}{outcome.rows_rejected:>10}")
    for message in outcome.file_errors:
        print(f"could not read: {message}")
    print()
    print(f"sessions analysed: {len(outcome.results)}")
    if outcome.results:
        print()
        print(format_summary_table(outcome.results))
    print()
    print("files created:")
    for path in outcome.created_files:
        print(f"  {path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        outcome = run(args.profiles, args.sessions, args.output)
    except DataFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FATAL

    print_completion_summary(outcome)
    return EXIT_PARTIAL if outcome.file_errors else EXIT_OK
