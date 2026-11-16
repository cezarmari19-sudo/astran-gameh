"""Teste GDPR (privacy.py + privacy_routes.py). Ruleaza fara MongoDB / FastAPI:
    cd backend && python -m unittest astran_sandbox.test_privacy -v

Bazele de date sunt simulate (test_support_fakedb.py); FastAPI e inlocuit cu stub-ul minimal din
test_support_fakeweb.py, deci handler-ele rutelor se apeleaza direct. `resolve_user_from_token` din server.py este
EXTRAS din sursa reala si executat, ca sa fie testata exact logica de productie pentru sesiuni vechi.
"""
import ast
import asyncio
import inspect
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys

from astran_sandbox import test_support_fakeweb as _fw

# stub-ul fastapi/pydantic e folosit DOAR la importul privacy_routes; apoi sys.modules se restaureaza, ca sa nu
# interfereze cu celelalte teste (care isi instaleaza propriul stub si sunt sensibile la ordinea de import)
_STUB_KEYS = ("fastapi", "fastapi.responses", "pydantic")
_SAVED_MODULES = {k: sys.modules.get(k) for k in _STUB_KEYS}
_fw.install()

import jwt as pyjwt  # noqa: E402  (PyJWT: dependenta reala a serverului)

from astran_sandbox import email_verification as emailver  # noqa: E402
from astran_sandbox import privacy as P  # noqa: E402
from astran_sandbox import privacy_routes as PR  # noqa: E402
from astran_sandbox.test_support_fakedb import FakeDB  # noqa: E402

from fastapi import HTTPException  # noqa: E402

for _k, _v in _SAVED_MODULES.items():
    if _v is None:
        sys.modules.pop(_k, None)
    else:
        sys.modules[_k] = _v

NOW = datetime.now(timezone.utc)
A, B, C, D = "user_alice", "user_bob", "user_carol", "user_dora"
SERVER_PY = Path(__file__).resolve().parent.parent / "server.py"


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


