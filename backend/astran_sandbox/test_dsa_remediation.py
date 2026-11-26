"""Teste DSA remediation (Art. 9, 10, 15, 16, 17, 20, 24(5)) - FakeDB, fara FastAPI/Mongo/retea.

    cd backend && python -m unittest astran_sandbox.test_dsa_remediation -v
"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from astran_sandbox import authority_orders as AO
from astran_sandbox import dsa_applicability as APPL
from astran_sandbox import dsa_delivery as DL
from astran_sandbox import dsa_migrations as DM
from astran_sandbox import dsa_public_notices as PN
from astran_sandbox import dsa_tdb as TDB
from astran_sandbox import dsa_transparency as TR
from astran_sandbox import moderation_service as MS
from astran_sandbox.test_moderation import Base, LONG, raises, run


async def ok_sender(to, subject, body):
    ok_sender.sent.append((to, subject, body))
    return True, None
ok_sender.sent = []


async def bad_sender(to, subject, body):
    return False, "smtp_send_failed"


async def exploding_sender(to, subject, body):
    raise RuntimeError("boom")


class DSABase(Base):
    def setUp(self):
        super().setUp()
        ok_sender.sent.clear()
        for u in ({"user_id": "legal", "username": "legal", "is_legal_officer": True},
                  {"user_id": "legalmod", "username": "legalmod", "is_legal_officer": True, "is_moderator": True},
                  {"user_id": "noemail", "username": "noemail"}):
            run(self.db.users.insert_one(u))
            self.U[u["user_id"]] = u
        run(self.db.games.insert_one({"game_id": "g2", "owner_id": "noemail", "title": "Quiet Game", "description": "d", "is_public": True, "status": "active"}))
        run(self.db.games.insert_one({"game_id": "g3", "owner_id": "bad", "title": "Third Game", "description": "d", "is_public": True, "status": "active"}))
        run(DM.migrate_up(self.db))
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in list(os.environ) if k.startswith(("ASTRAN_DSA", "ASTRAN_TDB", "ASTRAN_ORDERS", "ASTRAN_APPEAL"))])

    def remove(self, target="g1", actor="m1", reporter="rep", kind=MS.KIND_RULE, **body):
        rid, _ = self.report(target_id=target, reporter=reporter, kind=kind, category="illegal_content" if kind == MS.KIND_NOTICE else "harassment",
                             notice=self.notice_body(content_location=f"astran://game/{target}") if kind == MS.KIND_NOTICE else None)
        body.setdefault("action", "remove_content_policy")
        if body["action"] == "remove_content_policy":
            body.setdefault("tos_provision", "3.2(c)")
        return rid, self.decide(rid, actor=actor, **body)["decision"]

    def order_body(self, **kw):
        b = {"order_type": AO.ART9, "authority": {"name": "Tribunalul X", "member_state": "RO", "contact_email": "registry@court.example", "their_reference": "A-1"},
             "legal_basis": "Art. 1 Legea 123/2020", "statement_of_reasons": "The content is illegal because it infringes provision Y.",
             "issued_at": "2026-11-20T10:00:00+00:00", "territorial_scope": "RO", "redress_information": True,
             "targets": [{"url": "astran://game/g1"}], "illegal_content_category": "hate_speech"}
        b.update(kw)
        return b

    def approved_order(self, body=None, creator="adm", reviewer="legal"):
        o = run(AO.create_order(self.db, self.U[creator], body or self.order_body()))
        run(AO.set_authenticity(self.db, self.U[reviewer], o["order_id"], verified=True, method="Called back the court registry on its published number."))
        run(AO.legal_review(self.db, self.U[reviewer], o["order_id"], decision="approve", reasoning="Order is complete and appears legitimate."))
        return o["order_id"]


# ============================================================================================ aplicabilitate
class Applicability(unittest.TestCase):
    def test_nothing_mandatory_when_status_undetermined(self):
        o = APPL.obligations({})
        for k in ("art_15", "art_16", "art_17", "art_20", "art_24_5"):
            self.assertEqual(o[k]["status"], APPL.UNDETERMINED, k)
        self.assertEqual(o["art_9"]["status"], APPL.APPLIES)
        self.assertFalse(APPL.tdb_submission_required({}))

    def test_micro_small_platform_is_exempt_from_art_15_20_24(self):
        o = APPL.obligations({"ASTRAN_DSA_SERVICE_CLASS": "online_platform", "ASTRAN_DSA_ENTERPRISE_SIZE": "micro_small"})
        self.assertEqual((o["art_15"]["status"], o["art_20"]["status"], o["art_24_5"]["status"]), ("not_applicable",) * 3)
        self.assertEqual((o["art_16"]["status"], o["art_17"]["status"]), ("applies", "applies"))    # sectiunea 2: si pentru mici

    def test_large_platform_requires_tdb_but_plain_host_does_not(self):
        self.assertTrue(APPL.tdb_submission_required({"ASTRAN_DSA_SERVICE_CLASS": "online_platform", "ASTRAN_DSA_ENTERPRISE_SIZE": "not_micro_small"}))
        self.assertFalse(APPL.tdb_submission_required({"ASTRAN_DSA_SERVICE_CLASS": "hosting", "ASTRAN_DSA_ENTERPRISE_SIZE": "not_micro_small"}))

    def test_invalid_values_are_reported_not_guessed(self):
        self.assertEqual(APPL.classification({"ASTRAN_DSA_SERVICE_CLASS": "banana"})["invalid_values"], ["ASTRAN_DSA_SERVICE_CLASS"])


# ============================================================================================ Art. 17 livrare
class StatementDelivery(DSABase):
    def test_statement_creates_pending_email_delivery_and_in_app_notification(self):
        _, d = self.remove()
        dl = run(self.db[DL.COLL].find_one({"kind": "statement_of_reasons"}))
        self.assertEqual(dl["status"], "pending")
        self.assertIn("moderation_decision", [n["kind"] for n in run(MS.list_notifications(self.db, "bad"))])

    def test_failed_send_is_never_marked_delivered_and_is_retried(self):
        _, d = self.remove()
        t0 = MS.now_utc()
        r = run(DL.process_due(self.db, bad_sender, now=t0))
        self.assertEqual(r[0]["status"], "failed")
        row = run(self.db[DL.COLL].find_one({"kind": "statement_of_reasons"}))
        self.assertIsNone(row["sent_at"])
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(run(DL.public_status(self.db, f"sor:{d['statement_id']}"))["status"], "pending")      # nu "sent"
        self.assertEqual(run(DL.process_due(self.db, ok_sender, now=t0 + timedelta(seconds=5))), [])           # backoff respectat
        r2 = run(DL.process_due(self.db, ok_sender, now=t0 + timedelta(hours=1)))
        self.assertEqual(r2[0]["status"], "sent")
        self.assertEqual(run(DL.public_status(self.db, f"sor:{d['statement_id']}"))["status"], "sent")
        self.assertEqual(ok_sender.sent[0][0], "bad@example.com")

    def test_exception_in_sender_counts_as_failure(self):
        self.remove()
        self.assertEqual(run(DL.process_due(self.db, exploding_sender))[0]["status"], "failed")

    def test_abandoned_after_max_attempts_and_manual_retry(self):
        os.environ["ASTRAN_DELIVERY_MAX_ATTEMPTS"] = "2"
        self.addCleanup(os.environ.pop, "ASTRAN_DELIVERY_MAX_ATTEMPTS", None)
        self.remove()
        t = MS.now_utc()
        run(DL.process_due(self.db, bad_sender, now=t))
        r = run(DL.process_due(self.db, bad_sender, now=t + timedelta(hours=2)))
        self.assertEqual(r[0]["status"], "abandoned")
        did = r[0]["delivery_id"]
        with raises("forbidden"):
            run(DL.admin_retry(self.db, self.U["bad"], did))
        run(DL.admin_retry(self.db, self.U["m1"], did))
        self.assertEqual(run(DL.process_due(self.db, ok_sender))[0]["status"], "sent")

    def test_no_email_on_file_is_skipped_not_failed(self):
        self.remove(target="g2")
        r = run(DL.process_due(self.db, ok_sender))
        self.assertEqual(r[0]["status"], "skipped_no_contact")
        self.assertEqual(ok_sender.sent, [])
        self.assertEqual(run(DL.public_status(self.db, f"sor:{run(self.db.statements_of_reasons.find_one({}))['sor_id']}"))["status"], "no_email_on_file")

    def test_enqueue_is_idempotent(self):
        a = run(DL.enqueue(self.db, kind="statement_of_reasons", dedupe_key="k", recipient={"user_id": "bad"}, ref={}))
        b = run(DL.enqueue(self.db, kind="statement_of_reasons", dedupe_key="k", recipient={"user_id": "bad"}, ref={}))
        self.assertEqual(a["delivery_id"], b["delivery_id"])
        self.assertEqual(run(self.db[DL.COLL].count_documents({"dedupe_key": "k"})), 1)

    def test_email_contains_statement_but_not_reporter_identity(self):
        self.remove(kind=MS.KIND_NOTICE, action="remove_content_illegal", legal_ground="Art. 1 Legea 1/2020")
        run(DL.process_due(self.db, ok_sender))
        to, subject, body = next(m for m in ok_sender.sent if "statement of reasons" in m[1].lower())
        self.assertIn("Statement of reasons", body)
        self.assertIn("Legea 1/2020", body)
        self.assertIn("complaint", body.lower())
        for secret in ("Jane Doe", "jane@example.com", "rep@example.com", "reporter"):
            self.assertNotIn(secret, body)
        self.assertNotIn("notifier", json.dumps(run(self.db.statements_of_reasons.find_one({})), default=str).lower())

    def test_delivery_audit_has_no_email_address(self):
        self.remove()
        run(DL.process_due(self.db, ok_sender))
        blob = json.dumps(run(MS.audit_history(self.db)), default=str)
        self.assertNotIn("bad@example.com", blob)
        self.assertIn("delivery_sent", blob)

    def test_default_smtp_sender_reports_unconfigured_honestly(self):
        try:
            from astran_sandbox import project_security as PS
        except Exception:  # noqa: BLE001
            self.skipTest("project_security dependencies unavailable")
        with mock.patch.object(PS, "email_is_configured", return_value=False):
            self.assertEqual(run(DL.smtp_sender("a@b.c", "s", "b")), (False, "smtp_not_configured"))
        with mock.patch.object(PS, "email_is_configured", return_value=True), mock.patch.object(PS, "send_plain_email", return_value=False):
            self.assertEqual(run(DL.smtp_sender("a@b.c", "s", "b")), (False, "smtp_send_failed"))

    def test_user_sees_delivery_status_but_not_technical_errors(self):
        _, d = self.remove()
        run(DL.process_due(self.db, bad_sender))
        view = run(MS.my_decision(self.db, "bad", d["decision_id"]))
        self.assertEqual(view["delivery"]["status"], "pending")
        self.assertNotIn("smtp", json.dumps(view, default=str).lower())


# ============================================================================================ Art. 16 public + own initiative
class PublicNotices(DSABase):
    async def exists(self, tt, tid, cur):
        return await MS._report_target_exists(self.db, tt, tid, cur) if hasattr(MS, "_report_target_exists") else True

    def submit(self, **kw):
        b = {"content_url": "astran://game/g1", "category": "hate_speech", "explanation": LONG + " It is allegedly illegal.", "reporter_name": "Anon Person",
             "reporter_email": "anon@example.com", "good_faith_declaration": True, "alleged_legal_basis": "Art. 5 Legea 2/2021"}
        b.update(kw)
        return run(PN.submit_public_notice(self.db, b, ip="203.0.113.9", target_exists=self._te))

    async def _te(self, tt, tid, cur):
        return (await self.db.games.find_one({"game_id": tid})) is not None if tt == "game" else (await self.db.users.find_one({"user_id": tid})) is not None

    def test_non_registered_person_can_submit_and_it_is_persisted_without_classifying_as_illegal(self):
        out = self.submit()
        r = run(self.db.reports.find_one({"report_id": out["report_id"]}))
        self.assertIsNone(r["reporter_id"])
        self.assertEqual((r["report_kind"], r["status"]), (MS.KIND_NOTICE, MS.STATUS_PENDING))
        self.assertEqual(run(self.db.moderation_decisions.count_documents({})), 0)              # nimic nu e automat "ilegal"
        self.assertIn("tracking_token", out)
        self.assertNotIn(out["tracking_token"], json.dumps(r, default=str))                      # doar hash-ul tokenului
        self.assertNotIn("203.0.113.9", json.dumps(r, default=str))                              # doar HMAC al IP-ului
        self.assertEqual(run(self.db.notifications.count_documents({})) if hasattr(self.db, "notifications") else 0, 0)

    def test_receipt_email_is_queued_and_sent_to_anonymous_notifier(self):
        out = self.submit()
        run(DL.process_due(self.db, ok_sender))
        self.assertEqual(ok_sender.sent[0][0], "anon@example.com")
        self.assertIn(out["report_id"], ok_sender.sent[0][2])

    def test_outcome_email_for_anonymous_notifier(self):
        out = self.submit()
        self.decide(out["report_id"], actor="m1", action="remove_content_illegal", legal_ground="Art. 5 Legea 2/2021")
        run(DL.process_due(self.db, ok_sender))
        bodies = [b for _, s, b in ok_sender.sent if "decision" in s.lower()]
        self.assertTrue(bodies and "action was taken" in bodies[0])

    def test_validation_requires_contact_data_and_good_faith(self):
        for bad in ({"reporter_email": None}, {"reporter_name": None}, {"good_faith_declaration": False}, {"explanation": "short"}):
            with self.assertRaises(MS.ModerationError):
                self.submit(**bad)

    def test_url_id_and_account_identifiers_supported_private_messages_not(self):
        self.submit(content_url="astran://game/g1")
        self.submit(content_url=None, account_username="OFFENDER", category="harassment", reporter_email="b@example.com", content_location="account")
        with raises("target_invalid"):
            self.submit(content_url="astran://message/gm1", reporter_email="c@example.com")
        with raises("content_reference_invalid"):
            self.submit(content_url="javascript:alert(1)", reporter_email="d@example.com")

    def test_duplicates_honeypot_and_daily_cap(self):
        self.submit()
        with raises("duplicate_notice"):
            self.submit()
        with raises("submission_rejected"):
            self.submit(hp="http://spam", reporter_email="e@example.com")
        os.environ["ASTRAN_PUBLIC_NOTICE_DAILY_CAP"] = "2"
        self.addCleanup(os.environ.pop, "ASTRAN_PUBLIC_NOTICE_DAILY_CAP", None)
        self.submit(content_url="astran://game/g2", content_location="astran://game/g2")
        with raises("notice_limit_reached"):
            self.submit(content_url="astran://game/g3", content_location="astran://game/g3")

    def test_status_requires_the_secret_token_and_does_not_leak_between_reports(self):
        a = self.submit()
        b = self.submit(content_url="astran://game/g2", content_location="x", reporter_email="z@example.com")
        self.assertEqual(run(PN.public_status(self.db, a["report_id"], a["tracking_token"]))["report_id"], a["report_id"])
        for rid, tok in ((a["report_id"], b["tracking_token"]), (a["report_id"], "wrong"), ("rep_nope", a["tracking_token"]), (a["report_id"], None)):
            with raises("not_found"):
                run(PN.public_status(self.db, rid, tok))

    def test_abuse_metadata_is_purged_after_retention(self):
        a = self.submit()
        run(self.db.reports.update_one({"report_id": a["report_id"]}, {"$set": {"created_at": MS.now_utc() - timedelta(days=40)}}))
        self.assertEqual(run(PN.purge_abuse_metadata(self.db, 30)), 1)
        self.assertNotIn("submitter", run(self.db.reports.find_one({"report_id": a["report_id"]})))

    def test_trusted_flagger_flag_is_recorded_on_notices_only_from_admin_granted_status(self):
        run(MS.set_flag(self.db, self.U["adm"], "rep", "trusted_flagger", True))
        with raises("admin_required"):
            run(MS.set_flag(self.db, self.U["m1"], "rep", "trusted_flagger", True))
        rid, _ = self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body())
        self.assertTrue(run(self.db.reports.find_one({"report_id": rid}))["notice"]["trusted_flagger"])


class OwnInitiativeAndAppeals(DSABase):
    def test_own_initiative_case_gets_statement_with_initiated_by_astran(self):
        c = run(MS.open_own_initiative_case(self.db, self.U["m1"], target_type="game", target_id="g1", category="spam", summary="Found while patrolling new games."))
        res = self.decide(c["report_id"], actor="m2", action="remove_content_policy", tos_provision="4.1")
        sor = run(self.db.statements_of_reasons.find_one({"decision_id": res["decision"]["decision_id"]}))
        self.assertEqual((sor["initiated_by"], sor["source"]["type"]), ("astran", "own_initiative"))
        self.assertEqual(run(self.db.moderation_decisions.find_one({}))["source"], MS.SRC_OWN)
        with raises("forbidden"):
            run(MS.open_own_initiative_case(self.db, self.U["bad"], target_type="game", target_id="g1", category="spam", summary="x" * 12))

    def _account_decision(self, **kw):
        rid, _ = self.report(target_type="user", target_id="bad")
        return rid, self.decide(rid, actor="adm", action="suspend_account_permanent", **kw)["decision"]

    def test_complaint_can_modify_a_permanent_suspension(self):
        rid, d = self._account_decision(tos_provision="5.1")
        a = run(MS.submit_appeal(self.db, self.U["bad"], d["decision_id"], grounds="This is disproportionate for a first offence."))
        out = run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="modify", reasoning="A 7 day restriction is proportionate here.",
                                    modification={"days": 7, "scope": "full"}))
        self.assertEqual(out["status"], MS.APPEAL_MODIFIED)
        old = run(self.db.moderation_decisions.find_one({"decision_id": d["decision_id"]}))
        self.assertEqual(old["status"], MS.DEC_MODIFIED)
        new = run(self.db.moderation_decisions.find_one({"supersedes": d["decision_id"]}))
        self.assertEqual((new["action"], new["status"]), (MS.ACTION_RESTRICT_ACCOUNT, MS.DEC_IN_EFFECT))
        self.assertIsNotNone(run(self.db.statements_of_reasons.find_one({"decision_id": new["decision_id"]})))
        self.assertTrue(run(self.db.moderation_appeals.find_one({"appeal_id": a["appeal_id"]}))["reviewed_by_human"])
        run(DL.process_due(self.db, ok_sender))
        subjects = [m[1].lower() for m in ok_sender.sent]
        self.assertEqual(len(subjects), 3)                                                        # SoR initial + SoR nou + rezultatul plangerii
        self.assertEqual(sum("statement of reasons" in x for x in subjects), 2)
        self.assertEqual(sum("complaint" in x for x in subjects), 1)
        with raises("decision_not_appealable"):
            run(MS.submit_appeal(self.db, self.U["bad"], d["decision_id"], grounds="Appealing again the replaced decision."))

    def test_modify_is_rejected_for_content_decisions_and_leaves_complaint_open(self):
        rid, _ = self.report()
        d = self.decide(rid, action="remove_content_policy", tos_provision="3.2")["decision"]
        a = run(MS.submit_appeal(self.db, self.U["bad"], d["decision_id"], grounds="The game follows every rule, please check again."))
        with raises("modify_not_supported"):
            run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="modify", reasoning="Trying to modify a content decision.", modification={"days": 3}))
        self.assertEqual(run(self.db.moderation_appeals.find_one({"appeal_id": a["appeal_id"]}))["status"], MS.APPEAL_RECEIVED)

    def test_original_moderator_cannot_review_and_no_invented_legal_deadline(self):
        rid, _ = self.report()
        d = self.decide(rid, actor="m1", action="remove_content_policy", tos_provision="3.2")["decision"]
        a = run(MS.submit_appeal(self.db, self.U["bad"], d["decision_id"], grounds="The game follows every rule, please check again."))
        with raises("reviewer_not_independent"):
            run(MS.resolve_appeal(self.db, self.U["m1"], a["appeal_id"], outcome="maintain", reasoning="I still think it was right."))
        t = MS.appeal_timing(run(self.db.moderation_appeals.find_one({"appeal_id": a["appeal_id"]})), env={})
        self.assertIsNone(t["legal_deadline"])
        self.assertIsNone(t["internal_target_days"])
        self.assertFalse(t["internal_target_exceeded"])

    def test_internal_target_is_optional_and_configurable(self):
        a = {"created_at": MS.now_utc() - timedelta(days=10), "resolved_at": None}
        self.assertTrue(MS.appeal_timing(a, env={"ASTRAN_APPEAL_TARGET_DAYS": "7"})["internal_target_exceeded"])
        self.assertIsNone(MS.appeal_timing(a, env={"ASTRAN_APPEAL_TARGET_DAYS": "abc"})["internal_target_days"])

    def test_reversal_is_recorded_and_effect_undone(self):
        rid, d = self.remove()
        self.assertFalse(run(self.db.games.find_one({"game_id": "g1"}))["is_public"])
        a = run(MS.submit_appeal(self.db, self.U["bad"], d["decision_id"], grounds="The game follows every rule, please check again."))
        run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="reverse", reasoning="Content is compliant after re-review."))
        self.assertTrue(run(self.db.games.find_one({"game_id": "g1"}))["is_public"])
        with raises("decision_not_appealable"):
            run(MS.submit_appeal(self.db, self.U["bad"], d["decision_id"], grounds="Appealing an already reversed decision now."))


# ============================================================================================ Art. 24(5)
CAT_OK = "hate_speech"


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def post_json(self, url, token, payload):
        self.calls.append((url, token, payload))
        r = self.responses.pop(0) if self.responses else (201, {"uuid": "u-1", "id": 7, "permalink": "https://x/y"})
        if isinstance(r, Exception):
            raise r
        return r


class TransparencyDatabase(DSABase):
    def required(self):
        os.environ.update({"ASTRAN_DSA_SERVICE_CLASS": "online_platform", "ASTRAN_DSA_ENTERPRISE_SIZE": "not_micro_small"})

    def api(self):
        os.environ.update({"ASTRAN_TDB_ENABLED": "1", "ASTRAN_TDB_MODE": "sandbox", "ASTRAN_TDB_API_URL": "https://sandbox.example/api/v1", "ASTRAN_TDB_API_TOKEN": "tok"})

    def illegal(self):
        rid, _ = self.report(kind=MS.KIND_NOTICE, category=CAT_OK, notice=self.notice_body())
        return self.decide(rid, action="remove_content_illegal", legal_ground="Art. 5 Legea 2/2021 contact jane@example.com")["decision"]

    def test_nothing_is_queued_when_legal_status_is_undetermined(self):
        self.illegal()
        self.assertEqual(run(self.db[TDB.COLL].count_documents({})), 0)

    def test_payload_has_no_personal_data_and_redacts_free_text(self):
        self.required()
        d = self.illegal()
        s = run(self.db[TDB.COLL].find_one({"decision_id": d["decision_id"]}))
        self.assertEqual(s["status"], "pending")
        blob = json.dumps(s["payload"]).lower()
        for secret in ("bad@example.com", "offender", "jane", "rep@example.com", "bad", "g1", d["decision_id"], d["report_id"]):
            if secret == "bad":
                continue                                    # subsir comun in engleza; id-ul real e verificat mai jos
            self.assertNotIn(secret.lower(), blob, secret)
        self.assertNotIn('"bad"', blob)
        self.assertIn("[redacted]", s["payload"]["illegal_content_legal_ground"])
        self.assertEqual(s["payload"]["source_type"], "SOURCE_ARTICLE_16")
        self.assertEqual(s["payload"]["puid"], d["statement_id"])
        self.assertNotIn("moderator", blob)

    def test_unsure_category_needs_review_instead_of_guessing(self):
        self.required()
        rid, _ = self.report()
        d = self.decide(rid, action="remove_content_policy", tos_provision="3.2")["decision"]       # harassment -> fara mapare sigura
        s = run(self.db[TDB.COLL].find_one({"decision_id": d["decision_id"]}))
        self.assertEqual(s["status"], "needs_review")
        self.assertEqual(run(TDB.submit_pending(self.db, FakeClient())), [])
        with raises("category_invalid"):
            run(TDB.set_category(self.db, self.U["adm"], s["submission_id"], "category"))
        r = run(TDB.set_category(self.db, self.U["adm"], s["submission_id"], "STATEMENT_CATEGORY_CYBER_VIOLENCE"))
        self.assertEqual(r["status"], "pending")

    def test_leak_of_identifiers_blocks_submission(self):
        p, issues = TDB.build_payload({"sor_id": "sor_1"}, {"action": "remove_content_policy", "created_at": MS.now_utc(), "basis": {"type": "tos", "tos_provision": "uniqueuser42 posted"},
                                                            "target_type": "game"}, {"category": "hate_speech", "report_kind": MS.KIND_RULE}, forbidden=["uniqueuser42"])
        self.assertNotIn("uniqueuser42", json.dumps(p))                      # redactat inainte de verificare
        self.assertEqual(TDB.redact_text("mail a@b.co ip 10.0.0.1 http://x.y @nick tel +40 722 123 456 usr_1234abcd", []),
                         "mail [redacted] ip [redacted] [redacted] [redacted] tel [redacted] [redacted]")

    def test_successful_submission_requires_confirmation_from_the_service(self):
        self.required(); self.api()
        d = self.illegal()
        c = FakeClient((201, {"uuid": "abc", "id": 9, "permalink": "https://t/9"}))
        r = run(TDB.submit_pending(self.db, c))
        self.assertEqual(r[0]["status"], "submitted")
        self.assertEqual(c.calls[0][0], "https://sandbox.example/api/v1/statement")
        s = run(self.db[TDB.COLL].find_one({"decision_id": d["decision_id"]}))
        self.assertEqual(s["external_ref"]["uuid"], "abc")
        self.assertEqual(run(TDB.submit_pending(self.db, c)), [])                 # nu se retrimite (fara duplicate)
        run(TDB.maybe_enqueue(self.db, run(self.db.statements_of_reasons.find_one({})), d))
        self.assertEqual(run(self.db[TDB.COLL].count_documents({})), 1)

    def test_created_without_identifiers_is_not_success_and_server_errors_retry_then_abandon(self):
        self.required(); self.api()
        os.environ["ASTRAN_TDB_MAX_ATTEMPTS"] = "2"
        self.addCleanup(os.environ.pop, "ASTRAN_TDB_MAX_ATTEMPTS", None)
        self.illegal()
        t = MS.now_utc()
        self.assertEqual(run(TDB.submit_pending(self.db, FakeClient((201, {}))))[0]["status"], "failed")
        r = run(TDB.submit_pending(self.db, FakeClient((503, "down")), now=t + timedelta(hours=1)))
        self.assertEqual(r[0]["status"], "abandoned")
        run(TDB.retry(self.db, self.U["adm"], r[0]["submission_id"]))
        self.assertEqual(run(TDB.submit_pending(self.db, FakeClient(RuntimeError("net"))))[0]["status"], "failed")

    def test_rejected_and_duplicate_puid_are_not_retried_blindly(self):
        self.required(); self.api()
        self.illegal()
        self.assertEqual(run(TDB.submit_pending(self.db, FakeClient((422, {"errors": {"puid": "has already been taken"}}))))[0]["status"], "needs_reconciliation")

    def test_not_configured_api_does_nothing_and_export_workflow_is_manual(self):
        self.required()                                                          # obligatie stabilita, dar fara credentiale
        d = self.illegal()
        self.assertEqual(run(TDB.submit_pending(self.db, FakeClient())), [])
        s = run(self.db[TDB.COLL].find_one({"decision_id": d["decision_id"]}))
        self.assertEqual(s["status"], "pending")
        with raises("admin_required"):
            run(TDB.export_statements(self.db, self.U["m1"]))
        ex = run(TDB.export_statements(self.db, self.U["adm"]))
        self.assertEqual(len(ex["statements"]), 1)
        self.assertEqual(run(self.db[TDB.COLL].find_one({"submission_id": s["submission_id"]}))["status"], "exported")
        with raises("field_required"):
            run(TDB.confirm_manual(self.db, self.U["adm"], s["submission_id"], ""))
        out = run(TDB.confirm_manual(self.db, self.U["adm"], s["submission_id"], "webform confirmation 2026-11-26 #1234"))
        self.assertFalse(out["verified_with_service"])
        self.assertEqual(run(TDB.export_statements(self.db, self.U["adm"]))["skipped"][0]["reason"], "already_submitted")

    def test_admin_overview_counts_and_authorization(self):
        self.required(); self.illegal()
        ov = run(TDB.overview(self.db, self.U["adm"]))
        self.assertEqual(ov["counts"]["pending"], 1)
        self.assertNotIn("payload", ov["items"][0])
        self.assertFalse(ov["config"]["api_configured"])
        with raises("admin_required"):
            run(TDB.overview(self.db, self.U["m1"]))

    def test_export_available_even_if_obligation_not_established_but_enqueue_is_not(self):
        self.illegal()
        ex = run(TDB.export_statements(self.db, self.U["adm"]))
        self.assertEqual(len(ex["statements"]), 1)


# ============================================================================================ Art. 9 / 10
class AuthorityOrders(DSABase):
    def test_only_legal_or_admin_can_access_orders(self):
        for u in ("bad", "m1", "rep"):
            with raises("forbidden"):
                run(AO.list_orders(self.db, self.U[u]))
            with raises("forbidden"):
                run(AO.create_order(self.db, self.U[u], self.order_body()))
        self.assertEqual(run(AO.list_orders(self.db, self.U["legal"]))["items"], [])
        self.assertEqual(run(AO.list_orders(self.db, self.U["adm"]))["items"], [])

    def test_incomplete_order_cannot_be_approved_or_executed_and_upload_is_not_execution(self):
        o = run(AO.create_order(self.db, self.U["adm"], self.order_body(legal_basis=None, redress_information=False, territorial_scope=None)))
        self.assertEqual(o["status"], AO.ST_INCOMPLETE)
        self.assertEqual({"legal_basis", "redress_information", "territorial_scope"}, set(o["validation"]["missing"]))
        run(AO.add_attachment(self.db, self.U["adm"], o["order_id"], filename="order.pdf", data=b"%PDF-1.4 hello"))
        self.assertTrue(run(self.db.games.find_one({"game_id": "g1"}))["is_public"])          # incarcarea nu executa nimic
        with raises("order_incomplete"):
            run(AO.legal_review(self.db, self.U["legal"], o["order_id"], decision="approve", reasoning="Trying to approve an incomplete order."))
        with raises("order_not_approved"):
            run(AO.execute_art9(self.db, self.U["adm"], o["order_id"]))
        fixed = run(AO.supplement(self.db, self.U["adm"], o["order_id"], {"legal_basis": "Art. 1 Legea 123/2020", "redress_information": True, "territorial_scope": "RO"}))
        self.assertEqual(fixed["status"], AO.ST_PENDING)

    def test_art9_requires_exact_content_identifier(self):
        o = run(AO.create_order(self.db, self.U["adm"], self.order_body(targets=[{"target_type": "user", "target_id": "bad"}])))
        self.assertIn("targets:art9_requires_exact_content_identifier", o["validation"]["missing"])
        with raises("target_url_invalid"):
            run(AO.create_order(self.db, self.U["adm"], self.order_body(targets=[{"url": "ftp://x/y"}])))

    def test_four_eyes_authenticity_and_execution_gate(self):
        o = run(AO.create_order(self.db, self.U["legal"], self.order_body()))
        with raises("authenticity_not_verified"):
            run(AO.legal_review(self.db, self.U["adm"], o["order_id"], decision="approve", reasoning="Reviewing without authenticity check."))
        run(AO.set_authenticity(self.db, self.U["legal"], o["order_id"], verified=True, method="Verified via the registry's published number."))
        with raises("reviewer_not_independent"):
            run(AO.legal_review(self.db, self.U["legal"], o["order_id"], decision="approve", reasoning="Approving my own recorded order."))
        run(AO.legal_review(self.db, self.U["adm"], o["order_id"], decision="approve", reasoning="Complete and authentic; proportionate scope."))
        with raises("execution_requires_moderator_capability"):
            run(AO.execute_art9(self.db, self.U["legal"], o["order_id"]))
        res = run(AO.execute_art9(self.db, self.U["legalmod"], o["order_id"]))
        self.assertEqual(res["status"], AO.ST_EXECUTED)

    def test_execution_removes_content_issues_statement_and_is_idempotent(self):
        oid = self.approved_order(self.order_body(targets=[{"url": "astran://game/g1"}, {"url": "astran://game/g3"}]))
        run(AO.execute_art9(self.db, self.U["adm"], oid))
        self.assertFalse(run(self.db.games.find_one({"game_id": "g1"}))["is_public"])
        self.assertFalse(run(self.db.games.find_one({"game_id": "g3"}))["is_public"])
        sors = run(self.db.statements_of_reasons.find({}).to_list(10))
        self.assertEqual(len(sors), 2)
        o = run(AO.get_order(self.db, self.U["adm"], oid))
        self.assertTrue(all(s["initiated_by"] == "authority_order" and s["order_reference"] == o["reference"] for s in sors))
        self.assertEqual(run(self.db.moderation_decisions.find_one({}))["source"], MS.SRC_ORDER)
        with raises("order_not_approved"):
            run(AO.execute_art9(self.db, self.U["adm"], oid))                          # nu se executa de doua ori
        self.assertEqual(len(run(self.db.moderation_decisions.find({}).to_list(10))), 2)
        self.assertIn("moderation_decision", [n["kind"] for n in run(MS.list_notifications(self.db, "bad"))])

    def test_partial_failure_is_recorded_and_retry_only_redoes_failed_targets(self):
        oid = self.approved_order(self.order_body(targets=[{"url": "astran://game/g1"}, {"url": "astran://game/missing"}]))
        res = run(AO.execute_art9(self.db, self.U["adm"], oid))
        self.assertEqual(res["status"], AO.ST_PARTIAL)
        self.assertEqual([t["state"] for t in res["targets"]], ["executed", "failed"])
        run(self.db.games.insert_one({"game_id": "missing", "owner_id": "bad", "title": "Late", "description": "d", "is_public": True, "status": "active"}))
        res2 = run(AO.execute_art9(self.db, self.U["adm"], oid))
        self.assertEqual(res2["status"], AO.ST_EXECUTED)
        self.assertEqual(len(run(self.db.moderation_decisions.find({}).to_list(10))), 2)

    def test_non_disclosure_withholds_notice_until_legal_releases_it(self):
        body = self.order_body(non_disclosure={"requested": True, "basis": "Criminal procedure confidentiality"})
        o = run(AO.create_order(self.db, self.U["adm"], body))
        run(AO.set_authenticity(self.db, self.U["legal"], o["order_id"], verified=True, method="Verified via the registry's published number."))
        run(AO.legal_review(self.db, self.U["legal"], o["order_id"], decision="approve", reasoning="Non-disclosure confirmed by counsel.", confirm_non_disclosure=True))
        run(AO.execute_art9(self.db, self.U["adm"], o["order_id"]))
        self.assertEqual(run(MS.list_notifications(self.db, "bad")), [])
        self.assertEqual(run(self.db[DL.COLL].count_documents({"kind": "statement_of_reasons"})), 0)
        self.assertEqual(run(AO.release_withheld_notice(self.db, self.U["legal"], o["order_id"], "Investigation closed, disclosure allowed."))["released"], 1)
        self.assertEqual(run(self.db[DL.COLL].count_documents({"kind": "statement_of_reasons"})), 1)

    def test_art10_only_whitelisted_fields_never_credentials(self):
        run(self.db.users.update_one({"user_id": "bad"}, {"$set": {"password_hash": "SECRETHASH", "created_at": MS.now_utc()}}))
        body = self.order_body(order_type=AO.ART10, targets=[{"account_username": "offender"}, {"account_username": "ghost"}],
                               requested_items=["email", "username", "password_hash", "private_messages"], territorial_scope=None)
        oid = self.approved_order(body)
        res = run(AO.prepare_information(self.db, self.U["legal"], oid))
        info = res["information_package"]
        self.assertEqual(sorted(info["fields_provided"]), ["email", "username"])
        self.assertEqual((info["subjects_found"], info["identifiers_not_found"], info["items_refused"]), (1, 1, 2))
        name, ctype, data = run(AO.read_attachment(self.db, self.U["legal"], oid, info["attachment_id"]))
        pkg = json.loads(data)
        self.assertNotIn("SECRETHASH", data.decode())
        self.assertEqual(pkg["subjects"][0]["email"], "bad@example.com")
        self.assertNotIn("password_hash", pkg["subjects"][0])
        self.assertNotIn("bad@example.com", json.dumps(run(AO.get_order(self.db, self.U["legal"], oid)), default=str).replace(data.decode(), ""))  # nu e copiat in documentul ordinului

    def test_art10_cannot_be_executed_as_art9_and_vice_versa(self):
        oid = self.approved_order(self.order_body(order_type=AO.ART10, targets=[{"target_type": "user", "target_id": "bad"}], requested_items=["email"], territorial_scope=None))
        with raises("wrong_order_type"):
            run(AO.execute_art9(self.db, self.U["adm"], oid))
        oid9 = self.approved_order()
        with raises("wrong_order_type"):
            run(AO.prepare_information(self.db, self.U["adm"], oid9))

    def test_attachments_are_private_validated_and_idor_safe(self):
        a = run(AO.create_order(self.db, self.U["adm"], self.order_body()))
        b = run(AO.create_order(self.db, self.U["adm"], self.order_body(authority={"name": "Other", "member_state": "FR", "contact_email": "o@o.example"})))
        att = run(AO.add_attachment(self.db, self.U["legal"], a["order_id"], filename="../../etc/passwd.pdf", data=b"%PDF-1.7 content"))
        self.assertNotIn("/", att["filename"])
        self.assertNotIn("stored_path", att)
        stored = list(AO.storage_dir().rglob(f"{att['attachment_id']}.bin"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(oct(stored[0].stat().st_mode & 0o777), "0o600")
        self.assertNotIn("static", str(stored[0]).lower())
        for bad in (("x.exe", b"MZ"), ("x.pdf", b"not a pdf"), ("x.json", b"{broken"), ("x.txt", b"")):
            with self.assertRaises(MS.ModerationError):
                run(AO.add_attachment(self.db, self.U["legal"], a["order_id"], filename=bad[0], data=bad[1]))
        with raises("attachment_too_large"):
            run(AO.add_attachment(self.db, self.U["legal"], a["order_id"], filename="big.txt", data=b"a" * (AO.MAX_FILE + 1)))
        with raises("not_found"):
            run(AO.read_attachment(self.db, self.U["legal"], b["order_id"], att["attachment_id"]))      # alt ordin => 404
        with raises("forbidden"):
            run(AO.read_attachment(self.db, self.U["m1"], a["order_id"], att["attachment_id"]))
        self.assertEqual(run(AO.read_attachment(self.db, self.U["legal"], a["order_id"], att["attachment_id"]))[2], b"%PDF-1.7 content")

    def test_tampered_attachment_is_detected(self):
        a = run(AO.create_order(self.db, self.U["adm"], self.order_body()))
        att = run(AO.add_attachment(self.db, self.U["adm"], a["order_id"], filename="o.txt", data=b"original"))
        next(AO.storage_dir().rglob(f"{att['attachment_id']}.bin")).write_bytes(b"tampered")
        with raises("attachment_integrity_failed"):
            run(AO.read_attachment(self.db, self.U["adm"], a["order_id"], att["attachment_id"]))

    def test_encryption_at_rest_when_key_configured(self):
        try:
            from cryptography.fernet import Fernet
        except Exception:  # noqa: BLE001
            self.skipTest("cryptography not installed")
        os.environ["ASTRAN_ORDERS_ENC_KEY"] = Fernet.generate_key().decode()
        self.addCleanup(os.environ.pop, "ASTRAN_ORDERS_ENC_KEY", None)
        a = run(AO.create_order(self.db, self.U["adm"], self.order_body()))
        att = run(AO.add_attachment(self.db, self.U["adm"], a["order_id"], filename="o.txt", data=b"plain secret text"))
        self.assertNotIn(b"plain secret text", next(AO.storage_dir().rglob(f"{att['attachment_id']}.bin")).read_bytes())
        self.assertEqual(run(AO.read_attachment(self.db, self.U["adm"], a["order_id"], att["attachment_id"]))[2], b"plain secret text")

    def test_response_milestones_only_after_confirmed_delivery(self):
        o = run(AO.create_order(self.db, self.U["adm"], self.order_body()))
        run(AO.send_response(self.db, self.U["adm"], o["order_id"], "receipt"))
        with raises("response_already_sent"):
            run(AO.send_response(self.db, self.U["adm"], o["order_id"], "receipt"))
        run(DL.process_due(self.db, bad_sender))
        got = run(AO.get_order(self.db, self.U["adm"], o["order_id"]))
        self.assertIsNone(got["milestones"]["acknowledged_at"])
        self.assertEqual(got["responses"][0]["delivery"]["status"], "failed")
        run(DL.process_due(self.db, ok_sender, now=MS.now_utc() + timedelta(hours=1)))
        got = run(AO.get_order(self.db, self.U["adm"], o["order_id"]))
        self.assertIsNotNone(got["milestones"]["acknowledged_at"])
        self.assertEqual(ok_sender.sent[0][0], "registry@court.example")
        self.assertNotIn("bad@example.com", ok_sender.sent[0][2])
        with raises("response_not_applicable"):
            run(AO.send_response(self.db, self.U["adm"], o["order_id"], "effect"))

    def test_full_lifecycle_close_and_retention_purge(self):
        os.environ["ASTRAN_ORDERS_RETENTION_DAYS"] = "30"
        oid = self.approved_order()
        run(AO.execute_art9(self.db, self.U["adm"], oid))
        att = run(AO.add_attachment(self.db, self.U["adm"], oid, filename="o.pdf", data=b"%PDF-1.4 x"))
        with raises("order_not_closable"):
            run(AO.close_order(self.db, self.U["adm"], oid, "closing too early without any response"))
        run(AO.send_response(self.db, self.U["adm"], oid, "receipt")); run(AO.send_response(self.db, self.U["adm"], oid, "effect"))
        c = run(AO.close_order(self.db, self.U["adm"], oid, "All responses were delivered to the authority."))
        self.assertTrue(c["retention_configured"])
        self.assertEqual(run(AO.purge_expired(self.db)), 0)
        self.assertEqual(run(AO.purge_expired(self.db, now=MS.now_utc() + timedelta(days=31))), 1)
        o = run(self.db[AO.COLL].find_one({"order_id": oid}))
        self.assertIsNone(o["legal_basis"]); self.assertIsNone(o["authority"]["contact_email"])
        self.assertEqual(o["reference"][:4], "ORD-")
        self.assertEqual(list(AO.storage_dir().rglob(f"{att['attachment_id']}.bin")), [])

    def test_public_intake_records_unverified_order_and_cannot_execute(self):
        r = run(AO.public_intake(self.db, self.order_body()))
        self.assertEqual(r["status"], "received_unverified")
        self.assertEqual(set(r), {"reference", "received_at", "status"})
        o = run(self.db[AO.COLL].find_one({"reference": r["reference"]}))
        self.assertEqual((o["authenticity"]["state"], o["received_via"]), ("unverified", "contact_point_web"))
        with raises("authenticity_not_verified"):
            run(AO.legal_review(self.db, self.U["legal"], o["order_id"], decision="approve", reasoning="Approving an unverified intake."))
        self.assertTrue(run(self.db.games.find_one({"game_id": "g1"}))["is_public"])

    def test_deadline_is_recorded_only_when_the_order_states_one(self):
        a = run(AO.create_order(self.db, self.U["adm"], self.order_body()))
        self.assertEqual(a["deadline_status"], "none_stated_without_undue_delay")
        past = (MS.now_utc() - timedelta(days=1)).isoformat()
        b = run(AO.create_order(self.db, self.U["adm"], self.order_body(response_deadline=past)))
        self.assertEqual(b["deadline_status"], "overdue")

    def test_references_are_unique_and_sequential(self):
        refs = [run(AO.create_order(self.db, self.U["adm"], self.order_body()))["reference"] for _ in range(3)]
        self.assertEqual(len(set(refs)), 3)

    def test_order_audit_is_tamper_evident_and_hidden_from_moderators(self):
        o = run(AO.create_order(self.db, self.U["adm"], self.order_body()))
        run(AO.set_authenticity(self.db, self.U["legal"], o["order_id"], verified=True, method="Verified via the registry's published number."))
        self.assertNotIn("order_received", [e["event_type"] for e in run(MS.audit_history(self.db))])
        self.assertIn("order_received", [e["event_type"] for e in run(MS.audit_history(self.db, include_orders=True))])
        self.assertTrue(run(MS.verify_audit_chain(self.db))["ok"])
        first = run(self.db.moderation_audit_log.find_one({"event_type": "order_received"}))
        run(self.db.moderation_audit_log.update_one({"seq": first["seq"]}, {"$set": {"actor_id": "mallory"}}))
        self.assertFalse(run(MS.verify_audit_chain(self.db))["ok"])
        self.assertEqual([e["event_type"] for e in run(AO.get_order(self.db, self.U["legal"], o["order_id"]))["history"]][:1], ["order_received"])

    def test_only_admin_grants_legal_role(self):
        with raises("admin_required"):
            run(MS.set_flag(self.db, self.U["m1"], "rep", "legal_officer", True))
        run(MS.set_flag(self.db, self.U["adm"], "rep", "legal_officer", True))
        self.assertTrue(run(self.db.users.find_one({"user_id": "rep"}))["is_legal_officer"])


# ============================================================================================ Art. 15
class Transparency(DSABase):
    YEAR = (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))

    def seed(self):
        run(self.db.users.update_one({"user_id": "other"}, {"$set": {"is_trusted_flagger": True}}))
        n1, _ = self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body(), target_id="g1")
        n2, _ = self.report(kind=MS.KIND_NOTICE, category="hate_speech", notice=self.notice_body(content_location="astran://game/g3"), target_id="g3", reporter="other")
        self.report(target_id="g2")                                                    # raport ToS ramas fara decizie
        d1 = self.decide(n1, actor="m1", action="remove_content_illegal", legal_ground="Art. 5 Legea 2/2021")["decision"]
        self.decide(n2, actor="m1", action="no_action", notifier_reasoning="The content was reviewed and is lawful.")
        c = run(MS.open_own_initiative_case(self.db, self.U["m1"], target_type="game", target_id="g2", category="spam", summary="Seen during a patrol of new games.",
                                            automated_detection=True))
        self.decide(c["report_id"], actor="m2", action="restrict_content", tos_provision="4.1", automated_detection=True)
        a = run(MS.submit_appeal(self.db, self.U["bad"], d1["decision_id"], grounds="The game follows every rule, please check again."))
        run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="reverse", reasoning="Content is compliant after re-review."))
        o = run(AO.create_order(self.db, self.U["adm"], self.order_body(), received_at=MS.now_utc() - timedelta(hours=10)))
        run(AO.send_response(self.db, self.U["adm"], o["order_id"], "receipt"))
        run(DL.process_due(self.db, ok_sender))

    def test_empty_database_gives_zeros_and_nulls_not_sample_data(self):
        r = run(TR.build_report(self.db, *self.YEAR))
        self.assertEqual(r["notices_art_16"]["received_total"], 0)
        self.assertIsNone(r["notices_art_16"]["median_hours_to_decision"])
        self.assertEqual(r["orders"]["art_9_illegal_content"]["received_total"], 0)
        self.assertIsNone(r["complaints_art_20"]["share_of_resolved_decisions_reversed_or_modified"])

    def test_aggregates_come_from_real_data(self):
        self.seed()
        r = run(TR.build_report(self.db, *self.YEAR))
        n = r["notices_art_16"]
        self.assertEqual((n["received_total"], n["decided"], n["awaiting_decision"]), (2, 2, 0))
        self.assertEqual(n["by_alleged_category"], {"hate_speech": 1, "illegal_content": 1})
        self.assertEqual(n["submitted_by_trusted_flaggers"], 1)
        self.assertEqual(n["actions_taken"], {"no_action": 1, "remove_content_illegal": 1})
        self.assertIsNotNone(n["median_hours_to_decision"])
        o = r["own_initiative_moderation"]
        self.assertEqual(o["by_source"].get("own_initiative"), 1)
        self.assertEqual(o["by_restriction_type"], {"visibility_restriction": 1})
        self.assertEqual(r["automated_moderation"]["decisions_with_automated_detection"], 1)
        self.assertEqual(r["automated_moderation"]["fully_automated_decisions"], 0)
        c = r["complaints_art_20"]
        self.assertEqual((c["received_total"], c["outcomes"]["decision_reversed"]), (1, 1))
        self.assertEqual(c["share_of_resolved_decisions_reversed_or_modified"], 1.0)
        self.assertIsNotNone(c["median_hours_to_resolution"])
        a9 = r["orders"]["art_9_illegal_content"]
        self.assertEqual((a9["received_total"], a9["by_member_state"], a9["by_illegal_content_category"]), (1, {"RO": 1}, {"hate_speech": 1}))
        self.assertEqual(a9["acknowledged_count"], 1)
        self.assertGreaterEqual(a9["median_hours_to_acknowledge_receipt"], 9.9)
        self.assertEqual(r["all_moderation_decisions"]["by_source"]["art16_notice"], 2)

    def test_period_filtering(self):
        self.seed()
        r = run(TR.build_report(self.db, datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2021, 1, 1, tzinfo=timezone.utc)))
        self.assertEqual(r["notices_art_16"]["received_total"], 0)
        with raises("period_invalid"):
            run(TR.build_report(self.db, self.YEAR[1], self.YEAR[0]))

    def test_report_contains_no_personal_data_or_identifiers(self):
        self.seed()
        r = run(TR.build_report(self.db, *self.YEAR))
        blob = json.dumps(r, default=str).lower()
        for secret in ("jane", "example.com", "offender", "bad@", "tribunalul", "registry", "court.example", "g1", "g3", "legea"):
            self.assertNotIn(secret, blob, secret)
        self.assertFalse(r["personal_data_included"])
        with raises("report_contains_identifier"):
            TR.assert_no_identifiers({"x": "usr_abcdef12"})

    def test_csv_is_machine_readable_and_formula_safe(self):
        self.seed()
        r = run(TR.build_report(self.db, *self.YEAR))
        text = TR.to_csv(r)
        lines = text.splitlines()
        self.assertEqual(lines[0], "section,indicator,value")
        self.assertIn("notices_art_16,received_total,2", lines)
        self.assertEqual(TR.to_csv({"a": {"b": "=HYPERLINK(1)"}}).splitlines()[1], "a,b,'=HYPERLINK(1)")

    def test_unimplemented_indicators_are_declared_not_zero_filled(self):
        r = run(TR.build_report(self.db, *self.YEAR))
        self.assertTrue(r["automated_moderation"]["not_collected"])
        self.assertTrue(r["qualitative_requires_manual_input"])
        self.assertIn("not mapped", r["template"]["mapping_status"])

    def test_publish_is_admin_only_versioned_and_public_view_hides_publisher(self):
        self.seed()
        with raises("admin_required"):
            run(TR.publish(self.db, self.U["m1"], *self.YEAR))
        with raises("period_not_finished"):
            run(TR.publish(self.db, self.U["adm"], self.YEAR[0], datetime(2099, 1, 1, tzinfo=timezone.utc)))
        p1 = run(TR.publish(self.db, self.U["adm"], datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)))
        p2 = run(TR.publish(self.db, self.U["adm"], datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc)))
        self.assertEqual((p1["version"], p2["version"]), (1, 2))
        lst = run(TR.list_public(self.db))
        self.assertEqual(len(lst), 2)
        self.assertNotIn("published_by", json.dumps(lst, default=str))
        got = run(TR.get_public(self.db, p1["report_id"]))
        self.assertEqual(len(got["sha256_json"]), 64)
        self.assertNotIn("adm", json.dumps(got, default=str).replace("admin", ""))
        self.assertIn("notices_art_16", run(TR.get_public_csv(self.db, p1["report_id"])))
        with raises("not_found"):
            run(TR.get_public(self.db, "nope"))

    def test_preview_requires_staff(self):
        with raises("forbidden"):
            run(TR.preview(self.db, self.U["bad"], *self.YEAR))

    def test_calendar_helpers(self):
        s, e = TR.period_for_year(2026)
        self.assertEqual((s.month, s.day, e.year), (1, 1, 2027))
        self.assertEqual(TR.publication_deadline(e).month, 3)


# ============================================================================================ migrari
class Migrations(unittest.TestCase):
    def test_up_is_idempotent_and_down_removes_only_indexes(self):
        from astran_sandbox.test_support_fakedb import FakeDB
        db = FakeDB()
        self.assertEqual(run(DM.migrate_up(db)), ["0001_dsa_remediation_indexes"])
        self.assertEqual(run(DM.migrate_up(db)), [])
        self.assertIn("dsa_order_ref", run(db["authority_orders"].index_information()))
        run(db["authority_orders"].insert_one({"order_id": "o1", "reference": "ORD-1"}))
        self.assertEqual(run(DM.migrate_down(db)), ["0001_dsa_remediation_indexes"])
        self.assertEqual(run(DM.applied(db)), [])
        self.assertNotIn("dsa_order_ref", run(db["authority_orders"].index_information()))
        self.assertEqual(run(db["authority_orders"].count_documents({})), 1)             # datele nu se pierd implicit
        self.assertEqual(run(DM.migrate_up(db)), ["0001_dsa_remediation_indexes"])        # reaplicare dupa rollback
        run(DM.migrate_down(db, destroy_data=True))
        self.assertEqual(run(db["authority_orders"].count_documents({})), 0)

    def test_unique_constraints_enforced_after_migration(self):
        from astran_sandbox.test_support_fakedb import FakeDB
        db = FakeDB()
        run(DM.migrate_up(db))
        run(db["tdb_submissions"].insert_one({"submission_id": "a", "sor_id": "s1"}))
        with self.assertRaises(Exception):
            run(db["tdb_submissions"].insert_one({"submission_id": "b", "sor_id": "s1"}))


if __name__ == "__main__":
    unittest.main()
