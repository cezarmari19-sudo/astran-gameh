"""Teste de regresie pentru bug-urile gasite la auditul complet (vezi AUDIT_REPORT.md).

Rulare din folderul backend (nu necesita MongoDB, FastAPI, pytest sau retea):

    python -m unittest astran_sandbox.test_audit_regressions -v

Ce sunt aceste teste:
  - Ruleaza codul REAL din runner.py / project_security.py.
  - Pentru partea de baza de date folosesc FakeDB (mai jos): un dublu de test, in memorie,
    care implementeaza doar operatiile Mongo folosite de cod ($set/$inc/$unset, $gte/$lt/$gt,
    upsert, index unic) si CEDEAZA bucla asyncio la fiecare operatie (ca o retea reala), ca
    sa se vada conditiile de cursa. NU inlocuieste nimic din aplicatie, doar baza de date.
  - Daca `fastapi` nu e instalat, se injecteaza un HTTPException minimal DOAR in procesul de
    test, ca sa poata fi importat project_security.py.
"""
from __future__ import annotations

import asyncio
import copy
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - depinde de mediu
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    fake = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail=None, headers=None):
            super().__init__(f"{status_code}: {detail}")
            self.status_code = status_code
            self.detail = detail
            self.headers = headers

    fake.HTTPException = HTTPException
    sys.modules["fastapi"] = fake

from astran_sandbox import project_security as psec  # noqa: E402
from astran_sandbox import runner  # noqa: E402


# --------------------------------------------------------------------------------------
# FakeDB: dublu de test pentru Mongo
# --------------------------------------------------------------------------------------
class _Res:
    def __init__(self, matched=0, modified=0):
        self.matched_count = matched
        self.modified_count = modified


def _cmp(doc_val, cond) -> bool:
    if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
        for op, v in cond.items():
            if op == "$gte" and not (doc_val is not None and doc_val >= v):
                return False
            if op == "$gt" and not (doc_val is not None and doc_val > v):
                return False
            if op == "$lt" and not (doc_val is not None and doc_val < v):
                return False
            if op == "$ne" and doc_val == v:
                return False
            if op == "$in" and doc_val not in v:
                return False
        return True
    return doc_val == cond


class FakeColl:
    def __init__(self, unique=None):
        self.docs: list[dict] = []
        self.unique = unique  # tuple de campuri cu index unic

    @staticmethod
    def _match(doc, flt) -> bool:
        return all(_cmp(doc.get(k), v) for k, v in flt.items())

    async def find_one(self, flt, proj=None):
        await asyncio.sleep(0)
        for d in self.docs:
            if self._match(d, flt):
                return copy.deepcopy(d)
        return None

    async def insert_one(self, doc):
        await asyncio.sleep(0)
        self._check_unique(doc)
        self.docs.append(copy.deepcopy(doc))

    def _check_unique(self, doc):
        if self.unique and any(all(d.get(f) == doc.get(f) for f in self.unique) for d in self.docs):
            raise RuntimeError("E11000 duplicate key")

    @staticmethod
    def _apply(doc, upd):
        for k, v in upd.get("$set", {}).items():
            doc[k] = v
        for k, v in upd.get("$inc", {}).items():
            doc[k] = doc.get(k, 0) + v
        for k in upd.get("$unset", {}):
            doc.pop(k, None)

    async def update_one(self, flt, upd, upsert=False):
        await asyncio.sleep(0)
        for d in self.docs:
            if self._match(d, flt):
                self._apply(d, upd)
                return _Res(1, 1)
        if upsert:
            new = {k: v for k, v in flt.items() if not (isinstance(v, dict) and any(x.startswith("$") for x in v))}
            self._apply(new, upd)
            self._check_unique(new)
            self.docs.append(new)
            return _Res(0, 0)
        return _Res(0, 0)

    async def find_one_and_update(self, flt, upd, return_document=False, **_):
        await asyncio.sleep(0)
        for d in self.docs:
            if self._match(d, flt):
                before = copy.deepcopy(d)
                self._apply(d, upd)
                return copy.deepcopy(d) if return_document else before
        return None

    async def delete_one(self, flt):
        await asyncio.sleep(0)
        for i, d in enumerate(self.docs):
            if self._match(d, flt):
                del self.docs[i]
                return _Res(1, 1)
        return _Res(0, 0)


class FakeDB:
    def __init__(self):
        self.security_attempts = FakeColl(unique=("user_id", "scope_id", "purpose"))
        self.recovery_tokens = FakeColl(unique=("token_id",))
        self.webauthn_challenges = FakeColl(unique=("challenge_id",))


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------------------
# BUG-001: trailing newline accepted by Python regexes ($ vs \Z)
# --------------------------------------------------------------------------------------
class TestTrailingNewlineRegexes(unittest.TestCase):
    def test_script_name_with_trailing_newline_is_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_files([{"name": "main", "source": "x"}, {"name": "evil\n", "source": "x"}])

    def test_bundle_header_cannot_be_split_by_a_name(self):
        # chiar daca ar trece de validare, un nume cu \n ar produce un header "@@FILE evil\n 1"
        # pe care host.cpp il respinge ca "invalid script bundle" -> toata rularea pica.
        for bad in ("evil\n", "a/b\n"):
            with self.assertRaises(ValueError):
                runner.validate_files([{"name": bad, "source": "x"}])

    def test_ids_colors_materials_with_trailing_newline_rejected(self):
        self.assertIsNone(runner._ID_RE.match("abc\n"))
        self.assertIsNone(runner._HEX_COLOR.match("#aabbcc\n"))
        self.assertIsNone(runner._MATERIAL_ID_RE.match("m1\n"))

    def test_sanitize_ops_drops_op_with_newline_id_and_color(self):
        ops = runner.sanitize_ops([
            {"op": "create", "id": "box\n", "x": 1},
            {"op": "create", "id": "ok", "color": "#aabbcc\n", "x": 1},
        ])
        self.assertEqual([o["id"] for o in ops], ["ok"])
        self.assertNotIn("color", ops[0])

    def test_valid_values_still_accepted(self):
        runner.validate_files([{"name": "main", "source": ""}, {"name": "server/physics", "source": ""}])
        self.assertTrue(runner._ID_RE.match("obj_1.a:b-c"))
        self.assertTrue(runner._HEX_COLOR.match("#AaBb09"))
        self.assertTrue(runner._MATERIAL_ID_RE.match("m1234"))


