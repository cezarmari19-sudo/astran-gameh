"""Teste pentru serviciul central de entitlement-uri (age_entitlement_service) pe codul REAL:
matricea pe categorii, scopuri parentale separate, retragere => revocare automata, acces direct la endpoint-uri,
transferuri Wallet, manipularea `age_category`, jocuri 18+, contact intre categorii incompatibile.
(Webhook invalid / duplicat / expirat, verificare esuata: vezi test_age_verification_gaps + test_age_system.)"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from astran_sandbox import age_entitlement_service as ENT
from astran_sandbox import age_guard as AG
from astran_sandbox import age_policy as AP
from astran_sandbox import age_service as AS
from astran_sandbox import contact_policy as CP
from astran_sandbox import parental_consent as PC
from astran_sandbox.test_age_system import (BASE, NOW, PROVIDER, VERIFIER, Notifier, add_user, attest, fresh, new_db,  # noqa: F401
                                           run, sealed, setUpModule, tearDownModule, years_ago)

BACKEND = Path(__file__).resolve().parent.parent
ALL_ACKS = {k: True for k in PC.REQUIRED_ACKS}


def allowed(user, feature):
    return ENT.evaluate(user, feature).allowed


async def approved_child(db, uid="kid", age=12):
    """Copil cu acord parental APROBAT (parinte adult dovedit + verificare externa), fara scopuri."""
    await add_user(db, uid, years_ago(age), email=f"{uid}@example.com")
    if not await db.users.find_one({"user_id": "mom"}):
        await add_user(db, "mom", years_ago(40), email="mom@example.com", age_assurance=sealed("mom", 18), age_category="adult_18")
    n = Notifier()
    r = await PC.request_consent(db, await fresh(db, uid), "mom@example.com", notifier=n, app_base_url=BASE, now=NOW)
    out = await PC.panel_decide(db, await fresh(db, "mom"), r["consent_id"], "approve", ALL_ACKS, verifier=VERIFIER, now=NOW)
    vid = re.search(r"verification=([A-Za-z0-9_\-]+)", out["verification"]["redirect_url"]).group(1)
    body, sig = attest({"verification_id": vid, "consent_id": r["consent_id"], "parent_verified": True, "nonce": "nonce-12345678"})
    await PC.complete_verification(db, VERIFIER.parse_callback(body, sig), notifier=n, app_base_url=BASE, now=NOW)
    return r["consent_id"], n


def verified_teen(uid="teen", age=17):
    return {"user_id": uid, **AS.registration_fields(years_ago(age)), "age_assurance": sealed(uid, 16)}


def adult(uid="adult"):
    return {"user_id": uid, **AS.registration_fields(years_ago(30)), "age_category": "adult_18", "age_assurance": sealed(uid, 18)}


def declared(age, uid="d"):
    return {"user_id": uid, **AS.registration_fields(years_ago(age))}


class MatrixPerCategory(unittest.TestCase):
    def test_under_16_without_consent_is_fully_restricted_by_default(self):
        u = declared(12)
        for f in (ENT.F_FRIENDS, ENT.F_DM, ENT.F_WALLET_VIEW, ENT.F_PUBLISH, ENT.F_ADULT, ENT.F_REAL_MONEY, ENT.F_PLAY):
            self.assertFalse(allowed(u, f), f)       # blocare totala = decizie juridica (ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL)
        self.assertEqual(ENT.evaluate(u, ENT.F_FRIENDS).reason, ENT.R_PARENT_REQUIRED)
        self.assertEqual(ENT.ui_status(u), "parental_consent_required")

    def test_under_16_flag_off_gets_basic_features_only_never_social_or_commercial(self):
        import os
        os.environ["ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL"] = "0"
        try:
            u = declared(12)
            self.assertTrue(allowed(u, ENT.F_PLAY))
            for f in (ENT.F_FRIENDS, ENT.F_DM, ENT.F_GROUPS, ENT.F_WALLET_VIEW, ENT.F_SHOP_PURCHASE, ENT.F_REAL_MONEY,
                      ENT.F_PUBLISH, ENT.F_TRANSFER, ENT.F_MONETIZE, ENT.F_ADULT):
                self.assertFalse(allowed(u, f), f)
        finally:
            os.environ.pop("ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL", None)

    def test_under_16_with_parental_consent_gets_base_features_and_needs_scopes_for_the_rest(self):
        async def go():
            db = await new_db()
            await approved_child(db)
            u = await fresh(db, "kid")
            for f in (ENT.F_PLAY, ENT.F_BLOCK, ENT.F_WALLET_VIEW, ENT.F_SHOP_BROWSE):
                self.assertTrue(allowed(u, f), f)
            for f, scope in ((ENT.F_FRIENDS, "social"), (ENT.F_DM, "messaging"), (ENT.F_GROUPS, "groups"), (ENT.F_SHOP_PURCHASE, "purchases"),
                             (ENT.F_REAL_MONEY, "purchases"), (ENT.F_PUBLISH, "publish")):
                d = ENT.evaluate(u, f)
                self.assertEqual((d.allowed, d.reason, d.scope), (False, ENT.R_SCOPE, scope), f)
            for f in (ENT.F_TRANSFER, ENT.F_MONETIZE, ENT.F_ADULT):         # nu se deblocheaza prin niciun scop
                self.assertFalse(allowed(u, f), f)
        run(go())

    def test_16_17_unverified_is_limited_with_clear_reason(self):
        for age in (16, 17):
            u = declared(age)
            self.assertFalse(allowed(u, ENT.F_FRIENDS))
            self.assertEqual(ENT.evaluate(u, ENT.F_FRIENDS).reason, ENT.R_VERIFY_AGE)
            self.assertTrue(allowed(u, ENT.F_PLAY))
            self.assertTrue(allowed(u, ENT.F_BLOCK))
            self.assertEqual(ENT.ui_status(u), "not_verified")

    def test_16_17_verified_gets_social_publish_shop_but_not_real_money_transfer_monetize_adult(self):
        u = verified_teen()
        for f in (ENT.F_FRIENDS, ENT.F_DM, ENT.F_GROUPS, ENT.F_WALLET_VIEW, ENT.F_SHOP_PURCHASE, ENT.F_PUBLISH):
            self.assertTrue(allowed(u, f), f)
        self.assertFalse(allowed(u, ENT.F_REAL_MONEY))
        self.assertFalse(allowed(u, ENT.F_TRANSFER))
        self.assertFalse(allowed(u, ENT.F_MONETIZE))
        self.assertFalse(allowed(u, ENT.F_ADULT))
        self.assertEqual(ENT.evaluate(u, ENT.F_ADULT).reason, ENT.R_ADULT_ONLY)

    def test_real_money_for_verified_minor_is_a_legal_switch(self):
        import os
        os.environ["ASTRAN_MINOR_REAL_MONEY_REQUIRES_PARENT"] = "0"
        try:
            self.assertTrue(allowed(verified_teen(), ENT.F_REAL_MONEY))
        finally:
            os.environ.pop("ASTRAN_MINOR_REAL_MONEY_REQUIRES_PARENT", None)

    def test_adult_verified_gets_everything(self):
        u = adult()
        for f in ENT.ALL_FEATURES:
            self.assertTrue(allowed(u, f), f)
        self.assertEqual(ENT.ui_status(u), "verified")

    def test_adult_unverified_is_not_adult(self):
        u = declared(30)
        for f in (ENT.F_ADULT, ENT.F_MONETIZE, ENT.F_TRANSFER, ENT.F_FRIENDS, ENT.F_REAL_MONEY):
            self.assertFalse(allowed(u, f), f)
        self.assertEqual(ENT.evaluate(u, ENT.F_ADULT).reason, ENT.R_VERIFY_AGE)
        self.assertEqual(ENT.evaluate(u, ENT.F_ADULT).next_action, "verify_age_18")

    def test_unknown_feature_or_user_is_denied(self):
        self.assertFalse(ENT.evaluate(adult(), "nonexistent").allowed)
        self.assertFalse(ENT.evaluate(None, ENT.F_PLAY).allowed)
        self.assertFalse(ENT.evaluate(adult(), ENT.F_FRIENDS, suspended=True).allowed)

    def test_failed_and_pending_verification_never_grant(self):
        u = {**declared(30), "age_assurance": {"status": AP.ASSURANCE_FAILED, "threshold": 18}}
        self.assertFalse(allowed(u, ENT.F_ADULT))
        self.assertEqual(ENT.ui_status(u), "verification_failed")
        u = {**declared(30), "age_assurance": {"status": AP.ASSURANCE_PENDING, "threshold": 18}}
        self.assertFalse(allowed(u, ENT.F_ADULT))
        self.assertEqual(ENT.ui_status(u), "verification_pending")

    def test_unsealed_verified_attestation_in_db_gives_nothing(self):
        u = {**declared(30), "age_category": "adult_18",
             "age_assurance": {"status": "verified", "threshold": 18, "seal": "forged"}}
        self.assertFalse(allowed(u, ENT.F_ADULT))
        self.assertFalse(allowed(u, ENT.F_TRANSFER))


class ParentalScopes(unittest.TestCase):
    def test_scope_grant_then_feature_then_revoke(self):
        async def go():
            db = await new_db()
            cid, _ = await approved_child(db)
            mom = await fresh(db, "mom")
            self.assertFalse(allowed(await fresh(db, "kid"), ENT.F_DM))
            out = await PC.set_scope(db, mom, cid, "messaging", True, now=NOW)
            self.assertEqual(out["scopes"], ["messaging"])
            kid = await fresh(db, "kid")
            self.assertTrue(allowed(kid, ENT.F_DM))
            self.assertFalse(allowed(kid, ENT.F_GROUPS))                    # scopurile sunt SEPARATE
            await PC.set_scope(db, mom, cid, "messaging", False, now=NOW)
            self.assertFalse(allowed(await fresh(db, "kid"), ENT.F_DM))
        run(go())

    def test_withdrawing_consent_revokes_every_scope_automatically(self):
        async def go():
            db = await new_db()
            cid, _ = await approved_child(db)
            mom = await fresh(db, "mom")
            for s in ("social", "messaging", "groups", "purchases", "publish"):
                await PC.set_scope(db, mom, cid, s, True, now=NOW)
            kid = await fresh(db, "kid")
            for f in (ENT.F_DM, ENT.F_GROUPS, ENT.F_REAL_MONEY, ENT.F_PUBLISH, ENT.F_SHOP_PURCHASE):
                self.assertTrue(allowed(kid, f), f)
            await PC.panel_withdraw(db, mom, cid, now=NOW)
            kid = await fresh(db, "kid")
            self.assertEqual(ENT.valid_scopes(kid), frozenset())
            for f in (ENT.F_FRIENDS, ENT.F_DM, ENT.F_GROUPS, ENT.F_REAL_MONEY, ENT.F_PUBLISH, ENT.F_WALLET_VIEW, ENT.F_PLAY):
                self.assertFalse(allowed(kid, f), f)                        # sub 16: iar blocat total
            self.assertEqual(ENT.ui_status(kid), "parental_consent_revoked")
        run(go())

    def test_stale_scopes_written_in_db_do_not_survive_a_state_change(self):
        """Chiar daca `parental.scopes` ramane scris in DB, un scop dat pe ALT acord / dupa retragere nu e valid."""
        async def go():
            db = await new_db()
            cid, _ = await approved_child(db)
            await PC.set_scope(db, await fresh(db, "mom"), cid, "publish", True, now=NOW)
            kid = await fresh(db, "kid")
            self.assertTrue(allowed(kid, ENT.F_PUBLISH))
            tampered = {**kid, "parental": {**kid["parental"], "consent_id": "pc_other"}}
            self.assertFalse(allowed(tampered, ENT.F_PUBLISH))
            tampered = {**kid, "parental": {**kid["parental"], "state": AP.PARENTAL_WITHDRAWN}}
            self.assertFalse(allowed(tampered, ENT.F_PUBLISH))
        run(go())

    def test_only_the_linked_verified_parent_can_set_scopes_and_scopes_are_validated(self):
        async def go():
            db = await new_db()
            cid, _ = await approved_child(db)
            await add_user(db, "evil", years_ago(40), email="evil@example.com", age_assurance=sealed("evil", 18), age_category="adult_18")
            for who in (await fresh(db, "evil"), await fresh(db, "kid")):
                with self.assertRaises(PC.ParentalError):
                    await PC.set_scope(db, who, cid, "publish", True, now=NOW)
            mom = await fresh(db, "mom")
            for bad in ("admin", "transfer", "adult_content", None, 1):
                with self.assertRaises(PC.ParentalError) as c:
                    await PC.set_scope(db, mom, cid, bad, True, now=NOW)
                self.assertEqual(c.exception.code, "invalid_scope")
            with self.assertRaises(PC.ParentalError):
                await PC.set_scope(db, mom, cid, "publish", "yes", now=NOW)
            self.assertFalse(allowed(await fresh(db, "kid"), ENT.F_PUBLISH))
        run(go())

    def test_scope_cannot_be_set_before_approval(self):
        async def go():
            db = await new_db()
            await add_user(db, "kid2", years_ago(12), email="kid2@example.com")
            await add_user(db, "mom", years_ago(40), email="mom@example.com", age_assurance=sealed("mom", 18), age_category="adult_18")
            r = await PC.request_consent(db, await fresh(db, "kid2"), "mom@example.com", notifier=Notifier(), app_base_url=BASE, now=NOW)
            await PC.panel_decide(db, await fresh(db, "mom"), r["consent_id"], "approve", ALL_ACKS, verifier=VERIFIER, now=NOW)
            with self.assertRaises(PC.ParentalError) as c:
                await PC.set_scope(db, await fresh(db, "mom"), r["consent_id"], "publish", True, now=NOW)
            self.assertEqual(c.exception.code, "consent_not_approved")
        run(go())

    def test_scope_record_keeps_date_purpose_and_policy_version(self):
        async def go():
            db = await new_db()
            cid, _ = await approved_child(db)
            await PC.set_scope(db, await fresh(db, "mom"), cid, "groups", True, now=NOW)
            c = await db[PC.C_CONSENTS].find_one({"consent_id": cid}, {"_id": 0})
            rec = c["scopes"]["groups"]
            self.assertTrue(rec["granted"])
            self.assertEqual(rec["at"], NOW)
            self.assertTrue(rec["notice_version"] and rec["terms_version"] and rec["privacy_version"])
        run(go())


class DirectEndpointAccess(unittest.TestCase):
    """Poarta centrala (get_current_user) foloseste exact `ENT.check_request`; aici o exercitam pe cai reale."""

    def denied(self, user, path, method="GET"):
        return ENT.check_request(user, path, method)

    def test_every_sensitive_route_is_mapped_so_none_falls_through_to_authenticated_only(self):
        cases = {("/api/friends/request", "POST"): ENT.F_FRIENDS, ("/api/users/search", "GET"): ENT.F_FRIENDS,
                 ("/api/dm/open", "POST"): ENT.F_DM, ("/ws/dm", "GET"): ENT.F_DM, ("/api/groups", "POST"): ENT.F_GROUPS,
                 ("/api/groups/g1/join", "POST"): ENT.F_GROUPS, ("/api/wallet/balance", "GET"): ENT.F_WALLET_VIEW,
                 ("/api/wallet/transfer", "POST"): ENT.F_TRANSFER, ("/api/wallet/transfer/preview", "POST"): ENT.F_TRANSFER,
                 ("/api/wallet/buy", "POST"): ENT.F_REAL_MONEY, ("/api/wallet/stripe/intent", "POST"): ENT.F_REAL_MONEY,
                 ("/api/consumer/checkout", "POST"): ENT.F_REAL_MONEY, ("/api/shop/items/i1", "GET"): ENT.F_SHOP_BROWSE,
                 ("/api/shop/items/i1/buy", "POST"): ENT.F_SHOP_PURCHASE, ("/api/shop/models", "POST"): ENT.F_MONETIZE,
                 ("/api/shop/items/i1", "DELETE"): ENT.F_MONETIZE, ("/api/games", "POST"): ENT.F_PUBLISH,
                 ("/api/games/g1", "PATCH"): ENT.F_PUBLISH, ("/api/studio/models", "POST"): ENT.F_PUBLISH,
                 ("/api/games/g1/collaborators", "GET"): ENT.F_PUBLISH, ("/api/friends/block", "POST"): ENT.F_BLOCK}
        for (path, method), feat in cases.items():
            self.assertEqual(ENT.feature_for_request(path, method), feat, (path, method))
        for path, method in (("/api/games/discover", "GET"), ("/api/games/g1/play", "POST"), ("/api/age/me", "GET"),
                             ("/api/privacy/export", "GET"), ("/api/reports", "POST"), ("/api/consumer/refunds", "POST")):
            self.assertIsNone(ENT.feature_for_request(path, method), (path, method))

    def test_limited_and_restricted_accounts_are_refused_on_every_sensitive_path(self):
        for user in (declared(17), declared(12), declared(30)):
            for path, method in (("/api/friends/request", "POST"), ("/api/dm/open", "POST"), ("/api/groups", "POST"),
                                 ("/api/wallet/buy", "POST"), ("/api/wallet/transfer", "POST"), ("/api/shop/items/i1/buy", "POST"),
                                 ("/api/games", "POST"), ("/api/shop/models", "POST")):
                self.assertIsNotNone(self.denied(user, path, method), (user["user_id"], path))

    def test_minor_cannot_transfer_buy_real_money_or_sell_even_when_verified(self):
        u = verified_teen()
        for path, method in (("/api/wallet/transfer", "POST"), ("/api/wallet/buy", "POST"), ("/api/wallet/stripe/intent", "POST"),
                             ("/api/shop/models", "POST")):
            self.assertIsNotNone(self.denied(u, path, method), path)
        self.assertIsNone(self.denied(u, "/api/shop/items/i1/buy", "POST"))
        self.assertIsNone(self.denied(u, "/api/games", "POST"))

    def test_blocking_is_always_available_to_a_limited_account_but_not_to_a_restricted_one(self):
        self.assertIsNone(self.denied(declared(17), "/api/friends/block", "POST"))
        self.assertIsNone(self.denied(declared(17), "/api/friends/blocked", "GET"))
        self.assertIsNotNone(self.denied(declared(12), "/api/friends/block", "POST"))

    def test_denial_detail_explains_reason_and_next_step(self):
        d = self.denied(declared(17), "/api/dm/open", "POST")
        self.assertEqual((d.reason, d.next_action), (ENT.R_VERIFY_AGE, "verify_age_16"))
        self.assertEqual(d.detail()["code"], "feature_not_available")

    def test_gate_in_server_uses_the_service_and_not_client_values(self):
        src = (BACKEND / "server.py").read_text(encoding="utf-8")
        body = src[src.index("async def get_current_user"):src.index("@app.on_event(\"startup\")")]
        self.assertIn("ENT.check_request(user, request.url.path, request.method)", body)
        self.assertNotIn("age_category", body.replace("detail", ""))        # nimic din cerere/client in decizie
        self.assertIn("ENT.transfer_allowed(current, recipient", src)


class FrontendTamper(unittest.TestCase):
    def test_client_age_category_never_changes_the_decision(self):
        u = {**declared(12), "age_category": "adult_18"}                     # valoare falsificata (DevTools / DB)
        self.assertFalse(allowed(u, ENT.F_ADULT))
        self.assertFalse(allowed(u, ENT.F_FRIENDS))
        u2 = {**declared(17), "age_category": "adult_18", "limited": False, "consent_eligibility": "verified"}
        self.assertFalse(allowed(u2, ENT.F_FRIENDS))
        self.assertFalse(allowed(u2, ENT.F_ADULT))

    def test_profile_patch_cannot_carry_age_or_scope_fields(self):
        clean, ignored = AS.strip_age_fields({"display_name": "x", "age_category": "adult_18", "parental": {"scopes": {"publish": {}}},
                                              "age_assurance": {"status": "verified"}})
        self.assertEqual(clean, {"display_name": "x"})
        self.assertEqual(set(ignored), {"age_category", "parental", "age_assurance"})


class AdultGamesAndContact(unittest.TestCase):
    GAME = {"game_id": "g", "age_category": "adult_18", "owner_id": "o"}

    def test_adult_game_by_direct_url_is_refused_to_every_non_verified_adult(self):
        for u in (declared(12), declared(17), declared(30), verified_teen(), {**declared(17), "age_category": "adult_18"}):
            with self.assertRaises(AG.AdultAccessDenied):
                AG.check_game_access(self.GAME, u)
        AG.check_game_access(self.GAME, adult())

    def test_adult_game_with_missing_category_is_treated_as_adult(self):
        with self.assertRaises(AG.AdultAccessDenied):
            AG.check_game_access({"game_id": "g"}, verified_teen())

    def test_friends_dm_and_search_between_incompatible_categories(self):
        a, m = AP.derive_category_doc(adult("a")), AP.derive_category_doc(verified_teen("m"))
        self.assertFalse(CP.users_contact_allowed(a, m))
        self.assertFalse(CP.users_contact_allowed(m, a))
        self.assertTrue(CP.users_contact_allowed(m, AP.derive_category_doc(verified_teen("m2", 16))))
        self.assertFalse(CP.chat_allowed(AP.effective_category(declared(17)), "under_18"))     # categorie necunoscuta => refuz

    def test_publishing_adult_game_needs_verified_adult(self):
        self.assertFalse(allowed(verified_teen(), ENT.F_ADULT))
        self.assertTrue(allowed(adult(), ENT.F_ADULT))


class WalletTransfers(unittest.TestCase):
    def test_unauthorised_transfers_are_refused(self):
        a, b = adult("a"), adult("b")
        self.assertTrue(ENT.transfer_allowed(a, b).allowed)
        for sender in (declared(30), verified_teen(), declared(12), None):
            self.assertFalse(ENT.transfer_allowed(sender, b).allowed)

    def test_recipient_must_be_eligible(self):
        a = adult("a")
        for r in (verified_teen("t"), declared(30, "d"), declared(12, "k"), None, {**adult("x"), "deletion_state": "pending"}):
            d = ENT.transfer_allowed(a, r)
            self.assertEqual((d.allowed, d.reason), (False, ENT.R_RECIPIENT))
        self.assertFalse(ENT.transfer_allowed(a, adult("s"), recipient_suspended=True).allowed)

    def test_transfer_check_runs_before_any_balance_movement(self):
        src = (BACKEND / "server.py").read_text(encoding="utf-8")
        fn = src[src.index("async def transfer_astrans"):src.index('@api.get("/wallet/transactions")')]
        self.assertLess(fn.index("ENT.transfer_allowed"), fn.index("money."))
        self.assertLess(fn.index("ENT.transfer_allowed"), fn.index("astran_ledger"))


class UiStatuses(unittest.TestCase):
    def test_all_required_statuses_are_reachable_from_real_state(self):
        async def go():
            seen = {ENT.ui_status(declared(17)), ENT.ui_status(declared(12)), ENT.ui_status(adult()),
                    ENT.ui_status({**declared(30), "age_assurance": {"status": AP.ASSURANCE_PENDING, "threshold": 18}}),
                    ENT.ui_status({**declared(30), "age_assurance": {"status": AP.ASSURANCE_FAILED, "threshold": 18}})}
            db = await new_db()
            await add_user(db, "kidp", years_ago(12), email="kidp@example.com")
            await PC.request_consent(db, await fresh(db, "kidp"), "m@example.com", notifier=Notifier(), app_base_url=BASE, now=NOW)
            seen.add(ENT.ui_status(await fresh(db, "kidp")))
            cid, _ = await approved_child(db)
            await PC.panel_withdraw(db, await fresh(db, "mom"), cid, now=NOW)
            seen.add(ENT.ui_status(await fresh(db, "kid")))
            self.assertTrue({"not_verified", "verification_pending", "verified", "verification_failed", "parental_consent_required",
                             "parental_consent_pending", "parental_consent_revoked"} <= seen, seen)
        run(go())

    def test_entitlements_payload_is_complete_and_has_no_personal_data(self):
        out = ENT.entitlements(declared(17))
        self.assertEqual(set(out["features"]), set(ENT.ALL_FEATURES))
        self.assertTrue(all({"allowed", "reason", "next_action", "scope"} == set(v) for v in out["features"].values()))
        self.assertNotIn("dob", str(out).lower())


if __name__ == "__main__":
    unittest.main()
