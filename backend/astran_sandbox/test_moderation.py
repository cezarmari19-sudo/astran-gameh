"""Teste pentru moderation_service (DSA Art. 16/17/20) cu FakeDB - ruleaza fara FastAPI/Mongo.
Testele HTTP (autorizare pe rute, IDOR) sunt in clasa de la final si se SARA daca fastapi/httpx lipsesc.

    cd backend && python -m unittest astran_sandbox.test_moderation -v
"""
import asyncio
import json
import re
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from astran_sandbox import moderation_service as MS
from astran_sandbox.test_support_fakedb import FakeDB

try:
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    HAVE_HTTP = True
except Exception:  # noqa: BLE001
    HAVE_HTTP = False

LONG = "This is a sufficiently long explanation of the problem."


def run(coro):
    return asyncio.run(coro)


def isolated_tos_registry():
    """Registru cu DOAR ToS 1.1 (in arhiva incarcata, privacy_policy lipseste iar hash-urile ToS 1.0 nu se potrivesc - preexistent)."""
    from astran_sandbox import legal_docs as L
    legal = Path(__file__).resolve().parent.parent / "legal"
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(legal, tmp / "legal")
    man = json.loads((tmp / "legal/manifest.json").read_text(encoding="utf-8"))
    del man["documents"]["privacy_policy"]
    tos = man["documents"]["terms_of_service"]
    tos["versions"] = [v for v in tos["versions"] if v["version"] == "1.1"]
    tos["current"] = "1.1"      # manifestul real are 1.2 ca versiune curenta; acest test verifica continutul lui 1.1
    (tmp / "legal/manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return L.LegalRegistry(tmp / "legal"), tmp


def raises(code):
    """context manager: ModerationError cu `code`."""
    class _C:
        def __enter__(self): return self
        def __exit__(self, et, ev, tb):
            assert et is MS.ModerationError, f"expected ModerationError({code}), got {et}"
            assert ev.code == code, f"expected {code}, got {ev.code}"
            return True
    return _C()


class Base(unittest.TestCase):
    def setUp(self):
        from astran_sandbox import legal_docs as L
        reg, self._tmp = isolated_tos_registry()
        L.reset_registry_for_tests(reg)
        self.addCleanup(lambda: (L.reset_registry_for_tests(None), shutil.rmtree(self._tmp, ignore_errors=True)))
        self.db = FakeDB()
        run(MS.ensure_indexes(self.db))
        users = [
            {"user_id": "rep", "username": "reporter", "email": "rep@example.com"},
            {"user_id": "bad", "username": "offender", "email": "bad@example.com"},
            {"user_id": "other", "username": "other"},
            {"user_id": "m1", "username": "mod1", "is_moderator": True},
            {"user_id": "m2", "username": "mod2", "is_moderator": True},
            {"user_id": "adm", "username": "admin", "is_platform_admin": True},
        ]
        for u in users:
            run(self.db.users.insert_one(u))
        self.U = {u["user_id"]: u for u in users}
        run(self.db.games.insert_one({"game_id": "g1", "owner_id": "bad", "title": "Bad Game", "description": "d",
                                      "is_public": True, "status": "active"}))
        run(self.db.group_chat_messages.insert_one({"message_id": "gm1", "group_id": "grp", "user_id": "bad", "text": "nasty text"}))
        run(self.db.user_sessions.insert_one({"session_token": "t", "user_id": "bad"}))

    # ---- helpers
    def report(self, target_type="game", target_id="g1", reporter="rep", kind=MS.KIND_RULE, category="harassment", notice=None):
        rid = MS.new_id("rep_")
        doc = {"report_id": rid, "reporter_id": reporter, "target_type": target_type, "target_id": target_id,
               "reason": "inappropriate", "details": None, "status": "open", "created_at": MS.now_utc()}
        receipt = run(MS.register_report(self.db, doc, kind=kind, category=category, notice=notice))
        return rid, receipt

    def notice_body(self, **kw):
        b = {"explanation": LONG + " It is allegedly illegal.", "content_location": "astran://game/g1",
             "reporter_name": "Jane Doe", "reporter_email": "jane@example.com", "good_faith_declaration": True}
        b.update(kw)
        return b

    def decide(self, rid, actor="m1", **body):
        body.setdefault("reasoning", "Reviewed the content and it breaks the rules.")
        return run(MS.decide(self.db, self.U[actor], rid, body))


# =============================================================================================== 1-3: raport + confirmare
class ReportsAndReceipts(Base):
    def test_user_can_submit_report_and_it_is_stored_correctly(self):
        rid, receipt = self.report()
        r = run(self.db.reports.find_one({"report_id": rid}))
        self.assertEqual((r["reporter_id"], r["target_type"], r["target_id"]), ("rep", "game", "g1"))
        self.assertEqual(r["status"], MS.STATUS_PENDING)
        self.assertEqual(r["report_kind"], MS.KIND_RULE)
        self.assertEqual(r["reported_user_id"], "bad")                      # rezolvat de server din joc
        ev = run(self.db.report_evidence.find_one({"report_id": rid}))
        self.assertEqual(ev["content"]["title"], "Bad Game")               # instantaneul continutului
        self.assertEqual(len(ev["sha256"]), 64)

    def test_reporter_gets_confirmation_with_required_fields(self):
        rid, rc = self.report()
        for k in ("report_id", "received_at", "reported_content", "received", "track", "status"):
            self.assertIn(k, rc)
        self.assertEqual(rc["report_id"], rid)
        self.assertTrue(rc["received"])
        self.assertIn(rid, rc["track"]["path"])
        self.assertEqual(rc["reported_content"]["summary"], "Bad Game")
        notes = run(MS.list_notifications(self.db, "rep"))
        self.assertEqual([n["kind"] for n in notes], ["report_receipt"])

    def test_ordinary_report_is_not_classified_as_art16_notice(self):
        _, rc = self.report()
        self.assertFalse(rc["is_dsa_notice"])
        _, rc2 = self.report(kind=MS.KIND_NOTICE, notice=self.notice_body(), category="illegal_content")
        self.assertTrue(rc2["is_dsa_notice"])

    def test_legacy_open_status_is_treated_as_pending_and_not_rewritten(self):
        run(self.db.reports.insert_one({"report_id": "old1", "reporter_id": "rep", "target_type": "user", "target_id": "bad",
                                        "reason": "x", "status": "open", "created_at": MS.now_utc()}))
        out = run(MS.list_reports(self.db, status="pending"))
        self.assertIn("old1", [i["report_id"] for i in out["items"]])
        self.assertEqual(out["items"][0]["status"], "pending")
        self.assertEqual(run(self.db.reports.find_one({"report_id": "old1"}))["status"], "open")   # date vechi neatinse


class NoticeValidation(Base):
    def test_notice_needs_good_faith_identity_and_substantiation(self):
        def go(**kw):
            self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body(**kw))
        with raises("good_faith_required"): go(good_faith_declaration=False)
        with raises("field_too_short"): go(explanation="too short")
        with raises("reporter_identity_required"): go(reporter_email=None)
        with raises("email_invalid"): go(reporter_email="not-an-email")
        go()   # valid

    def test_identity_exemption_only_for_child_safety_category(self):
        n = self.notice_body(reporter_name=None, reporter_email=None, identity_exempt=True)
        with raises("reporter_identity_required"):
            self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=dict(n))
        rid, _ = self.report(kind=MS.KIND_NOTICE, category="child_safety", notice=dict(n))
        self.assertTrue(run(self.db.reports.find_one({"report_id": rid}))["notice"]["identity_exempt"])

    def test_notice_assessment_is_recorded_and_audited(self):
        rid, _ = self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body())
        run(MS.assess_notice(self.db, self.U["m1"], rid, "sufficient", "Location and reasons are precise."))
        a = run(self.db.reports.find_one({"report_id": rid}))["notice"]["assessment"]
        self.assertEqual((a["state"], a["assessed_by"]), ("sufficient", "m1"))
        self.assertIn("notice_assessed", [e["event_type"] for e in run(MS.audit_history(self.db, report_id=rid))])
        rid2, _ = self.report()
        with raises("not_a_notice"):
            run(MS.assess_notice(self.db, self.U["m1"], rid2, "sufficient", "Looks fine to me."))


