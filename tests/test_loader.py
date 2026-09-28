"""CSV loading: valid data, invalid rows, missing files, boundaries."""

import csv
import unittest
from pathlib import Path
from unittest import mock

from fitness_analyzer.exceptions import DataFileError
from fitness_analyzer.loader import (
    PROFILE_COLUMNS,
    SESSION_COLUMNS,
    load_profiles,
    load_sessions,
    read_csv_table,
)
from tests.helpers import (
    DATA_DIR,
    good_row,
    load_one,
    temp_dir,
    write_csv,
    write_profiles,
    write_sessions,
)


def reasons(record):
    return {field: reason for field, reason in record.issues}


class OfficialFileTests(unittest.TestCase):
    def setUp(self):
        self.profiles = load_profiles(DATA_DIR / "participants.csv")

    def test_profiles_load_with_types(self):
        self.assertEqual(self.profiles.rows_accepted, 3)
        p1 = self.profiles.profiles["P001"]
        self.assertEqual(p1.name, "Amina Noor")
        self.assertIsInstance(p1.baseline_heart_rate, float)
        self.assertEqual(p1.baseline_heart_rate, 68.0)

    def test_valid_session_file_is_fully_accepted(self):
        result = load_sessions(DATA_DIR / "fitness_sessions.csv", self.profiles.profiles)
        self.assertEqual((result.rows_read, result.rows_accepted, result.rows_rejected), (29, 29, 0))
        self.assertEqual([s.session_id for s in result.sessions],
                         ["FIT-2026-001", "FIT-2026-002", "FIT-2026-003", "FIT-2026-004", "FIT-2026-005"])

    def test_values_are_typed_not_strings(self):
        result = load_sessions(DATA_DIR / "fitness_sessions.csv", self.profiles.profiles)
        first = result.sessions[0].observations[0]
        self.assertIsInstance(first.timestamp, int)
        for value in [first.heart_rate, first.skin_response, first.temperature,
                      first.activity_level, first.signal_quality]:
            self.assertIsInstance(value, float)

    def test_sessions_are_linked_to_their_participant(self):
        result = load_sessions(DATA_DIR / "fitness_sessions.csv", self.profiles.profiles)
        by_id = {s.session_id: s.participant.participant_id for s in result.sessions}
        self.assertEqual(by_id["FIT-2026-001"], "P001")
        self.assertEqual(by_id["FIT-2026-003"], "P003")

    def test_invalid_file_rejects_every_bad_row_with_row_numbers(self):
        result = load_sessions(DATA_DIR / "fitness_sessions_invalid.csv", self.profiles.profiles)
        self.assertEqual((result.rows_read, result.rows_accepted, result.rows_rejected), (11, 1, 10))
        by_row = {r.row_number: reasons(r) for r in result.rejected}
        self.assertEqual(sorted(by_row), list(range(3, 13)))
        self.assertIn("heart_rate", by_row[3])                       # 'fast'
        self.assertIn("participant_id", by_row[4])                   # '001'
        self.assertIn("activity_level", by_row[5])                   # empty
        self.assertIn("signal_quality", by_row[6])                   # 1.40
        self.assertIn("session_id", by_row[7])                       # FIT-26-102
        self.assertIn("unknown participant", by_row[8]["participant_id"])   # P999
        self.assertIn("timestamp", by_row[9])                        # 'two'
        self.assertIn("heart_rate", by_row[10])                      # -15
        self.assertEqual({"temperature", "activity_level", "skin_response"}, set(by_row[11]))
        self.assertIn("row", by_row[12])                             # short row
        self.assertTrue(all(r.source_file.endswith("fitness_sessions_invalid.csv") for r in result.rejected))

    def test_fully_rejected_sessions_are_still_reported(self):
        result = load_sessions(DATA_DIR / "fitness_sessions_invalid.csv", self.profiles.profiles)
        by_id = {s.session_id: s for s in result.sessions}
        self.assertEqual(sorted(by_id), ["FIT-2026-101", "FIT-2026-102", "FIT-2026-103"])
        self.assertEqual(len(by_id["FIT-2026-102"].observations), 0)
        self.assertEqual(by_id["FIT-2026-102"].rows_received, 4)

    def test_official_files_are_not_modified_by_loading(self):
        before = (DATA_DIR / "fitness_sessions_invalid.csv").read_bytes()
        load_sessions(DATA_DIR / "fitness_sessions_invalid.csv", self.profiles.profiles)
        self.assertEqual(before, (DATA_DIR / "fitness_sessions_invalid.csv").read_bytes())


