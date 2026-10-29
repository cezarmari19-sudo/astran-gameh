"""Teste pentru partea din astran_sandbox/runner.py care NU are nevoie de binarul
luau-sandbox compilat: validarea fisierelor trimise de client, sanitizarea rezultatului
(ops) inainte sa ajunga la alti jucatori, si constructia fisierelor speciale __assets/__scene.

Ruleaza cu stdlib pur (unittest) - nu necesita pytest, fastapi, sau binarul C++:

    cd backend && python3 -m unittest tests.test_runner_validation -v

(sau `python3 -m pytest tests/test_runner_validation.py -v` daca ai pytest instalat).
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ pe sys.path

from astran_sandbox import runner  # noqa: E402


class ValidateFilesTests(unittest.TestCase):
    def test_accepts_well_formed_project(self):
        files = runner.validate_files([{"name": "main", "source": "print('hi')"}])
        self.assertEqual(files, [{"name": "main", "source": "print('hi')"}])

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            runner.validate_files("not a list")

    def test_rejects_too_many_files(self):
        files = [{"name": f"f{i}", "source": "x"} for i in range(runner.MAX_FILES + 1)]
        with self.assertRaises(ValueError):
            runner.validate_files(files)

    def test_rejects_bad_name_characters(self):
        # Numele scriptului ajunge in bundle-ul trimis binarului C++ (host.cpp: validName
        # accepta doar [A-Za-z0-9_-/]) - orice altceva ar trebui respins deja in Python,
        # nu lasat sa pice la nivelul procesului copil.
        for bad_name in ("../etc/passwd", "main.lua", "a b", "a;b", "a\nb", ""):
            with self.subTest(bad_name=bad_name):
                with self.assertRaises(ValueError):
                    runner.validate_files([{"name": bad_name, "source": "x"}])

    def test_rejects_reserved_names(self):
        for reserved in (runner.ASSETS_FILE_NAME, runner.SCENE_FILE_NAME):
            with self.assertRaises(ValueError):
                runner.validate_files([{"name": reserved, "source": "x"}])

    def test_rejects_duplicate_names(self):
        with self.assertRaises(ValueError):
            runner.validate_files([
                {"name": "main", "source": "a"},
                {"name": "main", "source": "b"},
            ])

    def test_rejects_oversized_single_file(self):
        with self.assertRaises(ValueError):
            runner.validate_files([{"name": "main", "source": "x" * (runner.MAX_SOURCE_CHARS + 1)}])

    def test_rejects_oversized_total(self):
        # Fiecare fisier sub limita individuala, dar impreuna peste limita totala.
        n = runner.MAX_TOTAL_CHARS // runner.MAX_FILES + 100
        files = [{"name": f"f{i}", "source": "x" * n} for i in range(runner.MAX_FILES)]
        with self.assertRaises(ValueError):
            runner.validate_files(files)

    def test_rejects_non_string_fields(self):
        with self.assertRaises(ValueError):
            runner.validate_files([{"name": 123, "source": "x"}])
        with self.assertRaises(ValueError):
            runner.validate_files([{"name": "main", "source": 123}])


class SanitizeOpsTests(unittest.TestCase):
    """sanitize_ops primeste rezultatul JSON intors de scriptul Luau (necunoscut, posibil
    rau-intentionat) si trebuie sa produca DOAR date pe care clientul le poate afisa in
    siguranta. Orice camp in afara formei asteptate trebuie sa disparea, nu doar sa treaca
    mai departe nevalidat."""

    def test_drops_non_list(self):
        self.assertEqual(runner.sanitize_ops("not a list"), [])
        self.assertEqual(runner.sanitize_ops(None), [])
        self.assertEqual(runner.sanitize_ops({"op": "create"}), [])

    def test_drops_unknown_op_kind(self):
        ops = runner.sanitize_ops([{"op": "delete_everything", "id": "x"}])
        self.assertEqual(ops, [])

    def test_drops_entries_without_valid_id(self):
        ops = runner.sanitize_ops([{"op": "create", "id": "../../etc/passwd"}])
        self.assertEqual(ops, [])
        ops = runner.sanitize_ops([{"op": "create", "id": 123}])
        self.assertEqual(ops, [])

    def test_clamps_position_to_world_bounds(self):
        ops = runner.sanitize_ops([{"op": "create", "id": "a", "x": 1e9, "y": -1e9}])
        self.assertEqual(len(ops), 1)
        self.assertLessEqual(ops[0]["x"], 10000.0)
        self.assertGreaterEqual(ops[0]["y"], -10000.0)

    def test_rejects_non_finite_numbers(self):
        # Un script Luau nu poate produce NaN/inf in JSON standard, dar sanitize_ops nu
        # trebuie sa aiba incredere in asta - testeaza explicit calea _number().
        self.assertIsNone(runner._number(float("nan")))
        self.assertIsNone(runner._number(float("inf")))
        self.assertIsNone(runner._number(True))  # bool nu e un numar valid aici

    def test_shape_type_whitelist(self):
        ops = runner.sanitize_ops([{"op": "create", "id": "a", "type": "cube"}])
        self.assertEqual(ops[0]["type"], "cube")
        ops = runner.sanitize_ops([{"op": "create", "id": "a", "type": "<script>alert(1)</script>"}])
        self.assertNotIn("type", ops[0])

    def test_color_must_match_hex_pattern(self):
        ops = runner.sanitize_ops([{"op": "create", "id": "a", "color": "#ff00aa"}])
        self.assertEqual(ops[0]["color"], "#ff00aa")
        ops = runner.sanitize_ops([{"op": "create", "id": "a", "color": "javascript:alert(1)"}])
        self.assertNotIn("color", ops[0])

    def test_world_op_clamps_gravity_and_speeds(self):
        ops = runner.sanitize_ops([{"op": "world", "id": "w", "gravity": 1e6, "move_speed": -50}])
        self.assertEqual(len(ops), 1)
        self.assertLessEqual(ops[0]["gravity"], 200.0)
        self.assertGreaterEqual(ops[0].get("move_speed", 0.0), 0.0)

    def test_material_id_must_match_pattern(self):
        ops = runner.sanitize_ops([{"op": "material", "id": "m1", "density": 5}])
        self.assertEqual(len(ops), 1)
        ops = runner.sanitize_ops([{"op": "material", "id": "DROP TABLE", "density": 5}])
        self.assertEqual(ops, [])

    def test_ops_sorted_by_time_stable(self):
        ops = runner.sanitize_ops([
            {"op": "create", "id": "b", "t": 1},
            {"op": "create", "id": "a", "t": 0},
            {"op": "create", "id": "c", "t": 0},
        ])
        self.assertEqual([o["id"] for o in ops], ["a", "c", "b"])

    def test_respects_max_ops_cap(self):
        raw = [{"op": "create", "id": f"i{i}"} for i in range(runner.MAX_OPS + 50)]
        ops = runner.sanitize_ops(raw)
        self.assertLessEqual(len(ops), runner.MAX_OPS)


class AssetsAndSceneFileTests(unittest.TestCase):
    def test_assets_file_none_when_empty(self):
        self.assertIsNone(runner.assets_file([]))
        self.assertIsNone(runner.assets_file(None))

    def test_assets_file_drops_malformed_entries(self):
        f = runner.assets_file([{"id": "x", "object": {"type": "cube"}}, {"not": "valid"}, "junk"])
        self.assertIsNotNone(f)
        self.assertEqual(f["name"], runner.ASSETS_FILE_NAME)

    def test_scene_file_rejects_bad_ids_and_caps_count(self):
        objs = [{"id": "ok1", "type": "cube"}] + [{"id": f"<bad {i}>", "type": "cube"} for i in range(5)]
        f = runner.scene_file({"objects": objs})
        self.assertIsNotNone(f)
        # doar obiectul cu id valid trece

    def test_scene_file_filters_spawn_marker(self):
        f = runner.scene_file({"objects": [{"id": "s1", "type": "spawn"}]})
        self.assertIsNone(f)  # singurul obiect era un marker de spawn -> nimic de trimis


class ParseOutputTests(unittest.TestCase):
    def test_parses_print_and_error_lines(self):
        stdout = (
            b'{"type":"print","msg":"hello"}\n'
            b'{"type":"error","msg":"boom"}\n'
        )
        result = runner.parse_output(stdout, 0)
        self.assertFalse(result["ok"])  # a avut o eroare
        self.assertEqual(result["output"][0]["msg"], "hello")
        self.assertEqual(result["errors"][0], "boom")

    def test_ignores_garbage_lines(self):
        stdout = b"not json\n{\"type\":\"print\",\"msg\":\"ok\"}\n"
        result = runner.parse_output(stdout, 0)
        self.assertEqual(result["output"][0]["msg"], "ok")

    def test_nonzero_exit_without_explicit_error_is_reported(self):
        result = runner.parse_output(b"", 137)  # SIGKILL (ex: cgroup OOM / timeout extern)
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_untrusted_ops_payload_is_sanitized_not_trusted_raw(self):
        # Scriptul ar putea incerca sa trimita campuri in plus prin emit("ops", ...) -
        # parse_output trebuie sa treaca mereu prin sanitize_ops, nu sa copieze ca atare.
        stdout = b'{"type":"ops","data":{"ops":[{"op":"create","id":"a","evil":"<script>"}],"duration":5}}\n'
        result = runner.parse_output(stdout, 0)
        self.assertEqual(len(result["ops"]), 1)
        self.assertNotIn("evil", result["ops"][0])


if __name__ == "__main__":
    unittest.main()