# =============================================================================================== 4-5: staff
class StaffAccess(Base):
    def test_role_is_read_from_user_document(self):
        self.assertIsNone(MS.role_of(self.U["rep"]))
        self.assertEqual(MS.role_of(self.U["m1"]), "moderator")
        self.assertEqual(MS.role_of(self.U["adm"]), "admin")
        self.assertIsNone(MS.role_of({"user_id": "x", "role": "admin", "is_admin": True}))   # campuri inventate nu dau drepturi

    def test_non_staff_cannot_decide_or_manage_roles(self):
        rid, _ = self.report()
        with raises("forbidden"):
            run(MS.decide(self.db, self.U["rep"], rid, {"action": "warn", "reasoning": "x" * 20}))
        with raises("admin_required"):
            run(MS.set_moderator(self.db, self.U["m1"], "other", True))
        run(MS.set_moderator(self.db, self.U["adm"], "other", True))
        self.assertTrue(run(self.db.users.find_one({"user_id": "other"}))["is_moderator"])

    def test_moderator_can_filter_search_and_open_reports(self):
        r1, _ = self.report(category="harassment")
        r2, _ = self.report(target_type="user", target_id="bad", category="spam", reporter="other")
        r3, _ = self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body())
        ids = lambda **f: {i["report_id"] for i in run(MS.list_reports(self.db, **f))["items"]}
        self.assertEqual(ids(), {r1, r2, r3})
        self.assertEqual(ids(category="spam"), {r2})
        self.assertEqual(ids(target_type="game"), {r1, r3})
        self.assertEqual(ids(kind=MS.KIND_NOTICE), {r3})
        self.assertEqual(ids(status="pending"), {r1, r2, r3})
        self.assertEqual(ids(status="resolved"), set())
        self.assertEqual(ids(q=r2[-6:]), {r2})
        self.assertEqual(ids(date_from=MS.now_utc() + timedelta(days=1)), set())
        self.assertEqual(run(MS.list_reports(self.db, page=2, page_size=2))["items"].__len__(), 1)
        d = run(MS.report_detail(self.db, r1))
        self.assertEqual(d["reporter"]["username"], "reporter")
        self.assertEqual(d["reported_user"]["user_id"], "bad")
        self.assertEqual(d["evidence"]["title"], "Bad Game")
        with raises("status_invalid"):
            run(MS.list_reports(self.db, status="bogus"))

    def test_assign_status_notes(self):
        rid, _ = self.report()
        with raises("assignee_not_moderator"):
            run(MS.assign_report(self.db, self.U["m1"], rid, "rep"))
        run(MS.assign_report(self.db, self.U["m1"], rid, "m2"))
        run(MS.set_status(self.db, self.U["m2"], rid, "under_review"))
        r = run(self.db.reports.find_one({"report_id": rid}))
        self.assertEqual((r["assigned_to"], r["status"]), ("m2", "under_review"))
        self.assertIsNotNone(r["reviewed_at"])
        note = run(MS.add_note(self.db, self.U["m2"], rid, "internal: suspicious account, check history"))
        self.assertEqual(note["author_id"], "m2")
        with raises("status_invalid"):
            run(MS.set_status(self.db, self.U["m1"], rid, "weird"))