class RowValidationTests(unittest.TestCase):
    def test_wrong_row_length_too_long(self):
        result = load_one([good_row() + ["extra"]])
        self.assertEqual(result.rows_rejected, 1)
        self.assertIn("expected 8 fields but found 9", reasons(result.rejected[0])["row"])

    def test_wrong_row_length_too_short(self):
        result = load_one([good_row()[:-1]])
        self.assertIn("found 7", reasons(result.rejected[0])["row"])

    def test_blank_lines_are_ignored(self):
        with temp_dir() as d:
            path = write_sessions(d, [good_row(t=0)])
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n\n")
            profiles = load_profiles(write_profiles(d)).profiles
            result = load_sessions(path, profiles)
            self.assertEqual((result.rows_read, result.rows_rejected), (1, 0))

    def test_unconvertible_and_missing_fields(self):
        result = load_one([good_row(heart_rate="fast"), good_row(t=1, skin_response=""),
                           good_row(t=2, temperature="3.2.1"), good_row(t=3, timestamp="1.5")])
        self.assertEqual(result.rows_accepted, 0)
        fields = [set(reasons(r)) for r in result.rejected]
        self.assertEqual(fields, [{"heart_rate"}, {"skin_response"}, {"temperature"}, {"timestamp"}])

    def test_nan_and_infinity_are_rejected(self):
        result = load_one([good_row(heart_rate="nan"), good_row(t=1, skin_response="inf")])
        self.assertEqual(result.rows_rejected, 2)

    def test_negative_timestamp_is_rejected(self):
        result = load_one([good_row(t=-1)])
        self.assertIn("timestamp", reasons(result.rejected[0]))

    def test_duplicate_timestamp_in_a_session_is_rejected(self):
        result = load_one([good_row(t=0), good_row(t=0)])
        self.assertEqual((result.rows_accepted, result.rows_rejected), (1, 1))
        self.assertIn("duplicate", reasons(result.rejected[0])["timestamp"])

    def test_participant_contradicting_its_session_is_rejected(self):
        profiles = [["P001", "A", 70, 1.5, 32.0], ["P002", "B", 70, 1.5, 32.0]]
        result = load_one([good_row(participant="P001", t=0), good_row(participant="P002", t=1)], profiles)
        self.assertEqual(result.rows_rejected, 1)
        self.assertIn("belongs to P001", reasons(result.rejected[0])["participant_id"])

    def test_session_with_no_known_participant_is_unlinked(self):
        result = load_one([good_row(participant="P999")])
        self.assertEqual(result.sessions, [])
        self.assertEqual(result.unlinked_sessions, ["FIT-2026-001"])

    def test_one_row_can_report_several_problems(self):
        result = load_one([good_row(session="BAD", participant="X", heart_rate="fast", activity_level=5)])
        self.assertEqual(set(reasons(result.rejected[0])),
                         {"session_id", "participant_id", "heart_rate", "activity_level"})

    def test_rows_with_bad_session_id_join_no_session(self):
        result = load_one([good_row(session="FIT-26-1")])
        self.assertEqual(result.sessions, [])
        self.assertEqual(result.unlinked_sessions, [])
        self.assertEqual(result.rows_rejected, 1)


class BoundaryTests(unittest.TestCase):
    def accepted(self, **overrides):
        return load_one([good_row(**overrides)]).rows_accepted == 1

    def test_limits_are_accepted(self):
        for overrides in [dict(heart_rate=30), dict(heart_rate=220), dict(temperature=25),
                          dict(temperature=42), dict(activity_level=0), dict(activity_level=1),
                          dict(signal_quality=0), dict(signal_quality=1), dict(skin_response=0),
                          dict(t=0)]:
            with self.subTest(overrides=overrides):
                self.assertTrue(self.accepted(**overrides))

    def test_just_beyond_limits_are_rejected(self):
        for overrides in [dict(heart_rate=29.99), dict(heart_rate=220.01), dict(temperature=24.99),
                          dict(temperature=42.01), dict(activity_level=-0.01), dict(activity_level=1.01),
                          dict(signal_quality=1.01), dict(signal_quality=-0.01), dict(skin_response=-0.01)]:
            with self.subTest(overrides=overrides):
                self.assertFalse(self.accepted(**overrides))

    def test_identifier_boundaries(self):
        self.assertFalse(self.accepted(session="FIT-2026-1000"))
        self.assertFalse(self.accepted(participant="P0001"))
        self.assertTrue(self.accepted(session="FIT-0000-000"))


