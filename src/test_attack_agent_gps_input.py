import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attack_agent import (
    GhostPilotAttack,
    bearing_step_m,
    clamp_vector,
    offset_latlon_m,
    slew_toward,
)
from utils import haversine_m


class _RecordingMav:
    def __init__(self):
        self.gps_input_calls = []
        self.hil_gps_calls = []

    def gps_input_send(self, *args):
        self.gps_input_calls.append(args)

    def hil_gps_send(self, *args):
        self.hil_gps_calls.append(args)


class _FakeConn:
    def __init__(self):
        self.mav = _RecordingMav()


class GpsInputAttackTest(unittest.TestCase):
    def test_offset_latlon_moves_about_requested_distance(self):
        lat, lon = offset_latlon_m(37.0, 127.0, north_m=3.0, east_m=4.0)
        dist = haversine_m(37.0, 127.0, lat, lon)
        self.assertAlmostEqual(dist, 5.0, delta=0.05)

    def test_bearing_step_uses_aircraft_bearing_convention(self):
        north, east = bearing_step_m(10.0, 90.0)
        self.assertAlmostEqual(north, 0.0, delta=1e-9)
        self.assertAlmostEqual(east, 10.0, delta=1e-9)

    def test_slew_toward_limits_velocity_change(self):
        self.assertAlmostEqual(slew_toward(0.0, 5.0, 0.3), 0.3)
        self.assertAlmostEqual(slew_toward(1.0, -5.0, 0.4), 0.6)
        self.assertAlmostEqual(slew_toward(1.0, 1.2, 0.4), 1.2)

    def test_clamp_vector_preserves_bearing_under_speed_limit(self):
        north, east = clamp_vector(3.0, 4.0, 2.5)
        self.assertAlmostEqual((north ** 2 + east ** 2) ** 0.5, 2.5)
        self.assertAlmostEqual(north / east, 3.0 / 4.0)

    def test_send_gps_input_builds_mavlink_sensor_input_payload(self):
        attacker = object.__new__(GhostPilotAttack)
        attacker.conn = _FakeConn()

        attacker._send_gps_input(
            lat=37.1234567,
            lon=127.7654321,
            alt_m=42.5,
            vn=1.25,
            ve=0.75,
            vd=0.0,
            gps_id=2,
            fix_type=3,
            satellites=14,
            hacc=0.6,
            vacc=1.1,
            sacc=0.2,
        )

        self.assertEqual(len(attacker.conn.mav.gps_input_calls), 1)
        args = attacker.conn.mav.gps_input_calls[0]
        self.assertGreaterEqual(len(args), 18)
        self.assertEqual(args[1], 2)                  # gps_id
        self.assertEqual(args[2], 0)                  # ignore_flags
        self.assertEqual(args[5], 3)                  # fix_type
        self.assertEqual(args[6], int(37.1234567 * 1e7))
        self.assertEqual(args[7], int(127.7654321 * 1e7))
        self.assertAlmostEqual(args[8], 42.5)
        self.assertAlmostEqual(args[11], 1.25)        # vn
        self.assertAlmostEqual(args[12], 0.75)        # ve
        self.assertAlmostEqual(args[14], 0.2)         # speed_accuracy
        self.assertAlmostEqual(args[15], 0.6)         # horiz_accuracy
        self.assertAlmostEqual(args[16], 1.1)         # vert_accuracy
        self.assertEqual(args[17], 14)                # satellites_visible

    def test_send_hil_gps_builds_raw_gps_payload(self):
        attacker = object.__new__(GhostPilotAttack)
        attacker.conn = _FakeConn()

        attacker._send_hil_gps(
            lat=37.1234567,
            lon=127.7654321,
            alt_m=42.5,
            vn=1.25,
            ve=0.75,
            vd=0.0,
            fix_type=3,
            satellites=14,
            hacc=0.6,
            vacc=1.1,
        )

        self.assertEqual(len(attacker.conn.mav.hil_gps_calls), 1)
        args = attacker.conn.mav.hil_gps_calls[0]
        self.assertGreaterEqual(len(args), 13)
        self.assertEqual(args[1], 3)                  # fix_type
        self.assertEqual(args[2], int(37.1234567 * 1e7))
        self.assertEqual(args[3], int(127.7654321 * 1e7))
        self.assertEqual(args[4], int(42.5 * 1000))   # alt mm
        self.assertEqual(args[5], 60)                 # eph cm
        self.assertEqual(args[6], 110)                # epv cm
        self.assertEqual(args[8], 125)                # vn cm/s
        self.assertEqual(args[9], 75)                 # ve cm/s
        self.assertEqual(args[12], 14)                # satellites_visible


if __name__ == "__main__":
    unittest.main()
