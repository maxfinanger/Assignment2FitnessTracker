"""Calculations, recovery detection, classification and end-to-end scenarios."""

import unittest

from fitness_analyzer.analysis import (
    analyze_session,
    audit_data_quality,
    compare_with_baseline,
    detect_recovery,
    summarize_measurements,
)
from fitness_analyzer.models import RejectedRecord, TrainingSession
from fitness_analyzer.reporting import build_report, format_measurement
from tests.helpers import PROFILE, analyze_generated, make_observation, make_session


def label(result):
    return result["classification"]["label"]


def series(heart_rates, activities, **kw):
    return [make_observation(timestamp=t, heart_rate=hr, activity_level=a, **kw)
            for t, (hr, a) in enumerate(zip(heart_rates, activities))]


class CalculationTests(unittest.TestCase):
    def test_summaries_cover_every_field(self):
        summaries = summarize_measurements([make_observation(timestamp=t) for t in range(4)])
        for name in ["heart_rate", "skin_response", "temperature", "activity_level"]:
            for statistic in ["average", "minimum", "maximum"]:
                self.assertIn(statistic, summaries[name])

    def test_comparison_is_relative_to_baseline(self):
        observations = [make_observation(timestamp=t, heart_rate=90) for t in range(4)]
        self.assertEqual(compare_with_baseline(PROFILE, observations)["heart_rate_delta"], 20.0)

    def test_comparison_handles_empty_input(self):
        self.assertIsNone(compare_with_baseline(PROFILE, [])["heart_rate_delta"])


class RecoveryTests(unittest.TestCase):
    def test_detected_when_measurements_fall_at_the_end(self):
        obs = series([130 - 8 * t for t in range(9)], [max(0.05, 0.85 - 0.1 * t) for t in range(9)])
        self.assertTrue(detect_recovery(obs)["is_recovering"])

    def test_not_detected_in_steady_session(self):
        self.assertFalse(detect_recovery(series([120] * 9, [0.6] * 9))["is_recovering"])

    def test_too_few_windows(self):
        result = detect_recovery(series([100, 90], [0.5, 0.4]))
        self.assertFalse(result["is_recovering"])
        self.assertIsNone(result["heart_rate_drop"])

    def test_hard_session_that_only_dips_is_not_recovery(self):
        obs = series([125, 130, 135, 140, 138, 136, 128, 126, 124],
                     [0.80, 0.85, 0.90, 0.92, 0.90, 0.88, 0.82, 0.80, 0.78])
        self.assertFalse(detect_recovery(obs)["is_recovering"])

    def test_climb_then_drop_is_recovery(self):
        obs = series([80, 100, 120, 130, 130, 130, 120, 100, 80],
                     [0.2, 0.5, 0.8, 0.9, 0.9, 0.9, 0.6, 0.3, 0.1])
        self.assertTrue(detect_recovery(obs)["is_recovering"])

    def test_drop_thresholds_are_inclusive(self):
        # closing block mean is exactly 12 bpm and 0.10 below the peak block
        obs = series([100, 100, 100, 88, 88, 88], [0.5, 0.5, 0.5, 0.4, 0.4, 0.4])
        self.assertTrue(detect_recovery(obs)["is_recovering"])
        obs = series([100, 100, 100, 88.5, 88.5, 88.5], [0.5, 0.5, 0.5, 0.4, 0.4, 0.4])
        self.assertFalse(detect_recovery(obs)["is_recovering"])