# =============================================================================================== 6, 7: decizii + Art. 17
class Decisions(Base):
    def test_moderator_can_remove_illegal_content_and_sor_is_complete(self):
        rid, _ = self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body())
        with raises("field_required"):
            self.decide(rid, action="remove_content_illegal", legal_ground="")
        with raises("field_too_short"):
            self.decide(rid, action="remove_content_illegal", legal_ground="ab")
        out = self.decide(rid, action="remove_content_illegal", legal_ground="[Law X, art. Y]", automated_detection=False)
        g = run(self.db.games.find_one({"game_id": "g1"}))
        self.assertFalse(g["is_public"]); self.assertEqual(g["moderation_state"], "removed")
        sor = out["statement_of_reasons"]
        self.assertEqual(sor["affected_user_id"], "bad")
        self.assertEqual(sor["decision"]["action"], "remove_content_illegal")
        self.assertEqual(sor["content"]["target_id"], "g1")
        self.assertIn("breaks the rules", sor["facts_and_circumstances"])
        self.assertEqual(sor["basis"]["type"], "illegal_content")
        self.assertEqual(sor["basis"]["legal_ground"], "[Law X, art. Y]")
        self.assertEqual(sor["basis"]["tos_document"], "terms_of_service@1.1")
        self.assertEqual(sor["automated_means"], {"used_for_detection": False, "used_for_decision": False})
        self.assertTrue(sor["redress"]["internal_complaint"]["available"])
        self.assertTrue(sor["redress"]["judicial_redress"])
        self.assertFalse(sor["redress"]["out_of_court_settlement"]["configured"])      # nimic inventat
        self.assertEqual(len(sor["sha256"]), 64)
        self.assertEqual(run(self.db.reports.find_one({"report_id": rid}))["status"], "resolved")
        self.assertNotIn('"rep"', json.dumps(sor, default=str))     # id-ul exact al notificatorului                           # identitatea notificatorului nu apare

    def test_policy_removal_needs_tos_provision(self):
        rid, _ = self.report()
        with raises("field_required"):
            self.decide(rid, action="remove_content_policy")
        out = self.decide(rid, action="remove_content_policy", tos_provision="3.2(c)")
        self.assertEqual(out["statement_of_reasons"]["basis"]["type"], "terms")
        self.assertEqual(out["statement_of_reasons"]["basis"]["tos_provision"], "3.2(c)")

    def test_affected_user_can_read_statement_and_others_cannot(self):
        rid, _ = self.report()
        d = self.decide(rid, action="remove_content_policy", tos_provision="3.2(c)")["decision"]
        mine = run(MS.my_decision(self.db, "bad", d["decision_id"]))
        self.assertEqual(mine["statement_of_reasons"]["decision_id"], d["decision_id"])
        self.assertNotIn("moderator_id", json.dumps(mine, default=str))
        self.assertEqual(len(run(MS.my_decisions(self.db, "bad"))), 1)
        self.assertEqual(run(MS.my_decisions(self.db, "other")), [])
        with raises("not_found"):
            run(MS.my_decision(self.db, "other", d["decision_id"]))                       # IDOR
        kinds = [n["kind"] for n in run(MS.list_notifications(self.db, "bad"))]
        self.assertIn("moderation_decision", kinds)

    def test_warning_has_notification_but_no_statement(self):
        rid, _ = self.report(target_type="user", target_id="bad")
        out = self.decide(rid, action="warn", tos_provision="3.2(c)")
        self.assertIsNone(out["statement_of_reasons"])
        self.assertEqual(run(self.db.users.find_one({"user_id": "bad"}))["moderation_warning_count"], 1)
        self.assertEqual(run(self.db.statements_of_reasons.count_documents({})), 0)
        self.assertIn("moderation_warning", [n["kind"] for n in run(MS.list_notifications(self.db, "bad"))])

    def test_temporary_restrictions(self):
        r1, _ = self.report(target_type="user", target_id="bad")
        self.decide(r1, action="restrict_account_temporary", tos_provision="9.2", days=3, scope="chat")
        u = run(self.db.users.find_one({"user_id": "bad"}))
        self.assertIsNotNone(u.get("chat_muted_until")); self.assertIsNone(MS.account_block(u))
        r2, _ = self.report(target_type="user", target_id="bad", reporter="other")
        self.decide(r2, action="restrict_account_temporary", tos_provision="9.2", days=2, scope="full")
        blk = MS.account_block(run(self.db.users.find_one({"user_id": "bad"})))
        self.assertFalse(blk["permanent"]); self.assertIsNotNone(blk["until"])
        r3, _ = self.report(target_type="user", target_id="bad", reporter="other")
        with raises("restriction_invalid"):
            self.decide(r3, action="restrict_account_temporary", tos_provision="9.2", days=9999)

    def test_permanent_suspension_requires_admin_and_staff_are_protected(self):
        rid, _ = self.report(target_type="user", target_id="bad")
        with raises("admin_required"):
            self.decide(rid, actor="m1", action="suspend_account_permanent", tos_provision="9.2")
        out = self.decide(rid, actor="adm", action="suspend_account_permanent", tos_provision="9.2")
        u = run(self.db.users.find_one({"user_id": "bad"}))
        self.assertTrue(MS.account_block(u)["permanent"])
        self.assertEqual(run(self.db.user_sessions.count_documents({"user_id": "bad"})), 0)
        self.assertTrue(out["statement_of_reasons"]["decision"]["permanent"])
        r2, _ = self.report(target_type="user", target_id="m2")
        with raises("target_protected"):
            self.decide(r2, actor="adm", action="warn", tos_provision="3.2")

    def test_restrict_content_and_message_hiding(self):
        rid, _ = self.report(target_type="message", target_id="gm1", reporter="other")
        self.decide(rid, action="remove_content_policy", tos_provision="3.2(c)")
        self.assertTrue(run(self.db.group_chat_messages.find_one({"message_id": "gm1"}))["moderation_hidden"])

    def test_report_can_be_decided_only_once_even_concurrently(self):
        rid, _ = self.report()
        async def go():
            return await asyncio.gather(*[MS.decide(self.db, self.U[a], rid, {"action": "remove_content_policy", "tos_provision": "3.2",
                                                                              "reasoning": "Concurrent decision attempt."}) for a in ("m1", "m2")],
                                        return_exceptions=True)
        res = run(go())
        self.assertEqual(sum(isinstance(r, dict) for r in res), 1)
        self.assertEqual(run(self.db.moderation_decisions.count_documents({})), 1)
        with raises("report_closed"):
            self.decide(rid, action="warn", tos_provision="3.2")

    def test_rejecting_through_status_creates_a_reasoned_decision(self):
        rid, _ = self.report()
        with raises("field_required"):
            run(MS.set_status(self.db, self.U["m1"], rid, "rejected", None))
        run(MS.set_status(self.db, self.U["m1"], rid, "rejected", "No violation found on review."))
        self.assertEqual(run(self.db.reports.find_one({"report_id": rid}))["status"], "rejected")
        self.assertEqual(run(self.db.moderation_decisions.find_one({"report_id": rid}))["action"], "no_action")

    def test_corrections_are_new_events_and_never_edit_history(self):
        rid, _ = self.report()
        d = self.decide(rid, action="remove_content_policy", tos_provision="3.2")["decision"]
        before = run(self.db.moderation_decisions.find_one({"decision_id": d["decision_id"]}))["reasoning"]
        run(MS.add_correction(self.db, self.U["m2"], d["decision_id"], "Correction: wrong date in the facts above."))
        after = run(self.db.moderation_decisions.find_one({"decision_id": d["decision_id"]}))["reasoning"]
        self.assertEqual(before, after)
        mine = run(MS.my_decision(self.db, "bad", d["decision_id"]))
        self.assertEqual(len(mine["corrections"]), 1)
        self.assertNotIn("author_id", mine["corrections"][0])
        self.assertIn("decision_correction", [e["event_type"] for e in run(MS.audit_history(self.db, decision_id=d["decision_id"]))])