async def seed(db):
    def ins(c, *docs):
        return [db[c].insert_one(d) for d in docs]
    tasks = []
    tasks += ins("users",
        {"user_id": A, "email": "alice@example.com", "username": "alice", "display_name": "Alice", "password_hash": "pw:secret",
         "astrans_balance": 50, "created_at": NOW, "age_category": "adult_18", "_money_ops": ["x:d"], "sessions_valid_after": NOW,
         "avatar_url": None, "language": "ro", "email_verified": True},
        {"user_id": B, "email": "bob@example.com", "username": "bob", "display_name": "Bob", "password_hash": "pw:bobpw",
         "astrans_balance": 10, "created_at": NOW, "age_category": "adult_18"},
        {"user_id": C, "email": "carol@example.com", "username": "carol", "password_hash": "pw:carolpw", "created_at": NOW},
        {"user_id": D, "email": "dora@example.com", "username": "dora", "password_hash": None, "email_verified": True, "created_at": NOW})
    tasks += ins("user_sessions", {"session_token": "tokA1", "user_id": A, "created_at": NOW, "expires_at": NOW + timedelta(days=7)},
                 {"session_token": "tokA2", "user_id": A, "created_at": NOW, "expires_at": NOW + timedelta(days=7)},
                 {"session_token": "tokB1", "user_id": B, "created_at": NOW, "expires_at": NOW + timedelta(days=7)})
    tasks += ins("games",
        {"game_id": "gA1", "owner_id": A, "title": "Alice game", "project_password_hash": "argon2$x", "deleted": False},
        {"game_id": "gA2", "owner_id": A, "title": "Alice trashed", "deleted": True, "deleted_at": NOW},
        {"game_id": "gB1", "owner_id": B, "title": "Bob game", "group_id": "grp_A2"})
    tasks += ins("game_scripts", {"game_id": "gA1", "path": "main.lua", "source": "print(1)"}, {"game_id": "gA2", "path": "m"},
                 {"game_id": "gB1", "path": "main.lua", "source": "print(2)"})
    tasks += ins("game_assets", {"game_id": "gA1", "asset_ids": []})
    tasks += ins("game_collaborators", {"game_id": "gA1", "user_id": B, "granted_by": A}, {"game_id": "gB1", "user_id": A, "granted_by": B})
    tasks += ins("game_testers", {"game_id": "gA1", "user_id": C}, {"game_id": "gB1", "user_id": A})
    tasks += ins("recently_played", {"user_id": A, "game_id": "gB1"}, {"user_id": B, "game_id": "gA1"}, {"user_id": C, "game_id": "gA1"})
    tasks += ins("game_instances", {"instance_id": "iA1", "game_id": "gA1", "player_count": 1},
                 {"instance_id": "iB1", "game_id": "gB1", "player_count": 2})
    tasks += ins("instance_players", {"instance_id": "iB1", "user_id": A}, {"instance_id": "iB1", "user_id": B})
    tasks += ins("shop_items", {"item_id": "sh_sold", "owner_id": A, "owner_username": "alice", "name": "Sold", "is_public": True},
                 {"item_id": "sh_unsold", "owner_id": A, "owner_username": "alice", "name": "Unsold"},
                 {"item_id": "sh_B", "owner_id": B, "owner_username": "bob", "name": "BobItem"})
    tasks += ins("shop_purchases", {"user_id": B, "item_id": "sh_sold"}, {"user_id": A, "item_id": "sh_B"})
    tasks += ins("clothes_items", {"item_id": "cl_sold", "owner_id": A, "owner_username": "alice"},
                 {"item_id": "cl_unsold", "owner_id": A, "owner_username": "alice"})
    tasks += ins("clothes_purchases", {"user_id": C, "item_id": "cl_sold"})
    tasks += ins("studio_models", {"model_id": "mA", "owner_id": A, "parts": []}, {"model_id": "mB", "owner_id": B, "parts": []})
    tasks += ins("user_settings", {"user_id": A, "settings": {"volume": 7}}, {"user_id": B, "settings": {}})
    tasks += ins("user_avatars", {"user_id": A, "slots": {}})
    tasks += ins("friendships", {"user_a": A, "user_b": B, "since": NOW})
    tasks += ins("friend_requests", {"from_id": C, "to_id": A, "status": "pending"})
    tasks += ins("blocks", {"blocker_id": A, "blocked_id": C}, {"blocker_id": C, "blocked_id": A})
    tasks += ins("dm_conversations", {"conversation_id": "dAB", "user_a": A, "user_b": B}, {"conversation_id": "dAC", "user_a": A, "user_b": C},
                 {"conversation_id": "dBC", "user_a": B, "user_b": C})
    tasks += ins("dm_messages",
        {"message_id": "m1", "conversation_id": "dAB", "sender_id": A, "ts": 1, "raw": "secret hello", "safe": "hello"},
        {"message_id": "m2", "conversation_id": "dAB", "sender_id": B, "ts": 2, "raw": "hi from bob", "safe": "hi from bob"},
        {"message_id": "m3", "conversation_id": "dAC", "sender_id": A, "ts": 3, "raw": None, "safe": "yo"},
        {"message_id": "m4", "conversation_id": "dBC", "sender_id": B, "ts": 4, "raw": "bob to carol", "safe": "bob to carol"})
    tasks += ins("groups", {"group_id": "grp_A1", "owner_id": A, "name": "G1", "token": "SECRETJOINTOKEN"},
                 {"group_id": "grp_A2", "owner_id": A, "name": "G2", "token": "T2"}, {"group_id": "grp_B1", "owner_id": B, "name": "GB"})
    j = lambda d: NOW - timedelta(days=d)  # noqa: E731
    tasks += ins("group_members",
        {"group_id": "grp_A1", "user_id": A, "role": "owner", "status": "active", "joined_at": j(30)},
        {"group_id": "grp_A1", "user_id": C, "role": "member", "status": "active", "joined_at": j(20)},
        {"group_id": "grp_A1", "user_id": B, "role": "admin", "status": "active", "joined_at": j(10)},
        {"group_id": "grp_A2", "user_id": A, "role": "owner", "status": "active", "joined_at": j(5)},
        {"group_id": "grp_B1", "user_id": B, "role": "owner", "status": "active", "joined_at": j(5)},
        {"group_id": "grp_B1", "user_id": A, "role": "member", "status": "active", "joined_at": j(4)})
    tasks += ins("group_chat_messages",
        {"message_id": "gm1", "group_id": "grp_A1", "user_id": A, "username": "alice", "text": "hello group"},
        {"message_id": "gm2", "group_id": "grp_A1", "user_id": A, "username": "alice", "text": "reported text"},
        {"message_id": "gm3", "group_id": "grp_A1", "user_id": B, "username": "bob", "text": "bob says hi"},
        {"message_id": "gm4", "group_id": "grp_B1", "user_id": A, "username": "alice", "text": "in bobs group"})
    tasks += ins("reports",
        {"report_id": "r1", "reporter_id": A, "target_type": "user", "target_id": B, "reason": "spam", "status": "open"},
        {"report_id": "r2", "reporter_id": B, "target_type": "user", "target_id": A, "reason": "abuse", "status": "open"},
        {"report_id": "r3", "reporter_id": C, "target_type": "message", "target_id": "m1", "status": "open",
         "context": {"source": "private_dm", "sender_id": A, "text": "secret hello"}},
        {"report_id": "r4", "reporter_id": B, "target_type": "message", "target_id": "gm2", "status": "open"})
    tasks += ins("astran_ledger",
        {"tx_id": "t1", "user_id": A, "type": "welcome_bonus", "amount": 100, "counterparty_id": "system", "timestamp": NOW},
        {"tx_id": "t2", "user_id": A, "type": "purchase", "amount": 120, "counterparty_id": "system", "provider_ref": "pi_123",
         "timestamp": NOW, "status": "completed"},
        {"tx_id": "t3", "user_id": A, "type": "shop_sale", "amount": 9, "counterparty_id": B, "timestamp": NOW},
        {"tx_id": "t4", "user_id": B, "type": "shop_purchase", "amount": 10, "counterparty_id": A, "timestamp": NOW},
        {"tx_id": "t5", "user_id": B, "type": "purchase", "amount": 60, "counterparty_id": "system", "provider_ref": "pi_999", "timestamp": NOW})
    tasks += ins("security_audit_log", {"event_id": "e1", "user_id": A, "action": "project_delete", "ip": "1.2.3.4", "created_at": NOW},
                 {"event_id": "e2", "user_id": B, "action": "x", "ip": "5.6.7.8", "created_at": NOW})
    tasks += ins("security_attempts", {"user_id": A, "scope_id": "account", "purpose": "account_delete", "count": 1},
                 {"user_id": "login", "scope_id": "1.2.3.4|alice@example.com", "purpose": "login", "count": 1},
                 {"user_id": B, "scope_id": "account", "purpose": "account_delete", "count": 1})
    tasks += ins("recovery_tokens", {"token_id": "rt1", "user_id": A, "game_id": "gA1"})
    tasks += ins("password_resets", {"user_id": A}); tasks += ins("email_verifications", {"user_id": A})
    tasks += ins("webauthn_credentials", {"user_id": A, "credential_id": "cred", "created_at": NOW})
    tasks += ins("webauthn_challenges", {"user_id": A, "challenge_id": "ch"})
    await asyncio.gather(*tasks)


def new_db():
    db = FakeDB()
    run(seed(db))
    return db


