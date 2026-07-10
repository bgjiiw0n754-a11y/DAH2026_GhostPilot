import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from b_path_experiment import (
    CONTROLLER_FIELDS,
    DECISION_EVENT_FIELDS,
    IMPACT_MATRIX_FIELDS,
    INJECTION_FIELDS,
    MISSION_MATRIX_FIELDS,
    MISSION_IMPACT_MATRIX_FIELDS,
    PAYLOAD_MATRIX_FIELDS,
    PLANNER_TRACE_FIELDS,
    PRECONDITION_MATRIX_FIELDS,
    ROUTE_MATRIX_FIELDS,
    ROUTE_SCORE_FIELDS,
    STEALTH_METRICS_FIELDS,
    TELEMETRY_FIELDS,
    AdaptiveMotionState,
    RunnerConfig,
    bounded_adaptive_sample,
    compute_reflection_metrics,
    first_decision_event,
    generate_sequence,
    negative_control_pass,
    mode_seen,
    payloads_to_run,
    positive_reflection_pass,
    validate_result_bundle,
    write_bundle,
)
from bpath.mission_decisions import detect_event, is_geofence_breach
from bpath.planner import choose_objective
from bpath.routes import selected_routes
from bpath.stealth import stealth_score


class BPathExperimentTest(unittest.TestCase):
    def test_ekf_smooth_sequence_reaches_same_final_drift_with_feasible_limits(self):
        config = RunnerConfig(
            ardupilot_dir=Path("/tmp/ardupilot"),
            out_root=Path("/tmp/results"),
            iterations=20,
            interval=0.5,
            step_m=0.5,
            max_speed=2.0,
            max_accel=1.0,
        )
        linear = generate_sequence(37.0, 127.0, 20.0, config, "linear", "gps-input", "test")
        smooth = generate_sequence(37.0, 127.0, 20.0, config, "ekf-smooth", "gps-input", "test")

        self.assertAlmostEqual(linear[-1].target_drift_m, smooth[-1].target_drift_m, delta=0.1)
        self.assertLessEqual(max(s.commanded_speed_mps for s in smooth), config.max_speed)
        self.assertLessEqual(max(s.commanded_accel_mps2 for s in smooth), config.max_accel)
        self.assertLess(
            smooth[0].commanded_accel_mps2,
            linear[0].commanded_accel_mps2,
        )

    def test_positive_verdict_requires_reflection_thresholds(self):
        telemetry = []
        for i in range(1, 12):
            telemetry.append(
                {
                    "message_type": "GLOBAL_POSITION_INT",
                    "target_drift_m": float(i),
                    "official_drift_m": float(i) * 0.9,
                    "target_error_m": 0.4,
                    "reflected": 1,
                    "gps_fix_type": 3,
                }
            )
        injection = [{"commanded_speed_mps": 1.0, "commanded_accel_mps2": 0.2}]
        metrics = compute_reflection_metrics(telemetry, injection)
        passed, reason = positive_reflection_pass(metrics)
        self.assertTrue(passed, reason)

    def test_negative_control_passes_only_when_official_position_does_not_follow(self):
        telemetry = []
        for i in range(1, 8):
            telemetry.append(
                {
                    "message_type": "GLOBAL_POSITION_INT",
                    "target_drift_m": float(i),
                    "official_drift_m": 0.2,
                    "target_error_m": float(i),
                    "reflected": 0,
                    "gps_fix_type": 3,
                }
            )
        metrics = compute_reflection_metrics(telemetry, [])
        passed, reason = negative_control_pass(metrics)
        self.assertTrue(passed, reason)

    def test_fence_present_status_is_not_counted_as_breach(self):
        metrics = compute_reflection_metrics(
            [
                {
                    "message_type": "STATUSTEXT",
                    "status_text": "fence present",
                    "fence_breach_status": "",
                },
                {
                    "message_type": "FENCE_STATUS",
                    "status_text": "",
                    "fence_breach_status": "0",
                },
            ],
            [],
        )
        self.assertFalse(metrics["fence_breach_observed"])
        self.assertEqual(metrics["fence_breach_count"], 0)

    def test_adaptive_controller_respects_speed_and_acceleration_limits(self):
        config = RunnerConfig(
            ardupilot_dir=Path("/tmp/ardupilot"),
            out_root=Path("/tmp/results"),
            interval=0.5,
            max_speed=2.5,
            max_accel=1.0,
            bearing_deg=0.0,
        )
        state = AdaptiveMotionState()
        samples = [
            bounded_adaptive_sample(37.0, 127.0, 20.0, config, state, 20.0, "test")
            for _ in range(8)
        ]
        self.assertTrue(all(s.commanded_speed_mps <= config.max_speed for s in samples))
        self.assertTrue(all(s.commanded_accel_mps2 <= config.max_accel for s in samples))
        self.assertGreater(samples[-1].target_drift_m, samples[0].target_drift_m)

    def test_mission_current_increment_is_decision_event(self):
        event = first_decision_event(
            [
                {
                    "timestamp": "t0",
                    "message_type": "MISSION_CURRENT",
                    "mission_seq": "0",
                },
                {
                    "timestamp": "t1",
                    "message_type": "MISSION_CURRENT",
                    "mission_seq": "1",
                    "elapsed_s": "3.5",
                },
            ],
            "unit",
            "adaptive",
            "mission_advance",
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "mission_advance")
        self.assertEqual(event["mission_seq"], "1")

    def test_advanced_csv_schemas_are_stable(self):
        self.assertIn("action", CONTROLLER_FIELDS)
        self.assertIn("event_type", DECISION_EVENT_FIELDS)
        self.assertIn("route", ROUTE_MATRIX_FIELDS)
        self.assertIn("objective", MISSION_MATRIX_FIELDS)
        self.assertIn("stealth_score", STEALTH_METRICS_FIELDS)
        self.assertIn("selected", PLANNER_TRACE_FIELDS)
        self.assertIn("decision_changed", ROUTE_SCORE_FIELDS)
        self.assertIn("best_route", IMPACT_MATRIX_FIELDS)
        self.assertIn("payload", PAYLOAD_MATRIX_FIELDS)
        self.assertIn("impact_type", MISSION_IMPACT_MATRIX_FIELDS)
        self.assertIn("required_access", PRECONDITION_MATRIX_FIELDS)

    def test_payload_selection_supports_all_and_single_payload(self):
        self.assertIn("gps-input", payloads_to_run("all"))
        self.assertEqual(payloads_to_run("param-edit"), ("param-edit",))
        with self.assertRaises(ValueError):
            payloads_to_run("unknown")

    def test_mode_seen_requires_heartbeat_mode_match(self):
        self.assertFalse(mode_seen([{"message_type": "STATUSTEXT", "mode": "RTL"}], "RTL"))
        self.assertFalse(mode_seen([{"message_type": "HEARTBEAT", "mode": "LAND"}], "RTL"))
        self.assertTrue(mode_seen([{"message_type": "HEARTBEAT", "mode": "RTL"}], "RTL"))

    def test_decision_detectors_do_not_treat_generic_fence_text_as_breach(self):
        self.assertFalse(is_geofence_breach({"message_type": "STATUSTEXT", "status_text": "fence present"}))
        self.assertIsNone(
            detect_event(
                [{"message_type": "STATUSTEXT", "status_text": "fence present"}],
                "geofence",
            )
        )
        self.assertIsNotNone(
            detect_event(
                [{"message_type": "FENCE_STATUS", "fence_breach_status": "1", "elapsed_s": "2.0"}],
                "geofence",
            )
        )

    def test_waypoint_decision_requires_mission_current_increment(self):
        self.assertIsNone(
            detect_event(
                [{"message_type": "STATUSTEXT", "status_text": "Reached command"}],
                "auto-waypoint",
            )
        )
        self.assertIsNotNone(
            detect_event(
                [{"message_type": "MISSION_CURRENT", "mission_seq": "1", "elapsed_s": "2.0"}],
                "auto-waypoint",
            )
        )

    def test_stealth_score_penalizes_constraint_violations(self):
        clean = stealth_score(
            {
                "commanded_speed_max_mps": 1.0,
                "commanded_accel_max_mps2": 0.2,
                "abrupt_jump_count": 0,
                "mean_target_error_m": 1.0,
                "final_target_drift_m": 10.0,
                "gps_fix_stability": 1.0,
            },
            "normal",
        )
        noisy = stealth_score(
            {
                "commanded_speed_max_mps": 8.0,
                "commanded_accel_max_mps2": 6.0,
                "abrupt_jump_count": 4,
                "mean_target_error_m": 9.0,
                "final_target_drift_m": 30.0,
                "gps_fix_stability": 0.5,
            },
            "normal",
        )
        self.assertLess(clean["stealth_score"], noisy["stealth_score"])
        self.assertGreater(noisy["constraint_violations"], clean["constraint_violations"])

    def test_planner_auto_selects_highest_scored_available_objective(self):
        selected, trace = choose_objective("auto", {"FENCE_ENABLE": 1})
        self.assertEqual(selected, "geofence")
        self.assertEqual(sum(row["selected"] for row in trace), 1)

    def test_route_selection_does_not_generalize_single_route(self):
        self.assertEqual(selected_routes("fc-direct"), ("fc-direct",))
        self.assertIn("mavproxy-udp", selected_routes("all"))

    def test_stealth_opt_sequence_respects_configured_limits(self):
        config = RunnerConfig(
            ardupilot_dir=Path("/tmp/ardupilot"),
            out_root=Path("/tmp/results"),
            iterations=24,
            interval=0.5,
            step_m=1.0,
            max_speed=2.5,
            max_accel=1.0,
        )
        seq = generate_sequence(37.0, 127.0, 20.0, config, "stealth-opt", "gps-input", "test")
        self.assertTrue(all(s.commanded_speed_mps <= config.max_speed + 1e-6 for s in seq))
        self.assertTrue(all(s.commanded_accel_mps2 <= config.max_accel + 1e-6 for s in seq))

    def test_result_bundle_required_files_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            missing = validate_result_bundle(bundle)
            self.assertIn("README.md", missing)

            (bundle / "sitl.log").write_text("", encoding="utf-8")
            (bundle / "params_before.txt").write_text("", encoding="utf-8")
            (bundle / "params_after.txt").write_text("", encoding="utf-8")
            write_bundle(
                bundle,
                "unit",
                {"created_at_utc": "test", "route": "unit", "connection": "unit"},
                [],
                [],
                {
                    "experiment": "unit",
                    "claim_tested": "unit",
                    "verdict": "pass",
                    "reason": "unit",
                    "metrics": {},
                    "not_claimed": [],
                },
            )
            self.assertEqual(validate_result_bundle(bundle), [])
            self.assertEqual((bundle / "injection.csv").read_text(encoding="utf-8").splitlines()[0], ",".join(INJECTION_FIELDS))
            self.assertEqual((bundle / "telemetry.csv").read_text(encoding="utf-8").splitlines()[0], ",".join(TELEMETRY_FIELDS))


if __name__ == "__main__":
    unittest.main()
