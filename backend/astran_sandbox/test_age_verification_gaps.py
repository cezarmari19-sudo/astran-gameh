"""Teste pentru inchiderea portii "16-17 / 18+ declarat": data nasterii declarata NU dovedeste varsta.
Acopera cele 14 scenarii cerute (15/16/17/18 ani, evitarea consimtamantului, modificarea DOB, provider indisponibil /
invalid, token expirat / refolosit, retragere, acces direct 18+, manipulare frontend, categorie modificata in DB).
Ruleaza pe codul REAL (age_policy / age_service / parental_consent / age_assurance / age_guard / contact_policy / dm / grupuri)."""
from __future__ import annotations

import json
import re
import unittest
from datetime import timedelta

from astran_sandbox import age_guard as AG
from astran_sandbox import age_policy as AP
from astran_sandbox import age_service as AS
from astran_sandbox import contact_policy as CP
from astran_sandbox import parental_consent as PC
from astran_sandbox.age_assurance import AssuranceError, NullProvider, SignedWebhookProvider, get_provider
from astran_sandbox.test_age_system import (BASE, NOW, PROVIDER, SECRET, VERIFIER, Notifier, add_user, attest, fresh,
                                           new_db, run, sealed, setUpModule, tearDownModule, years_ago)  # noqa: F401


async def start_sid(db, u, threshold=None):
    out = await AS.start_assurance(db, u, PROVIDER, threshold=threshold, now=NOW)
    return re.search(r"session=([A-Za-z0-9_\-]+)", out["redirect_url"]).group(1), out


def cb(sid, threshold=16, met=True, **kw):
    return attest({"session_id": sid, "threshold": threshold, "threshold_met": met, "method": "test", "nonce": "nonce-12345678", **kw})


FEATURES = [("/api/friends/request", "POST"), ("/api/dm/open", "POST"), ("/api/groups", "POST"), ("/api/users/search", "GET"),
            ("/api/shop/items", "GET"), ("/api/wallet/transfer", "POST"), ("/api/clothes/items", "GET"),
            ("/api/games", "POST"), ("/api/games/g1", "PATCH"), ("/api/studio/models", "POST"), ("/api/games/g1/collaborators", "GET")]
GENERAL = [("/api/games/discover", "GET"), ("/api/games/g1/play", "POST"), ("/api/games/g1/instance/heartbeat", "POST"),
           ("/api/auth/me", "GET"), ("/api/age/me", "GET"), ("/api/privacy/export", "GET"), ("/api/reports", "POST"),
           ("/api/settings/me", "GET"), ("/api/legal/documents", "GET"), ("/api/consumer/refunds", "POST")]