class DataQualityTests(unittest.TestCase):
    def test_audit_counts_loader_rejections(self):
        session = make_session([make_observation(timestamp=t) for t in range(3)])
        session.record_rejection(RejectedRecord("f.csv", 5, (("heart_rate", "is missing"),)))
        quality = audit_data_quality(session)
        self.assertEqual((quality["total_observations"], quality["usable_observations"]), (4, 3))
        self.assertEqual(quality["rejected_rows"][0]["row_number"], 5)
        self.assertEqual(quality["invalid_fraction"], 0.25)

    def test_audit_still_catches_invalid_observations_added_by_hand(self):
        session = make_session([make_observation(timestamp=0, heart_rate=None), make_observation(timestamp=1)])
        quality = audit_data_quality(session)
        self.assertEqual(quality["usable_observations"], 1)
        self.assertTrue(quality["rejected_rows"][0]["issues"])

    def test_low_signal_windows_are_listed(self):
        session = make_session([make_observation(timestamp=0, signal_quality=0.3), make_observation(timestamp=1)])
        self.assertEqual(audit_data_quality(session)["low_confidence_timestamps"], [0])

    def test_quality_gate_boundaries(self):
        def run(usable, rejected, quality=0.9):
            session = make_session([make_observation(timestamp=t, signal_quality=quality) for t in range(usable)])
            for i in range(rejected):
                session.record_rejection(RejectedRecord("f.csv", i + 2, (("x", "y"),)))
            return label(analyze_session(session))

        self.assertEqual(run(2, 0), "insufficient_data")     # fewer than 3 usable
        self.assertEqual(run(3, 0), "resting")                # exactly 3 usable
        self.assertEqual(run(7, 3), "resting")                # exactly 30% rejected
        self.assertEqual(run(6, 4), "insufficient_data")      # 40% rejected
        self.assertEqual(run(8, 0, quality=0.60), "resting")  # average quality on the limit
        self.assertEqual(run(8, 0, quality=0.59), "insufficient_data")

    def test_empty_session_does_not_crash(self):
        result = analyze_session(TrainingSession(PROFILE, "FIT-2026-001"))
        self.assertEqual(label(result), "insufficient_data")

    def test_session_of_only_rejections_is_insufficient(self):
        session = TrainingSession(PROFILE, "FIT-2026-001")
        session.record_rejection(RejectedRecord("f.csv", 2, (("x", "y"),)))
        result = analyze_session(session)
        self.assertEqual(label(result), "insufficient_data")
        self.assertIn("only 0 of 1 rows were usable", result["classification"]["explanation"])

    def test_explanation_names_the_real_reason(self):
        # all rows usable, but the signal is weak: must blame signal quality, not row count
        weak = make_session([make_observation(timestamp=t, signal_quality=0.3) for t in range(5)])
        text = analyze_session(weak)["classification"]["explanation"]
        self.assertIn("average signal quality was 0.3", text)
        self.assertNotIn("usable", text)
        # plenty of clean signal, but too many rows rejected: must blame the rejections
        many_rejected = make_session([make_observation(timestamp=t) for t in range(5)])
        for i in range(4):
            many_rejected.record_rejection(RejectedRecord("f.csv", i + 2, (("x", "y"),)))
        text = analyze_session(many_rejected)["classification"]["explanation"]
        self.assertIn("44% of rows were rejected", text)
        self.assertNotIn("signal quality", text)


class ClassificationTests(unittest.TestCase):
    def test_result_is_a_dictionary_with_expected_sections(self):
        result = analyze_session(make_session([make_observation(timestamp=t) for t in range(6)]))
        for section in ["session_id", "source_file", "participant", "data_quality", "summaries",
                        "baseline_comparison", "recovery", "classification"]:
            self.assertIn(section, result)

    def test_every_classification_explains_itself(self):
        for scenario in ["resting", "moderate_activity", "high_activity", "recovery", "poor_quality"]:
            self.assertTrue(analyze_generated(scenario, 42)["classification"]["explanation"].strip())

    def test_uncertain_between_categories(self):
        # +12 bpm and 0.28 movement: too much for resting, too little for moderate
        obs = series([82] * 6, [0.28] * 6)
        self.assertEqual(label(analyze_session(make_session(obs))), "uncertain")

    def test_threshold_edges(self):
        edge = lambda hr, act: label(analyze_session(make_session(series([hr] * 6, [act] * 6))))
        self.assertEqual(edge(80, 0.25), "resting")            # +10 bpm, 0.25 exactly
        self.assertEqual(edge(85, 0.30), "moderate_activity")  # +15 bpm, 0.30 exactly
        self.assertEqual(edge(115, 0.65), "high_activity")     # +45 bpm, 0.65 exactly


class ScenarioTests(unittest.TestCase):
    """The instructor's generator, pushed through CSV files and the real loader."""

    def test_each_scenario(self):
        expected = {"resting": "resting", "moderate_activity": "moderate_activity",
                    "high_activity": "high_activity", "recovery": "recovering",
                    "poor_quality": "insufficient_data"}
        for scenario, wanted in expected.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(label(analyze_generated(scenario, 42)), wanted)

    def test_stable_across_seeds(self):
        for seed in [1, 42, 99, 2024]:
            for scenario, wanted in [("resting", "resting"), ("recovery", "recovering"),
                                     ("poor_quality", "insufficient_data")]:
                with self.subTest(seed=seed, scenario=scenario):
                    self.assertEqual(label(analyze_generated(scenario, seed)), wanted)

    def test_poor_quality_rows_are_rejected_not_silently_dropped(self):
        result = analyze_generated("poor_quality", 13)
        self.assertEqual(result["data_quality"]["total_observations"], 12)
        self.assertEqual(len(result["data_quality"]["rejected_rows"]), 12)


class ReportTests(unittest.TestCase):
    def test_report_contains_the_key_sections(self):
        report = build_report(analyze_generated("moderate_activity", 42))
        for heading in ["DATA QUALITY", "MEASUREMENT SUMMARIES", "RECOVERY CHECK", "CLASSIFICATION"]:
            self.assertIn(heading, report)

    def test_report_handles_a_session_with_no_usable_data(self):
        report = build_report(analyze_generated("poor_quality", 13))
        self.assertIn("no usable data", report)
        self.assertIn("REJECTED ROWS", report)

    def test_format_measurement_handles_missing_summary(self):
        self.assertIn("no usable data", format_measurement("heart rate", None))


if __name__ == "__main__":
    unittest.main()