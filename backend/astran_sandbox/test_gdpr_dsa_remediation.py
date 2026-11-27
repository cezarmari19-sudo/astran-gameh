"""Teste pentru remedierea GDPR (Art. 16, 33-34) si DSA (Art. 27, 30-32).
    python -m unittest astran_sandbox.test_gdpr_dsa_remediation
Ruleaza pe codul REAL al serviciilor (fara FastAPI): rutele sunt straturi subtiri, verificate static mai jos.
"""
from __future__ import annotations

import re
import unittest
from datetime import timedelta
from pathlib import Path

from astran_sandbox import age_policy as AP
from astran_sandbox import age_service as AS
from astran_sandbox import compliance_migrations as CM
from astran_sandbox import dob_correction as DC
from astran_sandbox import incidents as INC
from astran_sandbox import merchants as M
from astran_sandbox import personal_data as PD
from astran_sandbox import recommendations as REC
from astran_sandbox.age_assurance import AssuranceError, NullProvider
from astran_sandbox.test_age_system import (NOW, PROVIDER, Notifier, add_user, attest, fresh, new_db, run,  # noqa: F401
                                           setUpModule, tearDownModule, years_ago)
from astran_sandbox.test_support_fakedb import FakeDB

HERE = Path(__file__).resolve().parent
ADMIN = {"user_id": "adm", "is_platform_admin": True}
LEGAL = {"user_id": "leg", "is_legal_officer": True}
SECURITY = {"user_id": "sec", "is_security_officer": True}
MODERATOR = {"user_id": "mod", "is_moderator": True}
PLAIN = {"user_id": "plain"}


def iso(hours_ago: float = 1) -> str:
    return (INC.now_utc() - timedelta(hours=hours_ago)).isoformat()


def new_incident(**kw):
    return {"title": "Leaked backup", "description": "A backup file was exposed for a short time.", "incident_type": "unauthorized_access",
            "discovered_at": iso(2), "data_categories": ["email_addresses"], "affected_users_estimate": 120, **kw}


