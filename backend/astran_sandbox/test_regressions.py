"""Teste de regresie pentru corecturile din auditul de securitate (stdlib only).
Rulare din folderul backend:  python -m unittest astran_sandbox.test_regressions -v

Doua categorii, marcate clar:
  * COMPORTAMENT - functia reala e importata si apelata.
  * STATIC       - verifica sursa (server.py/routes.py nu pot fi importate fara FastAPI+Mongo);
                   dovedesc doar ca fixul e prezent, NU ca endpointul merge end-to-end.
"""
import json
import math
import re
import unittest
from pathlib import Path

from astran_sandbox import runner
from astran_sandbox.realtime_validation import clean_state_technical

BACKEND = Path(__file__).resolve().parent.parent


def src(rel: str) -> str:
    return (BACKEND / rel).read_text(encoding="utf-8")


class RealtimeStateSanitizer(unittest.TestCase):  # COMPORTAMENT
    def test_rejects_nan_inf_bool_and_overflow(self):
        out = clean_state_technical({"x": float("nan"), "y": math.inf, "z": True, "ry": 1e12, "anim": "jump"})
        self.assertEqual(out, {"anim": "jump"})

    def test_json_output_is_valid_for_browsers(self):
        out = clean_state_technical(json.loads('{"x": NaN, "y": Infinity, "z": -Infinity, "ry": 1}'))
        json.dumps(out, allow_nan=False)  # ar ridica ValueError daca ar scapa NaN/Inf
        self.assertEqual(out, {"ry": 1.0})

    def test_no_gameplay_limits(self):  # cerinta: creatorul decide fizica/anti-cheat
        out = clean_state_technical({"x": 50000, "y": 99999.5, "z": -75000, "ry": 3})
        self.assertEqual(out, {"x": 50000.0, "y": 99999.5, "z": -75000.0, "ry": 3.0})

    def test_anim_length_and_type(self):
        self.assertNotIn("anim", clean_state_technical({"anim": "x" * 33}))
        self.assertNotIn("anim", clean_state_technical({"anim": 5}))


class LuauStringEncoding(unittest.TestCase):  # COMPORTAMENT - suspiciune infirmata
    """__scene/__assets sunt 'return "<json dublu-codat>"'. Verificam ca singurele escape-uri
    din literalul Luau sunt cele valide (\\\\ \\" \\n), chiar si cu caractere de control in nume."""

    def test_scene_literal_has_only_valid_luau_escapes(self):
        f = runner.scene_file({"objects": [{"id": "a1", "type": "cube\x01\x1f\"\\", "name": "n\x02\u00e9"}]})
        literal = f["source"][len("return "):]
        self.assertTrue(literal.startswith('"') and literal.endswith('"'))
        for m in re.finditer(r"\\(.)", literal):
            self.assertIn(m.group(1), '\\"nrt', f"escape invalid in Luau: \\{m.group(1)}")
        for ch in literal:
            self.assertGreaterEqual(ord(ch), 0x20)

    def test_assets_literal_has_only_valid_luau_escapes(self):
        f = runner.assets_file([{"id": "i", "name": "a\x01b\n\"c", "object": {"type": "cube"}}])
        for m in re.finditer(r"\\(.)", f["source"][len("return "):]):
            self.assertIn(m.group(1), '\\"nrt')


class StaticFixesPresent(unittest.TestCase):  # STATIC
    def setUp(self):
        self.server = src("server.py")

    def test_jwt_secret_fail_closed(self):
        self.assertIn("JWT_SECRET missing/weak", self.server)

    def test_demo_account_has_no_hardcoded_password(self):
        self.assertNotIn("Astran#Demo2026", self.server)

    def test_user_search_escapes_regex_and_hides_pii(self):
        body = self.server[self.server.index("async def search_users"):self.server.index("async def get_user_public")]
        self.assertIn("re.escape(q)", body)
        self.assertIn("_user_public_minimal", body)
        self.assertNotIn("_user_public(u)", body)

    def test_transfer_does_not_store_null_idempotency_key(self):
        body = self.server[self.server.index("async def transfer_astrans"):self.server.index("async def wallet_tx")]
        tx_literal = body[body.index("tx = {"):body.index("    try:\n        await db.astran_ledger.insert_one(tx)")]
        self.assertNotIn('"idempotency_key": body.idempotency_key,', tx_literal)  # null s-ar indexa
        self.assertIn('tx["idempotency_key"] = body.idempotency_key', body)
        self.assertIn('"user_id": current["user_id"]}, {"_id": 0})', body)  # lookup scopat pe user

    def test_logout_revokes_jwt(self):
        self.assertIn("revoked_tokens", self.server)

    def test_sandbox_routes_hide_trashed_games(self):
        r = src("astran_sandbox/routes.py")
        self.assertIn('if not g or g.get("deleted")', r[r.index("async def get_game"):r.index("async def get_access")])

    def test_realtime_uses_technical_sanitizer(self):
        self.assertIn("clean_state_technical", src("realtime.py"))


if __name__ == "__main__":
    unittest.main()