# --------------------------------------------------------------------------------------
# BUG-002: brute-force limiter can be bypassed with concurrent requests
# --------------------------------------------------------------------------------------
class TestRateLimitRace(unittest.TestCase):
    def test_parallel_failures_still_trigger_lockout(self):
        async def scenario():
            db = FakeDB()
            await asyncio.gather(*[
                psec.record_failed_attempt(db, user_id="u", scope_id="g", purpose="project_delete")
                for _ in range(20)
            ])
            doc = await db.security_attempts.find_one({"user_id": "u"})
            self.assertEqual(doc["count"], 20, "increments were lost under concurrency")
            with self.assertRaises(psec.HTTPException) as ctx:
                await psec.ensure_rate_limit_or_raise(db, user_id="u", scope_id="g", purpose="project_delete")
            self.assertEqual(ctx.exception.status_code, 429)
        run(scenario())

    def test_sequential_behaviour_unchanged(self):
        async def scenario():
            db = FakeDB()
            for i in range(psec.MAX_ATTEMPTS - 1):
                await psec.record_failed_attempt(db, user_id="u", scope_id="g", purpose="p")
                await psec.ensure_rate_limit_or_raise(db, user_id="u", scope_id="g", purpose="p")  # nu ridica
            await psec.record_failed_attempt(db, user_id="u", scope_id="g", purpose="p")
            with self.assertRaises(psec.HTTPException):
                await psec.ensure_rate_limit_or_raise(db, user_id="u", scope_id="g", purpose="p")
            await psec.reset_attempts(db, user_id="u", scope_id="g", purpose="p")
            await psec.ensure_rate_limit_or_raise(db, user_id="u", scope_id="g", purpose="p")
        run(scenario())

    def test_expired_window_restarts_counter(self):
        async def scenario():
            db = FakeDB()
            old = datetime.now(timezone.utc) - timedelta(minutes=psec.ATTEMPT_WINDOW_MINUTES + 5)
            db.security_attempts.docs.append({
                "user_id": "u", "scope_id": "g", "purpose": "p", "count": 4,
                "window_start": old, "updated_at": old,
            })
            await psec.record_failed_attempt(db, user_id="u", scope_id="g", purpose="p")
            doc = await db.security_attempts.find_one({"user_id": "u"})
            self.assertEqual(doc["count"], 1)
        run(scenario())


# --------------------------------------------------------------------------------------
# BUG-003: 6-digit recovery code can be guessed in a parallel burst
# --------------------------------------------------------------------------------------
class TestRecoveryCodeBruteForce(unittest.TestCase):
    def _setup(self, db):
        async def go():
            tok = await psec.create_recovery_token(db, user_id="u", game_id="g", purpose="project_recover")
            return tok
        return go

    def test_parallel_wrong_guesses_are_capped(self):
        async def scenario():
            db = FakeDB()
            tok = await psec.create_recovery_token(db, user_id="u", game_id="g", purpose="project_recover")
            wrong = "000000" if tok.code != "000000" else "111111"

            async def guess():
                try:
                    await psec.verify_recovery_code(
                        db, token_id=tok.token_id, user_id="u", game_id="g", purpose="project_recover", code=wrong)
                except psec.HTTPException as exc:
                    return exc.status_code
                return 200

            results = await asyncio.gather(*[guess() for _ in range(50)])
            self.assertLessEqual(results.count(401), psec.MAX_CODE_ATTEMPTS,
                                 f"more than {psec.MAX_CODE_ATTEMPTS} guesses were evaluated: {results.count(401)}")
            self.assertEqual(results.count(429), 50 - results.count(401))
        run(scenario())

    def test_correct_code_still_works_and_wrong_one_fails(self):
        async def scenario():
            db = FakeDB()
            tok = await psec.create_recovery_token(db, user_id="u", game_id="g", purpose="project_recover")
            wrong = "000000" if tok.code != "000000" else "111111"
            with self.assertRaises(psec.HTTPException) as ctx:
                await psec.verify_recovery_code(db, token_id=tok.token_id, user_id="u", game_id="g",
                                                purpose="project_recover", code=wrong)
            self.assertEqual(ctx.exception.status_code, 401)
            doc = await psec.verify_recovery_code(db, token_id=tok.token_id, user_id="u", game_id="g",
                                                  purpose="project_recover", code=tok.code)
            self.assertEqual(doc["stage"], "email_verified")
        run(scenario())


if __name__ == "__main__":
    unittest.main()