# =============================================================================================== GDPR Art. 33-34
class Incidents(unittest.TestCase):
    def test_unauthorized_users_cannot_touch_incidents(self):
        async def go():
            db = FakeDB()
            inc = await INC.create_incident(db, ADMIN, new_incident())
            for who in (PLAIN, MODERATOR, None, {"user_id": "x", "is_platform_admin": "yes"}):
                for call in (lambda: INC.list_incidents(db, who), lambda: INC.get_incident(db, who, inc["incident_id"]),
                             lambda: INC.create_incident(db, who, new_incident()), lambda: INC.audit_history(db, who, inc["incident_id"]),
                             lambda: INC.assess(db, who, inc["incident_id"], {}), lambda: INC.dashboard_summary(db, who)):
                    with self.assertRaises(INC.IncidentError) as e:
                        await call()
                    self.assertEqual(e.exception.status, 403)
            for ok in (ADMIN, LEGAL, SECURITY):
                self.assertEqual(len(await INC.list_incidents(db, ok)), 1)
        run(go())

    def test_incident_requires_facts_and_validates_fields(self):
        async def go():
            db = FakeDB()
            bad = [new_incident(title="x"), new_incident(incident_type="nope"), new_incident(discovered_at="yesterday"),
                   new_incident(discovered_at=(INC.now_utc() + timedelta(days=2)).isoformat()), new_incident(data_categories=["secret"]),
                   new_incident(affected_users_estimate=-1), new_incident(occurred_at=iso(0.5)),
                   {k: v for k, v in new_incident().items() if k != "affected_users_estimate"}]
            for b in bad:
                with self.assertRaises(INC.IncidentError) as e:
                    await INC.create_incident(db, ADMIN, b)
                self.assertEqual(e.exception.status, 422, b)
            ok = await INC.create_incident(db, ADMIN, {k: v for k, v in new_incident().items() if k != "affected_users_estimate"} | {"affected_users_unknown": True})
            self.assertTrue(ok["affected_users_unknown"])
        run(go())

    def test_72h_deadline_is_tracked_and_configurable(self):
        async def go():
            db = FakeDB()
            fresh_inc = await INC.create_incident(db, ADMIN, new_incident(discovered_at=iso(1)))
            self.assertEqual(fresh_inc["deadline"]["state"], "pending")
            self.assertAlmostEqual(fresh_inc["deadline"]["hours_remaining"], 71, delta=0.1)
            soon = await INC.create_incident(db, ADMIN, new_incident(discovered_at=iso(65)))
            self.assertEqual(soon["deadline"]["state"], "due_soon")
            late = await INC.create_incident(db, ADMIN, new_incident(discovered_at=iso(80)))
            self.assertEqual(late["deadline"]["state"], "overdue")
            with self.assertRaises(INC.IncidentError):                  # doar adminul schimba politica
                await INC.set_policy(db, LEGAL, {"notify_deadline_hours": 24})
            with self.assertRaises(INC.IncidentError):
                await INC.set_policy(db, ADMIN, {"notify_deadline_hours": 0})
            await INC.set_policy(db, ADMIN, {"notify_deadline_hours": 24})
            self.assertEqual((await INC.create_incident(db, ADMIN, new_incident(discovered_at=iso(1))))["deadline_hours"], 24)
        run(go())

    def test_full_notification_workflow_with_late_reason_and_audit(self):
        async def go():
            db = FakeDB()
            inc = await INC.create_incident(db, SECURITY, new_incident(discovered_at=iso(80), data_categories=["email_addresses", "date_of_birth_age"]))
            iid = inc["incident_id"]
            await INC.update_facts(db, SECURITY, iid, {"add_measure": "Rotated the exposed credentials", "affected_users_estimate": 150})
            with self.assertRaises(INC.IncidentError) as e:             # decizia cere evaluare de risc
                await INC.decide_authority_notification(db, SECURITY, iid, {"decision": "notify", "reasons": "x" * 30})
            self.assertEqual(e.exception.code, "assessment_required")
            await INC.assess(db, SECURITY, iid, {"severity": "high", "risk_level": "high_risk", "reasoning": "Emails and birth dates were exposed.",
                                                 "likely_consequences": "Phishing and identity misuse"})
            with self.assertRaises(INC.IncidentError) as e:             # nu se poate "nu notifica" cand riscul nu e improbabil
                await INC.decide_authority_notification(db, SECURITY, iid, {"decision": "not_notify", "reasons": "We prefer not to notify."})
            self.assertEqual(e.exception.code, "notification_required")
            await INC.decide_authority_notification(db, SECURITY, iid, {"decision": "notify", "reasons": "High risk to data subjects."})
            await INC.prepare_authority_notification(db, SECURITY, iid, {"dpo_contact": "privacy@example.test"})
            for who in (SECURITY, PLAIN):                                # nici responsabilul de securitate, nici altcineva nu inregistreaza depunerea
                with self.assertRaises(INC.IncidentError) as e:
                    await INC.record_authority_submission(db, who, iid, {"submitted_at": iso(0.1)})
                self.assertEqual(e.exception.status, 403)
            with self.assertRaises(INC.IncidentError) as e:             # inainte de aprobare
                await INC.record_authority_submission(db, ADMIN, iid, {"submitted_at": iso(0.1)})
            self.assertEqual(e.exception.code, "approval_required")
            await INC.approve_authority_decision(db, LEGAL, iid)
            with self.assertRaises(INC.IncidentError) as e:             # tardiv => motive obligatorii (Art. 33(1))
                await INC.record_authority_submission(db, LEGAL, iid, {"submitted_at": iso(0.1)})
            self.assertIn(e.exception.code, ("missing_field", "too_short"))
            done = await INC.record_authority_submission(db, LEGAL, iid, {"submitted_at": iso(0.1), "authority_reference": "REF-1",
                                                                          "late_reason": "Scope was confirmed only after forensic analysis."})
            self.assertEqual(done["deadline"]["state"], "late")
            self.assertTrue(done["authority_notification"]["late"])
            with self.assertRaises(INC.IncidentError) as e:             # inchiderea cere si decizia Art. 34
                await INC.close_incident(db, LEGAL, iid, {"lessons_learned": "Add alerting on public buckets."})
            self.assertEqual(e.exception.code, "data_subject_decision_pending")
            with self.assertRaises(INC.IncidentError) as e:             # risc ridicat: comunicarea nu se poate omite fara exceptie Art. 34(3)
                await INC.decide_data_subject_communication(db, SECURITY, iid, {"required": False, "reasons": "Not needed in our view."})
            self.assertEqual(e.exception.code, "communication_required")
            ds = await INC.decide_data_subject_communication(db, SECURITY, iid, {
                "required": True, "reasons": "High risk: Art. 34 applies.", "contact": "privacy@example.test", "advice": "Be careful with phishing e-mails."})
            self.assertIn("Astran Game", ds["data_subjects"]["draft"]["ro"])
            self.assertIn("Security incident", ds["data_subjects"]["draft"]["en"])
            await INC.record_data_subject_communication(db, ADMIN, iid, {"communicated_at": iso(0.05), "channel": "email", "recipients_count": 150})
            closed = await INC.close_incident(db, ADMIN, iid, {"lessons_learned": "Add alerting on public buckets."})
            self.assertEqual(closed["status"], "closed")
            with self.assertRaises(INC.IncidentError):                   # inchis: nu se mai modifica
                await INC.update_facts(db, ADMIN, iid, {"add_measure": "Something else"})
            hist = await INC.audit_history(db, ADMIN, iid)
            actions = [e["action"] for e in hist["events"]]
            for a in ("incident_created", "facts_updated", "assessed", "authority_decision_recorded", "authority_draft_prepared",
                      "authority_decision_approved", "authority_submission_recorded", "data_subject_decision_recorded",
                      "data_subject_communication_recorded", "incident_closed"):
                self.assertIn(a, actions)
            self.assertTrue(hist["chain"]["ok"])
        run(go())

    def test_not_notify_allowed_only_when_risk_unlikely_and_audit_detects_tampering(self):
        async def go():
            db = FakeDB()
            inc = await INC.create_incident(db, ADMIN, new_incident(incident_type="service_unavailability", data_categories=[]))
            iid = inc["incident_id"]
            await INC.assess(db, ADMIN, iid, {"severity": "low", "risk_level": "unlikely", "reasoning": "No personal data was involved at all."})
            out = await INC.decide_authority_notification(db, ADMIN, iid, {"decision": "not_notify", "reasons": "Breach unlikely to result in risk."})
            self.assertEqual(out["deadline"]["state"], "not_required")
            self.assertEqual(out["status"], "no_notification_needed")
            self.assertTrue((await INC.audit_history(db, ADMIN, iid))["chain"]["ok"])
            await db[INC.C_AUDIT].update_one({"incident_id": iid, "seq": 2}, {"$set": {"details": {"tampered": True}}})
            self.assertFalse((await INC.audit_history(db, ADMIN, iid))["chain"]["ok"])
        run(go())

    def test_children_data_and_special_categories_cannot_be_downgraded(self):
        async def go():
            db = FakeDB()
            kids = await INC.create_incident(db, ADMIN, new_incident(data_categories=["child_or_parental_data"]))
            with self.assertRaises(INC.IncidentError) as e:
                await INC.assess(db, ADMIN, kids["incident_id"], {"severity": "low", "risk_level": "risk", "reasoning": "Looks harmless to me."})
            self.assertEqual(e.exception.code, "severity_too_low")
            special = await INC.create_incident(db, ADMIN, new_incident(data_categories=["special_category_data"]))
            with self.assertRaises(INC.IncidentError) as e:
                await INC.assess(db, ADMIN, special["incident_id"], {"severity": "high", "risk_level": "unlikely", "reasoning": "Looks harmless to me."})
            self.assertEqual(e.exception.code, "risk_too_low")
        run(go())

    def test_separate_approver_policy(self):
        async def go():
            db = FakeDB()
            await INC.set_policy(db, ADMIN, {"require_separate_approver": True})
            inc = await INC.create_incident(db, ADMIN, new_incident())
            iid = inc["incident_id"]
            await INC.assess(db, ADMIN, iid, {"severity": "high", "risk_level": "risk", "reasoning": "Emails exposed to a third party."})
            await INC.decide_authority_notification(db, ADMIN, iid, {"decision": "notify", "reasons": "A risk exists for the people."})
            with self.assertRaises(INC.IncidentError) as e:
                await INC.approve_authority_decision(db, ADMIN, iid)
            self.assertEqual(e.exception.code, "separate_approver_required")
            self.assertIsNotNone((await INC.approve_authority_decision(db, LEGAL, iid))["authority_decision"]["approved_by"])
        run(go())

    def test_optimistic_concurrency(self):
        async def go():
            db = FakeDB()
            inc = await INC.create_incident(db, ADMIN, new_incident())
            stale = await INC._load(db, inc["incident_id"])
            await INC.update_facts(db, ADMIN, inc["incident_id"], {"add_measure": "First measure taken"})
            with self.assertRaises(INC.IncidentError) as e:
                await INC._save(db, stale, {"title": "overwrite"})
            self.assertEqual(e.exception.status, 409)
        run(go())

    def test_module_never_sends_anything(self):
        src = (HERE / "incidents.py").read_text(encoding="utf-8") + (HERE / "incident_routes.py").read_text(encoding="utf-8")
        for needle in ("smtplib", "send_plain_email", "notifier", "httpx", "import requests", "urllib", "_notify_email"):
            self.assertNotIn(needle, src)
        routes = (HERE / "incident_routes.py").read_text(encoding="utf-8")
        chunks = routes.split("    @router.")[1:]
        self.assertGreaterEqual(len(chunks), 14)
        for ch in chunks:
            signature = "\n".join(ch.split("\n")[:3])
            self.assertIn("Depends(staff)", signature, f"ruta fara autorizare de personal: {ch.splitlines()[0]}")


