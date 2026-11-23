"""Teste pentru protectia Adult <-> Minor (cereri de prietenie + mesagerie privata).

Rulare din folderul backend:
    python -m unittest astran_sandbox.test_contact_policy -v

Ce ruleaza REAL: contact_policy, friend_service (logica /friends/*), dm_core.DmService + dm_routes (handler-ele REST
si WebSocket) + MongoDmStore, contact_migration - peste un MongoDB fals in memorie.
Ce NU ruleaza: server.py insusi (FastAPI/Mongo nu sunt instalate in acest mediu). Endpoint-urile /friends/* din
server.py sunt verificate STATIC (apeleaza FRIENDS.send_request/accept_request, nicio alta scriere in friendships);
logica lor reala e in friend_service si e testata dinamic aici. Nu testeaza HTTP/Pydantic reale, un WebSocket real
sau Redis real.

Numerotarea (T1..T12) urmareste lista de teste obligatorii din cerinta.
"""
import asyncio
import json
import logging
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from astran_sandbox import contact_migration as cm  # noqa: E402
from astran_sandbox import contact_policy as cp  # noqa: E402
from astran_sandbox import friend_service as fs  # noqa: E402
from astran_sandbox import test_dm_routes as tdr  # noqa: E402  (harness: FakeDb / FakeWS / stub-uri FastAPI)
from astran_sandbox.test_support_fakedb import FakeDB  # noqa: E402

ADULT, MINOR = "adult_18", "under_18"
HTTPException = tdr.HTTPException
ENV = cp.ENV_VAR
AGE_WORDS = r"\b(minor|minors|adult|adults|under|child|children|kid|age|aged|18|varsta|minori?|adulti?)\b"


def _env_without_var():
    """Mediu fara variabila (T10) - indiferent ce e setat pe masina care ruleaza testele."""
    clean = {k: v for k, v in os.environ.items() if k != ENV}
    return mock.patch.dict(os.environ, clean, clear=True)


def _now():
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _user(uid, cat):
    return {"user_id": uid, "username": uid, "age_category": cat, "password_hash": "SECRET", "email": f"{uid}@x"}


def _public(u):
    return {"user_id": u["user_id"], "username": u["username"]}


def pair(a, b):
    return (a, b) if a < b else (b, a)


# =============================================================================== politica + configurare
class PolicyAndConfig(unittest.TestCase):
    def test_T10_protection_active_when_env_var_missing(self):
        with _env_without_var():
            self.assertNotIn(ENV, os.environ)
            self.assertTrue(cp.protection_enabled())
            self.assertFalse(cp.contact_allowed(ADULT, MINOR))
            self.assertFalse(cp.contact_allowed(MINOR, ADULT))
        self.assertTrue(cp.protection_enabled({}))

    def test_T10_empty_garbage_or_typo_values_do_not_disable(self):
        for v in ("", " ", "1", "true", "yes", "on", "00", "disable", "of", "2", "nu", "None", "null", "\n"):
            self.assertTrue(cp.protection_enabled({ENV: v}), repr(v))

    def test_only_explicit_off_values_disable_and_it_is_logged_loudly(self):
        for v in ("0", "false", "FALSE", " off ", "No", "disabled"):
            with self.assertLogs("astran.contact_policy", level=logging.CRITICAL):
                self.assertFalse(cp.protection_enabled({ENV: v}), repr(v))
                self.assertTrue(cp.contact_allowed(ADULT, MINOR, {ENV: v}))

    def test_policy_is_read_at_call_time_not_import_time(self):
        with mock.patch.dict(os.environ, {ENV: "0"}):
            with self.assertLogs("astran.contact_policy", level=logging.CRITICAL):
                self.assertTrue(cp.contact_allowed(ADULT, MINOR))
        with _env_without_var():
            self.assertFalse(cp.contact_allowed(ADULT, MINOR))

    def test_rule_matrix_is_symmetric_and_fail_closed(self):
        with _env_without_var():
            self.assertTrue(cp.contact_allowed(ADULT, ADULT))      # T4
            self.assertTrue(cp.contact_allowed(MINOR, MINOR))      # T3
            self.assertFalse(cp.contact_allowed(ADULT, MINOR))     # T1
            self.assertFalse(cp.contact_allowed(MINOR, ADULT))     # T2
            # categorie lipsa / necunoscuta / alta grafie => minor (nu devine "adult" din greseala)
            for weird in (None, "", "adult", "ADULT_18", "18_plus", "adult_18 ", 18, True, {"x": 1}):
                self.assertFalse(cp.is_adult_category(weird), repr(weird))
                self.assertFalse(cp.contact_allowed(ADULT, weird), repr(weird))
                self.assertTrue(cp.contact_allowed(MINOR, weird), repr(weird))   # minor <-> necunoscut = minor <-> minor
            self.assertFalse(cp.users_contact_allowed(None, {"age_category": ADULT}))
            self.assertFalse(cp.users_contact_allowed({"age_category": ADULT}, None))

    def test_env_var_is_read_in_exactly_one_place(self):
        offenders = []
        for f in BACKEND.rglob("*.py"):
            if f.name.startswith("test_") or f.name == "contact_policy.py":
                continue
            if ENV in f.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(f.name)
        # comentariile/documentatia pot numi variabila, dar nimeni altcineva nu o citeste din os.environ
        for f in BACKEND.rglob("*.py"):
            if f.name.startswith("test_") or f.name == "contact_policy.py":
                continue
            src = f.read_text(encoding="utf-8", errors="ignore")
            self.assertNotRegex(src, r"environ[^\n]*ASTRAN_BLOCK_ADULT_TO_MINOR|getenv[^\n]*ASTRAN_BLOCK_ADULT_TO_MINOR", f.name)
        self.assertNotIn('"0") == "1"', (BACKEND / "server.py").read_text(encoding="utf-8"))


