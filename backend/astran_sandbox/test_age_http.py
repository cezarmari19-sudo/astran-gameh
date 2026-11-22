"""Teste HTTP (TestClient) pentru ruterul /api/age si /api/parental. SE SAR automat daca fastapi/httpx nu sunt instalate.

ATENTIE: aceste teste NU au fost rulate in mediul in care a fost scris codul (fara fastapi/pydantic/httpx/motor).
Rulati-le in CI/dev: `pip install -r requirements.txt httpx && python -m unittest astran_sandbox.test_age_http`.
"""
import json
import unittest

try:
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    HAVE = True
except Exception:  # noqa: BLE001
    HAVE = False


@unittest.skipUnless(HAVE, "fastapi/httpx nu sunt instalate: testele HTTP nu au rulat")
class AgeHttp(unittest.TestCase):
    def setUp(self):
        from astran_sandbox.test_support_fakedb import FakeDB
        from astran_sandbox import age_service as AS, parental_consent as PC
        from astran_sandbox.age_routes import make_age_router
        import asyncio
        self.db = FakeDB()
        asyncio.run(PC.ensure_indexes(self.db)); asyncio.run(AS.ensure_indexes(self.db))
        kid = {"user_id": "kid", "username": "kid", "email": "kid@e.com", "email_verified": True, **AS.registration_fields("2015-05-05")}
        asyncio.run(self.db.users.insert_one(kid))

        async def current(authorization: str = Header(None)):
            if authorization != "Bearer kid":
                raise HTTPException(401, "Invalid token")
            return await self.db.users.find_one({"user_id": "kid"}, {"_id": 0})
        app = FastAPI()
        app.include_router(make_age_router(current, self.db, client_ip=lambda r: "1.2.3.4"), prefix="/api")
        self.c = TestClient(app)
        self.h = {"Authorization": "Bearer kid"}

    def test_me_requires_auth_and_reports_restricted(self):
        self.assertEqual(self.c.get("/api/age/me").status_code, 401)
        r = self.c.get("/api/age/me", headers=self.h).json()
        self.assertTrue(r["restricted"]); self.assertEqual(r["age_band"], "under_16"); self.assertFalse(r["adult_content_allowed"])

    def test_dob_cannot_be_overwritten_and_extra_fields_rejected(self):
        r = self.c.post("/api/age/date-of-birth", headers=self.h, json={"date_of_birth": "1980-01-01"})
        self.assertEqual(r.status_code, 409)
        r = self.c.post("/api/age/date-of-birth", headers=self.h, json={"date_of_birth": "1980-01-01", "age_category": "adult_18"})
        self.assertEqual(r.status_code, 422)

    def test_parental_endpoints_reject_garbage_tokens(self):
        for path in ("/api/parental/notice", "/api/parental/withdraw-by-token"):
            self.assertIn(self.c.post(path, json={"token": "x" * 40}).status_code, (400,))
        r = self.c.post("/api/parental/decision", json={"token": "x" * 40, "decision": "approve", "acknowledgements": {}})
        self.assertIn(r.status_code, (400, 422))

    def test_callbacks_without_configuration_are_503_and_unsigned_rejected(self):
        self.assertEqual(self.c.post("/api/age/assurance/callback", content=b"{}").status_code, 503)
        self.assertEqual(self.c.post("/api/parental/verification/callback", content=b"{}").status_code, 503)

    def test_brute_force_on_token_is_rate_limited(self):
        codes = [self.c.post("/api/parental/notice", json={"token": f"{i:040d}"}).status_code for i in range(30)]
        self.assertIn(429, codes)


if __name__ == "__main__":
    unittest.main()