def dump(db, skip=()):
    return json.dumps([[n, d] for n, d in db.all_docs() if n not in skip], default=str)


def count(db, coll, flt=None):
    return run(db[coll].count_documents(flt or {}))


# --------------------------------------------------------------------------- stergere
class DeletionTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.disconnected = []

        async def disc(uid):
            self.disconnected.append(uid)
        P._DISCONNECTORS.clear()
        P.register_disconnector(disc)

    def delete(self, uid=A):
        st, rec = run(P.begin_deletion(self.db, uid))
        self.assertEqual(st, "started")
        return run(P.run_deletion(self.db, uid, rec["deletion_id"])), rec

    def test_deletion_leaves_no_trace_of_the_user(self):
        status, _ = self.delete()
        self.assertEqual(status, "completed")
        blob = dump(self.db)
        for needle in (A, "alice@example.com", '"alice"', "1.2.3.4"):
            self.assertNotIn(needle, blob, needle)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 0)
        self.assertEqual(self.disconnected, [A])

    def test_sessions_and_auth_artifacts_removed(self):
        self.delete()
        for c in ("user_sessions", "recovery_tokens", "password_resets", "email_verifications", "webauthn_credentials",
                  "webauthn_challenges", "user_settings", "user_avatars", "studio_models", "friendships", "friend_requests", "blocks"):
            self.assertEqual(count(self.db, c, {"user_id": A}) if c not in ("friendships", "friend_requests", "blocks") else 0, 0, c)
        self.assertEqual(count(self.db, "user_sessions", {"session_token": "tokB1"}), 1)

    def test_other_users_data_untouched(self):
        before_b = {c: count(self.db, c, {"owner_id": B}) for c in ("games", "shop_items", "studio_models")}
        self.delete()
        after_b = {c: count(self.db, c, {"owner_id": B}) for c in ("games", "shop_items", "studio_models")}
        self.assertEqual(before_b, after_b)
        self.assertEqual(count(self.db, "game_scripts", {"game_id": "gB1"}), 1)
        self.assertEqual(count(self.db, "users", {"user_id": B}), 1)
        self.assertEqual(count(self.db, "users", {"user_id": C}), 1)
        self.assertEqual(count(self.db, "dm_messages", {"message_id": "m2"}), 1)       # mesajul lui Bob ramane
        self.assertEqual(count(self.db, "dm_messages", {"message_id": "m4"}), 1)
        self.assertEqual(count(self.db, "group_chat_messages", {"message_id": "gm3"}), 1)
        self.assertEqual(count(self.db, "shop_purchases", {"user_id": B, "item_id": "sh_sold"}), 1)
        self.assertEqual(count(self.db, "astran_ledger", {"tx_id": "t4"}), 1)
        self.assertEqual(count(self.db, "astran_ledger", {"tx_id": "t5", "user_id": B}), 1)
        self.assertEqual(count(self.db, "recently_played", {"user_id": B, "game_id": "gA1"}), 0)  # jocul lui Alice a disparut

    def test_owned_games_cascade_and_participation(self):
        self.delete()
        for g in ("gA1", "gA2"):
            self.assertEqual(count(self.db, "games", {"game_id": g}), 0)
            for c in ("game_scripts", "game_assets", "game_collaborators", "game_testers", "recently_played", "game_instances"):
                self.assertEqual(count(self.db, c, {"game_id": g}), 0, (c, g))
        self.assertEqual(count(self.db, "game_collaborators", {"user_id": A}), 0)
        self.assertEqual(count(self.db, "game_testers", {"user_id": A}), 0)
        self.assertEqual(count(self.db, "instance_players", {"user_id": A}), 0)
        inst = run(self.db.game_instances.find_one({"instance_id": "iB1"}))
        self.assertEqual(inst["player_count"], 1)

    def test_group_ownership_transfer_and_cleanup(self):
        self.delete()
        g1 = run(self.db.groups.find_one({"group_id": "grp_A1"}))
        self.assertEqual(g1["owner_id"], B)                                          # adminul preia grupul
        self.assertEqual(run(self.db.group_members.find_one({"group_id": "grp_A1", "user_id": B}))["role"], "owner")
        self.assertEqual(count(self.db, "groups", {"group_id": "grp_A2"}), 0)       # fara alti membri -> sters
        self.assertNotIn("group_id", run(self.db.games.find_one({"game_id": "gB1"})))  # jocul lui Bob nu mai pointeaza spre grup
        self.assertEqual(count(self.db, "groups", {"group_id": "grp_B1", "owner_id": B}), 1)
        self.assertEqual(count(self.db, "group_members", {"group_id": "grp_B1", "user_id": A}), 0)

    def test_group_messages_deleted_except_open_reports(self):
        self.delete()
        self.assertEqual(count(self.db, "group_chat_messages", {"message_id": "gm1"}), 0)
        self.assertEqual(count(self.db, "group_chat_messages", {"message_id": "gm4"}), 0)
        gm2 = run(self.db.group_chat_messages.find_one({"message_id": "gm2"}))
        self.assertTrue(gm2["user_id"].startswith("deleted_"))
        self.assertEqual(gm2["username"], "[deleted]")
        self.assertTrue(gm2["account_deleted"] and gm2["retain_until"] > NOW)

    def test_dm_messages_scrubbed_and_pseudonymized(self):
        self.delete()
        m1 = run(self.db.dm_messages.find_one({"message_id": "m1"}))
        self.assertTrue(m1["sender_id"].startswith("deleted_"))
        self.assertIsNone(m1["raw"])
        self.assertEqual(m1["safe"], "")
        conv = run(self.db.dm_conversations.find_one({"conversation_id": "dAB"}))
        self.assertIn(B, (conv["user_a"], conv["user_b"]))
        self.assertNotIn(A, (conv["user_a"], conv["user_b"]))

    def test_catalog_sold_items_delisted_unsold_deleted(self):
        self.delete()
        self.assertEqual(count(self.db, "shop_items", {"item_id": "sh_unsold"}), 0)
        self.assertEqual(count(self.db, "clothes_items", {"item_id": "cl_unsold"}), 0)
        sold = run(self.db.shop_items.find_one({"item_id": "sh_sold"}))
        self.assertTrue(sold["delisted"] and not sold["is_public"])
        self.assertTrue(sold["owner_id"].startswith("deleted_"))
        self.assertEqual(sold["owner_username"], "[deleted]")
        self.assertEqual(count(self.db, "shop_purchases", {"item_id": "sh_sold", "user_id": B}), 1)   # cumparatorul pastreaza
        self.assertEqual(count(self.db, "clothes_purchases", {"item_id": "cl_sold", "user_id": C}), 1)
        self.assertEqual(count(self.db, "shop_purchases", {"user_id": A}), 0)

    def test_financial_retention_and_sweep(self):
        self.delete()
        ps = P.pseudonym(A)
        led = run(self.db.astran_ledger.find(None).to_list(100))
        mine = [r for r in led if r["tx_id"] in ("t1", "t2", "t3")]
        self.assertEqual({r["tx_id"] for r in mine}, {"t2"})                       # doar achizitia reala se pastreaza
        t2 = mine[0]
        self.assertEqual(t2["user_id"], ps)
        self.assertEqual(t2["provider_ref"], "pi_123")                              # ramane unic: nu se poate re-credita plata
        self.assertTrue(t2["account_deleted"] and t2["retain_until"] > NOW + timedelta(days=365))
        t4 = run(self.db.astran_ledger.find_one({"tx_id": "t4"}))
        self.assertEqual(t4["counterparty_id"], ps)                                 # randul lui Bob ramane, cu pseudonim
        self.assertEqual(t4["amount"], 10)
        self.assertEqual(run(P.sweep_retention(self.db, NOW + timedelta(days=100))), 0 + count_expired(self.db, NOW + timedelta(days=100)))
        self.assertEqual(count(self.db, "astran_ledger", {"tx_id": "t2"}), 1)
        run(P.sweep_retention(self.db, NOW + timedelta(days=P.financial_retention_days() + 5)))
        self.assertEqual(count(self.db, "astran_ledger", {"tx_id": "t2"}), 0)
        self.assertEqual(count(self.db, "astran_ledger", {"tx_id": "t4"}), 1)       # al lui Bob: nu se atinge

    def test_reports_and_audit_pseudonymized(self):
        self.delete()
        ps = P.pseudonym(A)
        self.assertEqual(run(self.db.reports.find_one({"report_id": "r1"}))["reporter_id"], ps)
        self.assertEqual(run(self.db.reports.find_one({"report_id": "r2"}))["target_id"], ps)
        r3 = run(self.db.reports.find_one({"report_id": "r3"}))
        self.assertEqual(r3["context"]["sender_id"], ps)
        self.assertEqual(r3["context"]["sender_username"], "[deleted]")
        self.assertTrue(r3["retain_until"] > NOW)
        e1 = run(self.db.security_audit_log.find_one({"event_id": "e1"}))
        self.assertEqual(e1["user_id"], ps)
        self.assertIsNone(e1["ip"])
        self.assertEqual(run(self.db.security_audit_log.find_one({"event_id": "e2"}))["ip"], "5.6.7.8")
        proof = run(self.db.security_audit_log.find_one({"action": "account_delete", "result": "success"}))
        self.assertEqual(proof["user_id"], ps)

    def test_tombstone_has_no_user_id_or_email(self):
        status, rec = self.delete()
        t = run(self.db.account_deletions.find_one({"deletion_id": rec["deletion_id"]}))
        self.assertEqual(t["status"], "completed")
        self.assertNotIn("user_id", t)
        self.assertNotIn("alice", json.dumps(t, default=str))
        st = run(P.deletion_status(self.db, rec["deletion_id"]))
        self.assertEqual(st["status"], "completed")
        self.assertNotIn("pseudonym", st)
        self.assertIsNone(run(P.deletion_status(self.db, "del_unknown")))
        self.assertIsNone(run(P.deletion_status(self.db, "x" * 10)))

    def test_concurrent_requests_only_one_executes(self):
        async def go():
            return await asyncio.gather(*[P.begin_deletion(self.db, A) for _ in range(6)])
        res = run(go())
        self.assertEqual(sorted(r[0] for r in res), ["in_progress"] * 5 + ["started"])
        self.assertEqual(count(self.db, "account_deletions"), 1)
        self.assertEqual(len({r[1]["deletion_id"] for r in res}), 1)

    def test_interrupted_deletion_resumes(self):
        original = P._step_catalog

        async def boom(db, uid, pseud):
            raise RuntimeError("crash la jumatate")
        P._step_catalog = boom
        try:
            st, rec = run(P.begin_deletion(self.db, A))
            self.assertEqual(run(P.run_deletion(self.db, A, rec["deletion_id"])), "processing")
        finally:
            P._step_catalog = original
        u = run(self.db.users.find_one({"user_id": A}))
        self.assertEqual(u["deletion_state"], "processing")                          # inca blocat, nefinalizat
        self.assertEqual(count(self.db, "user_sessions", {"user_id": A}), 0)         # accesul a fost deja taiat
        self.assertEqual(count(self.db, "games", {"owner_id": A}), 0)
        run(self.db.account_deletions.update_one({"deletion_id": rec["deletion_id"]},
                                                 {"$set": {"lease_until": NOW - timedelta(seconds=1)}}))
        self.assertEqual(run(P.resume_stalled(self.db)), 1)
        self.assertEqual(run(self.db.account_deletions.find_one({"deletion_id": rec["deletion_id"]}))["status"], "completed")
        self.assertNotIn(A, dump(self.db))

    def test_resume_skipped_while_lease_active(self):
        original = P._step_catalog

        async def boom(db, uid, pseud):
            raise RuntimeError("x")
        P._step_catalog = boom
        try:
            _, rec = run(P.begin_deletion(self.db, A))
            run(P.run_deletion(self.db, A, rec["deletion_id"]))
        finally:
            P._step_catalog = original
        run(self.db.account_deletions.update_one({"deletion_id": rec["deletion_id"]}, {"$set": {"lease_until": NOW + timedelta(minutes=5)}}))
        self.assertEqual(run(P.resume_stalled(self.db)), 0)

    def test_deferred_while_money_operation_pending(self):
        run(self.db.astran_money_journal.insert_one({"op_id": "o1", "state": "pending", "payer_id": B, "credits": [{"user_id": A, "amount": 5}]}))
        st, rec = run(P.begin_deletion(self.db, A))
        self.assertEqual(run(P.run_deletion(self.db, A, rec["deletion_id"])), "processing")
        self.assertEqual(count(self.db, "users", {"user_id": A}), 1)
        self.assertEqual(count(self.db, "friendships"), 1)                           # nimic din datele sociale nu s-a atins inca
        run(self.db.astran_money_journal.update_one({"op_id": "o1"}, {"$set": {"state": "done"}}))
        run(self.db.account_deletions.update_one({"deletion_id": rec["deletion_id"]}, {"$set": {"lease_until": NOW - timedelta(seconds=1)}}))
        self.assertEqual(run(P.resume_stalled(self.db)), 1)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 0)

    def test_idempotent_steps(self):
        self.delete()
        for _ in range(2):   # rulat din nou dupa finalizare: nu strica nimic si nu ridica exceptii
            run(P._step_records(self.db, A, P.pseudonym(A)))
            run(P._step_groups(self.db, A, P.pseudonym(A)))
            run(P._step_catalog(self.db, A, P.pseudonym(A)))
        self.assertEqual(count(self.db, "users", {"user_id": B}), 1)

    def test_begin_deletion_unknown_user(self):
        self.assertEqual(run(P.begin_deletion(self.db, "user_nobody"))[0], "not_found")

    def test_pseudonym_is_stable_and_not_the_id(self):
        self.assertEqual(P.pseudonym(A), P.pseudonym(A))
        self.assertNotEqual(P.pseudonym(A), P.pseudonym(B))
        self.assertNotIn("alice", P.pseudonym(A))


