"""Teste pentru protectia Adult<->Minor in GRUPURI, chatul de grup, chatul din joc si WebSocket.

Ruleaza handler-ele REALE din group_routes.py si realtime.py (nu copii ale logicii) pe o baza de date in memorie.
Daca fastapi/pydantic/pymongo nu sunt instalate se folosesc inlocuitori minimali (test_support_stubs.py); daca sunt
instalate, testele ruleaza pe cele reale.

    cd backend && python -m unittest astran_sandbox.test_group_age_protection -v
"""
import asyncio
import inspect
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from astran_sandbox import test_support_age as AGE
from astran_sandbox import test_support_stubs as S

S.install_if_missing()

from fastapi import HTTPException, WebSocketDisconnect   # noqa: E402  (real sau stub)

from astran_sandbox import contact_policy as CP          # noqa: E402
from astran_sandbox import group_age as GA               # noqa: E402
from astran_sandbox import group_age_migration as GM     # noqa: E402
from astran_sandbox import group_routes as GR            # noqa: E402
from astran_sandbox import test_support_fakedb as FDB    # noqa: E402
from astran_sandbox.test_chat import ADULT, MINOR, setup as chat_setup   # noqa: E402

UNSET = object()


def _pymongo_dupkey():
    import pymongo.errors
    return pymongo.errors.DuplicateKeyError


class Env:
    """Un router de grupuri real peste un FakeDB. `user(uid)` e ce intoarce get_current_user: NU contine incredere in
    varsta (categoria se citeste mereu din DB)."""

    def __init__(self):
        self.db = FDB.FakeDB()
        self.router = GR.make_group_router(lambda: None, self.db, lambda u: {"user_id": u["user_id"], "username": u.get("username")})

    def add_user(self, uid, category=UNSET, balance=1000, **extra):
        doc = {"user_id": uid, "username": uid, "astrans_balance": balance, **extra}
        if category is not UNSET:
            AGE.set(doc, category)
        self.db.users.docs.append(doc)

    def user(self, uid):
        return {"user_id": uid, "username": uid}

    def ep(self, method, path):
        return S.endpoint(self.router, method, "/groups" + path)

    async def create(self, uid, name="Grup test"):
        out = await self.ep("POST", "")(body=GR.CreateGroupBody(name=name), current=self.user(uid))
        return out["group"]["group_id"]

    async def join(self, gid, uid):
        return await self.ep("POST", "/{group_id}/join")(group_id=gid, current=self.user(uid))

    async def leave(self, gid, uid):
        return await self.ep("POST", "/{group_id}/leave")(group_id=gid, current=self.user(uid))

    async def send(self, gid, uid, text="salut"):
        return await self.ep("POST", "/{group_id}/chat/messages")(group_id=gid, body=GR.ChatMessageBody(text=text), current=self.user(uid))

    async def read(self, gid, uid):
        return (await self.ep("GET", "/{group_id}/chat/messages")(group_id=gid, current=self.user(uid)))["messages"]

    async def active(self, gid):
        rows = await self.db.group_members.find({"group_id": gid, "status": "active"}, {"_id": 0}).to_list(None)
        return {r["user_id"] for r in rows}

    def raw_member(self, gid, uid, role="member", status="active"):
        self.db.group_members.docs.append({"group_id": gid, "user_id": uid, "role": role, "status": status,
                                           "muted": False, "joined_at": datetime.now(timezone.utc)})

    def raw_group(self, gid, owner, category=UNSET, **extra):
        doc = {"group_id": gid, "owner_id": owner, "name": gid, "description": "", "logo_url": None, "token": gid + "tok",
               "chat_mode": "everyone", "created_at": datetime.now(timezone.utc), **extra}
        if category is not UNSET:
            AGE.set(doc, category)
        self.db.groups.docs.append(doc)
        self.raw_member(gid, owner, role="owner")


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # FakeDB ridica propria clasa DuplicateKeyError; daca pymongo real e instalat, o aliniem DOAR in aceste teste
        # (altfel money.settle nu ar recunoaste duplicatele). Fara pymongo real, stub-ul foloseste deja clasa FakeDB.
        patcher = mock.patch.object(FDB, "DuplicateKeyError", _pymongo_dupkey())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = Env()
        for uid, cat in (("minor1", MINOR), ("minor2", MINOR), ("adult1", ADULT), ("adult2", ADULT)):
            self.env.add_user(uid, cat)

    async def denied(self, coro, status, code):
        with self.assertRaises(HTTPException) as cm:
            await coro
        self.assertEqual(cm.exception.status_code, status)
        self.assertIsInstance(cm.exception.detail, dict)
        self.assertEqual(cm.exception.detail["code"], code)
        return cm.exception