# =============================================================================================== 8-9: Art. 20
class Appeals(Base):
    def make_decision(self, **kw):
        rid, _ = self.report()
        d = self.decide(rid, actor="m1", action="remove_content_policy", tos_provision="3.2(c)", **kw)["decision"]
        return rid, d["decision_id"]

    def appeal(self, did, user="bad", **kw):
        kw.setdefault("grounds", "I disagree because the game follows all the rules.")
        return run(MS.submit_appeal(self.db, self.U[user], did, **kw))

    def test_user_can_submit_and_follow_appeal(self):
        _, did = self.make_decision()
        a = self.appeal(did, evidence_text="Screenshot description", evidence_urls=["https://example.com/proof.png"])
        self.assertEqual(a["status"], "received")
        self.assertEqual(len(run(MS.my_appeals(self.db, "bad"))), 1)
        self.assertEqual(run(MS.my_appeal(self.db, "bad", a["appeal_id"]))["status"], "received")
        with raises("not_found"):
            run(MS.my_appeal(self.db, "other", a["appeal_id"]))                         # IDOR
        with raises("appeal_exists"):
            self.appeal(did)
        self.assertIn("appeal_received", [n["kind"] for n in run(MS.list_notifications(self.db, "bad"))])

    def test_appeal_validation_and_idor(self):
        _, did = self.make_decision()
        with raises("field_too_short"): self.appeal(did, grounds="no")
        with raises("url_invalid"): self.appeal(did, evidence_urls=["http://insecure.example/x"])
        with raises("url_invalid"): self.appeal(did, evidence_urls=["javascript:alert(1)"])
        with raises("not_found"): self.appeal(did, user="other")
        with raises("not_found"): self.appeal("dec_missing")

    def test_six_month_period_is_enforced(self):
        _, did = self.make_decision()
        old = MS.now_utc() - timedelta(days=200)
        run(self.db.moderation_decisions.update_one({"decision_id": did}, {"$set": {"created_at": old}}))
        with raises("complaint_period_expired"):
            self.appeal(did)
        recent = MS.now_utc() - timedelta(days=170)
        run(self.db.moderation_decisions.update_one({"decision_id": did}, {"$set": {"created_at": recent}}))
        self.appeal(did)
        self.assertEqual(MS.add_months(MS.now_utc().replace(year=2026, month=8, day=31), 6).month, 2)   # 31 aug + 6 luni -> 28 feb

    def test_original_decider_cannot_review_and_reverse_restores_content(self):
        rid, did = self.make_decision()
        a = self.appeal(did)
        with raises("reviewer_not_independent"):
            run(MS.start_appeal_review(self.db, self.U["m1"], a["appeal_id"]))
        with raises("forbidden"):
            run(MS.start_appeal_review(self.db, self.U["rep"], a["appeal_id"]))
        run(MS.start_appeal_review(self.db, self.U["m2"], a["appeal_id"]))
        with raises("field_too_short"):
            run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="reverse", reasoning="ok"))
        out = run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="reverse",
                                    reasoning="The game does not breach clause 3.2(c); reversing the decision."))
        self.assertEqual(out["status"], "decision_reversed")
        g = run(self.db.games.find_one({"game_id": "g1"}))
        self.assertTrue(g["is_public"]); self.assertEqual(g["status"], "active"); self.assertNotIn("moderation_state", g)
        self.assertEqual(run(self.db.moderation_decisions.find_one({"decision_id": did}))["status"], "reversed")
        final = run(MS.my_appeal(self.db, "bad", a["appeal_id"]))
        self.assertEqual(final["outcome"], "reverse"); self.assertIn("does not breach", final["outcome_reasoning"])
        with raises("appeal_already_resolved"):
            run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="maintain", reasoning="x" * 25))
        ev = [e["event_type"] for e in run(MS.audit_history(self.db, decision_id=did))]
        for must in ("decision_made", "appeal_submitted", "appeal_review_started", "decision_reversed", "appeal_resolved"):
            self.assertIn(must, ev)

    def test_maintain_keeps_the_decision(self):
        _, did = self.make_decision()
        a = self.appeal(did)
        run(MS.resolve_appeal(self.db, self.U["adm"], a["appeal_id"], outcome="maintain", reasoning="The content clearly breaches 3.2(c)."))
        self.assertFalse(run(self.db.games.find_one({"game_id": "g1"}))["is_public"])
        self.assertEqual(run(self.db.moderation_decisions.find_one({"decision_id": did}))["status"], "in_effect")
        last = run(MS.audit_history(self.db, decision_id=did))[-1]
        self.assertEqual((last["event_type"], last["outcome"]), ("appeal_resolved", "maintain"))

    def test_reversing_a_suspension_lifts_it(self):
        rid, _ = self.report(target_type="user", target_id="bad")
        did = self.decide(rid, actor="adm", action="suspend_account_permanent", tos_provision="9.2")["decision"]["decision_id"]
        a = self.appeal(did)
        run(MS.resolve_appeal(self.db, self.U["m1"], a["appeal_id"], outcome="reverse", reasoning="Account was mistaken for another one."))
        self.assertIsNone(MS.account_block(run(self.db.users.find_one({"user_id": "bad"}))))

    def test_notifier_can_appeal_rejection_and_report_reopens(self):
        rid, _ = self.report(kind=MS.KIND_NOTICE, category="illegal_content", notice=self.notice_body())
        did = self.decide(rid, action="no_action", notifier_reasoning="We found no illegality.")["decision"]["decision_id"]
        out = run(MS.my_decision(self.db, "rep", did))
        self.assertEqual(out["role"], "notifier"); self.assertTrue(out["outcome"]["redress"]["internal_complaint"]["available"])
        self.assertNotIn("moderator_id", json.dumps(out, default=str))
        a = self.appeal(did, user="rep", grounds="The notice contained a precise legal basis that was ignored.")
        run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="reverse", reasoning="Reconsidered: needs new assessment."))
        r = run(self.db.reports.find_one({"report_id": rid}))
        self.assertEqual(r["status"], "under_review")
        out2 = self.decide(rid, actor="m2", action="remove_content_illegal", legal_ground="[Law X]")
        self.assertEqual(out2["decision"]["supersedes"], did)


