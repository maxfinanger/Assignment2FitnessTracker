"""Regular-expression identifier validation."""

import unittest

from fitness_analyzer.exceptions import InvalidIdentifierError
from fitness_analyzer.validators import validate_participant_id, validate_session_id


class ParticipantIdTests(unittest.TestCase):
    def test_valid_ids_are_returned_unchanged(self):
        for value in ["P001", "P123", "P999"]:
            self.assertEqual(validate_participant_id(value), value)

    def test_invalid_ids_raise(self):
        bad = ["001", "P01", "P0001", "p001", "PP001", "P00A", "", " P001", "P001 ", "P001\n", "P-001"]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(InvalidIdentifierError):
                    validate_participant_id(value)

    def test_non_ascii_digits_are_rejected(self):
        with self.assertRaises(InvalidIdentifierError):
            validate_participant_id("P\u0661\u0662\u0663")    # Arabic-Indic digits

    def test_non_strings_are_rejected(self):
        for value in [None, 1, 1.5]:
            with self.assertRaises(InvalidIdentifierError):
                validate_participant_id(value)

    def test_error_carries_context(self):
        with self.assertRaises(InvalidIdentifierError) as caught:
            validate_participant_id("001")
        self.assertEqual(caught.exception.field, "participant_id")
        self.assertEqual(caught.exception.value, "001")
        self.assertIn("'001'", str(caught.exception))

    def test_is_a_value_error(self):
        self.assertTrue(issubclass(InvalidIdentifierError, ValueError))


class SessionIdTests(unittest.TestCase):
    def test_valid_ids(self):
        for value in ["FIT-2026-001", "FIT-1999-999", "FIT-0000-000"]:
            self.assertEqual(validate_session_id(value), value)

    def test_invalid_ids_raise(self):
        bad = ["FIT-26-102", "FIT-2026-1", "FIT-2026-0001", "fit-2026-001", "FIT2026001",
               "FIT-2026-001\n", "REC-2026-001", "FIT-2026-00A", ""]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(InvalidIdentifierError):
                    validate_session_id(value)


if __name__ == "__main__":
    unittest.main()