def count_expired(db, now):
    return sum(1 for n, d in db.all_docs() if n in ("astran_ledger", "reports", "security_audit_log", "group_chat_messages")
               and d.get("account_deleted") and d.get("retain_until") and d["retain_until"] <= now)


# --------------------------------------------------------------------------- export
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()

    def test_export_contains_own_data(self):
        e = run(P.build_export(self.db, A))
        self.assertEqual(e["account"]["email"], "alice@example.com")
        self.assertEqual(e["preferences"], {"volume": 7})
        self.assertEqual({g["game_id"] for g in e["games"]}, {"gA1", "gA2"})
        self.assertEqual(e["games"][0]["files"][0]["path"] in ("main.lua", "m"), True)
        self.assertEqual({m["model_id"] for m in e["studio_models"]}, {"mA"})
        self.assertEqual({i["item_id"] for i in e["shop_items_published"]}, {"sh_sold", "sh_unsold"})
        self.assertEqual({p["item_id"] for p in e["purchases"]["shop"]}, {"sh_B"})
        self.assertEqual({t["tx_id"] for t in e["transactions"]}, {"t1", "t2", "t3"})
        self.assertEqual([f["user_id"] for f in e["social"]["friends"]], [B])
        self.assertEqual(e["social"]["friends"][0]["username"], "bob")
        self.assertEqual({m["group_id"] for m in e["groups"]["memberships"]}, {"grp_A1", "grp_A2", "grp_B1"})
        self.assertEqual({m["message_id"] for m in e["groups"]["messages_sent"]}, {"gm1", "gm2", "gm4"})
        self.assertEqual({x["game_id"] for x in e["collaborations"]["as_collaborator"]}, {"gB1"})
        sent = {m["message_id"]: m["text"] for c in e["direct_messages"] for m in c["messages_sent_by_you"]}
        self.assertEqual(sent, {"m1": "secret hello", "m3": "yo"})
        self.assertEqual({r["report_id"] for r in e["reports_submitted"]}, {"r1"})
        json.dumps(e, default=P.json_default)    # serializabil

    def test_export_excludes_secrets_and_other_users_data(self):
        blob = json.dumps(run(P.build_export(self.db, A)), default=P.json_default)
        for forbidden in ("pw:secret", "password_hash", "argon2$x", "project_password_hash", "SECRETJOINTOKEN", "tokA1", "tokA2",
                          "pi_123", "provider_ref", "_money_ops", "bob@example.com", "carol@example.com", "hi from bob",
                          "bob to carol", "bob says hi", "sessions_valid_after", "credential_id"):
            self.assertNotIn(forbidden, blob, forbidden)

    def test_export_is_scoped_to_the_requesting_user(self):
        eb = run(P.build_export(self.db, B))
        self.assertNotIn("alice@example.com", json.dumps(eb, default=P.json_default))
        self.assertEqual({g["game_id"] for g in eb["games"]}, {"gB1"})
        with self.assertRaises(LookupError):
            run(P.build_export(self.db, "user_nobody"))


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()

    def test_ticket_single_use_and_stored_hashed(self):
        t = run(P.create_export_ticket(self.db, A))
        self.assertNotIn(t["ticket"], dump(self.db))
        self.assertTrue(run(P.redeem_export_ticket(self.db, A, t["ticket"])))
        self.assertFalse(run(P.redeem_export_ticket(self.db, A, t["ticket"])))      # refolosire

    def test_ticket_expired(self):
        t = run(P.create_export_ticket(self.db, A))
        later = NOW + timedelta(seconds=P.export_ttl_seconds() + 5)
        self.assertFalse(run(P.redeem_export_ticket(self.db, A, t["ticket"], now=later)))

    def test_ticket_of_someone_else(self):
        t = run(P.create_export_ticket(self.db, A))
        self.assertFalse(run(P.redeem_export_ticket(self.db, B, t["ticket"])))
        self.assertTrue(run(P.redeem_export_ticket(self.db, A, t["ticket"])))        # al lui A ramane valid

    def test_garbage_tickets(self):
        for bad in ("", "short", None, "x" * 300, "a" * 43):
            self.assertFalse(run(P.redeem_export_ticket(self.db, A, bad)))

    def test_concurrent_redeem_only_once(self):
        t = run(P.create_export_ticket(self.db, A))

        async def go():
            return await asyncio.gather(*[P.redeem_export_ticket(self.db, A, t["ticket"]) for _ in range(5)])
        self.assertEqual(sorted(run(go())), [False] * 4 + [True])