# =============================================================================================== 10, 11: audit + privacidad
class AuditAndPrivacy(Base):
    def test_audit_chain_detects_tampering_and_records_required_fields(self):
        rid, _ = self.report()
        run(MS.set_status(self.db, self.U["m1"], rid, "under_review", "Starting review."))
        self.decide(rid, action="remove_content_policy", tos_provision="3.2")
        self.assertTrue(run(MS.verify_audit_chain(self.db))["ok"])
        ev = run(MS.audit_history(self.db, report_id=rid))
        sc = [e for e in ev if e["event_type"] == "status_changed"][0]
        self.assertEqual((sc["actor_id"], sc["previous_status"], sc["new_status"], sc["reasoning"]), ("m1", "pending", "under_review", "Starting review."))
        self.assertIsNotNone(sc["ts"]); self.assertEqual(sc["report_id"], rid)
        run(self.db.moderation_audit_log.update_one({"seq": 2}, {"$set": {"reasoning": "tampered"}}))
        res = run(MS.verify_audit_chain(self.db))
        self.assertFalse(res["ok"]); self.assertEqual(res["broken_at_seq"], 2)

    def test_audit_collection_is_only_ever_inserted_to(self):
        src = (Path(__file__).parent / "moderation_service.py").read_text(encoding="utf-8")
        self.assertFalse(re.search(r"moderation_audit_log\.(update|delete|replace|find_one_and)", src))
        routes = (Path(__file__).parent / "moderation_routes.py").read_text(encoding="utf-8")
        self.assertNotRegex(routes, r"@router\.(put|patch|delete)")                       # nicio ruta de modificare/stergere

    def test_internal_notes_and_staff_data_never_reach_users(self):
        rid, _ = self.report()
        secret = "SECRET-INTERNAL-NOTE-xyz"
        run(MS.add_note(self.db, self.U["m1"], rid, secret))
        d = self.decide(rid, action="remove_content_policy", tos_provision="3.2")["decision"]
        views = [run(MS.my_reports(self.db, "rep")), run(MS.my_report(self.db, "rep", rid)),
                 run(MS.my_decision(self.db, "bad", d["decision_id"])), run(MS.my_decisions(self.db, "bad")),
                 run(MS.list_notifications(self.db, "rep")), run(MS.list_notifications(self.db, "bad"))]
        blob = json.dumps(views, default=str)
        self.assertNotIn(secret, blob)
        self.assertNotIn('"m1"', blob)                                                       # id moderator
        self.assertNotIn("assigned_to", blob); self.assertNotIn("internal_notes", blob)
        self.assertIn(secret, json.dumps(run(MS.report_detail(self.db, rid)), default=str))   # dar personalul il vede

    def test_user_cannot_read_another_users_private_reports(self):
        rid, _ = self.report(reporter="rep")
        self.assertEqual(run(MS.my_reports(self.db, "other")), [])
        with raises("not_found"):
            run(MS.my_report(self.db, "other", rid))
        self.assertEqual(len(run(MS.my_reports(self.db, "rep"))), 1)

    def test_decision_corrections_and_failed_effect_leave_audit_trace(self):
        rid, _ = self.report()
        run(self.db.games.delete_one({"game_id": "g1"}))                                  # continut disparut intre timp
        with raises("content_not_found"):
            self.decide(rid, action="remove_content_policy", tos_provision="3.2")
        self.assertEqual(run(self.db.moderation_decisions.find_one({"report_id": rid}))["status"], "failed")
        self.assertIn("decision_failed", [e["event_type"] for e in run(MS.audit_history(self.db, report_id=rid))])
        self.assertNotIn("deciding", run(self.db.reports.find_one({"report_id": rid})))   # revendicarea e eliberata


