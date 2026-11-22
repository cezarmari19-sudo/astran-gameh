"""Teste pentru sistemul de varsta / consimtamant parental / age assurance.

Rulare (din backend/):   python -m unittest astran_sandbox.test_age_system -v
Doar stdlib + `cryptography` + `PyJWT`; DB-ul e FakeDB in memorie (test_support_fakedb).

LIMITE (spuse deschis): nu ruleaza FastAPI/Pydantic/Mongo reale (nu sunt instalate in mediul de dezvoltare al acestei
sarcini). Atacurile "prin modificarea request-urilor HTTP" sunt exercitate la nivelul functiilor de serviciu pe care
endpoint-urile le apeleaza (payload-uri brute, campuri in plus, tokenuri falsificate/reutilizate/expirate), iar prezenta
gardurilor in handler-e e verificata STATIC (analiza codului sursa). Un test HTTP real e in test_age_http.py
(se sare automat daca fastapi nu e instalat).
"""
import asyncio
import json
import re
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jwt

from astran_sandbox import age_audit as AU
from astran_sandbox import age_guard as AG
from astran_sandbox import age_policy as AP
from astran_sandbox import age_service as AS
from astran_sandbox import attestation
from astran_sandbox import parental_consent as PC
from astran_sandbox.age_assurance import AssuranceError, NullProvider, SignedWebhookProvider
from astran_sandbox.parental_verification import NotConfiguredVerifier, SignedAttestationVerifier
from astran_sandbox.test_support_fakedb import FakeDB

BACKEND = Path(__file__).resolve().parent.parent