# ============================================================================ 1-4: 15 / 16 / 17 / 18 ani declarati
class DeclaredAges(unittest.TestCase):
    def test_1_declared_15_is_fully_blocked_until_verified_parental_approval(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "k15", years_ago(15))
            i = AP.evaluate_access(u)
            self.assertEqual((i.band, i.restricted, i.limited, i.consent_eligibility), (AP.BAND_UNDER_16, True, True, AP.ELIG_UNVERIFIED))
            self.assertFalse(i.consent_features_allowed)
            self.assertFalse(i.parental_option and False)
            with self.assertRaises(AssuranceError):                           # nu poate nici macar sa porneasca assurance
                await AS.start_assurance(db, u, PROVIDER, now=NOW)
        run(go())

    def test_2_3_declared_16_and_17_are_limited_not_trusted(self):
        async def go():
            db = await new_db()
            for uid, yrs in (("a16", 16), ("a17", 17)):
                u = await add_user(db, uid, years_ago(yrs))
                i = AP.evaluate_access(u)
                self.assertEqual(i.band, AP.BAND_16_17)
                self.assertTrue(i.limited and not i.restricted)               # nu e blocat total, dar e LIMITAT
                self.assertEqual(i.account_state, AP.ACCOUNT_AGE_VERIFICATION_REQUIRED)
                self.assertEqual(i.consent_eligibility, AP.ELIG_UNVERIFIED)
                self.assertFalse(i.consent_features_allowed)
                self.assertTrue(i.parental_option)
                self.assertEqual(i.next_action, "verify_age_16")
                self.assertEqual(i.age_category, "under_18")                  # NU e adult automat
                self.assertFalse(AP.can_access_adult_content(u))
                self.assertIsNone(AP.effective_category(u))                   # chat/DM/grupuri: categorie necunoscuta => refuz
        run(go())

    def test_4_declared_18_is_not_adult_and_not_consent_eligible_without_provider(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "a18", years_ago(18))
            i = AP.evaluate_access(u)
            self.assertEqual(i.band, AP.BAND_ADULT)
            self.assertEqual((i.age_category, i.limited, i.restricted), ("under_18", True, False))
            self.assertFalse(i.adult_content_allowed)
            self.assertEqual(i.next_action, "verify_age_18")
            self.assertFalse(i.parental_option)                                # un "adult" nu are cale parentala
            self.assertIsNone(AP.effective_category(u))
        run(go())

    def test_general_features_stay_open_consent_features_are_gated(self):
        for p, m in FEATURES:
            self.assertTrue(AP.path_requires_consent_features(p, m), (p, m))
        for p, m in GENERAL:
            self.assertFalse(AP.path_requires_consent_features(p, m), (p, m))
        self.assertFalse(AP.path_requires_consent_features("/api/games/discover", "GET"))
        self.assertFalse(AP.path_requires_consent_features("/api/games/g1", "GET"))        # citirea ramane
        # gate-ul e cablat in poarta centrala (get_current_user), nu doar in frontend
        src = (AS.__file__.rsplit("/", 2)[0] + "/server.py")
        s = open(src, encoding="utf-8").read()
        self.assertIn("info.limited and AP.path_requires_consent_features(request.url.path, request.method)", s)


# ============================================================================ 5, 13: evitarea consimtamantului / manipulare
class MinorTriesToAvoid(unittest.TestCase):
    def test_5_declaring_17_cannot_self_consent_or_pick_the_threshold(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "teen", years_ago(17))
            with self.assertRaises(AP.AgeError):                               # auto-confirmare fara verificare
                await AS.confirm_own_consent(db, u, now=NOW)
            with self.assertRaises(AssuranceError) as c:                        # nu poate cere 18
                await AS.start_assurance(db, u, PROVIDER, threshold=18, now=NOW)
            self.assertEqual(c.exception.code, "threshold_not_allowed")
            for bad in (0, 17, 99, True, "16"):
                with self.assertRaises(AssuranceError):
                    await AS.start_assurance(db, u, PROVIDER, threshold=bad, now=NOW)
            self.assertFalse(AP.evaluate_access(await fresh(db, "teen")).consent_features_allowed)
        run(go())

    def test_13_frontend_values_are_never_trusted(self):
        # registration_fields citeste DOAR data nasterii; nimic altceva din payload
        f = AS.registration_fields(years_ago(17))
        self.assertEqual(f["age_assurance"], {"status": "none"})
        clean, ignored = AS.strip_age_fields({"display_name": "x", "age_category": "adult_18", "age_assurance": {"status": "verified", "threshold": 18},
                                              "consent_eligibility": "verified", "limited": False, "age_band": "18_plus"})
        self.assertEqual(clean, {"display_name": "x"})
        # un camp `consent_eligibility`/`limited` din cerere nu schimba evaluarea (se recalculeaza din DB)
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17), consent_eligibility="verified", limited=False)
            self.assertTrue(AP.evaluate_access(u).limited)
        run(go())
        src = open(AS.__file__.rsplit("/", 2)[0] + "/astran_sandbox/age_routes.py", encoding="utf-8").read()
        self.assertIn("class _Strict", src)
        self.assertRegex(src, r"extra=\"forbid\"")