# ======================================================================================================== 1-4: acces
class GroupAccess(Base):
    async def test_01_minor_joins_under18_group(self):
        gid = await self.env.create("minor1")
        self.assertEqual(await self.env.join(gid, "minor2"), {"ok": True})
        self.assertEqual(await self.env.active(gid), {"minor1", "minor2"})

    async def test_02_adult_joins_adult_group(self):
        gid = await self.env.create("adult1")
        self.assertEqual(await self.env.join(gid, "adult2"), {"ok": True})
        self.assertEqual(await self.env.active(gid), {"adult1", "adult2"})

    async def test_03_adult_cannot_join_under18_group(self):
        gid = await self.env.create("minor1")
        await self.denied(self.env.join(gid, "adult1"), 403, CP.CODE_AGE_MISMATCH)
        self.assertEqual(await self.env.active(gid), {"minor1"})

    async def test_04_minor_cannot_join_adult_group(self):
        gid = await self.env.create("adult1")
        exc = await self.denied(self.env.join(gid, "minor1"), 403, CP.CODE_AGE_MISMATCH)
        self.assertEqual(await self.env.active(gid), {"adult1"})
        # mesaj generic: nu dezvaluie care parte e minora
        self.assertNotIn("minor", exc.detail["message"].lower())
        self.assertNotIn("adult", exc.detail["message"].lower())

    async def test_group_category_is_persisted_from_owner_in_db(self):
        g_minor = await self.env.create("minor1")
        g_adult = await self.env.create("adult1")
        docs = {d["group_id"]: d for d in self.env.db.groups.docs}
        self.assertEqual(docs[g_minor]["age_category"], MINOR)
        self.assertEqual(docs[g_adult]["age_category"], ADULT)
        out = await self.env.ep("GET", "/{group_id}")(group_id=g_minor, current=self.env.user("minor2"))
        self.assertNotIn("age_category", out["group"])        # categoria nu se expune (ar dezvalui varsta proprietarului)
        self.assertFalse(out["group"]["age_restricted"])

    async def test_unknown_owner_cannot_create_group_and_is_not_charged(self):
        for i, cat in enumerate((UNSET, None, "", "ADULT_18", "adult", 18)):
            self.env.add_user(f"u{i}", cat)
            with self.assertRaises(HTTPException) as cm:
                await self.env.create(f"u{i}")
            self.assertEqual(cm.exception.detail["code"], CP.CODE_AGE_UNKNOWN)
            self.assertEqual(next(u for u in self.env.db.users.docs if u["user_id"] == f"u{i}")["astrans_balance"], 1000)
        self.assertEqual(self.env.db.groups.docs, [])

    async def test_members_list_not_enumerable_across_categories(self):
        gid = await self.env.create("minor1")
        await self.denied(self.env.ep("GET", "/{group_id}/members")(group_id=gid, current=self.env.user("adult1")), 403, CP.CODE_AGE_MISMATCH)
        out = await self.env.ep("GET", "/{group_id}/members")(group_id=gid, current=self.env.user("minor2"))
        self.assertEqual([m["user_id"] for m in out["members"]], ["minor1"])


# ======================================================================================================== 9: client
class ClientCannotChangeCategory(Base):
    async def test_09_forged_category_in_request_context_is_ignored(self):
        gid = await self.env.create("adult1")
        forged = {"user_id": "minor1", "username": "minor1", "age_category": "adult_18", "is_adult": True}
        await self.denied(self.env.ep("POST", "/{group_id}/join")(group_id=gid, current=forged), 403, CP.CODE_AGE_MISMATCH)
        self.assertEqual(await self.env.active(gid), {"adult1"})

    async def test_09_group_category_cannot_be_changed_by_owner_or_anyone(self):
        gid = await self.env.create("minor1")
        for payload in ({"age_category": "adult_18"}, {"name": "x", "age_category": "adult_18"}, {"age_review": False}, {"owner_id": "adult1"}):
            with self.assertRaises(Exception) as cm:       # pydantic ValidationError (422 in FastAPI): camp interzis
                GR.UpdateGroupBody(**payload)
            self.assertIn("ValidationError", type(cm.exception).__name__)
        out = await self.env.ep("PATCH", "/{group_id}")(group_id=gid, body=GR.UpdateGroupBody(name="Nume nou"), current=self.env.user("minor1"))
        self.assertEqual(out["group"]["name"], "Nume nou")
        doc = next(d for d in self.env.db.groups.docs if d["group_id"] == gid)
        self.assertEqual(doc["age_category"], MINOR)
        await self.denied(self.env.join(gid, "adult1"), 403, CP.CODE_AGE_MISMATCH)

    async def test_user_category_changes_only_from_db(self):
        gid = await self.env.create("adult1")
        AGE.set(next(u for u in self.env.db.users.docs if u["user_id"] == "minor1"), ADULT)   # verificare 18+ reala (backend)
        self.assertEqual(await self.env.join(gid, "minor1"), {"ok": True})


