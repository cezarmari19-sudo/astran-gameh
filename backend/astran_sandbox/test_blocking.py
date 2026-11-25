"""Teste pentru blocarea / deblocarea utilizatorilor (friend_service.block_user / unblock_user / list_blocked).

Rulare din folderul backend:
    python -m unittest astran_sandbox.test_blocking -v

Ruleaza REAL: friend_service (logica /friends/block, /friends/unblock, /friends/blocked), contact_policy, dm_core +
dm_routes (verificarea ca deblocarea nu redeschide DM-uri) - peste MongoDB fals in memorie (test_support_fakedb).
NU ruleaza: server.py insusi (FastAPI/Mongo nu sunt instalate aici). Handler-ele din server.py sunt verificate STATIC
(autentificare prin Depends(get_current_user), delegare catre friend_service, rate limit, nicio ruta duplicata);
nu se testeaza HTTP/Pydantic reale. Acelasi compromis ca in test_contact_policy.py.

Numerotarea (B1..B9) urmareste lista de teste din cerinta.
"""
import asyncio
import json
import os
import re
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from astran_sandbox import test_support_age as AGE  # noqa: E402
from astran_sandbox import contact_policy as cp  # noqa: E402
from astran_sandbox import friend_service as fs  # noqa: E402
from astran_sandbox import test_dm_routes as tdr  # noqa: E402  (stub-uri FastAPI / FakeWS)
from astran_sandbox.test_support_fakedb import FakeDB  # noqa: E402

ADULT, MINOR = "adult_18", "under_18"
HTTPException = tdr.HTTPException
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _now():
    return T0


def _user(uid, cat, **extra):
    return {"user_id": uid, "username": uid, "display_name": uid.title(), "avatar_url": f"https://img/{uid}.png",
            **AGE.fields(uid, cat), "password_hash": "SECRET", "email": f"{uid}@x.example",
            "astrans_balance": 123, **extra}


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        clean = {k: v for k, v in os.environ.items() if k != cp.ENV_VAR}
        self._env = mock.patch.dict(os.environ, clean, clear=True)
        self._env.start()
        self.db = FakeDB()
        await self.db.blocks.create_index([("blocker_id", 1), ("blocked_id", 1)], unique=True)
        await self.db.friendships.create_index([("user_a", 1), ("user_b", 1)], unique=True)
        await self.db.friend_requests.create_index([("from_id", 1), ("to_id", 1)], unique=True)
        for uid, cat in (("alice", ADULT), ("bob", ADULT), ("carol", ADULT), ("mia", MINOR), ("max", MINOR)):
            self.db.users.docs.append(_user(uid, cat))

    async def asyncTearDown(self):
        self._env.stop()

    def blocks(self):
        return sorted((d["blocker_id"], d["blocked_id"]) for d in self.db.blocks.docs)

    def friends(self):
        return sorted((d["user_a"], d["user_b"]) for d in self.db.friendships.docs)

    def reqs(self):
        return sorted((d["from_id"], d["to_id"]) for d in self.db.friend_requests.docs)

    async def raises(self, coro, status, detail=None, code=None):
        with self.assertRaises(fs.FriendError) as c:
            await coro
        self.assertEqual(c.exception.status, status)
        if detail is not None:
            self.assertEqual(c.exception.detail, detail)
        if code is not None:
            self.assertEqual(c.exception.code, code)
        return c.exception


