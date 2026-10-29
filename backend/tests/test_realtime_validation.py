"""Teste pentru astran_sandbox/realtime_validation.py - validarea starii (pozitie/
rotatie/animatie) primite de la clienti pe WebSocket-ul live, inainte sa fie
retransmisa altor jucatori. Modulul testat nu depinde de FastAPI/WebSocket, deci
testele ruleaza cu stdlib pur:

    cd backend && python3 -m unittest tests.test_realtime_validation -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astran_sandbox import realtime_validation as rv  # noqa: E402


class CleanStateTests(unittest.TestCase):
    def test_accepts_well_formed_state(self):
        out = rv.clean_state({"t": "state", "x": 1.0, "y": 2.0, "z": -3.5, "ry": 0.7, "anim": "jump"})
        self.assertEqual(out, {"x": 1.0, "y": 2.0, "z": -3.5, "ry": 0.7, "anim": "jump"})

    def test_drops_unknown_fields(self):
        out = rv.clean_state({"x": 1.0, "is_admin": True, "__proto__": "x"})
        self.assertEqual(out, {"x": 1.0})

    def test_rejects_nan_and_infinity(self):
        # isinstance(float('nan'), float) e True in Python - exact de-aia era un bug
        # real sa ne bazam doar pe isinstance (vezi istoricul fisierului).
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                out = rv.clean_state({"x": bad})
                self.assertNotIn("x", out)

    def test_rejects_bool_as_number(self):
        out = rv.clean_state({"x": True})
        self.assertNotIn("x", out)

    def test_rejects_coordinates_outside_world_bounds(self):
        out = rv.clean_state({"x": rv.MAX_COORD + 1})
        self.assertNotIn("x", out)
        out = rv.clean_state({"x": -rv.MAX_COORD - 1})
        self.assertNotIn("x", out)

    def test_accepts_coordinate_at_boundary(self):
        out = rv.clean_state({"x": rv.MAX_COORD})
        self.assertEqual(out["x"], rv.MAX_COORD)

    def test_anim_must_be_short_string(self):
        out = rv.clean_state({"anim": "a" * 33})
        self.assertNotIn("anim", out)
        out = rv.clean_state({"anim": 12345})
        self.assertNotIn("anim", out)

    def test_empty_message_yields_empty_state(self):
        self.assertEqual(rv.clean_state({}), {})
        self.assertEqual(rv.clean_state({"t": "state"}), {})


class SpeedCheckTests(unittest.TestCase):
    def test_first_state_always_passes(self):
        self.assertTrue(rv.passes_speed_check({}, None, {"x": 0, "y": 0, "z": 0}, now=100.0))

    def test_plausible_movement_passes(self):
        prev = {"x": 0.0, "y": 0.0, "z": 0.0}
        new = {"x": 1.0, "y": 0.0, "z": 0.0}  # 1m in 0.1s = 10 m/s, sub pragul de 120
        self.assertTrue(rv.passes_speed_check(prev, 100.0, new, now=100.1))

    def test_teleport_is_rejected(self):
        prev = {"x": 0.0, "y": 0.0, "z": 0.0}
        new = {"x": 5000.0, "y": 0.0, "z": 0.0}  # 5000m in 0.1s - imposibil
        self.assertFalse(rv.passes_speed_check(prev, 100.0, new, now=100.1))

    def test_boundary_speed_is_accepted(self):
        prev = {"x": 0.0, "y": 0.0, "z": 0.0}
        dt = 1.0
        new = {"x": rv.MAX_SPEED_MPS * dt, "y": 0.0, "z": 0.0}
        self.assertTrue(rv.passes_speed_check(prev, 100.0, new, now=100.0 + dt))

    def test_just_over_boundary_speed_is_rejected(self):
        prev = {"x": 0.0, "y": 0.0, "z": 0.0}
        dt = 1.0
        new = {"x": rv.MAX_SPEED_MPS * dt + 1.0, "y": 0.0, "z": 0.0}
        self.assertFalse(rv.passes_speed_check(prev, 100.0, new, now=100.0 + dt))

    def test_anim_only_update_skips_speed_check(self):
        prev = {"x": 0.0, "y": 0.0, "z": 0.0}
        new = {"anim": "jump"}  # fara pozitie - nu are sens sa verificam viteza
        self.assertTrue(rv.passes_speed_check(prev, 100.0, new, now=100.001))

    def test_near_zero_dt_does_not_divide_by_zero(self):
        prev = {"x": 0.0, "y": 0.0, "z": 0.0}
        new = {"x": 9999.0, "y": 0.0, "z": 0.0}
        # dt sub MIN_DT_FOR_SPEED_CHECK -> nu aruncam ZeroDivisionError, doar trecem
        try:
            result = rv.passes_speed_check(prev, 100.0, new, now=100.0)
        except ZeroDivisionError:
            self.fail("passes_speed_check should not divide by zero for dt==0")
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()