# ======================================================================================================== 11: invitatii
class EntryPaths(Base):
    def test_11_no_alternative_entry_point_bypasses_the_central_check(self):
        src = inspect.getsource(GR)
        self.assertNotIn("group_members.insert_one", src)        # nicio inserare directa de membru in rute
        self.assertIn("GA.add_member", inspect.getsource(GR.make_group_router))
        paths = {(m, r.path) for r in self.env.router.routes for m in (getattr(r, "methods", None) or {"WS"})}
        self.assertFalse([p for _, p in paths if "invit" in p.lower() or "code" in p.lower()],
                         "a aparut o ruta de invitatie/cod: trebuie sa foloseasca group_age.add_member")

    async def test_11_invitation_or_code_flow_would_be_verified_by_add_member(self):
        gid = await self.env.create("minor1")
        with self.assertRaises(GA.GroupAccessError) as cm:
            await GA.add_member(self.env.db, gid, "adult1")       # aceeasi functie pe care o foloseste orice flux de invitatie
        self.assertEqual(cm.exception.code, CP.CODE_AGE_MISMATCH)
        self.assertEqual(await GA.add_member(self.env.db, gid, "minor2"), "joined")
        with self.assertRaises(GA.GroupAccessError) as cm:
            await GA.add_member(self.env.db, "group_inexistent", "minor2")
        self.assertEqual(cm.exception.status, 404)

    async def test_banned_and_duplicate_still_behave_as_before(self):
        gid = await self.env.create("minor1")
        await self.env.join(gid, "minor2")
        with self.assertRaises(HTTPException) as cm:
            await self.env.join(gid, "minor2")
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (409, "Already a member"))
        await self.env.ep("POST", "/{group_id}/members/{user_id}/ban")(group_id=gid, user_id="minor2", current=self.env.user("minor1"))
        with self.assertRaises(HTTPException) as cm:
            await self.env.join(gid, "minor2")
        self.assertEqual((cm.exception.status_code, cm.exception.detail), (403, "You are banned from this Group"))


# ======================================================================================================== 12: concurenta
class Concurrency(Base):
    async def test_12_simultaneous_join_only_compatible_category_enters(self):
        for gid_owner in ("minor1", "adult1"):
            gid = await self.env.create(gid_owner)
            res = await asyncio.gather(self.env.join(gid, "minor2"), self.env.join(gid, "adult2"), return_exceptions=True)
            members = await self.env.active(gid)
            if gid_owner == "minor1":
                self.assertEqual(members, {"minor1", "minor2"})
                self.assertIsInstance(res[1], HTTPException)
            else:
                self.assertEqual(members, {"adult1", "adult2"})
                self.assertIsInstance(res[0], HTTPException)

    async def test_12_same_user_joining_twice_at_once_creates_one_membership(self):
        gid = await self.env.create("minor1")
        res = await asyncio.gather(self.env.join(gid, "minor2"), self.env.join(gid, "minor2"), return_exceptions=True)
        self.assertEqual(sum(1 for r in res if r == {"ok": True}), 1)
        self.assertEqual(sum(1 for d in self.env.db.group_members.docs if d["user_id"] == "minor2"), 1)

    async def test_12_group_frozen_during_join_rolls_the_membership_back(self):
        """Fereastra dintre verificare si scriere: grupul e inghetat (migrare) exact cand utilizatorul e inserat."""
        gid = await self.env.create("minor1")
        real_insert = self.env.db.group_members.insert_one

        async def insert_then_freeze(doc):
            res = await real_insert(doc)
            await self.env.db.groups.update_one({"group_id": gid}, {"$set": {"age_review": True}})
            return res
        with mock.patch.object(self.env.db.group_members, "insert_one", insert_then_freeze):
            await self.denied(self.env.join(gid, "minor2"), 423, CP.CODE_GROUP_REVIEW)
        self.assertEqual(await self.env.active(gid), {"minor1"})


# ======================================================================================================== 13: necunoscut
class UnknownAge(Base):
    BAD = (UNSET, None, "", "ADULT_18", "adult", "under18", 18, ["adult_18"])

    async def test_13_unknown_age_cannot_join_any_group(self):
        g_minor, g_adult = await self.env.create("minor1"), await self.env.create("adult1")
        for i, cat in enumerate(self.BAD):
            self.env.add_user(f"bad{i}", cat)
            for gid in (g_minor, g_adult):
                await self.denied(self.env.join(gid, f"bad{i}"), 403, CP.CODE_AGE_UNKNOWN)
        self.assertEqual(await self.env.active(g_minor), {"minor1"})
        self.assertEqual(await self.env.active(g_adult), {"adult1"})

    async def test_13_unknown_age_member_cannot_speak_or_read(self):
        gid = await self.env.create("minor1")
        self.env.add_user("bad", UNSET)
        self.env.raw_member(gid, "bad")                       # membru mostenit cu varsta necunoscuta
        await self.denied(self.env.send(gid, "bad"), 403, CP.CODE_AGE_UNKNOWN)
        await self.denied(self.env.ep("GET", "/{group_id}/chat/messages")(group_id=gid, current=self.env.user("bad")), 403, CP.CODE_AGE_UNKNOWN)
        self.assertEqual(self.env.db.group_chat_messages.docs, [])

    async def test_13_category_missing_in_db_is_not_defaulted_to_adult_or_minor(self):
        self.assertIsNone(CP.category_of(None))
        self.assertIsNone(CP.user_category({"user_id": "x"}))
        self.assertEqual(CP.same_group_allowed(None, ADULT), CP.CODE_AGE_UNKNOWN)
        self.assertEqual(CP.same_group_allowed(MINOR, "x"), CP.CODE_AGE_UNKNOWN)

    async def test_13_unknown_is_blocked_even_when_adult_minor_protection_is_switched_off(self):
        env = {CP.ENV_VAR: "off"}
        self.assertIsNone(CP.same_group_allowed(ADULT, MINOR, env))               # intrerupatorul explicit afecteaza DOAR amestecul
        self.assertEqual(CP.same_group_allowed(None, MINOR, env), CP.CODE_AGE_UNKNOWN)