class ProfileValidationTests(unittest.TestCase):
    def load(self, rows):
        with temp_dir() as d:
            return load_profiles(write_profiles(d, rows))

    def test_bad_profile_rows_are_rejected_with_reasons(self):
        result = self.load([
            ["P001", "Ok", 70, 1.5, 32.0],
            ["001", "BadId", 70, 1.5, 32.0],
            ["P002", "", 70, 1.5, 32.0],
            ["P003", "BadNum", "fast", 1.5, 32.0],
            ["P004", "Range", 5, -1, 60],
            ["P001", "Dup", 70, 1.5, 32.0],
        ])
        self.assertEqual(list(result.profiles), ["P001"])
        by_row = {r.row_number: reasons(r) for r in result.rejected}
        self.assertIn("participant_id", by_row[3])
        self.assertIn("name", by_row[4])
        self.assertIn("baseline_heart_rate", by_row[5])
        self.assertEqual({"baseline_heart_rate", "baseline_skin_response", "baseline_temperature"}, set(by_row[6]))
        self.assertIn("duplicate", by_row[7]["participant_id"])

    def test_short_profile_row(self):
        with temp_dir() as d:
            path = write_csv(d / "p.csv", PROFILE_COLUMNS, [["P001", "Ann", 70]])
            result = load_profiles(path)
        self.assertEqual(result.rows_rejected, 1)


class FileErrorTests(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(DataFileError) as caught:
            load_profiles("does/not/exist.csv")
        self.assertIn("file not found", str(caught.exception))
        self.assertIn("does/not/exist.csv", str(caught.exception))

    def test_missing_session_file(self):
        with self.assertRaises(DataFileError):
            load_sessions("nope.csv", {})

    def test_permission_denied(self):
        with temp_dir() as d:
            path = write_profiles(d)
            with mock.patch("builtins.open", side_effect=PermissionError):
                with self.assertRaises(DataFileError) as caught:
                    read_csv_table(path, PROFILE_COLUMNS)
        self.assertIn("permission denied", str(caught.exception))

    def test_directory_instead_of_file(self):
        with temp_dir() as d:
            with self.assertRaises(DataFileError):
                read_csv_table(d, PROFILE_COLUMNS)

    def test_empty_file(self):
        with temp_dir() as d:
            path = d / "empty.csv"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(DataFileError) as caught:
                read_csv_table(path, SESSION_COLUMNS)
        self.assertIn("empty", str(caught.exception))

    def test_missing_required_column(self):
        with temp_dir() as d:
            path = write_csv(d / "s.csv", SESSION_COLUMNS[:-1], [])
            with self.assertRaises(DataFileError) as caught:
                read_csv_table(path, SESSION_COLUMNS)
        self.assertIn("signal_quality", str(caught.exception))

    def test_undecodable_file(self):
        with temp_dir() as d:
            path = d / "bad.csv"
            path.write_bytes(b"participant_id,name\n\xff\xfe\x00bad\n")
            with self.assertRaises(DataFileError) as caught:
                read_csv_table(path, ["participant_id"])
        self.assertIn("UTF-8", str(caught.exception))

    def test_malformed_csv_is_reported_with_context(self):
        with temp_dir() as d:
            path = write_profiles(d)
            with mock.patch("fitness_analyzer.loader.csv.reader", side_effect=csv.Error("boom")):
                with self.assertRaises(DataFileError) as caught:
                    read_csv_table(path, PROFILE_COLUMNS)
        self.assertIn("malformed CSV", str(caught.exception))
        self.assertIn("boom", str(caught.exception))

    def test_header_with_bom_is_accepted(self):
        with temp_dir() as d:
            path = d / "bom.csv"
            path.write_bytes(b"\xef\xbb\xbfparticipant_id,name\nP001,Ann\n")
            header, rows = read_csv_table(path, ["participant_id"])
        self.assertEqual(header[0], "participant_id")
        self.assertEqual(rows, [(2, ["P001", "Ann"])])

    def test_columns_may_be_reordered(self):
        with temp_dir() as d:
            order = list(reversed(SESSION_COLUMNS))
            row = dict(zip(SESSION_COLUMNS, good_row()))
            path = write_csv(d / "s.csv", order, [[row[c] for c in order]])
            profiles = load_profiles(write_profiles(d)).profiles
            result = load_sessions(path, profiles)
        self.assertEqual(result.rows_accepted, 1)


if __name__ == "__main__":
    unittest.main()