# =============================================================================== B1 blocare
class Blocking(_Base):
    async def test_B1_block_creates_row_and_removes_friendship_and_requests(self):
        self.db.friendships.docs.append({"user_a": "alice", "user_b": "bob"})
        self.db.friend_requests.docs.append({"from_id": "bob", "to_id": "alice", "status": "pending"})
        self.assertEqual(await fs.block_user(self.db, "alice", "bob", _now), {"ok": True})
        self.assertEqual(self.blocks(), [("alice", "bob")])
        self.assertEqual((self.friends(), self.reqs()), ([], []))
        self.assertEqual(self.db.blocks.docs[0]["since"], T0)

    async def test_B1_block_is_idempotent(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await fs.block_user(self.db, "alice", "bob", _now)
        self.assertEqual(self.blocks(), [("alice", "bob")])

    async def test_B6_cannot_block_self(self):
        await self.raises(fs.block_user(self.db, "alice", "alice", _now), 400, "Cannot block yourself")
        self.assertEqual(self.blocks(), [])

    async def test_blocked_user_cannot_send_request_in_either_direction(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await self.raises(fs.send_request(self.db, "bob", "alice", _now), 403, "Blocked")
        await self.raises(fs.send_request(self.db, "alice", "bob", _now), 403, "Blocked")
        self.assertEqual(self.reqs(), [])


# =============================================================================== B2 listare
class ListBlocked(_Base):
    async def test_B2_lists_only_users_blocked_by_me_with_minimal_fields(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await fs.block_user(self.db, "carol", "alice", _now)      # carol l-a blocat pe alice: NU apare la alice
        out = await fs.list_blocked(self.db, "alice")
        self.assertEqual([u["user_id"] for u in out["blocked"]], ["bob"])
        self.assertEqual(set(out["blocked"][0]), {"user_id", "username", "display_name", "avatar_url"})
        self.assertEqual(out["blocked"][0]["display_name"], "Bob")
        # nimic privat nu scapa in raspuns
        dumped = json.dumps(out)
        for secret in ("SECRET", "@x.example", "astrans_balance", "age_category", "age_assurance", "dob", "password"):
            self.assertNotIn(secret, dumped)
        # simetric: carol vede doar ce a blocat ea
        self.assertEqual([u["user_id"] for u in (await fs.list_blocked(self.db, "carol"))["blocked"]], ["alice"])

    async def test_B2_empty_list(self):
        self.assertEqual(await fs.list_blocked(self.db, "alice"), {"blocked": []})

    async def test_B2_newest_block_first_and_deleted_accounts_hidden(self):
        for uid, days in (("bob", 1), ("carol", 3), ("mia", 2)):
            self.db.blocks.docs.append({"blocker_id": "alice", "blocked_id": uid, "since": T0 + timedelta(days=days)})
        self.db.blocks.docs.append({"blocker_id": "alice", "blocked_id": "ghost", "since": T0})   # cont inexistent
        next(u for u in self.db.users.docs if u["user_id"] == "max")["deletion_state"] = "pending"
        self.db.blocks.docs.append({"blocker_id": "alice", "blocked_id": "max", "since": T0 + timedelta(days=9)})
        out = await fs.list_blocked(self.db, "alice")
        self.assertEqual([u["user_id"] for u in out["blocked"]], ["carol", "mia", "bob"])


# =============================================================================== B3-B6 deblocare
class Unblocking(_Base):
    async def test_B3_unblock_removes_only_my_row(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await fs.block_user(self.db, "carol", "bob", _now)        # alta blocare a lui bob, de alt cont
        self.assertEqual(await fs.unblock_user(self.db, "alice", "bob"), {"ok": True})
        self.assertEqual(self.blocks(), [("carol", "bob")])
        self.assertEqual((await fs.list_blocked(self.db, "alice"))["blocked"], [])

    async def test_B4_unblock_not_blocked_is_404_and_changes_nothing(self):
        self.db.blocks.docs.append({"blocker_id": "carol", "blocked_id": "bob", "since": T0})
        e = await self.raises(fs.unblock_user(self.db, "alice", "bob"), 404, "User is not blocked", "not_blocked")
        self.assertEqual(e.status, 404)
        self.assertEqual(self.blocks(), [("carol", "bob")])
        await self.raises(fs.unblock_user(self.db, "alice", "ghost"), 404, code="not_blocked")   # id inexistent

    async def test_B4_second_unblock_is_404(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await fs.unblock_user(self.db, "alice", "bob")
        await self.raises(fs.unblock_user(self.db, "alice", "bob"), 404, code="not_blocked")

    async def test_B5_cannot_remove_a_block_made_by_another_account(self):
        await fs.block_user(self.db, "bob", "carol", _now)         # bob a blocat-o pe carol
        # carol nu poate sterge blocarea bob->carol (apel direct cu id-ul lui bob), nici alice nu poate
        await self.raises(fs.unblock_user(self.db, "carol", "bob"), 404, code="not_blocked")
        await self.raises(fs.unblock_user(self.db, "alice", "bob"), 404, code="not_blocked")
        await self.raises(fs.unblock_user(self.db, "alice", "carol"), 404, code="not_blocked")
        self.assertEqual(self.blocks(), [("bob", "carol")])

    async def test_B5_my_unblock_does_not_lift_the_other_sides_block(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await fs.block_user(self.db, "bob", "alice", _now)
        await fs.unblock_user(self.db, "alice", "bob")
        self.assertEqual(self.blocks(), [("bob", "alice")])        # blocarea lui bob ramane
        await self.raises(fs.send_request(self.db, "alice", "bob", _now), 403, "Blocked")
        await self.raises(fs.send_request(self.db, "bob", "alice", _now), 403, "Blocked")
        # si nu se dezvaluie lui alice: lista ei e goala, iar unblock-ul repetat e 404 ca la orice nebloc
        self.assertEqual((await fs.list_blocked(self.db, "alice"))["blocked"], [])

    async def test_B6_cannot_unblock_self(self):
        await self.raises(fs.unblock_user(self.db, "alice", "alice"), 400, "Cannot unblock yourself")

    async def test_B6_target_id_validation(self):
        for bad in ("", None, 5, ["bob"], {"$ne": ""}, "a b", "bob\n", "x" * 65, "bob;drop", "../x", "bób"):
            with self.assertRaises(fs.FriendError, msg=repr(bad)) as c:
                fs.clean_user_id(bad)
            self.assertEqual((c.exception.status, c.exception.detail), (400, "Invalid user id"))
        for ok in ("user_0123456789abcdef", "bob", "a-b_c", "x" * 64):
            self.assertEqual(fs.clean_user_id(ok), ok)

    async def test_unblock_makes_request_possible_again_but_only_as_a_pending_request(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        await fs.unblock_user(self.db, "alice", "bob")
        self.assertEqual(await fs.send_request(self.db, "bob", "alice", _now), {"ok": True})
        self.assertEqual((self.friends(), self.reqs()), ([], [("bob", "alice")]))     # cerere, nu prietenie


# =============================================================================== B8 fara prietenie automata
class UnblockSideEffects(_Base):
    async def test_B8_unblock_does_not_restore_friendship_or_send_request(self):
        self.db.friendships.docs.append({"user_a": "alice", "user_b": "bob"})
        await fs.block_user(self.db, "alice", "bob", _now)
        self.assertEqual(self.friends(), [])
        before = (self.friends(), self.reqs(), json.dumps(self.db.dm_conversations.docs, default=str))
        await fs.unblock_user(self.db, "alice", "bob")
        self.assertEqual((self.friends(), self.reqs(), json.dumps(self.db.dm_conversations.docs, default=str)), before)
        self.assertEqual((self.friends(), self.reqs()), ([], []))
        self.assertEqual(await fs.list_friends(self.db, {"user_id": "alice"}, lambda u: {"user_id": u["user_id"]}),
                         {"friends": []})

    async def test_B8_unblock_never_writes_outside_the_blocks_collection(self):
        src = (BACKEND / "astran_sandbox" / "friend_service.py").read_text(encoding="utf-8")
        body = src.split("async def unblock_user(", 1)[1].split("\ndef blocked_public", 1)[0]
        writes = re.findall(r"db\.(\w+)\.(insert_one|insert_many|update_one|update_many|replace_one|delete_one|delete_many|"
                            r"find_one_and_update|bulk_write)", body)
        self.assertEqual(writes, [("blocks", "delete_one")])
        self.assertIn('"blocker_id": me_id', body)      # filtrul nu poate lipsi: doar blocarile mele

    async def test_B8_stale_pending_request_is_not_created_by_unblock(self):
        await fs.block_user(self.db, "alice", "bob", _now)
        self.db.friend_requests.docs.append({"from_id": "carol", "to_id": "alice", "status": "pending"})
        await fs.unblock_user(self.db, "alice", "bob")
        self.assertEqual(self.reqs(), [("carol", "alice")])        # nu s-a atins nimic altceva


# =============================================================================== B9 reguli de contact / varsta
class ContactRulesSurviveUnblock(_Base):
    def denied(self, coro):
        return self.raises(coro, 403, None, cp.DENIED_CODE)

    async def test_B9_adult_minor_request_still_denied_after_block_and_unblock(self):
        for a, b in (("alice", "mia"), ("mia", "alice")):
            await fs.block_user(self.db, a, b, _now)
            await fs.unblock_user(self.db, a, b)
        await self.denied(fs.send_request(self.db, "alice", "mia", _now))
        await self.denied(fs.send_request(self.db, "mia", "alice", _now))
        self.assertEqual((self.friends(), self.reqs()), ([], []))

    async def test_B9_legacy_cross_age_pending_request_cannot_be_accepted_after_unblock(self):
        self.db.friend_requests.docs.append({"from_id": "alice", "to_id": "mia", "status": "pending"})
        await fs.block_user(self.db, "mia", "alice", _now)          # block sterge cererea; o recream "veche"
        self.db.friend_requests.docs.append({"from_id": "alice", "to_id": "mia", "status": "pending"})
        await fs.unblock_user(self.db, "mia", "alice")
        await self.denied(fs.accept_request(self.db, "mia", "alice", _now))
        self.assertEqual(self.friends(), [])

    async def test_B9_legacy_cross_age_friendship_stays_hidden_after_unblock(self):
        self.db.friendships.docs.append({"user_a": "alice", "user_b": "mia"})
        public = lambda u: {"user_id": u["user_id"]}  # noqa: E731
        await fs.block_user(self.db, "alice", "mia", _now)
        await fs.unblock_user(self.db, "alice", "mia")
        self.assertEqual(await fs.list_friends(self.db, {"user_id": "alice"}, public), {"friends": []})

    async def test_B9_same_category_rules_unchanged_after_unblock(self):
        await fs.block_user(self.db, "mia", "max", _now)
        await fs.unblock_user(self.db, "mia", "max")
        self.assertEqual(await fs.send_request(self.db, "mia", "max", _now), {"ok": True})
        self.assertEqual(await fs.accept_request(self.db, "max", "mia", _now), {"ok": True})
        self.assertEqual(self.friends(), [("max", "mia")])

    async def test_B9_dm_not_reopened_by_unblock(self):
        """dm_core real (REST handlers) peste FakeDB: dupa block+unblock nu exista prietenie => /dm/open refuzat;
        iar Adult<->Minor ramane refuzat chiar cu prietenie veche."""
        tdr.cf._SHARED = None
        tdr.realtime.hub.redis = None
        users = {u["user_id"]: u for u in self.db.users.docs}

        async def resolver(token):
            return users.get(token.replace("tok_", ""))

        api, _ws = tdr.dm_routes.make_dm_routers(lambda: None, resolver, self.db, tdr.friend_public)
        routes = {(r.method, r.path): r.endpoint for r in api.routes}

        async def open_(uid, friend):
            return await routes[("POST", "/dm/open")](current=users[uid], body=tdr.dm_routes.OpenBody(friend_id=friend))

        async def refused(coro):
            with self.assertRaises(HTTPException) as c:
                await coro
            self.assertEqual(c.exception.status_code, 403)

        try:
            # alice<->bob erau prieteni; block (sterge prietenia) + unblock
            self.db.friendships.docs.append({"user_a": "alice", "user_b": "bob"})
            await open_("alice", "bob")                          # inainte de block: merge
            await fs.block_user(self.db, "alice", "bob", _now)
            await refused(open_("alice", "bob"))                 # blocat
            await refused(open_("bob", "alice"))
            await fs.unblock_user(self.db, "alice", "bob")
            await refused(open_("alice", "bob"))                 # deblocat, dar NU mai sunt prieteni
            await refused(open_("bob", "alice"))
            # Adult<->Minor: refuzat si dupa block/unblock, chiar cu prietenie veche
            self.db.friendships.docs.append({"user_a": "alice", "user_b": "mia"})
            await fs.block_user(self.db, "mia", "alice", _now)
            await fs.unblock_user(self.db, "mia", "alice")
            self.db.friendships.docs.append({"user_a": "alice", "user_b": "mia"})   # prietenie veche, recreata in DB
            await refused(open_("alice", "mia"))
            await refused(open_("mia", "alice"))
        finally:
            tdr.cf._SHARED = None
            tdr.realtime.hub.dm_relay = None


# =============================================================================== B7 + cablaj server.py (static)
class ServerWiring(unittest.TestCase):
    SERVER = (BACKEND / "server.py").read_text(encoding="utf-8")

    def handler(self, route):
        m = re.search(re.escape(route) + r'\)\nasync def \w+\((.*?)\):\n((?:(?:    .*)?\n)+)', self.SERVER)
        self.assertIsNotNone(m, route)
        return m.group(1), m.group(2)

    def test_B7_all_blocking_endpoints_require_authentication(self):
        for route in ('@api.post("/friends/block"', '@api.get("/friends/blocked"', '@api.post("/friends/unblock"'):
            sig, _ = self.handler(route)
            self.assertIn("current=Depends(get_current_user)", sig, route)

    def test_B7_endpoints_use_only_the_authenticated_user_as_actor(self):
        _, blk = self.handler('@api.post("/friends/block"')
        _, unb = self.handler('@api.post("/friends/unblock"')
        _, lst = self.handler('@api.get("/friends/blocked"')
        self.assertIn('FRIENDS.block_user(db, current["user_id"], body.target_user_id, now_utc)', blk)
        self.assertIn('FRIENDS.unblock_user(db, current["user_id"], target_id)', unb)
        self.assertIn("FRIENDS.clean_user_id(body.target_user_id)", unb)
        self.assertIn('FRIENDS.list_blocked(db, current["user_id"])', lst)
        for body in (blk, unb, lst):
            self.assertNotIn("db.", body)        # nicio scriere/citire directa in handler: totul trece prin serviciu

    def test_unblock_is_rate_limited_like_friend_request(self):
        _, unb = self.handler('@api.post("/friends/unblock"')
        self.assertIn("_unblock_limiter.hit(current[\"user_id\"])", unb)
        self.assertIn("raise _too_many(wait)", unb)
        self.assertRegex(self.SERVER, r"_unblock_limiter = SlidingWindowLimiter\(\d+, \d+\)")

    def test_no_duplicate_routes(self):
        routes = re.findall(r'@api\.(get|post|put|delete|patch)\("([^"]+)"', self.SERVER)
        dup = {r for r in routes if routes.count(r) > 1}
        self.assertEqual(dup, set())
        for r in (("post", "/friends/block"), ("post", "/friends/unblock"), ("get", "/friends/blocked")):
            self.assertEqual(routes.count(r), 1, r)

    def test_request_body_is_unchanged_and_only_carries_the_target_id(self):
        body = re.search(r"class FriendRequestBody\(BaseModel\):\n((?:    .*\n)+)", self.SERVER).group(1)
        self.assertEqual([l.strip().split(":")[0] for l in body.splitlines() if l.strip()], ["target_user_id"])

    def test_blocks_rows_are_written_only_by_friend_service(self):
        pat = re.compile(r"\.blocks\.(insert_one|insert_many|update_one|update_many|replace_one|delete_one|find_one_and_update)")
        writers = {f.name for f in BACKEND.rglob("*.py")
                   if not f.name.startswith("test_") and pat.search(f.read_text(encoding="utf-8", errors="ignore"))}
        self.assertEqual(writers, {"friend_service.py"})

    def test_block_and_unblock_do_not_import_or_call_friendship_creation(self):
        src = (BACKEND / "astran_sandbox" / "friend_service.py").read_text(encoding="utf-8")
        for fn, end in (("async def block_user(", "async def unblock_user("), ("async def unblock_user(", "def blocked_public")):
            body = src.split(fn, 1)[1].split(end, 1)[0]
            self.assertNotIn("friendships.update_one", body)
            self.assertNotIn("friend_requests.insert_one", body)


if __name__ == "__main__":
    unittest.main()