# ======================================================================================================== 14: reintrare
class Rejoin(Base):
    async def test_14_leave_and_rejoin_runs_the_check_again(self):
        gid = await self.env.create("minor1")
        await self.env.join(gid, "minor2")
        await self.env.leave(gid, "minor2")
        self.assertEqual(await self.env.active(gid), {"minor1"})
        AGE.set(next(u for u in self.env.db.users.docs if u["user_id"] == "minor2"), ADULT)
        await self.denied(self.env.join(gid, "minor2"), 403, CP.CODE_AGE_MISMATCH)    # reintrarea e verificata din nou
        self.assertEqual(await self.env.active(gid), {"minor1"})

    async def test_14_muted_member_row_kept_on_leave_is_also_rechecked_and_stays_inactive(self):
        gid = await self.env.create("minor1")
        await self.env.join(gid, "minor2")
        await self.env.ep("POST", "/{group_id}/members/{user_id}/mute")(group_id=gid, user_id="minor2", current=self.env.user("minor1"))
        await self.env.leave(gid, "minor2")
        row = next(d for d in self.env.db.group_members.docs if d["user_id"] == "minor2")
        self.assertEqual(row["status"], "left")
        AGE.set(next(u for u in self.env.db.users.docs if u["user_id"] == "minor2"), None)
        await self.denied(self.env.join(gid, "minor2"), 403, CP.CODE_AGE_UNKNOWN)
        self.assertEqual(next(d for d in self.env.db.group_members.docs if d["user_id"] == "minor2")["status"], "left")

    async def test_14_compatible_rejoin_still_works_and_keeps_mute(self):
        gid = await self.env.create("minor1")
        await self.env.join(gid, "minor2")
        await self.env.ep("POST", "/{group_id}/members/{user_id}/mute")(group_id=gid, user_id="minor2", current=self.env.user("minor1"))
        await self.env.leave(gid, "minor2")
        await self.env.join(gid, "minor2")
        row = next(d for d in self.env.db.group_members.docs if d["user_id"] == "minor2")
        self.assertEqual((row["status"], row["muted"]), ("active", True))


# ======================================================================================================== 5-6: chat de grup
class GroupChat(Base):
    async def test_same_category_chat_works_and_stores_server_side_category(self):
        for owner, other in (("minor1", "minor2"), ("adult1", "adult2")):
            gid = await self.env.create(owner)
            await self.env.join(gid, other)
            await self.env.send(gid, owner, "salut")
            seen = await self.env.read(gid, other)
            self.assertEqual([m["text"] for m in seen], ["salut"])
            self.assertNotIn("sender_age_category", seen[0])             # nu se expune clientului
            stored = [d for d in self.env.db.group_chat_messages.docs if d["group_id"] == gid][0]
            self.assertEqual(stored["sender_age_category"], self.cat(owner))

    def cat(self, uid):
        return ADULT if uid.startswith("adult") else MINOR

    async def test_05_adult_cannot_message_minors_in_group_chat(self):
        gid = await self.env.create("minor1")
        self.env.raw_member(gid, "adult1")                  # stare mostenita: adult intr-un grup under_18
        await self.denied(self.env.send(gid, "adult1", "salut copii"), 403, CP.CODE_AGE_MISMATCH)
        self.assertEqual([d for d in self.env.db.group_chat_messages.docs], [])      # NU se salveaza
        self.assertEqual(await self.env.read(gid, "minor1"), [])                      # NU se distribuie

    async def test_06_minor_cannot_message_adults_in_group_chat(self):
        gid = await self.env.create("adult1")
        self.env.raw_member(gid, "minor1")
        await self.denied(self.env.send(gid, "minor1", "salut"), 403, CP.CODE_AGE_MISMATCH)
        self.assertEqual(self.env.db.group_chat_messages.docs, [])
        self.assertEqual(await self.env.read(gid, "adult1"), [])

    async def test_legacy_messages_from_other_category_are_never_returned(self):
        gid = await self.env.create("minor1")
        self.env.raw_member(gid, "adult1")
        now = datetime.now(timezone.utc)
        for i, (uid, extra) in enumerate((("adult1", {"sender_adult": True}),        # mesaj vechi, fara sender_age_category
                                          ("adult1", {"sender_age_category": ADULT}),
                                          ("minor1", {"sender_adult": False}))):
            self.env.db.group_chat_messages.docs.append({"message_id": f"m{i}", "group_id": gid, "user_id": uid, "username": uid,
                                                          "text": f"mesaj {i}", "created_at": now, **extra})
        seen = await self.env.read(gid, "minor1")
        self.assertEqual([m["user_id"] for m in seen], ["minor1"])
        await self.denied(self.env.ep("GET", "/{group_id}/chat/messages")(group_id=gid, current=self.env.user("adult1")), 403, CP.CODE_AGE_MISMATCH)

    async def test_frozen_legacy_group_neither_sends_nor_reads(self):
        self.env.raw_group("legacy", "minor1")                    # fara age_category
        self.env.raw_member("legacy", "adult1")
        await self.denied(self.env.send("legacy", "minor1"), 423, CP.CODE_GROUP_REVIEW)
        await self.denied(self.env.send("legacy", "adult1"), 423, CP.CODE_GROUP_REVIEW)
        await self.denied(self.env.ep("GET", "/{group_id}/chat/messages")(group_id="legacy", current=self.env.user("adult1")), 423, CP.CODE_GROUP_REVIEW)
        await self.denied(self.env.join("legacy", "minor2"), 423, CP.CODE_GROUP_REVIEW)
        out = await self.env.ep("GET", "/{group_id}")(group_id="legacy", current=self.env.user("minor1"))
        self.assertTrue(out["group"]["age_restricted"])

    async def test_member_who_changed_category_after_joining_is_cut_off(self):
        gid = await self.env.create("minor1")
        await self.env.join(gid, "minor2")
        AGE.set(next(u for u in self.env.db.users.docs if u["user_id"] == "minor2"), ADULT)
        await self.denied(self.env.send(gid, "minor2"), 403, CP.CODE_AGE_MISMATCH)


