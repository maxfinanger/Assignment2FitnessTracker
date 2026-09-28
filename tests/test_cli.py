"""End to end: command line, output files, repeatability, failure modes."""

import contextlib
import csv
import io
import unittest
from pathlib import Path
from unittest import mock

from fitness_analyzer.cli import EXIT_FATAL, EXIT_OK, EXIT_PARTIAL, main, run
from fitness_analyzer.exceptions import DataFileError
from fitness_analyzer.writers import SUMMARY_COLUMNS, prepare_output_directory, write_summary_csv
from tests.helpers import DATA_DIR, temp_dir

PROFILES = str(DATA_DIR / "participants.csv")
VALID = str(DATA_DIR / "fitness_sessions.csv")
INVALID = str(DATA_DIR / "fitness_sessions_invalid.csv")


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def read_summary(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class OutputFileTests(unittest.TestCase):
    def test_creates_missing_output_directory_and_all_three_files(self):
        with temp_dir() as d:
            target = d / "deep" / "output"
            code, out, _ = run_main("--profiles", PROFILES, "--sessions", VALID, INVALID, "--output", str(target))
            self.assertEqual(code, EXIT_OK)
            for name in ["analysis_summary.csv", "analysis_report.txt", "rejected_records.txt"]:
                self.assertTrue((target / name).is_file(), name)
                self.assertIn(name, out)

    def test_summary_has_one_row_per_session_with_expected_labels(self):
        with temp_dir() as d:
            run_main("--profiles", PROFILES, "--sessions", VALID, INVALID, "--output", str(d))
            rows = read_summary(d / "analysis_summary.csv")
        self.assertEqual(list(rows[0]), list(SUMMARY_COLUMNS))
        labels = {r["session_id"]: r["classification"] for r in rows}
        self.assertEqual(labels, {
            "FIT-2026-001": "resting", "FIT-2026-002": "moderate_activity",
            "FIT-2026-003": "high_activity", "FIT-2026-004": "recovering",
            "FIT-2026-005": "insufficient_data", "FIT-2026-101": "insufficient_data",
            "FIT-2026-102": "insufficient_data", "FIT-2026-103": "insufficient_data",
        })

    def test_rejected_records_names_file_row_field_and_reason(self):
        with temp_dir() as d:
            run_main("--profiles", PROFILES, "--sessions", INVALID, "--output", str(d))
            text = (d / "rejected_records.txt").read_text(encoding="utf-8")
        self.assertIn("fitness_sessions_invalid.csv, row 3", text)
        self.assertIn("field heart_rate: 'fast' cannot be converted", text)
        self.assertIn("unknown participant 'P999'", text)
        self.assertIn("expected 8 fields but found 7", text)

    def test_report_explains_every_session(self):
        with temp_dir() as d:
            run_main("--profiles", PROFILES, "--sessions", VALID, INVALID, "--output", str(d))
            text = (d / "analysis_report.txt").read_text(encoding="utf-8")
        for session_id in ["FIT-2026-001", "FIT-2026-004", "FIT-2026-103"]:
            self.assertIn(f"SESSION {session_id}", text)
        self.assertEqual(text.count("CLASSIFICATION"), 8)

    def test_completion_summary_shows_counts_and_files(self):
        with temp_dir() as d:
            _, out, _ = run_main("--profiles", PROFILES, "--sessions", VALID, INVALID, "--output", str(d))
        self.assertIn("total", out)
        self.assertIn("43", out)      # rows read: 3 + 29 + 11
        self.assertIn("33", out)      # accepted: 3 + 29 + 1
        self.assertIn("sessions analysed: 8", out)


class RepeatabilityTests(unittest.TestCase):
    def snapshot(self, directory):
        return {p.name: p.read_bytes() for p in sorted(Path(directory).iterdir())}

    def test_running_twice_gives_identical_files(self):
        with temp_dir() as d:
            args = ["--profiles", PROFILES, "--sessions", VALID, INVALID, "--output", str(d)]
            run_main(*args)
            first = self.snapshot(d)
            run_main(*args)
            self.assertEqual(first, self.snapshot(d))

    def test_stale_output_is_overwritten_not_appended(self):
        with temp_dir() as d:
            for name in ["analysis_summary.csv", "analysis_report.txt", "rejected_records.txt"]:
                (d / name).write_text("STALE CONTENT FROM A PREVIOUS RUN", encoding="utf-8")
            run_main("--profiles", PROFILES, "--sessions", VALID, "--output", str(d))
            for name in ["analysis_summary.csv", "analysis_report.txt", "rejected_records.txt"]:
                self.assertNotIn("STALE", (d / name).read_text(encoding="utf-8"))

    def test_default_arguments_match_the_official_layout(self):
        # the defaults are relative to the repo root, where main.py is run from
        import os
        with temp_dir() as d:
            previous = os.getcwd()
            os.chdir(DATA_DIR.parent)
            try:
                code, _, _ = run_main("--output", str(d))
            finally:
                os.chdir(previous)
            self.assertEqual(code, EXIT_OK)
            self.assertEqual(len(read_summary(d / "analysis_summary.csv")), 8)


class FailureModeTests(unittest.TestCase):
    def test_missing_profiles_file_is_fatal_with_a_clear_message(self):
        with temp_dir() as d:
            code, _, err = run_main("--profiles", "nope.csv", "--sessions", VALID, "--output", str(d))
        self.assertEqual(code, EXIT_FATAL)
        self.assertIn("nope.csv", err)
        self.assertIn("file not found", err)

    def test_missing_session_file_is_skipped_and_the_rest_still_processed(self):
        with temp_dir() as d:
            code, out, _ = run_main("--profiles", PROFILES, "--sessions", "missing.csv", VALID,
                                    "--output", str(d))
            rejected = (d / "rejected_records.txt").read_text(encoding="utf-8")
            rows = read_summary(d / "analysis_summary.csv")
        self.assertEqual(code, EXIT_PARTIAL)
        self.assertEqual(len(rows), 5)
        self.assertIn("could not read: missing.csv: file not found", out)
        self.assertIn("missing.csv: file not found", rejected)

    def test_all_session_files_missing_still_writes_valid_outputs(self):
        with temp_dir() as d:
            code, _, _ = run_main("--profiles", PROFILES, "--sessions", "a.csv", "--output", str(d))
            rows = read_summary(d / "analysis_summary.csv")
        self.assertEqual(code, EXIT_PARTIAL)
        self.assertEqual(rows, [])

    def test_output_path_that_is_a_file(self):
        with temp_dir() as d:
            blocker = d / "output"
            blocker.write_text("i am a file", encoding="utf-8")
            code, _, err = run_main("--profiles", PROFILES, "--sessions", VALID, "--output", str(blocker))
        self.assertEqual(code, EXIT_FATAL)
        self.assertIn("not a directory", err)

    def test_permission_denied_writing_output(self):
        with temp_dir() as d:
            with mock.patch("fitness_analyzer.writers.open", side_effect=PermissionError, create=True):
                with self.assertRaises(DataFileError) as caught:
                    write_summary_csv(d / "analysis_summary.csv", [])
        self.assertIn("permission denied", str(caught.exception))

    def test_permission_denied_creating_directory(self):
        with mock.patch("pathlib.Path.mkdir", side_effect=PermissionError):
            with self.assertRaises(DataFileError):
                prepare_output_directory("/nowhere/output")

    def test_run_returns_structured_outcome(self):
        with temp_dir() as d:
            outcome = run(Path(PROFILES), [Path(VALID), Path(INVALID)], d)
        self.assertEqual(outcome.rows_accepted, 33)
        self.assertEqual(outcome.rows_rejected, 10)
        self.assertEqual(len(outcome.results), 8)


if __name__ == "__main__":
    unittest.main()
