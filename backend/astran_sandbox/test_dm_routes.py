"""Test de integrare pentru mesageria privata: handler-ele REALE din dm_routes.py (REST + WebSocket),
MongoDmStore REAL (dm_store.py) si regulile de filtrare REALE livrate cu aplicatia, peste un
"MongoDB" fals in memorie si un WebSocket fals. Rulare din folderul backend:
    python -m unittest astran_sandbox.test_dm_routes -v

Daca FastAPI/Pydantic nu sunt instalate (ca in mediul acestui test), se folosesc stub-uri minime
DOAR pentru import; handler-ele testate sunt cele reale, apelate direct.

NU testeaza: un MongoDB real (semantica operatorilor e cea implementata in FakeCol de mai jos -
interogarile/indexurile din dm_store.py trebuie verificate si pe un Mongo real), validarea reala
Pydantic/FastAPI (422, dependinta get_current_user), un WebSocket real (Starlette/uvicorn), Redis real."""
import asyncio
import copy
import json
import sys
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

_STUBBED = []
try:  # pragma: no cover
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail

    class WebSocketDisconnect(Exception):
        pass

    class _Router:
        def __init__(self, *a, **k):
            self.routes = []

        def _reg(self, method):
            def factory(path, **k):
                def deco(fn):
                    self.routes.append(types.SimpleNamespace(method=method, path=path, endpoint=fn))
                    return fn
                return deco
            return factory

        def get(self, path, **k): return self._reg("GET")(path)
        def post(self, path, **k): return self._reg("POST")(path)
        def websocket(self, path, **k): return self._reg("WS")(path)

    fa.APIRouter, fa.HTTPException, fa.WebSocketDisconnect = _Router, HTTPException, WebSocketDisconnect
    fa.Depends = lambda x=None: x
    fa.Query = lambda default=None, **k: default
    fa.Header = lambda default=None, **k: default
    fa.WebSocket = type("WebSocket", (), {})
    pd = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    pd.BaseModel = BaseModel
    pd.Field = lambda default=None, **k: default
    sys.modules["fastapi"], sys.modules["pydantic"] = fa, pd
    _STUBBED = ["fastapi", "pydantic"]

import realtime  # noqa: E402,F401  (dm_routes il importa pentru wiring-ul Redis)
from astran_sandbox import chat_filter as cf  # noqa: E402
from astran_sandbox import dm_routes  # noqa: E402

for _m in _STUBBED:  # nu lasam stub-urile in sys.modules (ar strica alte teste din acelasi proces)
    sys.modules.pop(_m, None)

HTTPException = dm_routes.HTTPException
WebSocketDisconnect = dm_routes.WebSocketDisconnect


# ----------------------------------------------------------------------------- Mongo fals
def _cmp(val, cond):
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        for op, v in cond.items():
            if op == "$in":
                if val not in v: return False
            elif op == "$ne":
                if val == v: return False
            elif op == "$gt":
                if val is None or not val > v: return False
            elif op == "$lt":
                if val is None or not val < v: return False
            elif op == "$lte":
                if val is None or not val <= v: return False
            else:
                raise NotImplementedError(op)
        return True
    return val == cond  # camp lipsa == None, ca in Mongo


def _match(doc, q):
    for k, v in q.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in v): return False
        elif not _cmp(doc.get(k), v):
            return False
    return True


def _project(doc, proj):
    d = copy.deepcopy(doc)
    if not proj:
        return d
    if any(v == 0 for v in proj.values()):
        return {k: v for k, v in d.items() if proj.get(k, 1) != 0}
    keep = {k for k, v in proj.items() if v}
    return {k: v for k, v in d.items() if k in keep}


class FakeCursor:
    def __init__(self, rows, proj):
        self.rows, self.proj = rows, proj

    def sort(self, key, direction=None):
        spec = key if isinstance(key, list) else [(key, direction)]
        for k, d in reversed(spec):
            self.rows.sort(key=lambda r: r.get(k), reverse=(d == -1))
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, n=None):
        return [_project(r, self.proj) for r in self.rows[: n if n else None]]