# ======================================================================================================== migrare
class Migration(Base):
    def seed(self):
        e = self.env
        e.raw_group("g_minor", "minor1", UNSET); e.raw_member("g_minor", "minor2")
        e.raw_group("g_adult", "adult1", UNSET); e.raw_member("g_adult", "adult2")
        e.add_user("owner_m", MINOR); e.add_user("owner_u", UNSET); e.add_user("member_u", UNSET)
        e.raw_group("g_mixed", "owner_m", UNSET); e.raw_member("g_mixed", "adult2", role="admin"); e.raw_member("g_mixed", "minor2")
        e.raw_group("g_unknown_owner", "owner_u", UNSET)
        e.add_user("owner_m2", MINOR)
        e.raw_group("g_unknown_member", "owner_m2", MINOR); e.raw_member("g_unknown_member", "member_u")
        e.db.group_chat_messages.docs.append({"message_id": "x", "group_id": "g_mixed", "user_id": "adult2", "username": "adult2",
                                              "text": "istoric", "created_at": datetime.now(timezone.utc)})

    def snapshot(self):
        return (json.dumps(sorted(self.env.db.group_members.docs, key=lambda d: (d["group_id"], d["user_id"])), default=str),
                json.dumps(self.env.db.group_chat_messages.docs, default=str), json.dumps(self.env.db.users.docs, default=str))

    async def test_dry_run_changes_nothing(self):
        self.seed()
        before = json.dumps(self.env.db.groups.docs, default=str)
        rep = await GM.audit_groups(self.env.db)
        self.assertFalse(rep["applied"])
        self.assertEqual(json.dumps(self.env.db.groups.docs, default=str), before)
        self.assertEqual(set(rep["frozen"]), {"g_mixed", "g_unknown_owner", "g_unknown_member"})

    async def test_apply_categorizes_clean_groups_and_freezes_the_rest_without_deleting_anything(self):
        self.seed()
        snap = self.snapshot()
        rep = await GM.audit_groups(self.env.db, apply=True)
        docs = {d["group_id"]: d for d in self.env.db.groups.docs}
        self.assertEqual((docs["g_minor"]["age_category"], docs["g_adult"]["age_category"]), (MINOR, ADULT))
        self.assertEqual(rep["frozen"], {"g_mixed": GM.REASON_MIXED, "g_unknown_owner": GM.REASON_UNKNOWN_OWNER,
                                         "g_unknown_member": GM.REASON_UNKNOWN_MEMBER})
        for gid in rep["frozen"]:
            self.assertTrue(docs[gid]["age_review"])
            if gid != "g_unknown_member":
                self.assertNotIn("age_category", docs[gid])
        self.assertEqual(self.snapshot(), snap)              # membri, mesaje si utilizatori NEATINSI
        await self.denied(self.env.join("g_mixed", "minor1"), 423, CP.CODE_GROUP_REVIEW)   # nimeni nou in grupuri incompatibile
        self.env.add_user("minor3", MINOR)
        self.assertEqual(await self.env.join("g_minor", "minor3"), {"ok": True})            # grupul curatat functioneaza

    async def test_apply_is_idempotent(self):
        self.seed()
        await GM.audit_groups(self.env.db, apply=True)
        first = json.dumps(self.env.db.groups.docs, default=str)
        rep2 = await GM.audit_groups(self.env.db, apply=True)
        self.assertEqual(json.dumps(self.env.db.groups.docs, default=str), first)
        self.assertEqual(rep2["groups_categorized_or_confirmed"], 0)

    async def test_member_who_drifted_to_other_category_freezes_a_categorized_group(self):
        gid = await self.env.create("minor1")
        await self.env.join(gid, "minor2")
        AGE.set(next(u for u in self.env.db.users.docs if u["user_id"] == "minor2"), ADULT)
        rep = await GM.audit_groups(self.env.db, apply=True)
        self.assertEqual(rep["frozen"], {gid: GM.REASON_MIXED})

    async def test_split_moves_other_category_members_without_deleting_history(self):
        self.seed()
        await GM.audit_groups(self.env.db, apply=True)
        msgs_before = json.dumps(self.env.db.group_chat_messages.docs, default=str)
        plan = await GM.split_group(self.env.db, "g_mixed")                         # dry-run
        self.assertEqual((plan["status"], plan["members_to_move"], plan["applied"]), ("planned", 1, False))
        self.assertFalse([g for g in self.env.db.groups.docs if g.get("split_from")])
        res = await GM.split_group(self.env.db, "g_mixed", apply=True)
        new_gid = res["new_group_id"]
        new = next(g for g in self.env.db.groups.docs if g["group_id"] == new_gid)
        orig = next(g for g in self.env.db.groups.docs if g["group_id"] == "g_mixed")
        self.assertEqual((new["age_category"], new["owner_id"], new["split_from"]), (ADULT, "adult2", "g_mixed"))
        self.assertEqual(orig["age_category"], MINOR)
        self.assertFalse(orig["age_review"])
        self.assertEqual(await self.env.active("g_mixed"), {"owner_m", "minor2"})
        self.assertEqual(await self.env.active(new_gid), {"adult2"})
        old_row = next(d for d in self.env.db.group_members.docs if d["group_id"] == "g_mixed" and d["user_id"] == "adult2")
        self.assertEqual((old_row["status"], old_row["split_to"]), ("left", new_gid))   # randul vechi pastrat, nu sters
        self.assertEqual(json.dumps(self.env.db.group_chat_messages.docs, default=str), msgs_before)   # mesaje neatinse
        await self.denied(self.env.join("g_mixed", "adult1"), 403, CP.CODE_AGE_MISMATCH)

    async def test_split_without_eligible_owner_does_nothing(self):
        self.seed()
        self.env.raw_group("owned_by_adult2", "adult2", ADULT)       # adult2 detine deja un grup => nu poate detine altul
        before = json.dumps(self.env.db.groups.docs, default=str)
        res = await GM.split_group(self.env.db, "g_mixed", apply=True)
        self.assertEqual(res["status"], "no_eligible_owner")
        self.assertEqual(json.dumps(self.env.db.groups.docs, default=str), before)


