"""Object model: encapsulation, inheritance, composition, boundaries."""

import unittest

from fitness_analyzer.exceptions import ValidationError
from fitness_analyzer.models import (
    BaseObservation,
    FitnessObservation,
    ParticipantProfile,
    RejectedRecord,
    TrainingSession,
)
from tests.helpers import PROFILE, make_observation, make_session


class ProfileTests(unittest.TestCase):
    def test_baselines_are_read_only(self):
        profile = ParticipantProfile("P001", 70, 1.5, 32.0)
        with self.assertRaises(AttributeError):
            profile.baseline_heart_rate = 200

    def test_rejects_bad_input(self):
        bad = [("", 70, 1.5, 32.0), ("P", "fast", 1.5, 32.0), ("P", 70, None, 32.0),
               ("P", True, 1.5, 32.0), ("P", float("nan"), 1.5, 32.0)]
        for args in bad:
            with self.subTest(args=args):
                with self.assertRaises(ValidationError):
                    ParticipantProfile(*args)

    def test_rejects_implausible_baselines(self):
        for args in [("P001", 10, 1.5, 32.0), ("P001", 70, -1, 32.0), ("P001", 70, 1.5, 60)]:
            with self.subTest(args=args):
                with self.assertRaises(ValidationError):
                    ParticipantProfile(*args)

    def test_from_dict_reports_missing_key(self):
        with self.assertRaises(ValidationError):
            ParticipantProfile.from_dict({"participant_id": "P001"})

    def test_to_dict_round_trip(self):
        data = ParticipantProfile("P001", 70, 1.5, 32.0, name="Ann").to_dict()
        self.assertEqual(data["name"], "Ann")
        self.assertEqual(ParticipantProfile.from_dict(data).to_dict(), data)


class ObservationTests(unittest.TestCase):
    def test_inherits_from_base(self):
        self.assertIsInstance(make_observation(), BaseObservation)

    def test_subclass_extends_rather_than_replaces_validation(self):
        issues = make_observation(signal_quality=1.8).validate()
        self.assertTrue(any("signal_quality" in issue for issue in issues))

    def test_validate_fields_gives_field_and_reason(self):
        issues = make_observation(heart_rate=265).validate_fields()
        self.assertEqual(issues[0][0], "heart_rate")

    def test_to_dict_includes_base_fields(self):
        data = make_observation().to_dict()
        for name in ["timestamp", "signal_quality", "heart_rate", "activity_level"]:
            self.assertIn(name, data)

    def test_bad_timestamp_cannot_be_built(self):
        for bad in [-1, 1.5, "3", None, True]:
            with self.assertRaises(ValidationError):
                make_observation(timestamp=bad)

    def test_from_dict_missing_key(self):
        with self.assertRaises(ValidationError):
            FitnessObservation.from_dict({"timestamp": 0})


class ValidationBoundaryTests(unittest.TestCase):
    def assertValid(self, **kw):
        self.assertEqual(make_observation(**kw).validate(), [], kw)

    def assertInvalid(self, field, **kw):
        issues = make_observation(**kw).validate()
        self.assertTrue(any(field in issue for issue in issues), (kw, issues))

    def test_values_exactly_on_the_limits_are_accepted(self):
        self.assertValid(heart_rate=30)
        self.assertValid(heart_rate=220)
        self.assertValid(temperature=25.0)
        self.assertValid(temperature=42.0)
        self.assertValid(activity_level=0.0)
        self.assertValid(activity_level=1.0)
        self.assertValid(signal_quality=0.0)
        self.assertValid(signal_quality=1.0)
        self.assertValid(skin_response=0)

    def test_values_just_outside_the_limits_are_rejected(self):
        self.assertInvalid("heart_rate", heart_rate=29.9)
        self.assertInvalid("heart_rate", heart_rate=220.1)
        self.assertInvalid("temperature", temperature=24.9)
        self.assertInvalid("temperature", temperature=42.1)
        self.assertInvalid("activity_level", activity_level=-0.01)
        self.assertInvalid("activity_level", activity_level=1.01)
        self.assertInvalid("signal_quality", signal_quality=1.01)
        self.assertInvalid("skin_response", skin_response=-0.01)

    def test_missing_values_are_reported(self):
        for name in ["heart_rate", "skin_response", "temperature", "activity_level", "signal_quality"]:
            self.assertInvalid(name, **{name: None})

    def test_booleans_and_nan_are_not_numbers(self):
        self.assertInvalid("heart_rate", heart_rate=True)
        self.assertInvalid("skin_response", skin_response=float("nan"))
        self.assertInvalid("heart_rate", heart_rate=float("inf"))

    def test_low_signal_quality_is_flagged_not_rejected(self):
        observation = make_observation(signal_quality=0.20)
        self.assertEqual(observation.validate(), [])
        self.assertFalse(observation.is_trusted())

    def test_trust_threshold_boundary(self):
        self.assertTrue(make_observation(signal_quality=0.5).is_trusted())
        self.assertFalse(make_observation(signal_quality=0.49).is_trusted())


class SessionTests(unittest.TestCase):
    def test_observation_list_is_protected(self):
        session = make_session([make_observation()])
        session.observations.append(make_observation(timestamp=99))
        self.assertEqual(len(session), 1)

    def test_rejection_list_is_protected(self):
        session = make_session([])
        session.record_rejection(RejectedRecord("f.csv", 2, (("x", "y"),)))
        session.rejections.clear()
        self.assertEqual(len(session.rejections), 1)

    def test_rows_received_counts_good_and_bad(self):
        session = make_session([make_observation()])
        session.record_rejection(RejectedRecord("f.csv", 3, (("x", "y"),)))
        self.assertEqual(session.rows_received, 2)

    def test_rejects_wrong_types(self):
        session = TrainingSession(PROFILE)
        with self.assertRaises(ValidationError):
            session.add_observation({"timestamp": 0})
        with self.assertRaises(ValidationError):
            session.record_rejection("nope")
        with self.assertRaises(ValidationError):
            TrainingSession("P001")

    def test_orders_observations_by_timestamp(self):
        session = make_session([make_observation(timestamp=5), make_observation(timestamp=1)])
        self.assertEqual([o.timestamp for o in session.observations], [1, 5])

    def test_summarize(self):
        self.assertEqual(TrainingSession.summarize([10, 20, 30]),
                         {"average": 20.0, "minimum": 10, "maximum": 30})
        self.assertEqual(TrainingSession.summarize([None, 5, None, 15])["average"], 10.0)
        self.assertIsNone(TrainingSession.summarize([None, None]))
        self.assertIsNone(TrainingSession.summarize([]))


if __name__ == "__main__":
    unittest.main()