# --------------------------------------------------------------------------- re-autentificare
async def _vp(pw, h):
    return h == "pw:" + pw


class ReauthTests(unittest.TestCase):
    def setUp(self):
        self.db = new_db()

    def go(self, uid, **kw):
        u = run(self.db.users.find_one({"user_id": uid}))
        args = dict(password=None, code=None, verify_pw=_vp, emailver=emailver, session_age_seconds=None, email_is_configured=True)
        args.update(kw)
        return run(P.verify_reauth(self.db, u, **args))

    def test_password_accounts(self):
        self.go(A, password="secret")
        for bad in (None, "", "nope"):
            with self.assertRaises(P.ReauthError) as cm:
                self.go(A, password=bad)
            self.assertEqual(cm.exception.status, 401)

    def test_password_account_cannot_use_code_or_fresh_session(self):
        with self.assertRaises(P.ReauthError):
            self.go(A, code="123456", session_age_seconds=1, email_is_configured=False)

    def test_google_account_email_code(self):
        code = run(emailver.issue(self.db, D, "dora@example.com", collection=P.REAUTH_CODE_COLLECTION))
        with self.assertRaises(P.ReauthError):
            self.go(D, code="000000" if code != "000000" else "111111")
        self.go(D, code=code)
        with self.assertRaises(P.ReauthError):      # codul e de unica folosinta
            self.go(D, code=code)

    def test_google_account_needs_code_when_email_available(self):
        with self.assertRaises(P.ReauthError) as cm:
            self.go(D, session_age_seconds=5, email_is_configured=True)
        self.assertEqual(cm.exception.code, "REAUTH_REQUIRED")

    def test_google_fresh_session_only_without_email_service(self):
        self.go(D, session_age_seconds=10, email_is_configured=False)
        with self.assertRaises(P.ReauthError):
            self.go(D, session_age_seconds=P.REAUTH_RECENT_SESSION_SECONDS + 1, email_is_configured=False)
        with self.assertRaises(P.ReauthError):
            self.go(D, session_age_seconds=None, email_is_configured=False)