# ======================================================================================================== 7-8: chat joc (dispatcher)
class GameChatPolicy(unittest.IsolatedAsyncioTestCase):
    async def test_07_adult_message_to_minor_in_game_chat_is_blocked(self):
        w, d, room, (a, m) = chat_setup(("a", ADULT), ("m", MINOR))
        await d.handle_chat(room, a, {"text": "salut"})
        self.assertEqual(m.ws.of("chat"), [])
        self.assertEqual(w.published[0][1]["adult"], True)          # Redis primeste mesajul; filtrarea se repeta la destinatar

    async def test_08_minor_message_to_adult_in_game_chat_is_blocked(self):
        w, d, room, (a, m) = chat_setup(("a", ADULT), ("m", MINOR))
        await d.handle_chat(room, m, {"text": "salut"})
        self.assertEqual(a.ws.of("chat"), [])

    async def test_remote_process_message_is_filtered_per_local_recipient(self):
        w, d, room, (a, m) = chat_setup(("a", ADULT), ("m", MINOR))
        await d.relay_remote(room, {"t": "chat_x", "id": "r1", "ts": 5, "uid": "far", "un": "far", "adult": True, "raw": "x", "safe": "x"})
        await d.relay_remote(room, {"t": "chat_x", "id": "r2", "ts": 6, "uid": "far", "un": "far", "adult": False, "raw": None, "safe": "y"})
        self.assertEqual([x["text"] for x in a.ws.of("chat")], ["x"])
        self.assertEqual([x["text"] for x in m.ws.of("chat")], ["y"])

    async def test_cross_category_messages_are_not_kept_in_history_for_the_other_side(self):
        w, d, room, (a, m) = chat_setup(("a", ADULT), ("m", MINOR))
        await d.handle_chat(room, a, {"text": "doar adulti"})
        w.age["late_m"] = MINOR
        from astran_sandbox.test_chat import Member
        late = Member("late_m")
        await d.on_join(room, late)
        self.assertEqual(late.ws.of("chat_history")[0]["messages"], [])

    async def test_game_chat_audience_failure_delivers_nothing(self):
        w, d, room, (a, b) = chat_setup(("a", ADULT), ("b", ADULT))
        w.fail_blocks = True
        await d.handle_chat(room, a, {"text": "salut"})
        self.assertEqual(b.ws.of("chat"), [])
        self.assertEqual(a.ws.of("chat_error")[0]["code"], "unavailable")