# =============================================================================================== GDPR Art. 16 - e-mail
async def pw_ok(pw, h):
    return pw == h


class EmailChange(unittest.TestCase):
    def setUp(self):
        self.db, self.notifier, self.changed = None, Notifier(), []

    async def setup_db(self, **extra):
        db = await new_db()
        self.u = await add_user(db, "u1", years_ago(25), email="old@example.com", has_password=True, **extra)
        await add_user(db, "u2", years_ago(30), email="taken@example.com")
        return db

    async def on_changed(self, uid):
        self.changed.append(uid)

    async def start(self, db, new="new@example.com"):
        return await PD.request_email_change(db, self.u, new, reauth_method="password", notifier=self.notifier, confirm_url_base="https://app.test")

    def token(self, kind):
        for to, subj, body in reversed(self.notifier.sent):
            m = re.search(kind + r"\?token=([A-Za-z0-9_\-]+)", body)
            if m:
                return to, m.group(1)
        return None, None

    def test_email_changes_only_after_confirmation_from_new_address(self):
        async def go():
            db = await self.setup_db()
            res = await self.start(db)
            self.assertEqual(res["status"], "pending_verification")
            self.assertEqual((await fresh(db, "u1"))["email"], "old@example.com")          # inca neschimbat
            to_new, ctoken = self.token("confirm-email")
            to_old, rtoken = self.token("revert-email")
            self.assertEqual((to_new, to_old), ("new@example.com", "old@example.com"))      # link la noua adresa + notificare pe cea veche
            with self.assertRaises(PD.PersonalDataError):                                    # tokenul de revenire nu confirma
                await PD.confirm_email_change(db, rtoken)
            out = await PD.confirm_email_change(db, ctoken, on_email_changed=self.on_changed)
            self.assertEqual(out["email"], "new@example.com")
            u = await fresh(db, "u1")
            self.assertEqual((u["email"], u["email_verified"]), ("new@example.com", True))
            self.assertEqual(self.changed, ["u1"])                                           # sesiunile se invalideaza
            with self.assertRaises(PD.PersonalDataError):                                    # single-use
                await PD.confirm_email_change(db, ctoken)
            ev = [e["action"] for e in await db[PD.C_AUDIT].find({"user_id": "u1"}, {"_id": 0}).to_list(10)]
            self.assertIn("email_changed", ev)
            for e in await db[PD.C_AUDIT].find({}, {"_id": 0}).to_list(10):                  # niciodata adrese in clar / token-uri in audit
                self.assertNotIn("old@example.com", str(e)); self.assertNotIn("new@example.com", str(e))
                self.assertNotIn(ctoken, str(e))
        run(go())

    def test_owner_can_revert_after_confirmation_and_cancel_before(self):
        async def go():
            db = await self.setup_db()
            await self.start(db)
            _, ctoken = self.token("confirm-email"); _, rtoken = self.token("revert-email")
            await PD.revert_email_change(db, rtoken)                                         # cancel cat timp e in asteptare
            with self.assertRaises(PD.PersonalDataError):
                await PD.confirm_email_change(db, ctoken)
            self.notifier.sent.clear()
            await db[PD.C_REQUESTS].update_many({}, {"$set": {"status": "superseded", "created_at": INC.now_utc() - timedelta(hours=2)}})
            await self.start(db, "second@example.com")
            _, c2 = self.token("confirm-email"); _, r2 = self.token("revert-email")
            await PD.confirm_email_change(db, c2, on_email_changed=self.on_changed)
            self.assertEqual((await fresh(db, "u1"))["email"], "second@example.com")
            out = await PD.revert_email_change(db, r2, on_email_changed=self.on_changed)    # revenire dupa confirmare
            self.assertEqual(out["status"], "reverted")
            self.assertEqual((await fresh(db, "u1"))["email"], "old@example.com")
            with self.assertRaises(PD.PersonalDataError):
                await PD.revert_email_change(db, r2)
        run(go())

    def test_cannot_take_an_address_used_by_another_account_and_no_enumeration(self):
        async def go():
            db = await self.setup_db()
            free = await self.start(db, "free@example.com")
            self.notifier.sent.clear()
            await db[PD.C_REQUESTS].update_many({}, {"$set": {"status": "superseded", "created_at": INC.now_utc() - timedelta(hours=2)}})
            taken = await self.start(db, "Taken@Example.com")                                # alt caz de scriere, acelasi cont
            self.assertEqual(set(free) - {"new_email_masked"}, set(taken) - {"new_email_masked"})   # raspuns identic ca forma
            self.assertEqual(self.notifier.sent, [])                                         # nimic trimis catre adresa straina
            self.assertEqual((await fresh(db, "u1"))["email"], "old@example.com")
            # chiar daca o cerere era deja in asteptare, la confirmare se reverifica unicitatea
            await self.start(db, "later@example.com")
            _, ctoken = self.token("confirm-email")
            await db.users.update_one({"user_id": "u2"}, {"$set": {"email": "later@example.com"}})
            with self.assertRaises(PD.PersonalDataError) as e:
                await PD.confirm_email_change(db, ctoken)
            self.assertEqual(e.exception.code, "email_unavailable")
            self.assertEqual((await fresh(db, "u1"))["email"], "old@example.com")
        run(go())

    def test_validation_expiry_and_rate_limits(self):
        async def go():
            db = await self.setup_db()
            for bad in ("", "no-at", "a@b", "x" * 300 + "@e.com", None, 5):
                with self.assertRaises(PD.PersonalDataError) as e:
                    await self.start(db, bad)
                self.assertEqual(e.exception.status, 422)
            with self.assertRaises(PD.PersonalDataError) as e:
                await self.start(db, "OLD@example.com")
            self.assertEqual(e.exception.code, "same_email")
            await self.start(db)
            with self.assertRaises(PD.PersonalDataError) as e:                               # cooldown
                await self.start(db, "another@example.com")
            self.assertEqual(e.exception.status, 429)
            _, ctoken = self.token("confirm-email")
            await db[PD.C_REQUESTS].update_many({}, {"$set": {"expires_at": INC.now_utc() - timedelta(minutes=1)}})
            with self.assertRaises(PD.PersonalDataError):
                await PD.confirm_email_change(db, ctoken)
            with self.assertRaises(PD.PersonalDataError):
                await PD.confirm_email_change(db, "short")
            with self.assertRaises(PD.PersonalDataError) as e:
                await PD.request_email_change(db, self.u, "q@example.com", reauth_method="password", notifier=None, confirm_url_base="x")
            self.assertEqual(e.exception.status, 503)
        run(go())

    def test_reauthentication_reuses_the_existing_privacy_service(self):
        from astran_sandbox import email_verification as EV
        from astran_sandbox import privacy as P

        async def go():
            db = await self.setup_db()
            kw = dict(verify_pw=pw_ok, emailver=EV, session_age_seconds=None, email_is_configured=True)
            with self.assertRaises(P.ReauthError) as e:
                await P.verify_reauth(db, {**self.u, "password_hash": "secret"}, password="wrong", code=None, **kw)
            self.assertEqual(e.exception.code, "BAD_PASSWORD")
            await P.verify_reauth(db, {**self.u, "password_hash": "secret"}, password="secret", code=None, **kw)
            g = await add_user(db, "g1", years_ago(40), email="g@example.com", verified=True)      # cont Google: cod pe e-mailul curent
            with self.assertRaises(P.ReauthError) as e:                                            # fail-closed fara cod / fara scurtatura de sesiune
                await P.verify_reauth(db, g, password=None, code=None, **kw)
            self.assertEqual(e.exception.code, "REAUTH_REQUIRED")
            code = await EV.issue(db, "g1", "g@example.com", collection=P.REAUTH_CODE_COLLECTION)
            await P.verify_reauth(db, g, password=None, code=code, **kw)
        run(go())

    def test_no_parallel_reauth_system_was_added(self):
        src = (HERE / "personal_data.py").read_text(encoding="utf-8")
        self.assertNotIn("def verify_reauth", src); self.assertNotIn("def send_reauth_code", src); self.assertNotIn("reauth_codes", src)
        routes = (HERE / "personal_data_routes.py").read_text(encoding="utf-8")
        self.assertIn("privacy.verify_reauth", routes)
        self.assertIn("psec.ensure_rate_limit_or_raise", routes)
        self.assertNotIn("reauth/send-code", routes)