# --------------------------------------------------------------------------- rute (handler-e apelate direct)
class FakePsec:
    def __init__(self, db, email=True):
        self.db, self.email, self.sent = db, email, []

    def email_is_configured(self):
        return self.email

    async def ensure_rate_limit_or_raise(self, db, *, user_id, scope_id, purpose):
        d = await db.security_attempts.find_one({"user_id": user_id, "scope_id": scope_id, "purpose": purpose})
        if d and d.get("count", 0) >= 5:
            raise HTTPException(status_code=429, detail="locked")

    async def record_failed_attempt(self, db, *, user_id, scope_id, purpose):
        k = {"user_id": user_id, "scope_id": scope_id, "purpose": purpose}
        r = await db.security_attempts.update_one(k, {"$inc": {"count": 1}})
        if r.matched_count == 0:
            await db.security_attempts.insert_one({**k, "count": 1})

    async def reset_attempts(self, db, *, user_id, scope_id, purpose):
        await db.security_attempts.delete_one({"user_id": user_id, "scope_id": scope_id, "purpose": purpose})

    async def log_security_event(self, db, *, action, result, user_id, game_id=None, detail=None, ip=None):
        await db.security_audit_log.insert_one({"event_id": "ev%d" % len(db.security_audit_log.docs), "action": action,
                                                "result": result, "user_id": user_id, "detail": detail, "ip": ip, "created_at": NOW})

    def send_plain_email(self, to, subject, body):
        self.sent.append((to, subject))
        return True


async def _gcu(*a, **k):
    return None