# =============================================================================== cereri de prietenie
class FriendRequests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._env = _env_without_var()
        self._env.start()
        self.db = FakeDB()
        await self.db.friendships.create_index([("user_a", 1), ("user_b", 1)], unique=True)
        await self.db.friend_requests.create_index([("from_id", 1), ("to_id", 1)], unique=True)
        for uid, cat in (("alice", ADULT), ("bob", ADULT), ("mia", MINOR), ("max", MINOR)):
            self.db.users.docs.append(_user(uid, cat))

    async def asyncTearDown(self):
        self._env.stop()

    def friends(self):
        return [(d["user_a"], d["user_b"]) for d in self.db.friendships.docs]

    def reqs(self):
        return [(d["from_id"], d["to_id"]) for d in self.db.friend_requests.docs]

    async def denied(self, coro):
        with self.assertRaises(fs.FriendError) as cm_:
            await coro
        self.assertEqual(cm_.exception.status, 403)
        self.assertEqual(cm_.exception.detail["code"], "contact_restricted")
        return cm_.exception

    async def test_T1_adult_to_minor_request_blocked_and_nothing_saved(self):
        e = await self.denied(fs.send_request(self.db, "alice", "mia", _now))
        self.assertEqual((self.reqs(), self.friends()), ([], []))
        # mesajul e generic: nu dezvaluie cine e minor / nicio varsta
        self.assertNotRegex(json.dumps(e.detail).lower(), AGE_WORDS)

    async def test_T2_minor_to_adult_request_blocked(self):
        await self.denied(fs.send_request(self.db, "mia", "alice", _now))
        self.assertEqual((self.reqs(), self.friends()), ([], []))

    async def test_T3_minor_to_minor_allowed_then_accepted(self):
        self.assertEqual(await fs.send_request(self.db, "mia", "max", _now), {"ok": True})
        self.assertEqual(self.reqs(), [("mia", "max")])
        self.assertEqual(await fs.accept_request(self.db, "max", "mia", _now), {"ok": True})
        self.assertEqual((self.friends(), self.reqs()), ([("max", "mia")], []))

    async def test_T4_adult_to_adult_allowed_then_accepted(self):
        self.assertEqual(await fs.send_request(self.db, "alice", "bob", _now), {"ok": True})
        await fs.accept_request(self.db, "bob", "alice", _now)
        self.assertEqual(self.friends(), [("alice", "bob")])

    async def test_T9_category_comes_from_server_not_from_caller(self):
        # singurul input al cererii e target_user_id (FriendRequestBody) - verificat static mai jos; serviciul primeste
        # DOAR id-uri si recitește categoria ambelor conturi din DB: nu exista cale prin care sa se "declare" adult/minor.
        src = (BACKEND / "server.py").read_text(encoding="utf-8")
        body = re.search(r"class FriendRequestBody\(BaseModel\):\n((?:    .*\n)+)", src).group(1)
        self.assertEqual([l.strip().split(":")[0] for l in body.splitlines() if l.strip()], ["target_user_id"])
        import inspect
        self.assertEqual(list(inspect.signature(fs.send_request).parameters)[:3], ["db", "me_id", "target_id"])
        # DB spune minor => blocat, oricum ar pretinde clientul altceva
        await self.denied(fs.send_request(self.db, "mia", "alice", _now))
        # categoria se recitește la fiecare apel: dupa schimbarea in DB, decizia se schimba
        for u in self.db.users.docs:
            if u["user_id"] == "mia":
                u["age_category"] = ADULT
        self.assertEqual(await fs.send_request(self.db, "mia", "alice", _now), {"ok": True})

    async def test_T12_reverse_pending_cannot_auto_accept_across_categories(self):
        # cerere veche (inainte de politica) alice -> mia; mia trimite inapoi: NU devine prietenie
        self.db.friend_requests.docs.append({"from_id": "alice", "to_id": "mia", "status": "pending", "created_at": _now()})
        await self.denied(fs.send_request(self.db, "mia", "alice", _now))
        self.assertEqual(self.friends(), [])

    async def test_T12_accepting_a_legacy_pending_cross_age_request_is_refused_and_purged(self):
        self.db.friend_requests.docs.append({"from_id": "alice", "to_id": "mia", "status": "pending", "created_at": _now()})
        await self.denied(fs.accept_request(self.db, "mia", "alice", _now))
        self.assertEqual((self.friends(), self.reqs()), ([], []))   # cererea ilegala dispare, nu mai poate fi reincercata

    async def test_pending_request_dies_if_categories_diverge_before_accept(self):
        await fs.send_request(self.db, "alice", "bob", _now)
        for u in self.db.users.docs:
            if u["user_id"] == "bob":
                u["age_category"] = MINOR     # ex. age assurance revocat dupa trimitere
        await self.denied(fs.accept_request(self.db, "bob", "alice", _now))
        self.assertEqual(self.friends(), [])

    async def test_T12_legacy_cross_age_friendship_and_requests_not_listed(self):
        self.db.friendships.docs.append({"user_a": "alice", "user_b": "mia", "since": _now()})
        self.db.friendships.docs.append({"user_a": "alice", "user_b": "bob", "since": _now()})
        self.db.friend_requests.docs.append({"from_id": "mia", "to_id": "alice", "status": "pending"})
        self.db.friend_requests.docs.append({"from_id": "bob", "to_id": "alice", "status": "pending"})
        a = await fs.list_friends(self.db, self.db.users.docs[0], _public)
        self.assertEqual([f["user_id"] for f in a["friends"]], ["bob"])
        m = await fs.list_friends(self.db, self.db.users.docs[2], _public)   # mia: nu il mai vede pe alice
        self.assertEqual(m["friends"], [])
        r = await fs.list_requests(self.db, self.db.users.docs[0], _public)
        self.assertEqual([u["user_id"] for u in r["incoming"]], ["bob"])
        self.assertNotIn("SECRET", json.dumps(a) + json.dumps(r))

    async def test_T11_same_category_features_unchanged(self):
        # duplicat, cerere inversa simultana (auto-accept), prieteni existenti, blocare, limita - ca inainte
        await fs.send_request(self.db, "mia", "max", _now)
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "mia", "max", _now)
        self.assertEqual(c.exception.status, 409)
        self.assertEqual(await fs.send_request(self.db, "max", "mia", _now), {"ok": True, "auto_accepted": True})
        self.assertEqual((self.friends(), self.reqs()), ([("max", "mia")], []))
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "mia", "max", _now)
        self.assertEqual((c.exception.status, c.exception.detail), (409, "Already friends"))
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "alice", "alice", _now)
        self.assertEqual(c.exception.status, 400)
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "alice", "nobody", _now)
        self.assertEqual(c.exception.status, 404)
        self.db.blocks.docs.append({"blocker_id": "bob", "blocked_id": "alice"})
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "alice", "bob", _now)
        self.assertEqual((c.exception.status, c.exception.detail), (403, "Blocked"))
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "alice", "bob", _now, max_pending=0) if False else \
                await fs.send_request(self.db, "bob", "alice", _now)
        self.assertEqual(c.exception.status, 403)

    async def test_pending_limit_still_enforced(self):
        self.db.users.docs.append(_user("zed", ADULT))
        await fs.send_request(self.db, "alice", "bob", _now, max_pending=1)
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "alice", "zed", _now, max_pending=1)
        self.assertEqual(c.exception.status, 429)

    async def test_deleted_target_is_404(self):
        self.db.users.docs.append({**_user("gone", ADULT), "deletion_state": "pending"})
        with self.assertRaises(fs.FriendError) as c:
            await fs.send_request(self.db, "alice", "gone", _now)
        self.assertEqual(c.exception.status, 404)