# =============================================================================================== 12-13: ToS + contact
class TermsAndContact(unittest.TestCase):
    def test_terms_version_1_1_is_published_with_moderation_sections_and_valid_hashes(self):
        reg, tmp = isolated_tos_registry()      # LegalRegistry recalculeaza SHA-256 si ar arunca la orice nepotrivire
        try:
            self.assertEqual(reg.current_version("terms_of_service"), "1.1")
            self.assertEqual(reg.reacceptance_status("terms_of_service", "1.0"), "accept")
            en, _ = reg.text("terms_of_service", "1.1", "en"); ro, _ = reg.text("terms_of_service", "1.1", "ro")
            for needle in ("HOW WE MODERATE", "HOW TO REPORT", "STATEMENT OF REASONS", "COMPLAINTS", "Automated tools", "SUSPENSION AND ACCOUNT CLOSURE"):
                self.assertIn(needle, en)
            for needle in ("CUM MODERĂM", "CUM RAPORTEZI", "EXPUNEREA DE MOTIVE", "PLÂNGERI", "Instrumente automate", "SUSPENDAREA ȘI ÎNCHIDEREA"):
                self.assertIn(needle, ro)
            self.assertIn("NOT LEGALLY REVIEWED", en)                                      # nu pretinde conformitate
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_contact_info_never_invents_data(self):
        c = MS.contact_info({})
        self.assertFalse(c["configured"])
        self.assertIsNone(c["legal_entity_name"]); self.assertIsNone(c["authorities_email"]); self.assertIsNone(c["users_email"])
        self.assertIn("authorities_email", c["missing_fields"]); self.assertEqual(c["env_vars"]["users_email"], "ASTRAN_DSA_USERS_EMAIL")
        self.assertFalse(c["automated_only"])
        full = {v: "x@example.test" for v in MS.CONTACT_ENV.values()}
        c2 = MS.contact_info(full)
        self.assertTrue(c2["configured"]); self.assertEqual(c2["missing_fields"], [])

    def test_contact_messages_are_stored_for_staff_and_validated(self):
        db = FakeDB(); run(MS.ensure_indexes(db))
        out = run(MS.submit_contact_message(db, channel="authority", name="DSC", email="dsc@example.test",
                                            subject="Order to act", message="Please contact us about case 123."))
        self.assertTrue(out["message_id"])
        self.assertEqual(len(run(MS.list_contact_messages(db))), 1)
        with raises("channel_invalid"):
            run(MS.submit_contact_message(db, channel="hacker", name=None, email="a@b.co", subject="hello", message="x" * 20))
        with raises("email_invalid"):
            run(MS.submit_contact_message(db, channel="user", name=None, email="nope", subject="hello", message="x" * 20))