class RouteTests(unittest.TestCase):
    def setUp(self):
        PR._export_limiter = PR.SlidingWindowLimiter(5, 3600)
        PR._download_limiter = PR.SlidingWindowLimiter(20, 3600)
        PR._delete_limiter = PR.SlidingWindowLimiter(10, 3600)
        self.db = new_db()
        run(self.db.security_attempts.delete_many({"purpose": "account_delete"}))   # fara incercari esuate preexistente
        self.psec = FakePsec(self.db)
        self.router = PR.make_privacy_router(_gcu, self.db, verify_pw=_vp, client_ip=lambda r: "9.9.9.9", psec=self.psec, emailver=emailver)
        self.r = self.router.routes
        P._DISCONNECTORS.clear()

    def h(self, method, path):
        return self.r[(method, "/privacy" + path)]

    def cur(self, uid=A):
        return {"user_id": uid, "is_platform_owner": False}

    def delete(self, uid=A, **kw):
        body = PR.DeleteAccountBody(**{"password": "secret", "confirm": True, "confirmation_text": "DELETE", **kw})
        return run(self.h("POST", "/delete-account")(body, object(), current=self.cur(uid), authorization="Bearer tokA1"))

    def status_of(self, fn):
        try:
            res = fn()
            return res.status_code
        except HTTPException as e:
            return e.status_code

    def test_every_user_route_requires_authentication(self):
        for (m, path), fn in self.r.items():
            if path.endswith("/deletion/{deletion_id}"):
                continue
            self.assertIn("current", inspect.signature(fn).parameters, path)
            self.assertIs(inspect.signature(fn).parameters["current"].default, _gcu, path)

    def test_no_route_accepts_a_target_user_id(self):
        for model in (PR.ReauthBody, PR.DeleteAccountBody, PR.DownloadBody):
            self.assertFalse(hasattr(model(), "user_id"))
        for fn in self.r.values():
            self.assertNotIn("user_id", inspect.signature(fn).parameters)

    def test_delete_requires_explicit_confirmation(self):
        self.assertEqual(self.status_of(lambda: self.delete(confirm=False)), 400)
        self.assertEqual(self.status_of(lambda: self.delete(confirmation_text="")), 400)
        self.assertEqual(self.status_of(lambda: self.delete(confirmation_text="yes")), 400)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 1)

    def test_delete_requires_reauth(self):
        self.assertEqual(self.status_of(lambda: self.delete(password="wrong")), 401)
        self.assertEqual(self.status_of(lambda: self.delete(password=None)), 401)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 1)
        self.assertNotIn("deletion_state", run(self.db.users.find_one({"user_id": A})))

    def test_wrong_password_locks_after_five_attempts(self):
        codes = [self.status_of(lambda: self.delete(password="bad")) for _ in range(7)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5:], [429, 429])
        self.assertEqual(count(self.db, "users", {"user_id": A}), 1)

    def test_cannot_delete_someone_elses_account(self):
        # Bob autentificat, dar cu parola lui Alice: reautentificarea foloseste hash-ul lui Bob -> respins
        self.assertEqual(self.status_of(lambda: self.delete(uid=B, password="secret")), 401)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 1)
        self.assertEqual(count(self.db, "users", {"user_id": B}), 1)
        # Bob sterge DOAR contul lui
        res = self.delete(uid=B, password="bobpw")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 1)
        self.assertEqual(count(self.db, "games", {"owner_id": A}), 2)

    def test_platform_owner_cannot_delete(self):
        body = PR.DeleteAccountBody(password="secret", confirm=True, confirmation_text="DELETE")
        with self.assertRaises(HTTPException) as cm:
            run(self.h("POST", "/delete-account")(body, object(), current={"user_id": A, "is_platform_owner": True}, authorization=None))
        self.assertEqual(cm.exception.status_code, 403)

    def test_successful_delete_end_to_end(self):
        res = self.delete()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content["status"], "completed")
        self.assertEqual(count(self.db, "users", {"user_id": A}), 0)
        self.assertNotIn(A, dump(self.db))
        self.assertNotIn("alice@example.com", dump(self.db))
        run(asyncio.sleep(0.05))                                                    # lasa task-ul de email sa termine
        self.assertEqual([m[0] for m in self.psec.sent], ["alice@example.com"])     # confirmare trimisa, adresa nestocata
        st = run(self.h("GET", "/deletion/{deletion_id}")(res.content["deletion_id"], object()))
        self.assertEqual(st["status"], "completed")
        # a doua cerere dupa stergere: contul nu mai exista -> 401, nu o a doua stergere
        self.assertEqual(self.status_of(lambda: self.delete()), 401)

    def test_two_simultaneous_requests(self):
        async def go():
            mk = lambda: self.h("POST", "/delete-account")(  # noqa: E731
                PR.DeleteAccountBody(password="secret", confirm=True, confirmation_text="DELETE"), object(),
                current=self.cur(), authorization=None)
            return await asyncio.gather(mk(), mk(), return_exceptions=True)
        out = run(go())
        codes = sorted(getattr(o, "status_code", None) for o in out)
        self.assertEqual(codes.count(200), 1, codes)
        self.assertTrue(all(c in (200, 202, 401) for c in codes), codes)
        self.assertEqual(count(self.db, "account_deletions"), 1)
        self.assertEqual(count(self.db, "users", {"user_id": A}), 0)
        self.assertNotIn(A, dump(self.db))

    def test_google_account_delete_with_email_code(self):
        d = lambda **kw: run(self.h("POST", "/delete-account")(  # noqa: E731
            PR.DeleteAccountBody(confirm=True, confirmation_text="DELETE", **kw), object(), current=self.cur(D), authorization="Bearer x"))
        self.assertEqual(self.status_of(lambda: d()), 401)
        run(self.h("POST", "/reauth/code")(current=self.cur(D)))
        self.assertEqual(len(self.psec.sent), 1)                                    # codul a fost trimis pe email
        # codul e stocat doar ca hash: re-emitem direct (stergand inregistrarea, ca sa ocolim cooldown-ul) ca sa-l cunoastem
        run(self.db[P.REAUTH_CODE_COLLECTION].delete_many({"user_id": D}))
        code = run(emailver.issue(self.db, D, "dora@example.com", collection=P.REAUTH_CODE_COLLECTION))
        self.assertIn(self.status_of(lambda: d(code="999999" if code != "999999" else "111111")), (400, 401))
        self.assertEqual(d(code=code).status_code, 200)
        self.assertEqual(count(self.db, "users", {"user_id": D}), 0)

    def test_reauth_code_rejected_for_password_account(self):
        with self.assertRaises(HTTPException) as cm:
            run(self.h("POST", "/reauth/code")(current=self.cur(A)))
        self.assertEqual(cm.exception.status_code, 409)

    # ---- export
    def export(self, uid=A, **kw):
        return run(self.h("POST", "/export/request")(PR.ReauthBody(password=kw.get("password", "secret")), object(),
                                                   current=self.cur(uid), authorization="Bearer tokA1"))

    def download(self, ticket, uid=A):
        return run(self.h("POST", "/export/download")(PR.DownloadBody(ticket=ticket), object(), current=self.cur(uid)))

    def test_export_requires_reauth(self):
        self.assertEqual(self.status_of(lambda: self.export(password="bad")), 401)
        self.assertEqual(count(self.db, "privacy_exports"), 0)

    def test_export_flow_and_headers(self):
        t = self.export()
        self.assertEqual(t["format"], "json")
        res = self.download(t["ticket"])
        self.assertEqual(res.media_type, "application/json")
        self.assertIn("attachment", res.headers["Content-Disposition"])
        self.assertIn("no-store", res.headers["Cache-Control"])
        data = json.loads(res.content)
        self.assertEqual(data["account"]["user_id"], A)
        self.assertNotIn("pw:secret", res.content)

    def test_export_link_cannot_be_reused_or_used_by_others_or_after_expiry(self):
        t = self.export()
        self.assertEqual(self.status_of(lambda: self.download(t["ticket"], uid=B)), 404)   # al altcuiva
        ok = self.download(t["ticket"])
        self.assertTrue(json.loads(ok.content)["account"])
        self.assertEqual(self.status_of(lambda: self.download(t["ticket"])), 404)          # reutilizare
        t2 = self.export()
        run(self.db.privacy_exports.update_many({}, {"$set": {"expires_at": NOW - timedelta(seconds=1)}}))
        self.assertEqual(self.status_of(lambda: self.download(t2["ticket"])), 404)         # expirat
        self.assertEqual(self.status_of(lambda: self.download("A" * 43)), 404)             # inexistent

    def test_status_endpoint_has_no_secrets(self):
        s = run(self.h("GET", "/status")(current=self.cur(A)))
        self.assertTrue(s["has_password"])
        self.assertEqual(s["email_masked"], "a***@example.com")
        self.assertNotIn("password_hash", json.dumps(s, default=str))