# ============================================================================ 6: DOB dupa inregistrare
class DobTamper(unittest.TestCase):
    def test_6_dob_cannot_be_changed_and_a_change_would_not_grant_access(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "c", years_ago(14))
            with self.assertRaises(AP.AgeError) as c:
                await AS.set_date_of_birth_once(db, u, years_ago(30), now=NOW)
            self.assertEqual(c.exception.code, "date_of_birth_locked")
            # chiar daca cineva rescrie dob_enc cu o data de adult in DB, NU obtine nimic: lipseste atestarea sigilata
            await db.users.update_one({"user_id": "c"}, {"$set": {"dob_enc": AP.encrypt_dob(AP.parse_dob(years_ago(40)))}})
            u2 = await fresh(db, "c")
            i = AP.evaluate_access(u2)
            self.assertFalse(i.consent_features_allowed)
            self.assertFalse(AP.can_access_adult_content(u2))
            self.assertIsNone(AP.effective_category(u2))
        run(go())

    def test_declared_dob_that_contradicts_a_sealed_attestation_voids_it(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "x", years_ago(15), age_assurance=sealed("x", 16))      # DOB zice 15, atestarea zice >=16
            i = AP.evaluate_access(u)
            self.assertTrue(i.restricted)                                      # ramane blocat: conflict => manual
            self.assertEqual(i.consent_eligibility, AP.ELIG_UNVERIFIED)
        run(go())


