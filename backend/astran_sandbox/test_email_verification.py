"""python -m unittest astran_sandbox.test_email_verification"""
import unittest
from datetime import datetime, timedelta, timezone

from astran_sandbox.test_money import FakeColl, run  # stub pymongo; mini-DB
from astran_sandbox import email_verification as ev


class _DB:
    def __init__(self):
        self.c = FakeColl()

    def __getitem__(self, name):
        return self.c


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class EmailVerificationTests(unittest.TestCase):
    def test_happy_path_and_single_use(self):
        async def go():
            db = _DB()
            code = await ev.issue(db, "u1", "A@x.com", now=T0)
            self.assertEqual(len(code), 6)
            self.assertNotIn(code, str(db.c.docs))               # nu se stocheaza in clar
            await ev.verify(db, "u1", "a@x.com", code, now=T0 + timedelta(minutes=1))
            with self.assertRaises(ev.VerificationError):         # un singur folosinta
                await ev.verify(db, "u1", "a@x.com", code, now=T0 + timedelta(minutes=1))
        run(go())

    def test_wrong_code_attempt_limit_blocks_even_correct_code(self):
        async def go():
            db = _DB()
            code = await ev.issue(db, "u1", "a@x.com", now=T0)
            wrong = "000000" if code != "000000" else "111111"
            for _ in range(ev.MAX_ATTEMPTS):
                with self.assertRaises(ev.VerificationError) as c:
                    await ev.verify(db, "u1", "a@x.com", wrong, now=T0)
                self.assertEqual(c.exception.status, 400)
            with self.assertRaises(ev.VerificationError) as c:   # a 6-a incercare, chiar cu codul bun
                await ev.verify(db, "u1", "a@x.com", code, now=T0)
            self.assertEqual(c.exception.status, 429)
        run(go())

    def test_expiry_cooldown_and_email_binding(self):
        async def go():
            db = _DB()
            code = await ev.issue(db, "u1", "a@x.com", now=T0)
            with self.assertRaises(ev.VerificationError) as c:    # retrimitere prea devreme
                await ev.issue(db, "u1", "a@x.com", now=T0 + timedelta(seconds=10))
            self.assertEqual(c.exception.status, 429)
            self.assertGreaterEqual(c.exception.retry_after, 1)
            with self.assertRaises(ev.VerificationError):         # alt email decat cel pentru care s-a emis
                await ev.verify(db, "u1", "other@x.com", code, now=T0)
            with self.assertRaises(ev.VerificationError):         # expirat
                await ev.verify(db, "u1", "a@x.com", code, now=T0 + timedelta(minutes=ev.CODE_TTL_MINUTES + 1))
            code2 = await ev.issue(db, "u1", "a@x.com", now=T0 + timedelta(minutes=30))   # dupa cooldown e ok
            await ev.verify(db, "u1", "a@x.com", code2, now=T0 + timedelta(minutes=31))
        run(go())

    def test_code_of_other_user_does_not_work(self):
        async def go():
            db = _DB()
            c1 = await ev.issue(db, "u1", "a@x.com", now=T0)
            await ev.issue(db, "u2", "b@x.com", now=T0)
            # FakeColl are o singura colectie: u2 are propriul document; codul lui u1 nu trece la u2
            with self.assertRaises(ev.VerificationError):
                await ev.verify(db, "u2", "b@x.com", c1, now=T0)
        run(go())


if __name__ == "__main__":
    unittest.main()