# =============================================================================================== GDPR Art. 16 - data nasterii
class DobCorrection(unittest.TestCase):
    def test_more_restrictive_correction_applies_immediately_with_safety_rules(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "a1", years_ago(25))
            out = await DC.request_correction(db, u, years_ago(14), "typo", None, reauth_method="password", provider_configured=False, now=NOW)
            self.assertEqual((out["status"], out["direction"], out["new_band"]), ("applied", "more_restrictive", AP.BAND_UNDER_16))
            u2 = await fresh(db, "a1")
            self.assertEqual(AP.decrypt_dob(u2["dob_enc"]).isoformat(), years_ago(14))
            info = AP.evaluate_access(u2)
            self.assertFalse(info.adult_content_allowed)
            self.assertEqual(u2["parental"]["state"], AP.PARENTAL_PENDING)                   # regulile de siguranta, imediat
            self.assertEqual(u2["age_category"], AP.CAT_MINOR)
            rec = await db[DC.C_CORRECTIONS].find_one({"user_id": "a1"}, {"_id": 0})
            self.assertEqual((AP.decrypt_dob(rec["old_dob_enc"]).isoformat(), AP.decrypt_dob(rec["new_dob_enc"]).isoformat()), (years_ago(25), years_ago(14)))
            self.assertEqual((rec["reason_code"], rec["reauth_method"], rec["status"]), ("typo", "password", "applied"))
            self.assertIsNotNone(rec["applied_at"])
        run(go())

    def test_verified_adult_corrected_below_18_loses_adult_access(self):
        async def go():
            from astran_sandbox.test_age_system import sealed
            db = await new_db()
            u = await add_user(db, "v1", years_ago(30), age_assurance=sealed("v1", 18), age_category="adult_18")
            self.assertTrue(AP.can_access_adult_content(await fresh(db, "v1")))
            await DC.request_correction(db, u, years_ago(17), "typo", None, reauth_method="password", provider_configured=True, now=NOW)
            after = await fresh(db, "v1")
            self.assertFalse(AP.can_access_adult_content(after))
            self.assertIsNone(AP.verified_threshold(after))
        run(go())

    def test_upward_correction_waits_for_age_verification_and_stays_blocked_without_provider(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t1", years_ago(17))
            out = await DC.request_correction(db, u, years_ago(20), "wrong_year_or_day", None, reauth_method="password", provider_configured=False, now=NOW)
            self.assertEqual((out["status"], out["required_threshold"], out["next_action"]), ("awaiting_age_verification", 18, "verify_age_18"))
            self.assertFalse(out["provider_available"])
            cur = await fresh(db, "t1")
            self.assertEqual(AP.decrypt_dob(cur["dob_enc"]).isoformat(), years_ago(17))      # data efectiva NU s-a schimbat
            self.assertFalse(AP.can_access_adult_content(cur))
            with self.assertRaises(AssuranceError) as e:                                     # furnizor neconectat: 18+ ramane blocat
                await AS.start_assurance(db, cur, NullProvider(), threshold=18, now=NOW)
            self.assertEqual(e.exception.status, 503)
            self.assertFalse(AP.can_access_adult_content(await fresh(db, "t1")))
            with self.assertRaises(DC.DobCorrectionError) as e:                              # nu se pot stivui cereri
                await DC.request_correction(db, cur, years_ago(21), "typo", None, reauth_method="password", provider_configured=False, now=NOW)
            self.assertEqual(e.exception.code, "request_already_open")
        run(go())

    def test_upward_correction_becomes_effective_only_after_provider_attestation(self):
        from astran_sandbox.test_age_verification_gaps import cb, start_sid

        async def go():
            db = await new_db()
            u = await add_user(db, "t2", years_ago(17))
            await DC.request_correction(db, u, years_ago(20), "typo", None, reauth_method="password", provider_configured=True, now=NOW)
            cur = await fresh(db, "t2")
            self.assertEqual(AS.allowed_thresholds(AP.evaluate_access(cur), cur), (18,))     # pragul decurge din banda DORITA
            sid, _ = await start_sid(db, cur, 18)
            body, sig = cb(sid, threshold=18)
            await AS.handle_assurance_callback(db, PROVIDER, body, sig, now=NOW)
            done = await fresh(db, "t2")
            self.assertEqual(AP.decrypt_dob(done["dob_enc"]).isoformat(), years_ago(20))     # abia acum e efectiva
            self.assertEqual(AP.evaluate_access(done).band, AP.BAND_ADULT)
            self.assertEqual(AP.verified_threshold(done), 18)
            self.assertTrue(AP.can_access_adult_content(done))
            self.assertNotIn("dob_correction_pending", done)
            rec = await db[DC.C_CORRECTIONS].find_one({"user_id": "t2"}, {"_id": 0})
            self.assertEqual(rec["status"], "applied")
        run(go())

    def test_attestation_of_lower_threshold_does_not_apply_an_18_plus_correction(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "t3", years_ago(15))
            await DC.request_correction(db, u, years_ago(19), "typo", None, reauth_method="password", provider_configured=True, now=NOW)
            # simulam o atestare de 16 (valida) pe un cont cu cerere spre 18+: nu e suficienta
            await db.users.update_one({"user_id": "t3"}, {"$set": {"age_assurance": __import__("astran_sandbox.test_age_system", fromlist=["x"]).sealed("t3", 16)}})
            self.assertIsNone(await DC.finalize_after_assurance(db, "t3", now=NOW))
            self.assertEqual(AP.evaluate_access(await fresh(db, "t3")).band, AP.BAND_UNDER_16)
        run(go())

    def test_repeated_changes_cannot_bypass_age_restrictions(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "r1", years_ago(25))
            await DC.request_correction(db, u, years_ago(26), "typo", None, reauth_method="password", provider_configured=False, now=NOW)   # aceeasi banda
            u = await fresh(db, "r1")
            with self.assertRaises(DC.DobCorrectionError) as e:                              # limita de corectii aplicate
                await DC.request_correction(db, u, years_ago(14), "typo", None, reauth_method="password", provider_configured=False, now=NOW)
            self.assertEqual(e.exception.code, "correction_limit_reached")
            st = await DC.status(db, u, provider_configured=False, now=NOW)
            self.assertFalse(st["can_request"])
        run(go())

    def test_cooldown_and_yearly_limit_are_configurable_and_enforced(self):
        import os

        async def go():
            os.environ["ASTRAN_DOB_CORRECTION_MAX_APPLIED"] = "5"
            db = await new_db()
            u = await add_user(db, "r2", years_ago(25))
            await DC.request_correction(db, u, years_ago(26), "typo", None, reauth_method="password", provider_configured=False, now=NOW)
            u = await fresh(db, "r2")
            with self.assertRaises(DC.DobCorrectionError) as e:
                await DC.request_correction(db, u, years_ago(27), "typo", None, reauth_method="password", provider_configured=False, now=NOW + timedelta(days=5))
            self.assertEqual((e.exception.status, e.exception.code), (429, "correction_cooldown"))
            await DC.request_correction(db, u, years_ago(27), "typo", None, reauth_method="password", provider_configured=False, now=NOW + timedelta(days=31))
        old = os.environ.get("ASTRAN_DOB_CORRECTION_MAX_APPLIED")
        try:
            run(go())
        finally:
            os.environ.pop("ASTRAN_DOB_CORRECTION_MAX_APPLIED", None) if old is None else os.environ.__setitem__("ASTRAN_DOB_CORRECTION_MAX_APPLIED", old)

    def test_validation(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "x1", years_ago(25))
            cases = [(years_ago(25), "typo", None, DC.DobCorrectionError), ("2999-01-01", "typo", None, AP.AgeError), ("garbage", "typo", None, AP.AgeError),
                     (years_ago(30), "because", None, DC.DobCorrectionError), (years_ago(30), "other", "short", DC.DobCorrectionError),
                     (years_ago(30), "other", "x" * 700, DC.DobCorrectionError)]
            for dob, code, text, exc in cases:
                with self.assertRaises(exc, msg=(dob, code)):
                    await DC.request_correction(db, u, dob, code, text, reauth_method="password", provider_configured=False, now=NOW)
            nodob = await db.users.insert_one({"user_id": "n1", "email": "n@e.com"})
            with self.assertRaises(DC.DobCorrectionError) as e:
                await DC.request_correction(db, {"user_id": "n1"}, years_ago(20), "typo", None, reauth_method="password", provider_configured=False, now=NOW)
            self.assertEqual(e.exception.code, "date_of_birth_not_set")
            self.assertEqual(await db[DC.C_CORRECTIONS].count_documents({}), 0)              # cererile invalide nu lasa urme
        run(go())

    def test_no_identity_document_fields_or_storage(self):
        src = (HERE / "dob_correction.py").read_text(encoding="utf-8") + (HERE / "personal_data_routes.py").read_text(encoding="utf-8")
        for needle in ("upload", "UploadFile", "document_image", "id_card", "passport", "scan"):
            self.assertNotIn(needle, src.lower() if needle.islower() else src)

    def test_pending_correction_can_be_cancelled(self):
        async def go():
            db = await new_db()
            u = await add_user(db, "c1", years_ago(17))
            await DC.request_correction(db, u, years_ago(20), "typo", None, reauth_method="password", provider_configured=True, now=NOW)
            self.assertTrue((await DC.cancel(db, await fresh(db, "c1"), now=NOW))["cancelled"])
            cur = await fresh(db, "c1")
            self.assertNotIn("dob_correction_pending", cur)
            self.assertEqual(AS.allowed_thresholds(AP.evaluate_access(cur), cur), (16,))
        run(go())



# =============================================================================================== DSA Art. 30-32
TRADER = {"trader_type": "company", "name": "Studio SRL", "address": {"line1": "Str. Mare 1", "city": "Cluj-Napoca", "postal_code": "400001", "country": "RO"},
          "phone": "+40 700 000 000", "contact_email": "contact@studio.example", "registry": {"register_name": "ONRC", "number": "J12/345/2020"},
          "vat_id": "RO12345678", "eu_compliance_statement": True}
NON_TRADER_ANSWERS = {"regular_sales": False, "for_profit_or_business": False, "professional_or_company": False, "hired_staff_or_resells_goods": False}
TRADER_ANSWERS = {"regular_sales": True, "for_profit_or_business": True, "professional_or_company": True, "hired_staff_or_resells_goods": False}


class Merchants(unittest.TestCase):
    def test_sellers_are_not_assumed_to_be_traders_and_must_declare_before_selling(self):
        async def go():
            db = FakeDB()
            await db.users.insert_one({"user_id": "s1", "username": "s1"})
            el = await M.sale_eligibility(db, "s1")
            self.assertEqual((el["allowed"], el["reason"], el["seller_status"]), (False, "seller_status_required", "undeclared"))
            with self.assertRaises(M.MerchantError) as e:
                await M.require_sale_eligibility(db, "s1")
            self.assertEqual(e.exception.status, 409)
            await M.declare_status(db, {"user_id": "s1"}, {"seller_status": "non_trader", "assessment": NON_TRADER_ANSWERS})
            self.assertTrue((await M.sale_eligibility(db, "s1"))["allowed"])
            pub = M.public_seller_info(await M.get_profile(db, "s1"))
            self.assertEqual((pub["seller_status"], pub["consumer_rights_notice"], pub["trader"]), ("non_trader", "non_trader", None))
            self.assertNotIn("s1", str(pub))                                                  # nicio data personala suplimentara
        run(go())

    def test_inconsistent_non_trader_declaration_is_refused(self):
        async def go():
            db = FakeDB()
            with self.assertRaises(M.MerchantError) as e:
                await M.declare_status(db, {"user_id": "s2"}, {"seller_status": "non_trader", "assessment": TRADER_ANSWERS})
            self.assertEqual(e.exception.code, "likely_trader")
            for bad in ({"seller_status": "other", "assessment": NON_TRADER_ANSWERS}, {"seller_status": "non_trader", "assessment": {"regular_sales": True}},
                        {"seller_status": "non_trader", "assessment": {**NON_TRADER_ANSWERS, "x": True}}):
                with self.assertRaises(M.MerchantError):
                    await M.declare_status(db, {"user_id": "s2"}, bad)
        run(go())

    def test_trader_details_validation(self):
        base = dict(TRADER)
        bad_variants = [
            {**base, "name": ""}, {**base, "phone": "abc"}, {**base, "contact_email": "nope"}, {**base, "trader_type": "alien"},
            {**base, "address": {**TRADER["address"], "country": "Romania"}}, {**base, "address": {**TRADER["address"], "city": ""}},
            {**base, "registry": {}}, {**base, "eu_compliance_statement": False}, {**base, "name": "x\x00y"}, {**base, "address": "somewhere"}, "text",
        ]
        for b in bad_variants:
            with self.assertRaises(M.MerchantError, msg=b):
                M.validate_trader_details(b)
        ok = M.validate_trader_details({**base, "contact_email": "  CONTACT@Studio.Example "})
        self.assertEqual(ok["contact_email"], "contact@studio.example")
        person = {**base, "trader_type": "natural_person", "registry": {"not_applicable_reason": "Freelancer, not registered yet"}}
        self.assertIsNone(M.validate_trader_details(person)["registry"]["number"])
        with self.assertRaises(M.MerchantError):                                              # persoana fizica: sau date, sau motiv
            M.validate_trader_details({**base, "trader_type": "natural_person", "registry": {}})

    def test_trader_flow_verification_states_and_sale_restriction(self):
        async def go():
            db = FakeDB()
            actor = {"user_id": "s3"}
            await M.reclassify_as_trader(db, ADMIN, "s3", "Sells at scale with a company.")  # fortat: trebuie date complete
            self.assertEqual((await M.sale_eligibility(db, "s3"))["reason"], "trader_details_required")
            prof = await M.declare_status(db, actor, {"seller_status": "trader", "assessment": TRADER_ANSWERS, "trader": TRADER})
            self.assertEqual(prof["verification"]["state"], "in_review")
            self.assertTrue((await M.sale_eligibility(db, "s3"))["allowed"])                  # politica implicita: in_review e suficient
            await M.set_policy(db, ADMIN, {"min_verification_to_sell": "verified"})           # configurabil de admin
            self.assertEqual((await M.sale_eligibility(db, "s3"))["reason"], "trader_verification_required")
            await M.review(db, LEGAL, "s3", "verified", "Registry number checked online.")
            self.assertTrue((await M.sale_eligibility(db, "s3"))["allowed"])
            pub = M.public_seller_info(await M.get_profile(db, "s3"))
            self.assertEqual(pub["seller_status"], "trader")
            self.assertEqual(pub["verification_state"], "verified")
            self.assertEqual(pub["trader"]["name"], "Studio SRL")
            self.assertIn("Cluj-Napoca", pub["trader"]["address"])
            self.assertEqual(pub["trader"]["registry"], {"name": "ONRC", "number": "J12/345/2020"})
            self.assertNotIn("vat_id", pub["trader"]); self.assertNotIn("reviewer", str(pub)); self.assertNotIn("note", str(pub))
            # actualizare: o modificare a datelor readuce verificarea la "in_review"
            await M.declare_status(db, actor, {"seller_status": "trader", "assessment": TRADER_ANSWERS, "trader": {**TRADER, "phone": "+40 711 111 111"}})
            self.assertEqual((await M.get_profile(db, "s3"))["verification"]["state"], "in_review")
            self.assertFalse((await M.sale_eligibility(db, "s3"))["allowed"])
            await M.review(db, LEGAL, "s3", "rejected", "The registry number does not exist.")
            await M.set_policy(db, ADMIN, {"min_verification_to_sell": "in_review"})
            self.assertEqual((await M.sale_eligibility(db, "s3"))["reason"], "trader_verification_required")   # respins nu vinde niciodata
            hist = [e["action"] for e in (await M.admin_get(db, ADMIN, "s3"))["history"]]
            for a in ("status_declared", "verification_verified", "verification_rejected", "reclassified_as_trader"):
                self.assertIn(a, hist)
            self.assertEqual(len(await db[M.C_AUDIT].find({"user_id": "policy", "action": "policy_changed"}, {"_id": 0}).to_list(10)), 2)   # schimbarile de politica sunt in audit
        run(go())

    def test_review_and_policy_are_restricted_to_staff(self):
        async def go():
            db = FakeDB()
            await M.declare_status(db, {"user_id": "s4"}, {"seller_status": "trader", "assessment": TRADER_ANSWERS, "trader": TRADER})
            for who in (PLAIN, MODERATOR, {"user_id": "s4"}, None):
                for call in (lambda: M.review(db, who, "s4", "verified", None), lambda: M.admin_list(db, who), lambda: M.admin_get(db, who, "s4"),
                             lambda: M.reclassify_as_trader(db, who, "s4", "reason long enough")):
                    with self.assertRaises(M.MerchantError) as e:
                        await call()
                    self.assertEqual(e.exception.status, 403)
            for call in (lambda: M.set_policy(db, LEGAL, {"enforce_seller_status": False}), lambda: M.set_policy(db, PLAIN, {"enforce_seller_status": False})):
                with self.assertRaises(M.MerchantError):
                    await call()
            with self.assertRaises(M.MerchantError) as e:                                     # nu isi aproba propriile date
                await M.review(db, {"user_id": "s4", "is_legal_officer": True}, "s4", "verified", None)
            self.assertEqual(e.exception.code, "self_review_forbidden")
            with self.assertRaises(M.MerchantError):                                          # respingerea cere motiv
                await M.review(db, LEGAL, "s4", "rejected", None)
            self.assertEqual(len(await M.admin_list(db, LEGAL, state="in_review")), 1)
        run(go())

    def test_enforcement_is_configurable_and_grace_period_works(self):
        async def go():
            db = FakeDB()
            self.assertFalse((await M.sale_eligibility(db, "legacy"))["allowed"])
            grace = (INC.now_utc() + timedelta(days=10)).date().isoformat()
            await M.set_policy(db, ADMIN, {"grace_until": grace})
            self.assertTrue((await M.sale_eligibility(db, "legacy"))["allowed"])
            self.assertFalse((await M.sale_eligibility(db, "legacy", now=INC.now_utc() + timedelta(days=11)))["allowed"])
            await M.set_policy(db, ADMIN, {"grace_until": "", "enforce_seller_status": False})
            self.assertTrue((await M.sale_eligibility(db, "legacy"))["allowed"])
            with self.assertRaises(M.MerchantError):
                await M.set_policy(db, ADMIN, {"min_verification_to_sell": "maybe"})
            with self.assertRaises(M.MerchantError):
                await M.set_policy(db, ADMIN, {"unknown": 1})
        run(go())

    def test_public_info_hides_everything_for_non_traders_and_batches(self):
        async def go():
            db = FakeDB()
            await M.declare_status(db, {"user_id": "a"}, {"seller_status": "non_trader", "assessment": NON_TRADER_ANSWERS})
            await M.declare_status(db, {"user_id": "b"}, {"seller_status": "trader", "assessment": TRADER_ANSWERS, "trader": TRADER})
            info = await M.seller_info_for_items(db, ["a", "b", "c", "a", None])
            self.assertEqual(set(info), {"a", "b", "c"})
            self.assertIsNone(info["a"]["trader"]); self.assertIsNone(info["c"]["trader"])
            self.assertEqual(info["c"]["seller_status"], "undeclared")
            self.assertEqual(info["b"]["trader"]["email"], "contact@studio.example")
            own = M.own_view(await M.get_profile(db, "b"))
            self.assertNotIn("reviewed_by", own["verification"])
        run(go())

    def test_gate_is_wired_in_shop_clothes_and_checkout(self):
        for name, needles in (("shop_routes.py", ("seller_gate(current[\"user_id\"])", "seller_gate(it[\"owner_id\"])", "MER.seller_info_for_items")),
                              ("clothes_routes.py", ("seller_gate(it[\"owner_id\"])", "seller_gate(existing_item[\"owner_id\"])", "MER.seller_info_for_items")),
                              ("consumer.py", ("sale_eligibility", "public_seller_info", "seller_not_compliant"))):
            src = (HERE / name).read_text(encoding="utf-8")
            for n in needles:
                self.assertIn(n, src, f"{name}: {n}")


# =============================================================================================== DSA Art. 27
class Recommendations(unittest.TestCase):
    async def seed(self):
        db = FakeDB()
        games = [("g1", "racing", 500, 50), ("g2", "puzzle", 400, 40), ("g3", "racing", 300, 30), ("g4", "rpg", 200, 20), ("g5", "puzzle", 100, 10)]
        for gid, cat, plays, likes in games:
            await db.games.insert_one({"game_id": gid, "category": cat, "total_plays": plays, "likes": likes, "is_public": True, "status": "active",
                                       "created_at": INC.now_utc() - timedelta(days=10 - int(gid[1:]))})
        await db.recently_played.insert_one({"user_id": "u", "game_id": "g4", "played_at": INC.now_utc()})
        await db.recently_played.insert_one({"user_id": "u", "game_id": "g5", "played_at": INC.now_utc() - timedelta(hours=1)})
        return db

    @staticmethod
    async def attach(rows):
        return rows

    def test_default_order_is_popularity_and_does_not_use_the_profile(self):
        async def go():
            db = await self.seed()
            q = {"is_public": True, "status": "active"}
            a = await REC.discover_games(db, "u", "for_you", q, attach=self.attach)
            self.assertEqual([g["game_id"] for g in a["games"]], ["g1", "g2", "g3", "g4", "g5"])
            self.assertFalse(a["personalized"])
            other = await REC.discover_games(db, "someone-else", "for_you", q, attach=self.attach)
            self.assertEqual([g["game_id"] for g in other["games"]], [g["game_id"] for g in a["games"]])     # acelasi pentru toti
            self.assertEqual([g["game_id"] for g in (await REC.discover_games(db, "u", "new", q, attach=self.attach))["games"]][0], "g5")
            self.assertEqual((await REC.discover_games(db, "u", "bogus", q, attach=self.attach))["section"], "for_you")
        run(go())

    def test_personalization_is_opt_in_only_in_for_you_and_reversible(self):
        async def go():
            db = await self.seed()
            q = {"is_public": True, "status": "active"}
            await db.user_settings.insert_one({"user_id": "u", "settings": {"personalized_recommendations": True}})
            on = await REC.discover_games(db, "u", "for_you", q, attach=self.attach)
            self.assertTrue(on["personalized"])
            ids = [g["game_id"] for g in on["games"]]
            self.assertEqual(set(ids[:3]), {"g2", "g4", "g5"})                                  # categoriile recente (puzzle, rpg) trec inainte
            self.assertEqual(ids[:3], ["g2", "g4", "g5"])                                        # popularitatea (plays) in interiorul grupului
            self.assertEqual(ids[3:], ["g1", "g3"])                                              # popularitatea se pastreaza in fiecare grup
            for sec in ("trending", "popular", "new"):                                           # personalizarea nu atinge celelalte liste
                self.assertFalse((await REC.discover_games(db, "u", sec, q, attach=self.attach))["personalized"])
            await db.user_settings.update_one({"user_id": "u"}, {"$set": {"settings.personalized_recommendations": False}})
            self.assertEqual([g["game_id"] for g in (await REC.discover_games(db, "u", "for_you", q, attach=self.attach))["games"]], ["g1", "g2", "g3", "g4", "g5"])
            await db.user_settings.update_one({"user_id": "u"}, {"$set": {"settings.personalized_recommendations": "yes"}})   # doar True strict
            self.assertFalse((await REC.discover_games(db, "u", "for_you", q, attach=self.attach))["personalized"])
            await db.user_settings.update_one({"user_id": "u"}, {"$set": {"settings.personalized_recommendations": True}})
            await db.recently_played.delete_many({"user_id": "u"})                              # stergerea istoricului elimina semnalul
            self.assertFalse((await REC.discover_games(db, "u", "for_you", q, attach=self.attach))["personalized"])
        run(go())

    def test_setting_exists_default_off_and_is_strict_boolean(self):
        src = (HERE / "user_settings_routes.py").read_text(encoding="utf-8")
        self.assertIn('("personalized_recommendations", False, _bool())', src)        # implicit OPRIT; validatorul e boolean strict
        self.assertIn("def _bool", src)
        try:
            from astran_sandbox import user_settings_routes as USR
        except ImportError:                                                            # FastAPI lipseste in acest mediu: ramane verificarea statica
            return
        self.assertIs(USR.default_settings()["personalized_recommendations"], False)
        self.assertIs(USR.clean_settings({"personalized_recommendations": True})["personalized_recommendations"], True)
        self.assertIs(USR.clean_settings({"personalized_recommendations": "yes"})["personalized_recommendations"], False)

    def test_terms_explain_every_real_parameter_and_invent_none(self):
        import json
        man = json.loads((HERE.parent / "legal" / "manifest.json").read_text(encoding="utf-8"))["documents"]["terms_of_service"]
        self.assertEqual(man["current"], "1.2")
        ver = next(v for v in man["versions"] if v["version"] == "1.2")
        for lang, markers in (("en", ("RECOMMENDER SYSTEM", "turns on", "off by default", "SELLERS IN THE SHOP")),
                              ("ro", ("SISTEMUL DE RECOMANDARE", "activează", "dezactivat implicit", "VÂNZĂTORII DIN SHOP"))):
            text = (HERE.parent / "legal" / ver["files"][lang]["path"]).read_text(encoding="utf-8")
            for p in REC.PARAMETERS:
                self.assertIn(f'"{p["key"]}"' if lang == "en" else f"„{p['key']}”", text, f"{lang}: parametru nedocumentat {p['key']}")
            documented = set(re.findall(r'parameter[a-z]* [„"](\w+)[”"]', text, re.I)) | set(re.findall(r'parametrul [„"](\w+)[”"]', text))
            self.assertLessEqual(documented, {p["key"] for p in REC.PARAMETERS}, f"{lang}: parametri inventati")
            for m in markers:
                self.assertIn(m, text)
        # sectiunile si ordinea din cod = cele din text
        self.assertEqual(REC.sort_for("for_you"), [("total_plays", -1), ("likes", -1)])
        self.assertEqual(REC.sort_for("popular"), [("likes", -1), ("total_plays", -1)])
        self.assertEqual(REC.sort_for("new"), [("created_at", -1)])

    def test_server_discover_uses_the_documented_module_and_info_endpoint_is_public(self):
        src = (HERE.parent / "server.py").read_text(encoding="utf-8")
        body = src[src.index('@api.get("/games/discover")'):src.index('@api.get("/games/mine")')]
        self.assertIn("REC.discover_games", body)
        self.assertIn('@api.get("/games/recommendation-info")\nasync def recommendation_info():', body)   # fara autentificare
        self.assertIn('@api.delete("/games/recently-played")', src)


# =============================================================================================== migratii
class Migrations(unittest.TestCase):
    def test_up_is_idempotent_and_down_removes_indexes(self):
        async def go():
            db = FakeDB()
            first = await CM.migrate_up(db)
            self.assertEqual(first, [m[0] for m in CM.MIGRATIONS])
            self.assertEqual(await CM.migrate_up(db), [])
            self.assertIn("inc_id", db.security_incidents.indexes)
            self.assertIn("merchant_user", db.merchant_profiles.indexes)
            self.assertEqual(await CM.migrate_down(db, 1), ["0003_merchant_profiles"])
            self.assertNotIn("merchant_user", db.merchant_profiles.indexes)
            self.assertIn("inc_id", db.security_incidents.indexes)                               # datele/indexurile celorlalte raman
            self.assertEqual(await CM.migrate_up(db), ["0003_merchant_profiles"])
        run(go())

    def test_migrations_do_not_modify_existing_documents(self):
        async def go():
            db = FakeDB()
            await db.users.insert_one({"user_id": "old", "email": "o@e.com"})
            await db.shop_items.insert_one({"item_id": "i", "owner_id": "old", "price": 5})
            await CM.migrate_up(db)
            self.assertEqual(await db.users.find_one({"user_id": "old"}, {"_id": 0}), {"user_id": "old", "email": "o@e.com"})
            self.assertEqual((await db.shop_items.find_one({"item_id": "i"}, {"_id": 0}))["price"], 5)
        run(go())

    def test_unique_indexes_protect_the_flows(self):
        async def go():
            db = FakeDB()
            await CM.migrate_up(db)
            await db.merchant_profiles.insert_one({"user_id": "x"})
            with self.assertRaises(Exception) as e:
                await db.merchant_profiles.insert_one({"user_id": "x"})
            self.assertIn("Duplicate", type(e.exception).__name__)
        run(go())


# =============================================================================================== stergere / export (Art. 15-17, DSA Art. 30(4))
class ErasureAndExport(unittest.TestCase):
    async def seeded(self):
        from astran_sandbox import privacy as P
        db = await new_db()
        u = await add_user(db, "e1", years_ago(25), email="e1@example.com", has_password=True)
        await DC.request_correction(db, u, years_ago(26), "typo", None, reauth_method="password", provider_configured=False, now=NOW)
        await PD.request_email_change(db, await fresh(db, "e1"), "e1new@example.com", reauth_method="password", notifier=Notifier(), confirm_url_base="https://app.test")
        await PD.audit(db, "e1", "email_change_requested", {"new_email_masked": "e***@e***.com"})
        return P, db

    def test_export_contains_the_new_personal_data_and_no_secrets(self):
        async def go():
            P, db = await self.seeded()
            await M.declare_status(db, {"user_id": "e1"}, {"seller_status": "trader", "assessment": TRADER_ANSWERS, "trader": TRADER})
            await M.review(db, LEGAL, "e1", "verified", "Checked.")
            out = await P.build_export(db, "e1")
            corr = out["date_of_birth_corrections"][0]
            self.assertEqual((corr["old_date_of_birth"], corr["new_date_of_birth"], corr["reason_code"]), (years_ago(25), years_ago(26), "typo"))
            self.assertEqual(out["email_change_requests"][0]["new_email"], "e1new@example.com")
            self.assertEqual(out["seller_profile"]["trader"]["name"], "Studio SRL")
            self.assertNotIn("reviewed_by", out["seller_profile"]["verification"])
            blob = str(out)
            for secret in ("confirm_hash", "revert_hash", "old_dob_enc", "new_dob_enc"):
                self.assertNotIn(secret, blob)
        run(go())

    def test_account_deletion_removes_corrections_and_keeps_trader_data_six_months_pseudonymized(self):
        async def go():
            P, db = await self.seeded()
            await M.declare_status(db, {"user_id": "e1"}, {"seller_status": "trader", "assessment": TRADER_ANSWERS, "trader": TRADER})
            await P._step_profile_data(db, "e1", "pseud-e1")
            for coll in ("email_change_requests", "dob_corrections", "personal_data_audit"):
                self.assertEqual(await db[coll].count_documents({"user_id": "e1"}), 0, coll)
            self.assertIsNone(await db.merchant_profiles.find_one({"user_id": "e1"}))            # nu mai e legat de contul sters
            kept = await db.merchant_profiles.find_one({"user_id": "pseud-e1"}, {"_id": 0})
            self.assertTrue(kept["account_deleted"])
            days = (kept["retain_until"] - INC.now_utc()).days
            self.assertTrue(180 <= days <= 184, days)                                            # Art. 30(4): ~6 luni
            await P.sweep_retention(db, now=INC.now_utc() + timedelta(days=200))
            self.assertIsNone(await db.merchant_profiles.find_one({"user_id": "pseud-e1"}))      # apoi dispare
            # non-comerciant: sters imediat
            await M.declare_status(db, {"user_id": "n1"}, {"seller_status": "non_trader", "assessment": NON_TRADER_ANSWERS})
            await P._step_profile_data(db, "n1", "pseud-n1")
            self.assertIsNone(await db.merchant_profiles.find_one({"user_id": "n1"}))
            self.assertIsNone(await db.merchant_profiles.find_one({"user_id": "pseud-n1"}))
        run(go())


class RestrictedAccountsCanExerciseArt16(unittest.TestCase):
    def test_personal_data_paths_are_reachable_when_restricted_or_suspended(self):
        for p in ("/api/account/personal-data", "/api/account/email-change/request", "/api/account/dob-correction", "/api/privacy/reauth/code"):
            self.assertTrue(AP.path_allowed_when_restricted(p), p)
        self.assertFalse(AP.path_allowed_when_restricted("/api/shop/items"))
        src = (HERE.parent / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/account/personal-data", "/api/account/email-change", "/api/account/dob-correction"', src)


if __name__ == "__main__":
    unittest.main()