# =============================================================================== DM (REST + WebSocket)
class DmContact(unittest.IsolatedAsyncioTestCase):
    """Handler-ele REALE din dm_routes (+ MongoDmStore) peste Mongo fals. Prieteniile vechi Adult<->Minor sunt
    puse direct in DB, exact cum ar exista dupa o versiune anterioara fara politica."""

    async def asyncSetUp(self):
        self._env = _env_without_var()
        self._env.start()
        tdr.cf._SHARED = None
        tdr.realtime.hub.redis = None
        self.db = tdr.FakeDb()
        self.clock = 1_700_000_000.0
        self.users = {}
        for uid, age in (("alice", ADULT), ("bob", ADULT), ("carol", ADULT), ("mia", MINOR), ("max", MINOR)):
            doc = _user(uid, age)
            self.users[uid] = doc
            self.db.users.docs.append(doc)
        for a, b in (("alice", "bob"), ("max", "mia"),
                     ("alice", "mia")):   # <- prietenie VECHE Adult<->Minor (creata inainte de politica)
            self.db.friendships.docs.append({"user_a": a, "user_b": b})

        async def resolver(token):
            return self.users.get(token.replace("tok_", ""))

        self.api, self.ws = tdr.dm_routes.make_dm_routers(lambda: None, resolver, self.db, tdr.friend_public)
        self.routes = {(r.method, r.path): r.endpoint for r in self.api.routes}
        self.ws_endpoint = next(r.endpoint for r in self.ws.routes if r.path == "/ws/dm")
        self.service = self.api.service
        self.service._clock = lambda: self.clock
        self.service._mono = lambda: self.clock
        self.tasks = []
        # conversatie + mesaje VECHI intre alice (adult) si mia (minor), create inainte de politica
        st = self.service.store
        await st.get_or_create("alice", "mia", "dm_legacy", datetime.now(timezone.utc))
        self.legacy_cid = "dm_legacy"
        for i, (sid, text) in enumerate((("alice", "salut mia"), ("mia", "salut"))):
            msg = {"message_id": f"dmm_old{i}", "conversation_id": "dm_legacy", "sender_id": sid, "ts": 1000 + i,
                   "sender_adult": sid == "alice", "raw": text if sid == "alice" else None, "safe": text}
            await st.insert_message(msg)
            await st.bump_conversation("dm_legacy", msg["message_id"], msg["ts"], "b" if sid == "alice" else "a")

    async def asyncTearDown(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        tdr.realtime.hub.dm_relay = None
        tdr.cf._SHARED = None
        self._env.stop()

    # ---- scurtaturi
    def me(self, uid): return self.users[uid]

    async def call(self, method, path, uid, **kw):
        self.clock += 2.0
        return await self.routes[(method, path)](current=self.me(uid), **kw)

    async def open(self, uid, friend, **extra):
        body = tdr.dm_routes.OpenBody(friend_id=friend)
        for k, v in extra.items():       # campuri de varsta "inventate" de client: nu au niciun efect
            setattr(body, k, v)
        return await self.call("POST", "/dm/open", uid, body=body)

    async def send(self, uid, cid, text, **extra):
        body = tdr.dm_routes.SendBody(text=text, client_id="k1")
        for k, v in extra.items():
            setattr(body, k, v)
        return (await self.call("POST", "/dm/conversations/{conversation_id}/messages", uid, conversation_id=cid, body=body))["message"]

    async def history(self, uid, cid):
        return await self.call("GET", "/dm/conversations/{conversation_id}/messages", uid, conversation_id=cid, before=None, limit=30)

    async def refused(self, coro, status=403, code="not_allowed"):
        with self.assertRaises(HTTPException) as c:
            await coro
        self.assertEqual((c.exception.status_code, c.exception.detail["code"]), (status, code))

    async def connect(self, uid):
        ws = tdr.FakeWS()
        self.tasks.append(asyncio.create_task(self.ws_endpoint(ws, f"tok_{uid}")))
        await tdr.settle()
        return ws

    def n_msgs(self):
        return len(self.db.dm_messages.docs)

    def n_convs(self):
        return len(self.db.dm_conversations.docs)

    # ---- T5
    async def test_T5_adult_cannot_create_dm_with_minor_even_with_legacy_friendship_or_without_any(self):
        await self.refused(self.open("alice", "mia"))      # prietenie veche
        await self.refused(self.open("alice", "max"))      # fara prietenie (apel direct pe endpoint)
        await self.refused(self.open("carol", "mia"))
        self.assertEqual(self.n_convs(), 1)                # doar conversatia veche; nicio conversatie noua
        # invers: minorul nu poate initia DM spre adult
        await self.refused(self.open("mia", "alice"))
        await self.refused(self.open("max", "bob"))
        self.assertEqual(self.n_convs(), 1)

    # ---- T6
    async def test_T6_adult_cannot_send_dm_via_api_even_with_a_known_conversation_id(self):
        await self.refused(self.send("alice", self.legacy_cid, "hei, ai whatsapp?"))
        await self.refused(self.send("mia", self.legacy_cid, "raspuns"))     # nici minorul, inapoi
        self.assertEqual(self.n_msgs(), 2)                  # nimic nou in DB

    # ---- T7
    async def test_T7_websocket_cannot_send_and_never_delivers_across_the_boundary(self):
        w_mia, w_alice, w_bob = await self.connect("mia"), await self.connect("alice"), await self.connect("bob")
        # 1) clientul incearca sa "trimita" un DM prin WebSocket: ignorat (WS e doar server -> client)
        for frame in ({"t": "dm_send", "conversation_id": self.legacy_cid, "text": "hei", "to": "mia"},
                      {"t": "dm", "conversation_id": self.legacy_cid, "text": "hei", "sender_id": "alice"}):
            w_alice.inbox.put_nowait(json.dumps(frame))
        await tdr.settle()
        self.assertEqual(self.n_msgs(), 2)
        self.assertEqual(w_mia.of("dm"), [])
        # 2) mesaj venit de la alt proces (Redis) de la un adult catre un minor: NU se livreaza
        await tdr.realtime.hub.relay_remote("dm_mia", {"t": "dm_x", "cid": self.legacy_cid, "id": "x1", "ts": 5,
                                                       "sid": "alice", "adult": True, "raw": "hei", "safe": "hei"})
        await tdr.settle()
        self.assertEqual(w_mia.of("dm"), [])
        # ...si invers (minor -> adult)
        await tdr.realtime.hub.relay_remote("dm_alice", {"t": "dm_x", "cid": self.legacy_cid, "id": "x2", "ts": 6,
                                                         "sid": "mia", "adult": False, "raw": None, "safe": "hei"})
        await tdr.settle()
        self.assertEqual(w_alice.of("dm"), [])
        # 3) aceeasi cale ramane functionala in aceeasi categorie (T11)
        await tdr.realtime.hub.relay_remote("dm_bob", {"t": "dm_x", "cid": "c", "id": "x3", "ts": 7,
                                                       "sid": "alice", "adult": True, "raw": "salut", "safe": "salut"})
        await tdr.settle()
        self.assertEqual(w_bob.of("dm")[0]["message"]["text"], "salut")

    # ---- T8
    async def test_T8_legacy_friendship_cannot_open_read_or_list_the_conversation(self):
        await self.refused(self.open("alice", "mia"))
        await self.refused(self.history("alice", self.legacy_cid))
        await self.refused(self.history("mia", self.legacy_cid))
        for uid in ("alice", "mia"):
            self.assertEqual((await self.call("GET", "/dm/conversations", uid))["conversations"], [])
            self.assertEqual((await self.call("GET", "/dm/unread", uid))["unread"], 0)
        # istoricul NU a fost sters (retentie): ramane in DB, accesibil moderarii / raportarii
        self.assertEqual(self.n_msgs(), 2)

    async def test_legacy_conversation_still_allows_minor_to_report_adult_message(self):
        await self.call("POST", "/dm/messages/{message_id}/report", "mia", message_id="dmm_old0",
                        body=tdr.dm_routes.ReportBody(reason="harassment"))
        self.assertEqual(len(self.db.reports.docs), 1)
        r = self.db.reports.docs[0]
        self.assertEqual((r["reporter_id"], r["target_id"], r["context"]["sender_id"]), ("mia", "dmm_old0", "alice"))

    # ---- T9
    async def test_T9_age_fields_in_the_request_are_ignored(self):
        for fake in ({"age_category": ADULT}, {"age_category": MINOR}, {"is_adult": True}, {"adult": True}):
            await self.refused(self.open("alice", "mia", **fake))
            await self.refused(self.send("alice", self.legacy_cid, "x", **fake))
        # si un "current" fals nu schimba nimic: categoria ambilor se citeste din DB (user_ages), nu din dict-ul cererii
        forged = {**self.users["mia"], "age_category": ADULT}
        with self.assertRaises(HTTPException):
            await self.routes[("POST", "/dm/conversations/{conversation_id}/messages")](
                conversation_id=self.legacy_cid, body=tdr.dm_routes.SendBody(text="x"), current=forged)
        self.assertEqual(self.n_msgs(), 2)

    # ---- T11
    async def test_T11_same_category_dm_still_works_end_to_end(self):
        for a, b in (("alice", "bob"), ("mia", "max")):
            cid = (await self.open(a, b))["conversation_id"]
            m = await self.send(a, cid, "salut")
            self.assertEqual((m["text"], m["mine"]), ("salut", True))
            self.assertEqual([x["text"] for x in (await self.history(b, cid))["messages"]], ["salut"])
            self.assertEqual((await self.call("GET", "/dm/unread", b))["unread"], 1)
            self.assertEqual(len((await self.call("GET", "/dm/conversations", b))["conversations"]), 1)

    # ---- T12
    async def test_T12_no_alternative_route_exists_that_was_not_reviewed(self):
        # daca cineva adauga o ruta noua de mesagerie, acest test pica si obliga la o revizuire a politicii de contact
        self.assertEqual(sorted(self.routes), sorted([
            ("POST", "/dm/open"), ("GET", "/dm/conversations"), ("GET", "/dm/unread"),
            ("GET", "/dm/conversations/{conversation_id}/messages"),
            ("POST", "/dm/conversations/{conversation_id}/messages"),
            ("POST", "/dm/conversations/{conversation_id}/read"),
            ("POST", "/dm/messages/{message_id}/report"),
        ]))
        self.assertEqual([r.path for r in self.ws.routes], ["/ws/dm"])

    async def test_T12_every_send_path_rechecks_policy_per_message(self):
        # conversatie legitima minor<->minor; apoi unul trece (in DB) in alta categorie => urmatorul mesaj e refuzat
        cid = (await self.open("mia", "max"))["conversation_id"]
        await self.send("mia", cid, "unu")
        self.users["max"]["age_category"] = ADULT
        await self.refused(self.send("mia", cid, "doi"))
        await self.refused(self.send("max", cid, "doi"))
        await self.refused(self.history("mia", cid))
        self.assertEqual(self.n_msgs(), 3)                  # 2 vechi + 1

    async def test_policy_error_is_generic_and_does_not_reveal_age(self):
        with self.assertRaises(HTTPException) as c:
            await self.open("alice", "mia")
        self.assertNotRegex(json.dumps(c.exception.detail).lower(), AGE_WORDS)

    async def test_switch_off_explicitly_is_the_only_way_to_allow_it(self):
        with mock.patch.dict(os.environ, {ENV: "0"}):
            with self.assertLogs("astran.contact_policy", level=logging.CRITICAL):
                r = await self.open("alice", "mia")
            self.assertTrue(r["conversation_id"])
        await self.refused(self.open("alice", "mia"))        # varianta fara variabila: din nou blocat


# =============================================================================== migrare optionala
class Migration(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_counts_apply_removes_only_cross_age_and_keeps_dm_history(self):
        with _env_without_var():
            db = FakeDB()
            for uid, cat in (("alice", ADULT), ("bob", ADULT), ("mia", MINOR), ("max", MINOR)):
                db.users.docs.append(_user(uid, cat))
            db.friendships.docs += [{"user_a": "alice", "user_b": "bob"}, {"user_a": "alice", "user_b": "mia"},
                                    {"user_a": "max", "user_b": "mia"}, {"user_a": "alice", "user_b": "ghost"}]
            db.friend_requests.docs += [{"from_id": "alice", "to_id": "max", "status": "pending"},
                                        {"from_id": "bob", "to_id": "alice", "status": "pending"}]
            db.dm_conversations.docs.append({"conversation_id": "dm_1", "user_a": "alice", "user_b": "mia"})
            db.dm_messages.docs.append({"message_id": "m1", "conversation_id": "dm_1"})
            dry = await cm.purge_cross_age_contacts(db)
            self.assertEqual((dry["friendships_found"], dry["pending_requests_found"], dry["friendships_removed"]), (2, 1, 0))
            self.assertEqual(len(db.friendships.docs), 4)                        # dry-run nu schimba nimic
            done = await cm.purge_cross_age_contacts(db, apply=True)
            self.assertEqual((done["friendships_removed"], done["pending_requests_removed"]), (2, 1))
            self.assertEqual(sorted((d["user_a"], d["user_b"]) for d in db.friendships.docs), [("alice", "bob"), ("max", "mia")])
            self.assertEqual(len(db.dm_conversations.docs), 1)                   # istoricul DM NU se atinge
            self.assertEqual(len(db.dm_messages.docs), 1)
            again = await cm.purge_cross_age_contacts(db, apply=True)
            self.assertEqual((again["friendships_found"], again["pending_requests_found"]), (0, 0))   # idempotent

    async def test_refuses_to_run_when_protection_is_explicitly_off(self):
        with self.assertLogs("astran.contact_policy", level=logging.CRITICAL):
            with self.assertRaises(RuntimeError):
                await cm.purge_cross_age_contacts(FakeDB(), apply=True, environ={ENV: "0"})


# =============================================================================== verificari statice pe server.py
class ServerWiring(unittest.TestCase):
    SERVER = (BACKEND / "server.py").read_text(encoding="utf-8")

    def test_friend_endpoints_delegate_to_the_policy_enforcing_service(self):
        s = self.SERVER
        self.assertIn("FRIENDS.send_request(db, current[\"user_id\"], body.target_user_id", s)
        self.assertIn("FRIENDS.accept_request(db, current[\"user_id\"], body.target_user_id", s)
        self.assertIn("FRIENDS.list_friends(db, current", s)
        self.assertIn("FRIENDS.list_requests(db, current", s)

    def test_friendships_are_created_only_inside_friend_service(self):
        writers = []
        pat = re.compile(r"friendships\.(insert_one|insert_many|update_one|update_many|replace_one|find_one_and_update|bulk_write)")
        for f in BACKEND.rglob("*.py"):
            if f.name.startswith("test_"):
                continue
            if pat.search(f.read_text(encoding="utf-8", errors="ignore")):
                writers.append(f.name)
        self.assertEqual(sorted(set(writers)), ["friend_service.py"])
        pat2 = re.compile(r"friend_requests\.(insert_one|insert_many|update_one|replace_one)")
        self.assertEqual(sorted({f.name for f in BACKEND.rglob("*.py") if not f.name.startswith("test_")
                                 and pat2.search(f.read_text(encoding="utf-8", errors="ignore"))}), ["friend_service.py"])

    def test_dm_service_never_trusts_friendship_alone(self):
        src = (BACKEND / "astran_sandbox" / "dm_core.py").read_text(encoding="utf-8")
        # open + send + history: fiecare cheama _ages_pair
        cls = src.split("class DmService:", 1)[1]
        for fn in ("async def open_conversation(", "async def history(", "async def send("):
            body = cls.split(fn, 1)[1].split("\n    async def ", 1)[0]
            self.assertIn("_ages_pair", body, fn)

    def test_search_and_profile_expose_only_a_boolean_hint_not_the_age(self):
        self.assertIn('"contact_restricted": not contact_policy.users_contact_allowed(current, u)', self.SERVER)
        self.assertNotIn('"age_category": u', self.SERVER.split("def _user_public_minimal", 1)[1].split("@api.get", 1)[0])


if __name__ == "__main__":
    unittest.main()