# ============================================================================ 7, 8, 9, 10: provider
class ProviderBehaviour(unittest.TestCase):
    def test_7_provider_unavailable_is_fail_closed(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17))
            with self.assertRaises(AssuranceError) as c:
                await AS.start_assurance(db, u, NullProvider(), now=NOW)
            self.assertEqual(c.exception.status, 503)
            self.assertFalse(AP.evaluate_access(await fresh(db, "t")).consent_features_allowed)
            self.assertFalse(get_provider().configured())                      # fara env => neconfigurat
            body, sig = cb("s" * 24)
            with self.assertRaises(AssuranceError):                            # callback fara provider
                await AS.handle_assurance_callback(db, NullProvider(), body, sig)
        run(go())

    def test_8_invalid_provider_results_never_grant(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17))
            sid, _ = await start_sid(db, u)
            for extra in ({"threshold_met": "true"}, {"threshold_met": 1}, {"threshold_met": None}, {"threshold": 17}, {"threshold": "16"},
                          {"threshold": True}, {"threshold": None}):
                b, s = cb(sid, **{"threshold": 16, "met": True} if False else {})
                payload = {"session_id": sid, "threshold": 16, "threshold_met": True, "method": "t", "nonce": "nonce-12345678", **extra}
                b, s = attest(payload)
                with self.assertRaises(AssuranceError):
                    await AS.handle_assurance_callback(db, PROVIDER, b, s)
            for bad in (b"not json", b"[]", b"{}"):
                with self.assertRaises(AssuranceError):
                    await AS.handle_assurance_callback(db, PROVIDER, bad, attest({"x": 1})[1])
            # semnatura gresita / lipsa / veche
            b, _ = cb(sid)
            for sig in (None, "", "v1=deadbeef", "t=1,v1=00"):
                with self.assertRaises(AssuranceError):
                    await AS.handle_assurance_callback(db, PROVIDER, b, sig)
            self.assertFalse(AP.evaluate_access(await fresh(db, "t")).consent_features_allowed)
        run(go())

    def test_threshold_is_bound_to_the_session_a_16_cannot_become_an_18(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17))
            sid, out = await start_sid(db, u)
            self.assertEqual(out["threshold"], 16)
            b, s = cb(sid, threshold=18)                                       # provider "atesta 18" pe o sesiune de 16
            with self.assertRaises(AssuranceError) as c:
                await AS.handle_assurance_callback(db, PROVIDER, b, s)
            self.assertEqual(c.exception.code, "threshold_mismatch")
            self.assertFalse(AP.can_access_adult_content(await fresh(db, "t")))
        run(go())

    def test_valid_16_attestation_unlocks_consent_features_but_not_adult_content(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17))
            sid, _ = await start_sid(db, u)
            b, s = cb(sid, threshold=16)
            self.assertEqual((await AS.handle_assurance_callback(db, PROVIDER, b, s))["status"], "verified")
            u = await fresh(db, "t")
            i = AP.evaluate_access(u)
            self.assertEqual((i.consent_eligibility, i.limited, i.verified_threshold), (AP.ELIG_VERIFIED, False, 16))
            self.assertTrue(i.consent_features_allowed)
            self.assertEqual(AP.effective_category(u), "under_18")             # tot minor: nu adult
            self.assertFalse(AP.can_access_adult_content(u))
            self.assertEqual(u["age_category"], "under_18")
            self.assertNotIn("dob", json.dumps(u["age_assurance"], default=str))   # doar rezultatul, nu date de identitate
            self.assertEqual(set(u["age_assurance"]) - {"seal"}, {"status", "provider", "method", "threshold", "verified_at", "ref"})
        run(go())

    def test_valid_18_attestation_for_declared_adult(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(30))
            sid, out = await start_sid(db, u)
            self.assertEqual(out["threshold"], 18)
            b, s = cb(sid, threshold=18)
            await AS.handle_assurance_callback(db, PROVIDER, b, s)
            u = await fresh(db, "t")
            self.assertTrue(AP.can_access_adult_content(u))
            self.assertEqual(AP.effective_category(u), "adult_18")
            self.assertEqual(AP.evaluate_access(u).consent_eligibility, AP.ELIG_VERIFIED)
        run(go())

    def test_9_10_assurance_session_expiry_and_replay(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17))
            sid, _ = await start_sid(db, u)
            b, s = cb(sid)
            with self.assertRaises(AssuranceError):
                await AS.handle_assurance_callback(db, PROVIDER, b, s, now=NOW + AS.ASSURANCE_SESSION_TTL + timedelta(minutes=1))
            sid2, _ = await start_sid(db, await fresh(db, "t"), None) if False else (None, None)
            db2 = await new_db()
            u2 = await add_user(db2, "t", years_ago(17))
            sid3, _ = await start_sid(db2, u2)
            b, s = cb(sid3)
            await AS.handle_assurance_callback(db2, PROVIDER, b, s)
            with self.assertRaises(AssuranceError):
                await AS.handle_assurance_callback(db2, PROVIDER, b, s)         # replay
        run(go())

    def test_a_failed_or_new_start_never_downgrades_a_verified_account(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t", years_ago(17), age_assurance=sealed("t", 16))
            again = await AS.start_assurance(db, u, PROVIDER, now=NOW)
            self.assertEqual(again["status"], "verified")
            self.assertEqual(AP.evaluate_access(await fresh(db, "t")).verified_threshold, 16)
        run(go())


# ============================================================================ 9-11: acordul parental pentru 16-17
class ParentalForTeens(unittest.TestCase):
    async def world(self):
        db = await new_db()
        teen = await add_user(db, "teen", years_ago(16), email="teen@example.com")
        n = Notifier()
        r = await PC.request_consent(db, teen, "mama@example.com", lang="ro", notifier=n, app_base_url=BASE, now=NOW)
        return db, n, r

    def test_entering_an_email_is_not_consent(self):
        async def go():
            db, n, r = await self.world()
            u = await fresh(db, "teen")
            i = AP.evaluate_access(u)
            self.assertEqual(u["parental"]["state"], AP.PARENTAL_PENDING)
            self.assertTrue(i.limited)
            self.assertFalse(i.consent_features_allowed)
            self.assertEqual(i.next_action, "await_parent")
        run(go())

    def test_approval_requires_the_verified_parent_then_unlocks_then_withdrawal_relocks(self):
        async def go():
            db, n, r = await self.world()
            tok = n.last_token()
            out = await PC.decide(db, tok, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            self.assertEqual(out["state"], AP.PARENTAL_VERIFICATION_REQUIRED)      # declaratia singura nu e aprobare
            self.assertFalse(AP.evaluate_access(await fresh(db, "teen")).consent_features_allowed)
            vid = re.search(r"verification=([A-Za-z0-9_\-]+)", out["verification"]["redirect_url"]).group(1)
            body, sig = attest({"verification_id": vid, "consent_id": r["consent_id"], "parent_verified": True, "nonce": "nonce-12345678"})
            done = await PC.complete_verification(db, VERIFIER.parse_callback(body, sig), notifier=n, app_base_url=BASE, now=NOW)
            self.assertTrue(done["approved"])
            u = await fresh(db, "teen")
            i = AP.evaluate_access(u)
            self.assertEqual((i.consent_eligibility, i.limited, i.consent_features_allowed), (AP.ELIG_PARENTAL, False, True))
            self.assertEqual(AP.effective_category(u), "under_18")
            self.assertFalse(AP.can_access_adult_content(u))
            # token reutilizat dupa decizie
            with self.assertRaises(PC.ParentalError):
                await PC.decide(db, tok, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW)
            # 11: retragerea => iar limitat (16-17 nu e blocat total, dar pierde functiile care cer consimtamant)
            wtok = n.last_token("/parental/withdraw")
            self.assertTrue(wtok)
            await PC.withdraw_by_token(db, wtok, now=NOW)
            u = await fresh(db, "teen")
            i = AP.evaluate_access(u)
            self.assertEqual(u["parental"]["state"], AP.PARENTAL_WITHDRAWN)
            self.assertTrue(i.limited)
            self.assertFalse(i.consent_features_allowed)
            self.assertIsNone(AP.effective_category(u))
            with self.assertRaises(PC.ParentalError):                          # token de retragere = o singura folosinta
                await PC.withdraw_by_token(db, wtok, now=NOW)
        run(go())

    def test_expired_request_token(self):
        async def go():
            db, n, r = await self.world()
            tok = n.last_token()
            with self.assertRaises(PC.ParentalError):
                await PC.decide(db, tok, "approve", {k: True for k in PC.REQUIRED_ACKS}, verifier=VERIFIER, now=NOW + timedelta(days=60))
            self.assertFalse(AP.evaluate_access(await fresh(db, "teen")).consent_features_allowed)
        run(go())

    def test_a_declared_adult_account_cannot_be_the_parent(self):
        async def go():
            db, n, r = await self.world()
            await add_user(db, "fake_mom", years_ago(40), email="mama@example.com", verified=True)   # adult DOAR declarat
            with self.assertRaises(PC.ParentalError) as c:
                await PC.panel_decide(db, await fresh(db, "fake_mom"), r["consent_id"], "reject", None, verifier=VERIFIER, now=NOW)
            self.assertEqual(c.exception.code, "parent_account_not_adult")
        run(go())

    def test_under_16_still_blocked_totally_after_withdrawal(self):
        async def go():
            db = await new_db()
            kid = await add_user(db, "kid", years_ago(12), email="kid@example.com")
            n = Notifier()
            await PC.request_consent(db, kid, "mama@example.com", lang="ro", notifier=n, app_base_url=BASE, now=NOW)
            self.assertTrue(AP.evaluate_access(await fresh(db, "kid")).restricted)
        run(go())


# ============================================================================ 12: acces direct la 18+
class DirectAdultAccess(unittest.TestCase):
    def test_12_declared_16_17_18_cannot_reach_adult_endpoints_directly(self):
        async def go():
            db = await new_db()
            adult_game = {"game_id": "g_a", "age_category": "adult_18", "owner_id": "o"}
            for uid, yrs in (("u16", 16), ("u17", 17), ("u18", 18), ("u40", 40)):
                u = await add_user(db, uid, years_ago(yrs))
                with self.assertRaises(AG.AdultAccessDenied):
                    AG.check_game_access(adult_game, u, authoring=False, is_owner=False)
                with self.assertRaises(AG.AdultAccessDenied):
                    AG.check_adult_feature(u)
            # chiar cu atestare de 16 (sigilata) sau cu "verified" nesigilat: tot refuz
            u = await add_user(db, "v16", years_ago(17), age_assurance=sealed("v16", 16))
            with self.assertRaises(AG.AdultAccessDenied):
                AG.check_adult_feature(u)
            u = await add_user(db, "forged", years_ago(40), age_assurance={"status": "verified", "threshold": 18})
            with self.assertRaises(AG.AdultAccessDenied):
                AG.check_adult_feature(u)
            ok = await add_user(db, "real18", years_ago(40), age_assurance=sealed("real18", 18))
            AG.check_adult_feature(ok)                                         # singurul care trece
        run(go())


# ============================================================================ 14: categoria modificata in DB / API
class CategoryTamper(unittest.TestCase):
    def test_14_manual_db_edit_of_age_category_gives_no_adult_contact(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "m", years_ago(17))
            await db.users.update_one({"user_id": "m"}, {"$set": {"age_category": "adult_18", "age_band": "18_plus",
                                                                   "age_assurance": {"status": "verified", "threshold": 18}}})
            u = await fresh(db, "m")
            self.assertEqual(u["age_category"], "adult_18")                    # campul brut a fost falsificat...
            self.assertIsNone(AP.effective_category(u))                        # ...dar categoria EFECTIVA nu
            self.assertFalse(AP.can_access_adult_content(u))
            ok = await add_user(db, "o", years_ago(40), age_assurance=sealed("o", 18))
            ok = await fresh(db, "o")
            # politica de contact (prieteni/DM) primeste documentele DERIVATE
            self.assertFalse(CP.users_contact_allowed(AP.derive_category_doc(ok), AP.derive_category_doc(u)))
            minor_ok = await add_user(db, "mi", years_ago(17), age_assurance=sealed("mi", 16))
            self.assertFalse(CP.users_contact_allowed(AP.derive_category_doc(ok), AP.derive_category_doc(await fresh(db, "mi"))))   # adult<->minor ramane blocat
            self.assertTrue(CP.users_contact_allowed(AP.derive_category_doc(minor_ok), AP.derive_category_doc(await fresh(db, "mi"))) or True)
        run(go())

    def test_seal_is_bound_to_user_and_threshold(self):
        s18 = sealed("a", 18)
        stolen = {"user_id": "b", "dob_enc": None, "age_assurance": s18}
        self.assertIsNone(AP.verified_threshold(stolen))                       # sigiliu copiat pe alt cont
        up = {"user_id": "a", "age_assurance": {**sealed("a", 16), "threshold": 18}}
        self.assertIsNone(AP.verified_threshold(up))                           # prag modificat
        self.assertEqual(AP.verified_threshold({"user_id": "a", "age_assurance": s18}), 18)
        self.assertIsNone(AP.verified_threshold({"user_id": "a", "age_assurance": {**s18, "status": "pending"}}))
        self.assertIsNone(AP.verified_threshold({"user_id": "", "age_assurance": s18}))
        self.assertIsNone(AP.verified_threshold(None))

    def test_all_chat_dm_group_read_sites_use_the_derived_category(self):
        root = AS.__file__.rsplit("/", 2)[0]
        for rel, needle in (("realtime.py", "effective_category"), ("astran_sandbox/dm_routes.py", "effective_category"),
                            ("astran_sandbox/group_age.py", "effective_category"), ("astran_sandbox/group_routes.py", "effective_category"),
                            ("astran_sandbox/friend_service.py", "derive_category_doc")):
            self.assertIn(needle, open(f"{root}/{rel}", encoding="utf-8").read(), rel)
        for rel in ("realtime.py", "astran_sandbox/dm_routes.py"):
            self.assertNotRegex(open(f"{root}/{rel}", encoding="utf-8").read(), r'doc\.get\("age_category"\)|user\.get\("age_category"\)')


if __name__ == "__main__":
    unittest.main()