# ======================================================================================================== 10: WebSocket real
class FakeWS:
    def __init__(self):
        self.q, self.sent, self.closed, self.accepted = asyncio.Queue(), [], None, False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        item = await self.q.get()
        if item is None:
            raise WebSocketDisconnect(1000)
        return item

    async def close(self, code=1000):
        self.closed = code

    def push(self, obj):
        self.q.put_nowait(json.dumps(obj))

    def of(self, t):
        return [m for m in self.sent if m.get("t") == t]


class WebSocketFlow(unittest.IsolatedAsyncioTestCase):
    GAME, INST = "game_all", "inst_age_test"

    async def asyncSetUp(self):
        import realtime as RT
        self.RT = RT
        self.db = FDB.FakeDB()
        self.db.games.docs.append({"game_id": self.GAME, "age_category": "under_18", "owner_id": "owner", "deleted": False})
        self.db.game_instances.docs.append({"instance_id": self.INST, "game_id": self.GAME, "status": "open"})
        self.tokens = {}
        self.tasks, self.sockets = [], {}
        router = RT.make_realtime_router(self._resolve, self.db)
        self.ep = S.endpoint(router, "WS", "/ws/games/{game_id}/instances/{instance_id}")
        self.addAsyncCleanup(self._cleanup)

    async def _resolve(self, token):
        uid = self.tokens.get(token)
        doc = next((u for u in self.db.users.docs if u["user_id"] == uid), None)
        return dict(doc) if doc else None

    def add_user(self, uid, cat=UNSET, member=True):
        doc = {"user_id": uid, "username": uid}
        if cat is not UNSET:
            AGE.set(doc, cat)
        self.db.users.docs.append(doc)
        self.tokens["tok_" + uid] = uid
        if member:
            self.db.instance_players.docs.append({"instance_id": self.INST, "user_id": uid})

    async def connect(self, uid):
        ws = FakeWS()
        self.sockets[uid] = ws
        self.tasks.append(asyncio.create_task(self.ep(ws, self.GAME, self.INST, token="tok_" + uid)))
        for _ in range(200):
            if ws.of("chat_history") or ws.closed is not None:
                break
            await asyncio.sleep(0.005)
        return ws

    async def settle(self):
        await asyncio.sleep(0.05)

    async def _cleanup(self):
        for ws in self.sockets.values():
            ws.q.put_nowait(None)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.RT.hub.rooms.pop(self.INST, None)

    async def test_10_raw_websocket_chat_cannot_cross_age_categories(self):
        for uid, cat in (("a1", ADULT), ("a2", ADULT), ("m1", MINOR), ("m2", MINOR)):
            self.add_user(uid, cat)
        wa1, wa2, wm1, wm2 = [await self.connect(u) for u in ("a1", "a2", "m1", "m2")]
        wa1.push({"t": "chat", "text": "mesaj adulti"})
        wm1.push({"t": "chat", "text": "mesaj minori"})
        await self.settle()
        self.assertEqual([m["text"] for m in wa2.of("chat")], ["mesaj adulti"])
        self.assertEqual([m["text"] for m in wm2.of("chat")], ["mesaj minori"])
        self.assertEqual([m["text"] for m in wa1.of("chat")], ["mesaj adulti"])      # doar ecoul propriu
        self.assertEqual([m["text"] for m in wm1.of("chat")], ["mesaj minori"])
        for ws, forbidden in ((wm1, "adulti"), (wm2, "adulti"), (wa1, "minori"), (wa2, "minori")):
            self.assertNotIn(forbidden, json.dumps(ws.sent))

    async def test_10_forged_fields_alternative_kinds_and_targets_do_not_bypass(self):
        self.add_user("a1", ADULT); self.add_user("m1", MINOR)
        wa, wm = await self.connect("a1"), await self.connect("m1")
        wm.push({"t": "chat", "text": "forjat", "age_category": ADULT, "is_adult": True, "adult": True, "user_id": "a1", "to": "a1", "channel": "adult"})
        for kind in ("team_chat", "proximity_chat", "global_chat", "quick", "emote", "whisper", "dm", "say"):
            wm.push({"t": kind, "text": "ocolire", "to": "a1", "id": "quick_1"})
            wa.push({"t": kind, "text": "ocolire", "to": "m1", "id": "quick_1"})
        wa.push({"t": "chat", "text": "de la adult", "to": "m1", "channel": "team", "age_category": MINOR})
        await self.settle()
        self.assertEqual([m for m in wa.of("chat") if m["user_id"] != "a1"], [])
        self.assertEqual([m for m in wm.of("chat") if m["user_id"] != "m1"], [])
        self.assertNotIn("ocolire", json.dumps(wa.sent + wm.sent))     # tipurile alternative nu produc nicio livrare

    async def test_10_free_text_in_state_anim_does_not_cross_categories(self):
        """`anim` (<=32 caractere, text liber) ar fi un canal de comunicare ocolit."""
        for uid, cat in (("a1", ADULT), ("a2", ADULT), ("m1", MINOR), ("m2", MINOR)):
            self.add_user(uid, cat)
        wa1, wa2, wm1, wm2 = [await self.connect(u) for u in ("a1", "a2", "m1", "m2")]
        wa1.push({"t": "state", "x": 1, "y": 0, "z": 2, "ry": 0, "anim": "scrie-mi-pe-discord"})
        wm1.push({"t": "state", "x": 5, "y": 0, "z": 5, "ry": 0, "anim": "salut-minori"})
        await self.settle()
        st = lambda ws, uid: [m for m in ws.of("state") if m["user_id"] == uid]
        self.assertEqual(st(wa2, "a1")[0]["anim"], "scrie-mi-pe-discord")
        self.assertEqual(st(wm2, "m1")[0]["anim"], "salut-minori")
        self.assertNotIn("anim", st(wm1, "a1")[0])          # pozitia ajunge (aceeasi lume), textul nu
        self.assertEqual(st(wm1, "a1")[0]["x"], 1)
        self.assertNotIn("anim", st(wa1, "m1")[0])
        late = await self.connect_late()
        roster = late.of("roster")[0]["players"]
        by = {p["user_id"]: p for p in roster}
        self.assertNotIn("anim", by["a1"])
        self.assertEqual(by["m1"]["anim"], "salut-minori")
        self.assertNotIn("scrie-mi", json.dumps(wm1.sent + wm2.sent + late.sent))

    async def connect_late(self):
        self.add_user("m3", MINOR)
        return await self.connect("m3")

    async def test_unknown_age_connection_has_no_chat_and_receives_none(self):
        self.add_user("a1", ADULT); self.add_user("m1", MINOR); self.add_user("u1", UNSET); self.add_user("u2", "ADULT_18")
        wa, wm = await self.connect("a1"), await self.connect("m1")
        wa.push({"t": "chat", "text": "inainte"})
        await self.settle()
        wu, wu2 = await self.connect("u1"), await self.connect("u2")
        self.assertEqual(wu.of("chat_info")[0]["mode"], "restricted")
        self.assertEqual(wu.of("chat_history")[0]["messages"], [])
        wu.push({"t": "chat", "text": "salut"})
        wm.push({"t": "chat", "text": "salut minor"})
        wa.push({"t": "chat", "text": "salut adult", "cid": "c9"})
        await self.settle()
        self.assertEqual(wu.of("chat_error")[0]["code"], "age_restricted")
        self.assertEqual(wu.of("chat"), [])
        self.assertEqual(wu2.of("chat"), [])
        self.assertEqual([m for m in wa.of("chat") + wm.of("chat") if m["user_id"] in ("u1", "u2")], [])
        wu.push({"t": "state", "x": 1, "y": 0, "z": 0, "ry": 0, "anim": "ceva"})
        await self.settle()
        self.assertNotIn("anim", [m for m in wa.of("state") if m["user_id"] == "u1"][0])

    async def test_connecting_to_an_instance_one_is_not_in_is_rejected(self):
        self.add_user("m1", MINOR, member=False)
        ws = await self.connect("m1")
        self.assertEqual(ws.closed, 4403)
        self.assertFalse(ws.accepted)

    async def test_remote_process_state_relay_applies_the_same_anim_rule(self):
        RT = self.RT
        a, m, u = (RT._RoomMember(ws=FakeWS(), user_id=i, username=i, age_category=c) for i, c in (("a", ADULT), ("m", MINOR), ("u", None)))
        room = RT.hub.room("inst_remote_test")
        room.members.update({"a": a, "m": m, "u": u})
        try:
            await RT.hub.relay_remote("inst_remote_test", {"t": "state", "user_id": "far", "x": 1, "anim": "secret", "cat": ADULT})
            self.assertEqual(a.ws.sent[0]["anim"], "secret")
            self.assertNotIn("anim", m.ws.sent[0]); self.assertNotIn("anim", u.ws.sent[0])
            self.assertNotIn("cat", a.ws.sent[0])                     # categoria nu ajunge la clienti
            for member in (a, m, u):
                member.ws.sent.clear()
            await RT.hub.relay_remote("inst_remote_test", {"t": "state", "user_id": "far", "x": 1, "anim": "secret"})   # fara `cat`
            self.assertTrue(all("anim" not in x.ws.sent[0] for x in (a, m, u)))
        finally:
            RT.hub.rooms.pop("inst_remote_test", None)


if __name__ == "__main__":
    unittest.main()