# =============================================================================================== 14: moderarea existenta a chatului
class ExistingChatModerationStillWorks(Base):
    def test_dm_store_hides_only_moderated_messages(self):
        from astran_sandbox.dm_store import MongoDmStore as DmStore
        db = self.db
        for i, hidden in enumerate((False, True, False)):
            run(db.dm_messages.insert_one({"message_id": f"d{i}", "conversation_id": "c1", "ts": i, "sender_id": "bad",
                                           **({"moderation_hidden": True} if hidden else {})}))
        got = run(DmStore(db).page_messages("c1", None, 50))
        self.assertEqual(sorted(m["message_id"] for m in got), ["d0", "d2"])

    def test_chat_admin_mute_fields_are_compatible_with_decisions(self):
        # chat_admin_routes scrie users.chat_muted_until; restrict_account_temporary(scope=chat) foloseste ACELASI camp
        rid, _ = self.report(target_type="user", target_id="bad")
        self.decide(rid, action="restrict_account_temporary", tos_provision="9.2", days=1, scope="chat")
        self.assertIn("chat_muted_until", run(self.db.users.find_one({"user_id": "bad"})))
        did = run(self.db.moderation_decisions.find_one({"report_id": rid}))["decision_id"]
        a = run(MS.submit_appeal(self.db, self.U["bad"], did, grounds="I was only joking, please reconsider this."))
        run(MS.resolve_appeal(self.db, self.U["m2"], a["appeal_id"], outcome="reverse", reasoning="Joke context confirmed; lifting the mute."))
        self.assertNotIn("chat_muted_until", run(self.db.users.find_one({"user_id": "bad"})))