class FakeCol:
    def __init__(self):
        self.docs = []

    async def find_one(self, q, proj=None):
        return next((_project(d, proj) for d in self.docs if _match(d, q)), None)

    def find(self, q, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, q)], proj)

    async def insert_one(self, d):
        self.docs.append(d)

    async def update_one(self, q, u, upsert=False):
        d = next((x for x in self.docs if _match(x, q)), None)
        if d is None:
            if not upsert:
                return
            d = {k: v for k, v in q.items() if not k.startswith("$") and not isinstance(v, dict)}
            d.update(copy.deepcopy(u.get("$setOnInsert", {})))
            self.docs.append(d)
        for k, v in u.get("$set", {}).items():
            d[k] = v
        for k, v in u.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v

    async def create_index(self, *a, **k):
        pass


class FakeDb:
    def __init__(self):
        for n in ("users", "friendships", "blocks", "reports", "platform_config", "dm_conversations", "dm_messages"):
            setattr(self, n, FakeCol())


class FakeWS:
    def __init__(self):
        self.inbox, self.sent, self.accepted, self.closed = asyncio.Queue(), [], False, None

    async def accept(self): self.accepted = True
    async def close(self, code=1000): self.closed = code
    async def send_text(self, t): self.sent.append(json.loads(t))

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    def of(self, t): return [m for m in self.sent if m.get("t") == t]


async def settle(n=10):
    for _ in range(n):
        await asyncio.sleep(0)


def friend_public(u):
    return {"user_id": u["user_id"], "username": u["username"], "display_name": u["username"].title(), "avatar_url": None, "online": False}


class DmRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        cf._SHARED = None  # un RuleStore proaspat, cu regulile livrate cu aplicatia
        realtime.hub.redis = None
        self.db = FakeDb()
        self.clock = 1_700_000_000.0
        self.users = {}
        for uid, age in (("alice", "adult_18"), ("bob", "adult_18"), ("mia", "under_18"), ("carol", "adult_18")):
            doc = {"user_id": uid, "username": uid, "age_category": age, "password_hash": "SECRET", "email": f"{uid}@x"}
            self.users[uid] = doc
            self.db.users.docs.append(doc)
        for a, b in (("alice", "bob"), ("alice", "mia"), ("bob", "mia")):
            self.db.friendships.docs.append({"user_a": a, "user_b": b})

        async def resolver(token):
            return self.users.get(token.replace("tok_", ""))

        self.api, self.ws = dm_routes.make_dm_routers(lambda: None, resolver, self.db, friend_public)
        self.routes = {(r.method, r.path): r.endpoint for r in self.api.routes}
        self.ws_endpoint = next(r.endpoint for r in self.ws.routes if r.path == "/ws/dm")
        self.service = self.api.service
        # ceas controlat: anti-spam fara sleep-uri
        self.service._clock = lambda: self.clock
        self.service._mono = lambda: self.clock
        self.tasks = []

    async def asyncTearDown(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        realtime.hub.dm_relay = None
        cf._SHARED = None

    # ----------------------------------------------------------------- scurtaturi
    def me(self, uid): return self.users[uid]

    async def call(self, method, path, uid, **kw):
        self.clock += 2.0
        return await self.routes[(method, path)](current=self.me(uid), **kw)

    async def open(self, uid, friend):
        return (await self.call("POST", "/dm/open", uid, body=dm_routes.OpenBody(friend_id=friend)))["conversation_id"]

    async def send(self, uid, cid, text):
        return (await self.call("POST", "/dm/conversations/{conversation_id}/messages", uid, conversation_id=cid,
                                body=dm_routes.SendBody(text=text, client_id="k1")))["message"]

    async def history(self, uid, cid, before=None, limit=30):
        return await self.call("GET", "/dm/conversations/{conversation_id}/messages", uid, conversation_id=cid, before=before, limit=limit)

    async def conversations(self, uid):
        return (await self.call("GET", "/dm/conversations", uid))["conversations"]

    async def connect(self, uid, token=None):
        ws = FakeWS()
        self.tasks.append(asyncio.create_task(self.ws_endpoint(ws, token or f"tok_{uid}")))
        await settle()
        return ws

    async def fails(self, coro, status, code):
        with self.assertRaises(HTTPException) as cm:
            await coro
        self.assertEqual(cm.exception.status_code, status)
        self.assertEqual(cm.exception.detail["code"], code)  # client: err.code (api/client.ts)
        return cm.exception

    # ----------------------------------------------------------------- teste
    async def test_open_is_idempotent_and_persisted_in_db(self):
        c1, c2, c3 = await self.open("alice", "bob"), await self.open("alice", "bob"), await self.open("bob", "alice")
        self.assertEqual(c1, c2)
        self.assertEqual(c1, c3)
        self.assertEqual(len(self.db.dm_conversations.docs), 1)
        doc = self.db.dm_conversations.docs[0]
        self.assertEqual((doc["user_a"], doc["user_b"]), ("alice", "bob"))  # pereche sortata

    async def test_open_response_never_leaks_private_fields(self):
        r = await self.call("POST", "/dm/open", "alice", body=dm_routes.OpenBody(friend_id="bob"))
        self.assertEqual(r["friend"]["user_id"], "bob")
        self.assertNotIn("SECRET", json.dumps(r))
        self.assertNotIn("bob@x", json.dumps(r))

    async def test_open_requires_friendship_and_no_block(self):
        await self.fails(self.call("POST", "/dm/open", "alice", body=dm_routes.OpenBody(friend_id="carol")), 403, "not_allowed")
        self.db.blocks.docs.append({"blocker_id": "bob", "blocked_id": "alice"})
        await self.fails(self.call("POST", "/dm/open", "alice", body=dm_routes.OpenBody(friend_id="bob")), 403, "not_allowed")

    async def test_send_history_list_unread_read_full_flow(self):
        cid = await self.open("alice", "bob")
        m = await self.send("alice", cid, "salut bob")
        self.assertEqual((m["sender_id"], m["text"], m["mine"], m["client_id"]), ("alice", "salut bob", True, "k1"))
        await self.send("alice", cid, "esti acolo?")
        self.assertEqual((await self.call("GET", "/dm/unread", "bob"))["unread"], 2)
        lst = await self.conversations("bob")
        self.assertEqual((lst[0]["friend"]["user_id"], lst[0]["unread"], lst[0]["last_message"]["text"]), ("alice", 2, "esti acolo?"))
        h = await self.history("bob", cid)
        self.assertEqual([x["text"] for x in h["messages"]], ["esti acolo?", "salut bob"])
        await self.call("POST", "/dm/conversations/{conversation_id}/read", "bob", conversation_id=cid)
        self.assertEqual((await self.call("GET", "/dm/unread", "bob"))["unread"], 0)
        self.assertEqual((await self.conversations("bob"))[0]["unread"], 0)
        self.assertEqual((await self.call("GET", "/dm/unread", "alice"))["unread"], 0)

    async def test_pagination_through_mongo_store_cursor(self):
        cid = await self.open("alice", "bob")
        for i in range(12):
            await self.send("alice" if i % 2 == 0 else "bob", cid, f"m{i:02d}")
        seen, before = [], None
        for _ in range(5):
            h = await self.history("alice", cid, before=before, limit=5)
            seen += [x["text"] for x in h["messages"]]
            if not h["has_more"]:
                break
            before = h["next_before"]
        self.assertEqual(seen, [f"m{i:02d}" for i in reversed(range(12))])

    async def test_conversations_sorted_and_bump_to_top(self):
        c1, c2 = await self.open("alice", "bob"), await self.open("alice", "mia")
        await self.send("alice", c1, "unu")
        await self.send("alice", c2, "doi")
        self.assertEqual([c["friend"]["user_id"] for c in await self.conversations("alice")], ["mia", "bob"])
        await self.send("bob", c1, "bob raspunde")
        self.assertEqual([c["friend"]["user_id"] for c in await self.conversations("alice")], ["bob", "mia"])

    async def test_stranger_gets_404_on_every_endpoint(self):
        cid = await self.open("alice", "bob")
        m = await self.send("alice", cid, "secret")
        await self.fails(self.history("carol", cid), 404, "not_found")
        await self.fails(self.send("carol", cid, "intru"), 404, "not_found")
        await self.fails(self.call("POST", "/dm/conversations/{conversation_id}/read", "carol", conversation_id=cid), 404, "not_found")
        await self.fails(self.call("POST", "/dm/messages/{message_id}/report", "carol", message_id=m["id"], body=dm_routes.ReportBody(reason="spam")), 404, "not_found")

    async def test_block_stops_send_history_list_even_via_direct_api(self):
        cid = await self.open("alice", "bob")
        await self.send("alice", cid, "inainte de blocare")
        self.db.blocks.docs.append({"blocker_id": "bob", "blocked_id": "alice"})
        await self.fails(self.send("alice", cid, "dupa blocare"), 403, "not_allowed")
        await self.fails(self.history("bob", cid), 403, "not_allowed")
        self.assertEqual(await self.conversations("bob"), [])
        self.assertEqual((await self.call("GET", "/dm/unread", "bob"))["unread"], 0)
        self.assertEqual(len(self.db.dm_messages.docs), 1)

    async def test_validation_errors_have_codes_and_status(self):
        cid = await self.open("alice", "bob")
        await self.fails(self.send("alice", cid, "   "), 400, "empty")
        await self.fails(self.send("alice", cid, "x" * 501), 400, "too_long")
        self.assertEqual(len(self.db.dm_messages.docs), 0)

    async def test_rate_limit_returns_429_with_retry(self):
        cid = await self.open("alice", "bob")
        for i in range(2):
            self.clock += 10
            await self.routes[("POST", "/dm/conversations/{conversation_id}/messages")](
                conversation_id=cid, body=dm_routes.SendBody(text=f"a{i}"), current=self.me("alice"))
        self.clock += 0.01  # fara pauza => prea repede
        e = await self.fails(self.routes[("POST", "/dm/conversations/{conversation_id}/messages")](
            conversation_id=cid, body=dm_routes.SendBody(text="spam"), current=self.me("alice")), 429, "rate_limited")
        self.assertIn("retry_ms", e.detail)

    async def test_age_rules_end_to_end_with_shipped_filter(self):
        ca, cm = await self.open("alice", "bob"), await self.open("alice", "mia")
        text = "this is a shit game"
        await self.send("alice", ca, text)
        await self.send("alice", cm, text)
        self.assertEqual((await self.history("bob", ca))["messages"][0]["text"], text)  # 18+ <-> 18+: nefiltrat
        self.assertEqual((await self.history("mia", cm))["messages"][0]["text"], "this is a #### game")  # minor: filtrat de server
        self.assertEqual((await self.history("alice", cm))["messages"][0]["text"], text)  # expeditorul adult isi vede textul
        # ce s-a salvat: raw doar pentru expeditorul adult
        await self.send("mia", cm, "you shit")
        mia_doc = next(d for d in self.db.dm_messages.docs if d["sender_id"] == "mia")
        self.assertIsNone(mia_doc["raw"])
        self.assertEqual(mia_doc["safe"], "you ####")
        # adultul vede si el varianta filtrata a minorului
        self.assertEqual((await self.history("alice", cm))["messages"][0]["text"], "you ####")

    async def test_dm_uses_the_exact_same_rule_store_as_game_chat(self):
        store = cf.shared_store(self.db)
        self.assertIs(self.service._rules.__self__, store)  # aceeasi instanta, nu o copie a listei
        compiled = await store.get_compiled()
        cid = await self.open("alice", "mia")
        for text in ("this is a shit game", "totul e bine", "oh shit sorry"):
            await self.send("alice", cid, text)
            expected = cf.filter_text(text, compiled).text
            self.assertEqual((await self.history("mia", cid))["messages"][0]["text"], expected)

    async def test_websocket_live_delivery_per_viewer_and_auth(self):
        cid = await self.open("alice", "mia")
        w_mia, w_alice = await self.connect("mia"), await self.connect("alice")
        self.assertTrue(w_mia.accepted)
        self.assertEqual(w_mia.of("dm_ready")[0]["t"], "dm_ready")
        await self.send("alice", cid, "this is a shit game")
        await settle()
        self.assertEqual(w_mia.of("dm")[0]["message"]["text"], "this is a #### game")
        self.assertEqual(w_alice.of("dm")[0]["message"]["text"], "this is a shit game")
        self.assertNotIn("shit", json.dumps(w_mia.sent))
        # token invalid => inchis cu 4401, fara accept
        bad = FakeWS()
        await self.ws_endpoint(bad, "tok_nimeni")
        self.assertEqual(bad.closed, 4401)
        self.assertFalse(bad.accepted)

    async def test_websocket_client_cannot_send_messages_and_keepalive_works(self):
        cid = await self.open("alice", "bob")
        w = await self.connect("alice")
        w.inbox.put_nowait(json.dumps({"t": "dm", "conversation_id": cid, "text": "falsificat", "sender_id": "bob"}))
        w.inbox.put_nowait("ping")
        await settle()
        self.assertEqual(self.db.dm_messages.docs, [])  # nimic nu se salveaza prin WebSocket
        self.assertTrue(w.of("pong"))

    async def test_websocket_connection_cap_per_user(self):
        socks = [await self.connect("alice") for _ in range(5)]
        sixth = await self.connect("alice")
        self.assertEqual(sixth.closed, 4429)
        self.assertTrue(all(s.closed is None for s in socks))

    async def test_websocket_cleanup_on_disconnect(self):
        w = await self.connect("alice")
        self.assertTrue(self.api.hub.has("alice"))
        w.inbox.put_nowait(None)
        await settle()
        self.assertFalse(self.api.hub.has("alice"))

    async def test_redis_hook_in_realtime_delivers_dm_and_ignores_game_rooms(self):
        self.assertIsNotNone(realtime.hub.dm_relay)
        w = await self.connect("bob")
        payload = {"t": "dm_x", "cid": "c1", "id": "m1", "ts": 5, "sid": "alice", "adult": True, "raw": "salut", "safe": "salut"}
        await realtime.hub.relay_remote("dm_bob", payload)
        await settle()
        self.assertEqual(w.of("dm")[0]["message"]["text"], "salut")
        await realtime.hub.relay_remote("dm_bob", {"t": "state", "x": 1})  # alt tip: ignorat
        await realtime.hub.relay_remote("inst_abc", payload)  # camera de joc inexistenta: ignorat ca inainte
        self.assertEqual(len(w.of("dm")), 1)

    async def test_report_goes_into_existing_reports_collection(self):
        cid = await self.open("alice", "mia")
        m = await self.send("alice", cid, "this is a shit game")
        await self.call("POST", "/dm/messages/{message_id}/report", "mia", message_id=m["id"], body=dm_routes.ReportBody(reason="harassment"))
        self.assertEqual(len(self.db.reports.docs), 1)
        r = self.db.reports.docs[0]
        self.assertEqual((r["reporter_id"], r["target_type"], r["target_id"], r["reason"]), ("mia", "message", m["id"], "harassment"))

    async def test_game_chat_untouched_by_dm_wiring(self):
        # montarea DM a atins realtime.py doar prin hook-ul dm_relay; camerele de joc se comporta la fel
        self.assertEqual(realtime.hub.rooms, {})
        room = realtime.hub.room("inst_1")
        self.assertIs(realtime.hub.rooms["inst_1"], room)
        await realtime.hub.relay_remote("inst_1", {"t": "state"})  # fara membri: nu crapa
        realtime.hub.rooms.clear()


if __name__ == "__main__":
    unittest.main()