def setUpModule():
    """backend/legal/manifest.json refera legal/privacy_policy/1.0.{ro,en}.txt, dar fisierele NU exista in arhiva primita
    (serverul real ar refuza sa porneasca - fail-closed). Pentru teste folosim un director-fixture cu texte PLACEHOLDER
    si amprente recalculate; nu se modifica nimic in backend/legal."""
    import hashlib, os, shutil, tempfile
    from astran_sandbox import legal_docs as LD
    global _LEGAL_TMP, _OLD_ENV
    _LEGAL_TMP = tempfile.mkdtemp(prefix="legal_fixture_")
    src = BACKEND / "legal"
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    for doc, spec in manifest["documents"].items():
        for ver in spec["versions"]:
            for lang, f in ver["files"].items():
                dst = Path(_LEGAL_TMP) / f["path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                if (src / f["path"]).exists():
                    shutil.copy(src / f["path"], dst)
                else:
                    dst.write_text(f"PLACEHOLDER TEST TEXT {doc} {ver['version']} {lang}\n", encoding="utf-8")
                f["sha256"] = hashlib.sha256(dst.read_bytes()).hexdigest()   # si terms_of_service din arhiva nu se potrivea cu manifestul
    (Path(_LEGAL_TMP) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _OLD_ENV = os.environ.get("ASTRAN_LEGAL_DIR")
    os.environ["ASTRAN_LEGAL_DIR"] = _LEGAL_TMP
    LD._registry = None


def tearDownModule():
    import os, shutil
    from astran_sandbox import legal_docs as LD
    if _OLD_ENV is None:
        os.environ.pop("ASTRAN_LEGAL_DIR", None)
    else:
        os.environ["ASTRAN_LEGAL_DIR"] = _OLD_ENV
    LD._registry = None
    shutil.rmtree(_LEGAL_TMP, ignore_errors=True)
UTC = timezone.utc
NOW = datetime.now(UTC)
TODAY = NOW.date()


def years_ago(n: int, *, plus_days: int = 0) -> str:
    try:
        d = TODAY.replace(year=TODAY.year - n)
    except ValueError:  # 29 feb
        d = TODAY.replace(year=TODAY.year - n, day=28)
    return (d + timedelta(days=plus_days)).isoformat()


def run(coro):
    return asyncio.run(coro)


class Notifier:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    async def __call__(self, to, subject, body):
        self.sent.append((to, subject, body))
        return self.ok

    def last_token(self, purpose_path="/parental/decision"):
        for to, subj, body in reversed(self.sent):
            m = re.search(re.escape(purpose_path) + r"\?token=([A-Za-z0-9_\-]+)", body)
            if m:
                return m.group(1)
        return None


SECRET = "test-secret-123"
VERIFIER = SignedAttestationVerifier(SECRET, "https://verify.test/parent", "card_micro_charge")
PROVIDER = SignedWebhookProvider(SECRET, "https://age.test/start", "test-provider")
BASE = "https://app.test"


async def new_db():
    db = FakeDB()
    await PC.ensure_indexes(db)
    await AS.ensure_indexes(db)
    return db


async def add_user(db, uid, dob, *, email=None, verified=True, **extra):
    doc = {"user_id": uid, "username": uid, "display_name": uid, "email": email or f"{uid}@example.com",
           "email_verified": verified, "astrans_balance": 100, **AS.registration_fields(dob), **extra}
    await db.users.insert_one(doc)
    return await db.users.find_one({"user_id": uid}, {"_id": 0})


async def fresh(db, uid):
    return await db.users.find_one({"user_id": uid}, {"_id": 0})


def attest(payload: dict, t=None) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    return body, attestation.sign(SECRET, body, t)


# ============================================================================ 1-6: benzi de varsta
class AgeBands(unittest.TestCase):
    def check(self, years, band, plus_days=0):
        r = AP.evaluate_dob(years_ago(years, plus_days=plus_days), TODAY)
        self.assertEqual(r.band, band, (years, plus_days, r))
        return r

    def test_01_age_10(self):
        r = self.check(10, AP.BAND_UNDER_16)
        self.assertEqual(r.age, 10)

    def test_02_age_13(self):
        self.check(13, AP.BAND_UNDER_16)

    def test_03_age_15(self):
        self.check(15, AP.BAND_UNDER_16)
        self.check(16, AP.BAND_UNDER_16, plus_days=1)      # maine implineste 16: inca sub 16

    def test_04_just_turned_16(self):
        r = self.check(16, AP.BAND_16_17)
        self.assertEqual(r.age, 16)

    def test_05_age_17(self):
        self.check(17, AP.BAND_16_17)
        self.check(18, AP.BAND_16_17, plus_days=1)         # maine implineste 18: inca minor

    def test_06_just_turned_18_declared_is_still_not_adult_for_18plus_features(self):
        r = self.check(18, AP.BAND_ADULT)
        async def go():
            db = await new_db()
            u = await add_user(db, "u18", years_ago(18))
            self.assertEqual(AP.evaluate_access(u).band, AP.BAND_ADULT)
            self.assertEqual(u["age_category"], "under_18")                 # categoria legacy ramane under_18
            self.assertFalse(AP.can_access_adult_content(u))                # data declarata NU e verificare
        run(go())

    def test_leap_day_birthday(self):
        self.assertEqual(AP.compute_age(date(2008, 2, 29), date(2026, 2, 28)), 17)
        self.assertEqual(AP.compute_age(date(2008, 2, 29), date(2026, 3, 1)), 18)


# ============================================================================ 7-8: validare DOB
class DobValidation(unittest.TestCase):
    def err(self, raw):
        with self.assertRaises(AP.AgeError) as c:
            AP.parse_dob(raw, TODAY)
        return c.exception.code

    def test_07_future_date_rejected(self):
        self.assertEqual(self.err((TODAY + timedelta(days=1)).isoformat()), "date_of_birth_in_future")
        self.assertEqual(self.err("2999-01-01"), "date_of_birth_in_future")

    def test_08_invalid_dates_rejected(self):
        self.assertEqual(self.err("2010-02-30"), "date_of_birth_impossible")
        self.assertEqual(self.err("2010-13-01"), "date_of_birth_impossible")
        self.assertEqual(self.err("2010-00-10"), "date_of_birth_impossible")
        for bad in ("01/02/2010", "20100101", "2010-1-1", "2010-01-01T00:00:00", " 2010-01-01", "2010-01-01\n",
                    "２０１０-01-01", "abcd-ef-gh", 20100101, 2010.5, ["2010-01-01"], {"d": 1}, True):
            self.assertEqual(self.err(bad), "date_of_birth_invalid_format", repr(bad))
        self.assertEqual(self.err(None), "date_of_birth_required")
        self.assertEqual(self.err(""), "date_of_birth_required")
        self.assertEqual(self.err("1800-01-01"), "date_of_birth_implausible")

    def test_dob_is_encrypted_and_not_in_plain(self):
        f = AS.registration_fields(years_ago(14))
        self.assertTrue(f["dob_enc"].startswith("v1:"))
        self.assertNotIn(years_ago(14), json.dumps(f, default=str))
        self.assertEqual(AP.decrypt_dob(f["dob_enc"]).isoformat(), years_ago(14))
        self.assertIsNone(AP.decrypt_dob("v1:garbage"))
        self.assertIsNone(AP.decrypt_dob(None))


# ============================================================================ 9-10: falsificarea categoriei
class CategoryForgery(unittest.TestCase):
    def test_09_user_cannot_modify_age_category_via_profile_update(self):
        provided = {"display_name": "X", "age_category": "adult_18", "age_band": "18_plus", "age_locked": False,
                    "age_assurance": {"status": "verified"}, "dob_enc": "v1:x", "date_of_birth": "1990-01-01",
                    "parental": {"state": "PARENTAL_CONSENT_APPROVED"}, "age_info_required": False}
        clean, ignored = AS.strip_age_fields(provided)
        self.assertEqual(clean, {"display_name": "X"})
        self.assertEqual(set(ignored), set(provided) - {"display_name"})

    def test_10_declaring_adult_in_register_payload_has_no_effect(self):
        payload = {"email": "a@b.co", "age_category": "adult_18", "is_adult": True, "age_band": "18_plus",
                   "age_assurance": {"status": "verified"}, "date_of_birth": years_ago(10)}
        f = AS.registration_fields(payload.get("date_of_birth"))     # singurul camp de varsta citit
        self.assertEqual(f["age_category"], "under_18")
        self.assertEqual(f["age_band"], "under_16")
        self.assertEqual(f["age_assurance"], {"status": "none"})
        self.assertEqual(f["parental"]["state"], AP.PARENTAL_PENDING)

    def test_forged_age_category_field_in_db_doc_does_not_open_adult_access(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "x", years_ago(30))
            u["age_category"] = "adult_18"                       # chiar daca documentul ar fi falsificat
            self.assertFalse(AP.can_access_adult_content(u))
            u["age_assurance"] = {"status": "verified", "threshold": 18}
            self.assertTrue(AP.can_access_adult_content(u))
            u2 = await add_user(db, "y", years_ago(15))
            u2["age_assurance"] = {"status": "verified", "threshold": 18}     # verificat, dar data zice 15 ani
            self.assertFalse(AP.can_access_adult_content(u2))
            u3 = await add_user(db, "z", years_ago(17))
            u3["age_assurance"] = {"status": "verified", "threshold": 18}
            self.assertFalse(AP.can_access_adult_content(u3))
        run(go())

    def test_dob_cannot_be_changed_after_registration(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "c", years_ago(12))
            with self.assertRaises(AP.AgeError) as c:
                await AS.set_date_of_birth_once(db, u, years_ago(30))
            self.assertEqual(c.exception.code, "date_of_birth_locked")
            self.assertEqual(AP.evaluate_access(await fresh(db, "c")).band, AP.BAND_UNDER_16)
        run(go())


# ============================================================================ 11-15 + verificare parentala
class ParentalFlow(unittest.TestCase):
    async def setup_child(self, dob=None, verifier=VERIFIER):
        db = await new_db()
        child = await add_user(db, "kid", dob or years_ago(12), email="kid@example.com")
        n = Notifier()
        r = await PC.request_consent(db, child, "Mama@Example.com", lang="ro", notifier=n, app_base_url=BASE, now=NOW)
        return db, child, n, r

    def test_child_is_pending_and_restricted_until_approved(self):
        async def go():
            db, child, n, r = await self.setup_child()
            u = await fresh(db, "kid")
            info = AP.evaluate_access(u)
            self.assertEqual(r["state"], AP.PARENTAL_PENDING)
            self.assertEqual(u["parental"]["state"], AP.PARENTAL_PENDING)
            self.assertTrue(info.restricted)
            self.assertEqual(info.account_state, AP.ACCOUNT_PARENTAL_BLOCKED)
            self.assertFalse(AP.path_allowed_when_restricted("/api/games/discover"))
            self.assertFalse(AP.path_allowed_when_restricted("/api/dm/open"))
            self.assertTrue(AP.path_allowed_when_restricted("/api/age/me"))
            self.assertTrue(AP.path_allowed_when_restricted("/api/auth/me"))
        run(go())

    def test_email_explains_required_points_and_hides_nothing_secret(self):
        async def go():
            db, child, n, r = await self.setup_child()
            to, subj, body = n.sent[0]
            self.assertEqual(to, "mama@example.com")
            for needle in ("Astran Game", "data nașterii", "Scopuri", "funcțiile pentru minori", "autoriza sau refuza", "retrage"):
                self.assertIn(needle, body)
            self.assertIn("/parental/decision?token=", body)
            doc = await db.parental_consents.find_one({"consent_id": r["consent_id"]})
            self.assertNotIn("mama@example.com", json.dumps(doc, default=str))      # emailul e criptat in DB
            self.assertNotIn(n.last_token(), json.dumps(list(db.all_docs()), default=str))  # tokenul brut nu se stocheaza
        run(go())

    def test_11_parent_approves_requires_verification_then_approved(self):
        async def go():
            db, child, n, r = await self.setup_child()
            tok = n.last_token()
            peek = await PC.peek_notice(db, tok, "ro", now=NOW)
            self.assertEqual(peek["consent_id"], r["consent_id"])
            out = await PC.decide(db, tok, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            # ATENTIE: link-ul + declaratia NU inseamna aprobare
            self.assertEqual(out["state"], AP.PARENTAL_VERIFICATION_REQUIRED)
            self.assertTrue(AP.evaluate_access(await fresh(db, "kid")).restricted)
            vid = re.search(r"verification=([A-Za-z0-9_\-]+)", out["verification"]["redirect_url"]).group(1)
            body, sig = attest({"verification_id": vid, "consent_id": r["consent_id"], "parent_verified": True, "nonce": "nonce-12345678"})
            res = VERIFIER.parse_callback(body, sig)
            done = await PC.complete_verification(db, res, notifier=n, app_base_url=BASE, now=NOW)
            self.assertTrue(done["approved"])
            u = await fresh(db, "kid")
            self.assertEqual(u["parental"]["state"], AP.PARENTAL_APPROVED)
            self.assertFalse(AP.evaluate_access(u).restricted)
            c = await db.parental_consents.find_one({"consent_id": r["consent_id"]})
            for f in ("requested_at", "approved_at", "notice_version", "consent_id", "verification_method", "state", "notice_sha256"):
                self.assertTrue(c.get(f), f)
            self.assertEqual(c["verification_method"], "card_micro_charge")
            self.assertFalse(AP.can_access_adult_content(u))        # aprobarea parentala NU deschide 18+
        run(go())

    def test_approval_without_configured_verifier_never_becomes_approved(self):
        async def go():
            db, child, n, r = await self.setup_child()
            out = await PC.decide(db, n.last_token(), "approve", {k: True for k in PC.REQUIRED_ACKS},
                                  verifier=NotConfiguredVerifier(), now=NOW)
            self.assertEqual(out["state"], AP.PARENTAL_VERIFICATION_REQUIRED)
            self.assertIsNone(out["verification"])
            self.assertFalse(out["verification_configured"])
            c = await db.parental_consents.find_one({"consent_id": r["consent_id"]})
            self.assertEqual(c["verification_status"], "not_configured")
            self.assertIsNone(c["approved_at"])
            self.assertTrue(AP.evaluate_access(await fresh(db, "kid")).restricted)
        run(go())

    def test_approve_requires_explicit_acknowledgements(self):
        async def go():
            db, child, n, r = await self.setup_child()
            for acks in (None, {}, {"read_notice": True}, {k: "true" for k in PC.REQUIRED_ACKS}, {k: 1 for k in PC.REQUIRED_ACKS}):
                with self.assertRaises(PC.ParentalError) as c:
                    await PC.decide(db, n.last_token(), "approve", acks, verifier=VERIFIER, now=NOW)
                self.assertEqual(c.exception.code, "acknowledgement_required")
            # tokenul NU a fost consumat de incercarile esuate
            await PC.peek_notice(db, n.last_token(), "ro", now=NOW)
        run(go())

    def test_12_parent_rejects(self):
        async def go():
            db, child, n, r = await self.setup_child()
            out = await PC.decide(db, n.last_token(), "reject", None, verifier=VERIFIER, now=NOW)
            self.assertEqual(out["state"], AP.PARENTAL_REJECTED)
            u = await fresh(db, "kid")
            self.assertEqual(u["parental"]["state"], AP.PARENTAL_REJECTED)
            self.assertTrue(AP.evaluate_access(u).restricted)
            self.assertEqual(AP.evaluate_access(u).next_action, "parent_email")   # poate cere din nou, cu rate limit
        run(go())

    def test_13_withdrawal_after_approval(self):
        async def go():
            db, child, n, r = await self.setup_child()
            out = await PC.decide(db, n.last_token(), "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            vid = re.search(r"verification=([A-Za-z0-9_\-]+)", out["verification"]["redirect_url"]).group(1)
            body, sig = attest({"verification_id": vid, "consent_id": r["consent_id"], "parent_verified": True, "nonce": "nonce-abcdefgh"})
            await PC.complete_verification(db, VERIFIER.parse_callback(body, sig), notifier=n, app_base_url=BASE, now=NOW)
            wtok = n.last_token("/parental/withdraw")
            self.assertTrue(wtok)
            dropped = []
            async def on_withdrawn(uid): dropped.append(uid)
            res = await PC.withdraw_by_token(db, wtok, on_withdrawn=on_withdrawn, now=NOW + timedelta(days=2))
            self.assertEqual(res["state"], AP.PARENTAL_WITHDRAWN)
            self.assertEqual(dropped, ["kid"])
            u = await fresh(db, "kid")
            self.assertTrue(AP.evaluate_access(u).restricted)
            with self.assertRaises(PC.ParentalError) as c:                 # tokenul de retragere e single-use
                await PC.withdraw_by_token(db, wtok, now=NOW + timedelta(days=2))
            self.assertEqual(c.exception.code, "token_used")
            c = await db.parental_consents.find_one({"consent_id": r["consent_id"]})
            self.assertTrue(c["withdrawn_at"])
        run(go())

    def test_14_expired_token(self):
        async def go():
            db, child, n, r = await self.setup_child()
            late = NOW + PC.DECISION_TTL + timedelta(seconds=1)
            for fn in (lambda: PC.peek_notice(db, n.last_token(), "ro", now=late),
                       lambda: PC.decide(db, n.last_token(), "reject", None, verifier=VERIFIER, now=late)):
                with self.assertRaises(PC.ParentalError) as c:
                    await fn()
                self.assertEqual(c.exception.code, "token_expired")
            self.assertEqual((await fresh(db, "kid"))["parental"]["state"], AP.PARENTAL_PENDING)
        run(go())

    def test_15_reused_token_and_concurrent_race(self):
        async def go():
            db, child, n, r = await self.setup_child()
            tok = n.last_token()
            await PC.decide(db, tok, "reject", None, verifier=VERIFIER, now=NOW)
            with self.assertRaises(PC.ParentalError) as c:
                await PC.decide(db, tok, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            self.assertEqual(c.exception.code, "token_used")
            # cursa: doua decizii simultane cu acelasi token -> exact una reuseste
            db2, child2, n2, r2 = await self.setup_child()
            tok2 = n2.last_token()
            res = await asyncio.gather(
                PC.decide(db2, tok2, "reject", None, verifier=VERIFIER, now=NOW),
                PC.decide(db2, tok2, "reject", None, verifier=VERIFIER, now=NOW), return_exceptions=True)
            self.assertEqual(sum(1 for x in res if isinstance(x, dict)), 1)
            self.assertEqual(sum(1 for x in res if isinstance(x, PC.ParentalError)), 1)
        run(go())

    def test_token_attacks(self):
        async def go():
            db, child, n, r = await self.setup_child()
            tok = n.last_token()
            for bad in (None, "", "short", tok[:-1] + ("A" if tok[-1] != "A" else "B"), tok.upper(), "x" * 200, 12345, {"t": tok}):
                with self.assertRaises(PC.ParentalError) as c:
                    await PC.decide(db, bad, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
                self.assertEqual(c.exception.code, "token_invalid", repr(bad))
            with self.assertRaises(PC.ParentalError):       # tokenul de decizie nu poate fi folosit ca token de retragere
                await PC.withdraw_by_token(db, tok, now=NOW)
            out = await PC.decide(db, tok, "reject", None, verifier=VERIFIER, now=NOW)
            with self.assertRaises(PC.ParentalError):       # claim token != decision token
                await PC.decide(db, out.get("claim_token") or "y" * 40, "reject", None, verifier=VERIFIER, now=NOW)
            with self.assertRaises(PC.ParentalError):
                await PC.decide(db, tok, "maybe", None, verifier=VERIFIER, now=NOW)
        run(go())

    def test_parent_email_rules_and_rate_limits(self):
        async def go():
            db = await new_db()
            child = await add_user(db, "kid", years_ago(11), email="kid@example.com")
            for bad in (None, "", "no-at", "a@b", "x" * 400 + "@e.com", 123):
                with self.assertRaises(PC.ParentalError) as c:
                    await PC.request_consent(db, child, bad, notifier=Notifier(), app_base_url=BASE, now=NOW)
                self.assertEqual(c.exception.code, "parent_email_invalid", repr(bad))
            with self.assertRaises(PC.ParentalError) as c:
                await PC.request_consent(db, child, "KID@example.com", notifier=Notifier(), app_base_url=BASE, now=NOW)
            self.assertEqual(c.exception.code, "parent_email_same_as_child")
            n = Notifier()
            await PC.request_consent(db, child, "p@example.com", notifier=n, app_base_url=BASE, now=NOW)
            with self.assertRaises(PC.ParentalError) as c:        # cooldown 60s
                await PC.request_consent(db, child, "p@example.com", notifier=n, app_base_url=BASE, now=NOW + timedelta(seconds=5))
            self.assertEqual(c.exception.code, "too_many_requests")
            first = n.last_token()
            await PC.request_consent(db, child, "p@example.com", notifier=n, app_base_url=BASE, now=NOW + timedelta(minutes=2))
            with self.assertRaises(PC.ParentalError):             # cererea veche a fost inlocuita: tokenul vechi nu mai merge
                await PC.decide(db, first, "reject", None, verifier=VERIFIER, now=NOW + timedelta(minutes=3))
            await PC.request_consent(db, child, "p@example.com", notifier=n, app_base_url=BASE, now=NOW + timedelta(minutes=4))
            with self.assertRaises(PC.ParentalError) as c:        # max 3 / 24h / copil
                await PC.request_consent(db, child, "p@example.com", notifier=n, app_base_url=BASE, now=NOW + timedelta(minutes=6))
            self.assertEqual(c.exception.code, "too_many_requests")
        run(go())

    def test_delivery_failure_is_reported_not_hidden(self):
        async def go():
            db = await new_db()
            child = await add_user(db, "kid", years_ago(9))
            r = await PC.request_consent(db, child, "p@example.com", notifier=Notifier(ok=False), app_base_url=BASE, now=NOW)
            self.assertEqual(r["delivery"], "failed")
            self.assertEqual(r["state"], AP.PARENTAL_PENDING)
            r2 = await PC.request_consent(db, child, "p@example.com", notifier=None, app_base_url=None, now=NOW + timedelta(minutes=2))
            self.assertEqual(r2["delivery"], "not_configured")
        run(go())

    def test_request_only_for_under_16(self):
        async def go():
            db = await new_db()
            teen = await add_user(db, "teen", years_ago(16))
            with self.assertRaises(PC.ParentalError) as c:
                await PC.request_consent(db, teen, "p@example.com", notifier=Notifier(), app_base_url=BASE, now=NOW)
            self.assertEqual(c.exception.code, "parental_consent_not_required")
        run(go())


class VerificationCallbackAttacks(unittest.TestCase):
    async def pending(self):
        db = await new_db()
        await add_user(db, "kid", years_ago(10))
        child = await fresh(db, "kid")
        n = Notifier()
        r = await PC.request_consent(db, child, "p@example.com", notifier=n, app_base_url=BASE, now=NOW)
        out = await PC.decide(db, n.last_token(), "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
        vid = re.search(r"verification=([A-Za-z0-9_\-]+)", out["verification"]["redirect_url"]).group(1)
        return db, r["consent_id"], vid

    def test_bad_signature_stale_timestamp_and_non_boolean_rejected(self):
        async def go():
            db, cid, vid = await self.pending()
            good = {"verification_id": vid, "consent_id": cid, "parent_verified": True, "nonce": "nonce-12345678"}
            body = json.dumps(good).encode()
            from astran_sandbox.parental_verification import VerifierError
            for sig in (None, "", "t=1,v1=00", attestation.sign("wrong-secret", body), attestation.sign(SECRET, body, int(time.time()) - 3600)):
                with self.assertRaises(VerifierError):
                    VERIFIER.parse_callback(body, sig)
            tampered = json.dumps({**good, "consent_id": "pc_other"}).encode()
            with self.assertRaises(VerifierError):
                VERIFIER.parse_callback(tampered, attestation.sign(SECRET, body))   # semnatura de la alt corp
            for val in ("true", 1, "yes", None):
                b, s = attest({**good, "parent_verified": val})
                with self.assertRaises(VerifierError):
                    VERIFIER.parse_callback(b, s)
            with self.assertRaises(VerifierError):
                NotConfiguredVerifier().parse_callback(body, attestation.sign(SECRET, body))
            self.assertNotEqual((await db.parental_consents.find_one({"consent_id": cid}))["state"], AP.PARENTAL_APPROVED)
        run(go())

    def test_replay_and_wrong_consent_and_failed_verification(self):
        async def go():
            db, cid, vid = await self.pending()
            from astran_sandbox.parental_verification import VerifierError
            b, s = attest({"verification_id": vid, "consent_id": "pc_someoneelse", "parent_verified": True, "nonce": "nonce-aaaaaaaa"})
            with self.assertRaises(VerifierError):
                await PC.complete_verification(db, VERIFIER.parse_callback(b, s), now=NOW)
            b, s = attest({"verification_id": vid, "consent_id": cid, "parent_verified": False, "nonce": "nonce-bbbbbbbb"})
            out = await PC.complete_verification(db, VERIFIER.parse_callback(b, s), now=NOW)
            self.assertFalse(out["approved"])
            self.assertEqual((await db.parental_consents.find_one({"consent_id": cid}))["state"], AP.PARENTAL_VERIFICATION_REQUIRED)
            b, s = attest({"verification_id": vid, "consent_id": cid, "parent_verified": True, "nonce": "nonce-cccccccc"})
            with self.assertRaises(VerifierError):                 # sesiunea a fost deja consumata (replay)
                await PC.complete_verification(db, VERIFIER.parse_callback(b, s), now=NOW)
            self.assertNotEqual((await db.parental_consents.find_one({"consent_id": cid}))["state"], AP.PARENTAL_APPROVED)
        run(go())

    def test_expired_verification_session(self):
        async def go():
            db, cid, vid = await self.pending()
            from astran_sandbox.parental_verification import VerifierError
            b, s = attest({"verification_id": vid, "consent_id": cid, "parent_verified": True, "nonce": "nonce-dddddddd"})
            with self.assertRaises(VerifierError):
                await PC.complete_verification(db, VERIFIER.parse_callback(b, s), now=NOW + PC.VERIFICATION_TTL + timedelta(minutes=1))
        run(go())


# ============================================================================ panou parental: autorizare
class ParentalPanel(unittest.TestCase):
    async def world(self):
        db = await new_db()
        await add_user(db, "kid", years_ago(12), email="kid@example.com")
        await add_user(db, "mom", years_ago(40), email="mom@example.com", verified=True)
        await add_user(db, "evil", years_ago(40), email="evil@example.com")
        n = Notifier()
        r = await PC.request_consent(db, await fresh(db, "kid"), "mom@example.com", notifier=n, app_base_url=BASE, now=NOW)
        return db, n, r["consent_id"]

    def test_only_the_matching_verified_adult_account_can_act(self):
        async def go():
            db, n, cid = await self.world()
            evil, kid = await fresh(db, "evil"), await fresh(db, "kid")
            for who in (evil, kid):
                for call in (lambda w=who: PC.panel_withdraw(db, w, cid, now=NOW),
                             lambda w=who: PC.panel_decide(db, w, cid, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW),
                             lambda w=who: PC.create_data_request(db, w, cid, "access", now=NOW)):
                    with self.assertRaises(PC.ParentalError) as c:
                        await call()
                    self.assertIn(c.exception.code, ("consent_not_found", "parent_account_not_adult"))   # 404, nu 403
                try:
                    self.assertEqual((await PC.list_children(db, who, now=NOW))["pending_requests"], [])
                except PC.ParentalError as e:
                    self.assertEqual(e.code, "parent_account_not_adult")
            mom_unverified = {**(await fresh(db, "mom")), "email_verified": False}
            with self.assertRaises(PC.ParentalError) as c:
                await PC.panel_withdraw(db, mom_unverified, cid, now=NOW)
            self.assertEqual(c.exception.code, "parent_email_not_verified")
        run(go())

    def test_minor_account_with_parents_email_cannot_become_parent(self):
        async def go():
            db, n, cid = await self.world()
            await add_user(db, "sibling", years_ago(15), email="mom@example.com")
            with self.assertRaises(PC.ParentalError) as c:
                await PC.panel_decide(db, await fresh(db, "sibling"), cid, "reject", None, verifier=VERIFIER, now=NOW)
            self.assertEqual(c.exception.code, "parent_account_not_adult")
        run(go())

    def test_parent_lifecycle_in_panel(self):
        async def go():
            db, n, cid = await self.world()
            mom = await fresh(db, "mom")
            lst = await PC.list_children(db, mom, now=NOW)
            self.assertEqual([p["consent_id"] for p in lst["pending_requests"]], [cid])
            self.assertNotIn("email", json.dumps(lst, default=str).lower().replace("email_", ""))
            out = await PC.panel_decide(db, mom, cid, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            self.assertEqual(out["state"], AP.PARENTAL_VERIFICATION_REQUIRED)
            lst = await PC.list_children(db, mom, now=NOW)
            self.assertEqual(lst["pending_requests"], [])
            self.assertEqual(lst["children"][0]["state"], AP.PARENTAL_VERIFICATION_REQUIRED)
            self.assertEqual(lst["children"][0]["child"]["username"], "kid")
            # tokenul de decizie din email nu mai poate decide dupa decizia din panou
            with self.assertRaises(PC.ParentalError):
                await PC.decide(db, n.last_token(), "reject", None, verifier=VERIFIER, now=NOW)
            acc = await PC.create_data_request(db, mom, cid, "access", now=NOW)
            self.assertEqual(acc["status"], "fulfilled")
            self.assertIn("categories", acc["summary"])
            with self.assertRaises(PC.ParentalError) as c:
                await PC.create_data_request(db, mom, cid, "erasure", confirm=False, now=NOW)
            self.assertEqual(c.exception.code, "confirmation_required")
            er = await PC.create_data_request(db, mom, cid, "erasure", confirm=True, erase_child=None, now=NOW)
            self.assertEqual(er["status"], "pending_manual_review")      # fara verificare reala: NU se sterge automat
            w = await PC.panel_withdraw(db, mom, cid, now=NOW)
            self.assertEqual(w["state"], AP.PARENTAL_WITHDRAWN)
            self.assertEqual((await fresh(db, "kid"))["parental"]["state"], AP.PARENTAL_WITHDRAWN)
        run(go())

    def test_claim_link_needs_token_and_matching_verified_email(self):
        async def go():
            db, n, cid = await self.world()
            out = await PC.decide(db, n.last_token(), "reject", None, verifier=VERIFIER, now=NOW)
            # reject nu emite claim; pentru approve:
            db, n, cid = await self.world()
            out = await PC.decide(db, n.last_token(), "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            claim = out["claim_token"]
            with self.assertRaises(PC.ParentalError):
                await PC.claim_link(db, await fresh(db, "evil"), claim, now=NOW)
            with self.assertRaises(PC.ParentalError):
                await PC.claim_link(db, await fresh(db, "mom"), "z" * 43, now=NOW)
            res = await PC.claim_link(db, await fresh(db, "mom"), claim, now=NOW)
            self.assertEqual(res["consent_id"], cid)
            with self.assertRaises(PC.ParentalError):
                await PC.claim_link(db, await fresh(db, "mom"), claim, now=NOW)       # single-use
            self.assertEqual(len((await PC.list_children(db, await fresh(db, "mom"), now=NOW))["children"]), 1)
        run(go())

    def test_verified_erasure_triggers_deletion_hook(self):
        async def go():
            db, n, cid = await self.world()
            mom = await fresh(db, "mom")
            out = await PC.panel_decide(db, mom, cid, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            vid = re.search(r"verification=([A-Za-z0-9_\-]+)", out["verification"]["redirect_url"]).group(1)
            b, s = attest({"verification_id": vid, "consent_id": cid, "parent_verified": True, "nonce": "nonce-eeeeeeee"})
            await PC.complete_verification(db, VERIFIER.parse_callback(b, s), now=NOW)
            erased = []
            async def erase(uid): erased.append(uid)
            er = await PC.create_data_request(db, mom, cid, "erasure", confirm=True, erase_child=erase, now=NOW)
            self.assertEqual((er["status"], erased), ("erasure_started", ["kid"]))
        run(go())


# ============================================================================ 16-17 ani
class Teens(unittest.TestCase):
    def test_16_17_are_minors_for_adult_content_but_consent_themselves(self):
        async def go():
            db = await new_db()
            for uid, yrs in (("t16", 16), ("t17", 17)):
                u = await add_user(db, uid, years_ago(yrs))
                info = AP.evaluate_access(u)
                self.assertEqual(info.band, AP.BAND_16_17)
                self.assertFalse(info.restricted)                    # isi da singur consimtamantul
                self.assertEqual(info.age_category, "under_18")      # ramane in sistemul pentru minori
                self.assertFalse(AP.can_access_adult_content(u))
                self.assertFalse(AP.can_access_adult_content({**u, "age_assurance": {"status": "verified", "threshold": 18}}))
                with self.assertRaises(AssuranceError):              # nici nu poate porni age assurance 18+
                    await AS.start_assurance(db, u, PROVIDER, now=NOW)
            from astran_sandbox import consent as C
            self.assertTrue(C.purpose_eligible("marketing_communications", "under_18", AP.BAND_16_17))
            self.assertFalse(C.purpose_eligible("marketing_communications", "under_18", AP.BAND_UNDER_16))
            self.assertFalse(C.purpose_eligible("marketing_communications", "under_18", None))
        run(go())

    def test_turning_16_with_withdrawn_consent_needs_own_confirmation(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "k", years_ago(16))
            await db.users.update_one({"user_id": "k"}, {"$set": {"parental": {"state": AP.PARENTAL_WITHDRAWN, "consent_id": "pc_x", "self_consent_at": None}}})
            u = await fresh(db, "k")
            self.assertTrue(AP.evaluate_access(u).restricted)
            self.assertEqual(AP.evaluate_access(u).next_action, "confirm_own_consent")
            u2 = await AS.confirm_own_consent(db, u, now=NOW)
            self.assertFalse(AP.evaluate_access(u2).restricted)
            kid = await add_user(db, "small", years_ago(14))
            with self.assertRaises(AP.AgeError):
                await AS.confirm_own_consent(db, kid, now=NOW)
        run(go())


# ============================================================================ 16-19: acces 18+, chat, endpoint-uri
class AdultAccess(unittest.TestCase):
    def games(self):
        return {"adult": {"game_id": "g_a", "age_category": "adult_18", "owner_id": "o"},
                "all": {"game_id": "g_k", "age_category": "under_18", "owner_id": "o"},
                "missing": {"game_id": "g_m", "owner_id": "o"},
                "garbage": {"game_id": "g_g", "age_category": "ADULT_18 ", "owner_id": "o"}}

    def test_16_minor_cannot_access_18plus_game_by_any_id(self):
        async def go():
            db = await new_db()
            users = {"kid": await add_user(db, "kid", years_ago(10)), "t15": await add_user(db, "t15", years_ago(15)),
                     "t17": await add_user(db, "t17", years_ago(17)), "adult_declared": await add_user(db, "ad", years_ago(40)),
                     "legacy_no_dob": {"user_id": "l", "age_category": "adult_18"}, "legacy_missing_field": {"user_id": "m"}, "none": {}}
            for uname, u in users.items():
                for gname in ("adult", "missing", "garbage"):          # schimbarea ID-ului jocului nu ajuta
                    with self.assertRaises(AG.AdultAccessDenied, msg=f"{uname}->{gname}"):
                        AG.check_game_access(self.games()[gname], u)
                AG.check_game_access(self.games()["all"], u)           # jocul pentru toate varstele ramane accesibil
            with self.assertRaises(AG.AdultAccessDenied) as c:
                AG.check_game_access(self.games()["adult"], users["kid"])
            self.assertEqual(c.exception.code, "age_restricted")
            with self.assertRaises(AG.AdultAccessDenied) as c:
                AG.check_game_access(self.games()["adult"], users["adult_declared"])
            self.assertEqual(c.exception.code, "age_assurance_required")
            # owner neverificat: poate administra (authoring), dar nu poate juca/rula
            AG.check_game_access(self.games()["adult"], users["kid"], authoring=True, is_owner=True)
            with self.assertRaises(AG.AdultAccessDenied):
                AG.check_game_access(self.games()["adult"], users["kid"], authoring=False, is_owner=True)
            with self.assertRaises(AG.AdultAccessDenied):
                AG.check_game_access(self.games()["adult"], users["kid"], authoring=True, is_owner=False)
        run(go())

    def test_restricted_pending_account_never_passes_even_if_assured(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "p", years_ago(10))
            u["age_assurance"] = {"status": "verified", "threshold": 18}
            self.assertFalse(AP.can_access_adult_content(u))
        run(go())

    def test_17_chat_18plus_minor_never_gets_unfiltered_text(self):
        """Foloseste dispecerul REAL de chat (test_chat.setup); varsta vine din documentul creat de serviciul nostru."""
        from astran_sandbox import test_chat as TC

        async def go():
            db = await new_db()
            teen = await add_user(db, "teen", years_ago(17))
            declared_adult = await add_user(db, "dadult", years_ago(45))
            self.assertEqual(teen["age_category"], "under_18")
            self.assertEqual(declared_adult["age_category"], "under_18")
            # un adult REAL: doar dupa age assurance
            real = await add_user(db, "real", years_ago(45))
            res = AS.AssuranceResult("s" * 24, True, "test-provider", "eid", "nonce-12345678")
            await AS.apply_assurance_result(db, real, res, now=NOW)
            real = await fresh(db, "real")
            self.assertEqual(real["age_category"], "adult_18")
            ages = {"teen": teen["age_category"], "dadult": declared_adult["age_category"], "real": real["age_category"]}
            w, d, room, (a, b, c) = TC.setup(("real", ages["real"]), ("teen", ages["teen"]), ("dadult", ages["dadult"]))
            await d.handle_chat(room, a, {"text": "you are a badword"})
            self.assertEqual(b.ws.of("chat")[0]["text"], "you are a #######")     # minor 16-17: filtrat
            self.assertEqual(c.ws.of("chat")[0]["text"], "you are a #######")     # "adult" declarat: filtrat
            await d.handle_chat(room, b, {"text": "badword here", "age_category": "adult_18", "is_adult": True})
            self.assertNotIn("badword", json.dumps(a.ws.sent[-1]))                  # nici expeditorul minor nu trimite raw
        run(go())


# ============================================================================ assurance 18+
class AgeAssurance(unittest.TestCase):
    async def start(self, db, u):
        out = await AS.start_assurance(db, u, PROVIDER, now=NOW)
        return re.search(r"session=([A-Za-z0-9_\-]+)", out["redirect_url"]).group(1)

    def test_not_configured_keeps_access_blocked(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "a", years_ago(30))
            with self.assertRaises(AssuranceError) as c:
                await AS.start_assurance(db, u, NullProvider(), now=NOW)
            self.assertEqual((c.exception.code, c.exception.status), ("age_assurance_not_configured", 503))
            self.assertFalse(AP.can_access_adult_content(await fresh(db, "a")))
            self.assertEqual(AP.evaluate_access(await fresh(db, "a")).assurance_status, "none")
        run(go())

    def test_verified_flow_and_replay_and_forgery(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "a", years_ago(30))
            sid = await self.start(db, u)
            good = {"session_id": sid, "over_18": True, "method": "eid", "nonce": "nonce-12345678"}
            body, sig = attest(good)
            for bad_sig in (None, "", attestation.sign("other", body), attestation.sign(SECRET, body, int(time.time()) - 7200)):
                with self.assertRaises(AssuranceError):
                    await AS.handle_assurance_callback(db, PROVIDER, body, bad_sig)
            for val in ("true", 1, None):
                b, s = attest({**good, "over_18": val})
                with self.assertRaises(AssuranceError):
                    await AS.handle_assurance_callback(db, PROVIDER, b, s)
            self.assertFalse(AP.can_access_adult_content(await fresh(db, "a")))
            out = await AS.handle_assurance_callback(db, PROVIDER, body, sig)
            self.assertEqual(out["status"], "verified")
            u = await fresh(db, "a")
            self.assertTrue(AP.can_access_adult_content(u))
            self.assertEqual(u["age_category"], "adult_18")
            self.assertNotIn("dob", json.dumps(u["age_assurance"], default=str))           # doar rezultatul, nu date de identitate
            with self.assertRaises(AssuranceError):                                          # replay
                await AS.handle_assurance_callback(db, PROVIDER, body, sig)
            with self.assertRaises(AssuranceError):                                          # sesiune necunoscuta
                b, s = attest({**good, "session_id": "q" * 30})
                await AS.handle_assurance_callback(db, PROVIDER, b, s)
        run(go())

    def test_threshold_not_met_and_dob_conflict_and_expiry(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "a", years_ago(30))
            sid = await self.start(db, u)
            b, s = attest({"session_id": sid, "over_18": False, "method": "eid", "nonce": "nonce-12345678"})
            self.assertEqual((await AS.handle_assurance_callback(db, PROVIDER, b, s))["status"], "failed")
            self.assertFalse(AP.can_access_adult_content(await fresh(db, "a")))
            # expirare
            db2 = await new_db()
            u2 = await add_user(db2, "b", years_ago(30))
            sid2 = await self.start(db2, u2)
            b, s = attest({"session_id": sid2, "over_18": True, "method": "eid", "nonce": "nonce-12345678"})
            with self.assertRaises(AssuranceError):
                await AS.handle_assurance_callback(db2, PROVIDER, b, s, now=NOW + AS.ASSURANCE_SESSION_TTL + timedelta(minutes=1))
            self.assertFalse(AP.can_access_adult_content(await fresh(db2, "b")))
            # utilizator care a declarat sub 16 dar providerul zice 18+ => conflict, fara acces
            db3 = await new_db()
            k = await add_user(db3, "k", years_ago(14))
            res = AS.AssuranceResult("s" * 24, True, "p", "m", "nonce-12345678")
            self.assertEqual((await AS.apply_assurance_result(db3, k, res, now=NOW))["status"], "failed")
            self.assertFalse(AP.can_access_adult_content(await fresh(db3, "k")))
        run(go())


# ============================================================================ 19: token expirat
class ExpiredAuth(unittest.TestCase):
    def test_19_expired_jwt_is_rejected_with_the_server_decode_parameters(self):
        src = (BACKEND / "server.py").read_text(encoding="utf-8")
        secret = "s" * 32
        old = jwt.encode({"sub": "u", "iat": int(time.time()) - 900000, "exp": int(time.time()) - 60}, secret, algorithm="HS256")
        with self.assertRaises(jwt.ExpiredSignatureError):
            jwt.decode(old, secret, algorithms=["HS256"], leeway=2)
        self.assertIn("jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG], leeway=JWT_IAT_LEEWAY)", src)   # parametrii folositi de server
        self.assertRegex(src, r"except jwt\.PyJWTError:\s*\n\s*return None")                                 # expirat => None => 401
        self.assertRegex(src, r"if not user:\s*\n\s*raise HTTPException\(status_code=401")

    def test_19b_age_is_read_from_db_per_request_not_from_token(self):
        src = (BACKEND / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("age_category", re.search(r"def make_jwt.*?\n\n\n", src, re.S).group(0))


# ============================================================================ 20: cont existent fara DOB (migrare)
class LegacyMigration(unittest.TestCase):
    def test_20_existing_accounts_are_migrated_safely(self):
        async def go():
            db = await new_db()
            await db.users.insert_one({"user_id": "old_adult", "username": "oa", "age_category": "adult_18", "age_locked": True,
                                       "astrans_balance": 777, "password_hash": "x"})
            await db.users.insert_one({"user_id": "old_minor", "username": "om", "age_category": "under_18", "astrans_balance": 5})
            await db.users.insert_one({"user_id": "no_field", "username": "nf"})
            await db.users.insert_one({"user_id": "weird", "username": "w", "age_category": None})
            await db.users.insert_one({"user_id": "seed", "username": "s", "age_category": "adult_18",
                                       "age_assurance": {"status": "verified", "provider": "operator_seed", "threshold": 18}})
            await db.games.insert_one({"game_id": "g1", "owner_id": "old_adult"})
            before = len(db.users.docs)
            counts = await AS.migrate_existing_accounts(db, now=NOW)
            self.assertEqual(len(db.users.docs), before)                                    # nimic sters
            self.assertEqual(len(db.games.docs), 1)
            oa = await fresh(db, "old_adult")
            self.assertEqual((oa["age_category"], oa["legacy_declared_age_category"], oa["astrans_balance"]), ("under_18", "adult_18", 777))
            self.assertTrue(oa["age_info_required"])
            self.assertNotIn("dob_enc", oa)                                                 # NU inventam date de nastere
            self.assertFalse(AP.can_access_adult_content(oa))
            self.assertEqual(AP.evaluate_access(oa).next_action, "enter_date_of_birth")
            self.assertEqual((await fresh(db, "no_field"))["age_category"], "under_18")
            self.assertEqual((await fresh(db, "weird"))["age_category"], "under_18")
            self.assertEqual((await fresh(db, "seed"))["age_category"], "adult_18")        # adult cu assurance verificat ramane
            self.assertTrue(AP.can_access_adult_content(await fresh(db, "seed")))
            self.assertEqual(counts["adult_declarations_demoted"], 1)
            again = await AS.migrate_existing_accounts(db, now=NOW)                         # idempotent
            self.assertEqual(again, {"adult_declarations_demoted": 0, "unknown_category_fixed": 0, "age_info_required_set": 0})
            # fluxul de actualizare: DOB o singura data; adult declarat tot nu primeste 18+
            oa2 = await AS.set_date_of_birth_once(db, oa, years_ago(35), now=NOW)
            self.assertEqual(AP.evaluate_access(oa2).band, AP.BAND_ADULT)
            self.assertFalse(oa2["age_info_required"])
            self.assertFalse(AP.can_access_adult_content(oa2))
            with self.assertRaises(AP.AgeError):
                await AS.set_date_of_birth_once(db, oa2, years_ago(5), now=NOW)
            # legacy care se dovedeste a fi sub 16 => restrans pana la acord parental
            om = await AS.set_date_of_birth_once(db, await fresh(db, "old_minor"), years_ago(11), now=NOW)
            self.assertEqual(om["parental"]["state"], AP.PARENTAL_PENDING)
            self.assertTrue(AP.evaluate_access(om).restricted)
            with self.assertRaises(AP.AgeError):                                            # data viitoare / invalida si aici
                await AS.set_date_of_birth_once(db, await fresh(db, "no_field"), (TODAY + timedelta(days=3)).isoformat(), now=NOW)
        run(go())

    def test_legacy_unknown_age_is_fail_closed_but_not_hard_blocked_before_deadline(self):
        u = {"user_id": "l", "age_category": "under_18", "age_info_required": True}
        info = AP.evaluate_access(u)
        self.assertTrue(info.needs_age_info and not info.restricted and not info.adult_content_allowed)
        import os
        os.environ["ASTRAN_AGE_INFO_DEADLINE"] = (TODAY - timedelta(days=1)).isoformat()
        try:
            self.assertTrue(AP.evaluate_access(u).restricted)
        finally:
            del os.environ["ASTRAN_AGE_INFO_DEADLINE"]


# ============================================================================ audit
class Audit(unittest.TestCase):
    def test_chain_detects_tampering_and_holds_no_personal_data(self):
        async def go():
            db = await new_db()
            child = await add_user(db, "kid", years_ago(12), email="kid-secret@example.com")
            n = Notifier()
            await AS.audit_registration(db, "kid", "under_16", 12, "register", NOW)
            await PC.request_consent(db, child, "parent-secret@example.com", notifier=n, app_base_url=BASE, now=NOW)
            await PC.decide(db, n.last_token(), "reject", None, verifier=VERIFIER, now=NOW)
            await AU.record(db, AU.UNAUTHORIZED_ACCESS, subject_user_id="kid", details={"resource": "game", "email": "leak@x.com", "dob": "2010-01-01", "token": "abc"})
            rows = db.age_audit_log.docs
            self.assertGreaterEqual(len(rows), 5)
            blob = json.dumps(rows, default=str)
            for secret in ("kid-secret@example.com", "parent-secret@example.com", years_ago(12), "leak@x.com", "2010-01-01", n.last_token() or "zzzz", '"kid"'):
                self.assertNotIn(secret, blob)
            self.assertEqual((await AU.verify_chain(db))["ok"], True)
            events = [r["event"] for r in rows]
            for ev in (AU.DOB_REGISTERED, AU.AGE_BAND_COMPUTED, AU.PARENTAL_REQUESTED, AU.PARENTAL_REJECTED, AU.UNAUTHORIZED_ACCESS):
                self.assertIn(ev, events)
            rows[2]["event"] = "tampered"
            broken = await AU.verify_chain(db)
            self.assertFalse(broken["ok"])
            self.assertEqual(broken["broken_at"], 3)
        run(go())

    def test_audit_is_append_only_api(self):
        public = [n for n in dir(AU) if callable(getattr(AU, n)) and not n.startswith("_") and n.islower()]
        self.assertFalse([n for n in public if re.search(r"(update|delete|remove|purge|edit)", n)], public)

    def test_denied_access_is_throttled(self):
        async def go():
            db = await new_db()
            AG._last_logged.clear()
            u = {"user_id": "u1"}
            for _ in range(5):
                await AG.record_denied(db, u, "game_play", "g1")
            self.assertEqual(len(db.age_audit_log.docs), 1)
        run(go())


# ============================================================================ teste STATICE: gardurile din cod
SERVER = (BACKEND / "server.py").read_text(encoding="utf-8")
ROUTES = (BACKEND / "astran_sandbox" / "routes.py").read_text(encoding="utf-8")


def func_body(src: str, header_regex: str) -> str:
    m = re.search(header_regex, src)
    assert m, header_regex
    if header_regex.startswith("class "):
        nc = re.search(r"\n\n\n(?:class |[A-Za-z_#@])", src[m.end():])
        return src[m.start(): m.end() + (nc.start() if nc else len(src))]
    d = re.compile(r"(?:async )?def [A-Za-z_]+\(").search(src, m.start())
    nxt = re.search(r"\n(?:    )?@(?:api|router)\.|\n(?:async )?def [A-Za-z_]+\(|\nclass ", src[d.end():])
    return src[m.start(): d.end() + (nxt.start() if nxt else len(src))]


class StaticGuards(unittest.TestCase):
    def test_register_body_has_dob_and_no_client_age_category(self):
        body = func_body(SERVER, r"class RegisterBody\(BaseModel\):")
        self.assertIn("date_of_birth", body)
        self.assertNotIn("age_category", body)
        reg = func_body(SERVER, r'@api\.post\("/auth/register"\)')
        self.assertNotIn("body.age_category", reg)
        self.assertNotIn("class UpdateProfileBody(BaseModel):\n    display_name: Optional[str] = None\n    avatar_url: Optional[str] = None\n    age_category", SERVER)

    def test_no_fail_open_age_comparisons_remain(self):
        for name, src in (("server.py", SERVER), ("routes.py", ROUTES)):
            self.assertNotRegex(src, r'current\.get\("age_category"\)\s*==\s*"under_18"', name)
            self.assertNotRegex(src, r'\["age_category"\]\s*==\s*"adult_18"\s+and', name)

    def test_adult_literal_is_only_written_by_the_assurance_service_or_operator_seed(self):
        offenders = []
        for p in BACKEND.rglob("*.py"):
            if p.name.startswith("test_") or p.name in ("age_service.py", "age_policy.py"):
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'["\']age_category["\']\s*:\s*["\']adult_18["\']', line) and "operator_seed" not in line:
                    ctx = "\n".join(p.read_text(encoding="utf-8").splitlines()[max(0, i - 6): i + 6])
                    if "operator_seed" not in ctx and "Literal" not in line:
                        offenders.append(f"{p.name}:{i}")
        self.assertEqual(offenders, [])

    def test_every_game_content_endpoint_calls_the_adult_guard(self):
        checks = [
            (SERVER, r'@api\.get\("/games/\{game_id\}"\)\nasync def get_game'),
            (SERVER, r'@api\.get\("/games/\{game_id\}/player-character"\)'),
            (SERVER, r'@api\.post\("/games/\{game_id\}/play"\)'),
            (SERVER, r'@api\.get\("/games/\{game_id\}/instances"\)'),
            (SERVER, r'@api\.get\("/games/discover"\)'),
            (SERVER, r'@api\.get\("/games/recently-played"\)'),
            (ROUTES, r'@router\.post\("/games/\{game_id\}/run"\)'),
            (ROUTES, r'@router\.get\("/games/\{game_id\}/scene-models"\)'),
            (ROUTES, r'@router\.get\("/games/\{game_id\}/client-bundle"\)'),
            (ROUTES, r'@router\.get\("/games/\{game_id\}/files"\)'),
            (ROUTES, r'@router\.get\("/games/\{game_id\}/assets"\)'),
        ]
        for src, rx in checks:
            body = func_body(src, rx)
            self.assertTrue(re.search(r"_adult_guard|check_game_access|can_access_adult_content|_age_guard", body), rx)

    def test_guard_runs_before_content_is_loaded_in_client_bundle_and_run(self):
        for rx, loader in ((r'@router\.get\("/games/\{game_id\}/client-bundle"\)', "load_project"),
                           (r'@router\.post\("/games/\{game_id\}/run"\)', "load_project")):
            body = func_body(ROUTES, rx)
            self.assertLess(re.search(r"_age_guard|check_game_access", body).start(), body.index(loader), rx)

    def test_restricted_accounts_are_blocked_centrally_and_on_websockets(self):
        self.assertRegex(func_body(SERVER, r"async def get_current_user"), r"path_allowed_when_restricted")
        rt = (BACKEND / "realtime.py").read_text(encoding="utf-8")
        self.assertIn("restricted", rt)
        dm = (BACKEND / "astran_sandbox" / "dm_routes.py").read_text(encoding="utf-8")
        self.assertIn("restricted", dm)

    def test_startup_runs_the_migration_and_age_indexes(self):
        self.assertIn("migrate_existing_accounts", SERVER)
        self.assertIn("age_service.ensure_indexes", SERVER)
        self.assertIn("parental_consent.ensure_indexes", SERVER.replace("PC.ensure_indexes", "parental_consent.ensure_indexes"))

    def test_age_routes_are_mounted_and_have_no_audit_mutation_route(self):
        self.assertIn("make_age_router", SERVER)
        routes = (BACKEND / "astran_sandbox" / "age_routes.py").read_text(encoding="utf-8")
        self.assertNotRegex(routes, r"age_audit_log")
        self.assertNotRegex(routes, r'@router\.(put|delete|patch)\("/(audit|age_audit)')


if __name__ == "__main__":
    unittest.main()