class MigrationChecks(Base):
    def test_ensure_indexes_is_idempotent_and_legacy_reports_survive(self):
        run(self.db.reports.insert_one({"report_id": "legacy", "reporter_id": "rep", "target_type": "game", "target_id": "g1",
                                        "reason": "x", "status": "open", "created_at": MS.now_utc(), "context": {"text": "ctx"}}))
        run(MS.ensure_indexes(self.db)); run(MS.ensure_indexes(self.db))          # de doua ori: fara efect/erori
        legacy = run(self.db.reports.find_one({"report_id": "legacy"}))
        self.assertEqual(legacy["status"], "open")                                  # nimic rescris
        self.assertNotIn("report_kind", legacy)
        d = run(MS.report_detail(self.db, "legacy"))                                # dar apare corect in panou
        self.assertEqual((d["report"]["status"], d["report"]["report_kind"]), ("pending", MS.KIND_RULE))
        self.assertEqual(d["evidence"], {"text": "ctx"})                           # contextul vechi (DM/chat) e folosit ca dovada
        run(MS.my_reports(self.db, "rep"))                                          # si in "Raportarile mele"


# =============================================================================================== HTTP (se sare fara fastapi)
@unittest.skipUnless(HAVE_HTTP, "fastapi/httpx nu sunt instalate: testele HTTP nu au rulat")
class HttpAuthorization(unittest.TestCase):
    def setUp(self):
        from astran_sandbox.moderation_routes import make_moderation_router, make_contact_router
        self.db = FakeDB(); run(MS.ensure_indexes(self.db))
        for u in ({"user_id": "rep"}, {"user_id": "other"}, {"user_id": "m1", "is_moderator": True}, {"user_id": "adm", "is_platform_admin": True}):
            run(self.db.users.insert_one(dict(u)))
        run(self.db.games.insert_one({"game_id": "g1", "owner_id": "other", "title": "T"}))

        async def current(authorization: str = Header(None)):
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(401, "Missing")
            u = await self.db.users.find_one({"user_id": authorization[7:]}, {"_id": 0})
            if not u:
                raise HTTPException(401, "Invalid")
            return u

        async def exists(tt, tid, cur):
            return tt == "game" and tid == "g1"
        app = FastAPI()
        app.include_router(make_moderation_router(current, self.db, target_exists=exists), prefix="/api")
        app.include_router(make_contact_router(self.db, client_ip=lambda r: "1.2.3.4"), prefix="/api")
        self.c = TestClient(app)
        self.h = lambda u: {"Authorization": f"Bearer {u}"}

    def test_dashboard_requires_staff_role_on_the_server(self):
        self.assertEqual(self.c.get("/api/moderation/admin/reports").status_code, 401)
        self.assertEqual(self.c.get("/api/moderation/admin/reports", headers=self.h("rep")).status_code, 403)
        self.assertEqual(self.c.get("/api/moderation/admin/reports", headers=self.h("m1")).status_code, 200)
        self.assertEqual(self.c.get("/api/moderation/admin/reports", headers=self.h("adm")).status_code, 200)
        self.assertEqual(self.c.post("/api/moderation/admin/moderators", headers=self.h("m1"), json={"user_id": "rep", "enabled": True}).status_code, 403)
        for path in ("/audit", "/appeals", "/contact-messages", "/audit/verify"):
            self.assertEqual(self.c.get("/api/moderation/admin" + path, headers=self.h("rep")).status_code, 403, path)

    def test_notice_flow_receipt_and_idor(self):
        body = {"target_type": "game", "target_id": "g1", "explanation": LONG + " Allegedly illegal.", "reporter_name": "J", "reporter_email": "j@example.com",
                "good_faith_declaration": True}
        r = self.c.post("/api/moderation/notices", headers=self.h("rep"), json=body)
        self.assertEqual(r.status_code, 201, r.text)
        rid = r.json()["report_id"]; self.assertTrue(r.json()["receipt"]["is_dsa_notice"])
        self.assertEqual(self.c.get(f"/api/moderation/me/reports/{rid}", headers=self.h("rep")).status_code, 200)
        self.assertEqual(self.c.get(f"/api/moderation/me/reports/{rid}", headers=self.h("other")).status_code, 404)
        self.assertEqual(self.c.post("/api/moderation/notices", headers=self.h("rep"), json={**body, "good_faith_declaration": False}).status_code, 422)
        self.assertEqual(self.c.post("/api/moderation/notices", headers=self.h("rep"), json={**body, "target_id": "nope"}).status_code, 404)
        self.assertEqual(self.c.post("/api/moderation/notices", headers=self.h("rep"), json={**body, "hacker_field": 1}).status_code, 422)

    def test_contact_and_info_are_public(self):
        self.assertEqual(self.c.get("/api/contact/info").status_code, 200)
        r = self.c.post("/api/contact/messages", json={"channel": "user", "email": "a@b.co", "subject": "Hello", "message": "A real message here."})
        self.assertEqual(r.status_code, 201)


if __name__ == "__main__":
    unittest.main()