# --------------------------------------------------------------------------- sesiuni vechi (cod REAL din server.py)
def _load_server_auth():
    src = SERVER_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    want = {"now_utc", "_token_hash", "_strip_secret", "_jwt_predates_reset", "resolve_user_from_token", "make_jwt",
            "_credentials_reset_marker"}
    chunks = [ast.get_source_segment(src, n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in want]
    assert len(chunks) == len(want), "functii lipsa in server.py"
    return chunks


class OldSessionTests(unittest.TestCase):
    """Foloseste resolve_user_from_token EXACT cum e in server.py (extras prin ast)."""

    def setUp(self):
        self.db = new_db()
        import hashlib
        from typing import Optional
        self.ns = {"db": self.db, "jwt": pyjwt, "datetime": datetime, "timezone": timezone, "timedelta": timedelta, "Optional": Optional,
                   "hashlib": hashlib, "JWT_SECRET": "s" * 32, "JWT_ALG": "HS256", "JWT_IAT_LEEWAY": 2, "JWT_TTL_DAYS": 7}
        exec("from __future__ import annotations\n" + "\n\n".join(_load_server_auth()), self.ns)
        self.resolve = self.ns["resolve_user_from_token"]

    def test_tokens_work_before_deletion(self):
        self.assertEqual(run(self.resolve("tokA1"))["user_id"], A)
        self.assertEqual(run(self.resolve(self.ns["make_jwt"](A)))["user_id"], A)

    def test_old_session_and_jwt_rejected_while_deletion_in_progress(self):
        jwt_a = self.ns["make_jwt"](A)
        run(P.begin_deletion(self.db, A))                      # doar blocarea, inainte de orice pas
        self.assertIsNone(run(self.resolve("tokA1")))
        self.assertIsNone(run(self.resolve(jwt_a)))
        self.assertEqual(run(self.resolve("tokB1"))["user_id"], B)

    def test_old_session_and_jwt_rejected_after_deletion(self):
        jwt_a = self.ns["make_jwt"](A)
        _, rec = run(P.begin_deletion(self.db, A))
        run(P.run_deletion(self.db, A, rec["deletion_id"]))
        self.assertIsNone(run(self.resolve("tokA1")))
        self.assertIsNone(run(self.resolve("tokA2")))
        self.assertIsNone(run(self.resolve(jwt_a)))

    def test_new_account_with_same_email_does_not_inherit_old_jwt(self):
        jwt_a = self.ns["make_jwt"](A)
        _, rec = run(P.begin_deletion(self.db, A))
        run(P.run_deletion(self.db, A, rec["deletion_id"]))
        run(self.db.users.insert_one({"user_id": "user_new", "email": "alice@example.com", "username": "alice", "password_hash": "pw:x"}))
        self.assertIsNone(run(self.resolve(jwt_a)))             # sub = vechiul user_id


class ServerWiringStaticTests(unittest.TestCase):
    def setUp(self):
        self.src = SERVER_PY.read_text(encoding="utf-8")

    def test_login_and_google_session_refuse_accounts_being_deleted(self):
        self.assertEqual(len(re.findall(r'deletion_state"\):\s*\n\s*raise HTTPException\(status_code=403', self.src)), 2)

    def test_router_worker_and_indexes_wired(self):
        for needle in ("make_privacy_router(get_current_user, db", "privacy.deletion_worker(db)", "privacy.ensure_indexes(db)",
                       'getattr(app.state, "privacy_task"'):
            self.assertIn(needle, self.src)


if __name__ == "__main__":
    unittest.main()