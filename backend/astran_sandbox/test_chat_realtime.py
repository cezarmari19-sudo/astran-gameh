"""Test de integrare: handler-ul WebSocket REAL din backend/realtime.py (ws_instance), cu un
WebSocket si o baza de date false (in memorie). Verifica wiring-ul chatului: identitate din
token, varsta din DB, filtrare per destinatar, blocari, rate-limit, raportare, reguli
schimbate fara modificarea codului, si ca pozitiile (mesajele "state") merg ca inainte.

Rulare din folderul backend:   python -m unittest astran_sandbox.test_chat_realtime -v

Daca FastAPI nu e instalat (ca in mediul de dezvoltare al acestui test), se foloseste un stub
minimal pentru APIRouter/WebSocket - e folosit DOAR pentru import; handler-ul testat e cel real.
NU testeaza: un WebSocket real (Starlette/uvicorn), Mongo real, Redis real."""
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

_STUBBED = False
try:  # pragma: no cover
    import fastapi  # noqa: F401
    from fastapi import WebSocketDisconnect
except ImportError:
    stub = types.ModuleType("fastapi")

    class WebSocketDisconnect(Exception):
        pass

    class _Router:
        def __init__(self, *a, **k): self.routes = []
        def websocket(self, path):
            def deco(fn):
                self.routes.append(types.SimpleNamespace(path=path, endpoint=fn)); return fn
            return deco

    stub.APIRouter, stub.WebSocketDisconnect = _Router, WebSocketDisconnect
    stub.Query = lambda *a, **k: None
    stub.WebSocket = type("WebSocket", (), {})
    sys.modules["fastapi"] = stub
    _STUBBED = True

import realtime  # noqa: E402

if _STUBBED:  # nu lasam stub-ul in sys.modules: ar strica alte teste din acelasi proces (ex. project_security)
    del sys.modules["fastapi"]
from astran_sandbox import chat_filter as cf  # noqa: E402


def _match(doc, q):
    for k, v in q.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in v): return False
        elif isinstance(v, dict) and "$in" in v:
            if doc.get(k) not in v["$in"]: return False
        elif doc.get(k) != v:
            return False
    return True


class Cursor:
    def __init__(self, rows): self.rows = rows
    async def to_list(self, n=None): return list(self.rows)


class Col:
    def __init__(self): self.docs = []
    async def find_one(self, q, p=None): return next((d for d in self.docs if _match(d, q)), None)
    def find(self, q, p=None): return Cursor([d for d in self.docs if _match(d, q)])
    async def insert_one(self, d): self.docs.append(d)
    async def update_one(self, q, u, upsert=False):
        d = await self.find_one(q)
        if d is None:
            d = {}; self.docs.append(d)
        d.update(u["$set"])


class FakeDb:
    def __init__(self):
        for n in ("users", "blocks", "instance_players", "game_instances", "reports", "platform_config"):
            setattr(self, n, Col())


class FakeWS:
    def __init__(self):
        self.inbox = asyncio.Queue(); self.sent = []; self.accepted = False; self.closed = None
    async def accept(self): self.accepted = True
    async def close(self, code=1000): self.closed = code
    async def send_text(self, t): self.sent.append(json.loads(t))
    async def receive_text(self):
        item = await self.inbox.get()
        if item is None: raise WebSocketDisconnect()
        return item
    def push(self, obj): self.inbox.put_nowait(obj if isinstance(obj, str) else json.dumps(obj))
    def of(self, t): return [m for m in self.sent if m.get("t") == t]


async def settle(n=8):
    for _ in range(n): await asyncio.sleep(0)


class RealtimeChatIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        cf._SHARED = None
        realtime.hub.rooms.clear(); realtime.hub.redis = None
        self.db = FakeDb()
        self.tokens = {}
        for uid, age in (("u1", "under_18"), ("u2", "under_18"), ("u3", "adult_18"), ("u4", "adult_18")):
            doc = {"user_id": uid, "username": f"name_{uid}", "age_category": age}
            self.db.users.docs.append(doc)
            self.tokens[f"tok_{uid}"] = doc
            self.db.instance_players.docs.append({"instance_id": "i1", "user_id": uid})
        self.db.game_instances.docs.append({"instance_id": "i1", "game_id": "g1"})
        self.db.instance_players.docs.append({"instance_id": "other", "user_id": "u9"})

        async def resolver(token): return self.tokens.get(token)
        router = realtime.make_realtime_router(resolver, self.db)
        self.endpoint = next(r.endpoint for r in router.routes if r.path.startswith("/ws/"))
        self.tasks = []

    async def asyncTearDown(self):
        for t in self.tasks: t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def connect(self, uid, instance="i1", token=None):
        ws = FakeWS()
        self.tasks.append(asyncio.create_task(self.endpoint(ws, "g1", instance, token or f"tok_{uid}")))
        await settle()
        return ws

    async def test_two_minors_chat_and_identity_comes_from_token(self):
        a, b = await self.connect("u1"), await self.connect("u2")
        a.push({"t": "chat", "text": "salut", "cid": "c1", "user_id": "u2", "username": "FAKE"}); await settle()
        got = b.of("chat")[0]
        self.assertEqual((got["user_id"], got["username"], got["text"]), ("u1", "name_u1", "salut"))
        self.assertEqual(a.of("chat")[0]["cid"], "c1")

    async def test_chat_info_and_history_on_join(self):
        a = await self.connect("u3")
        a.push({"t": "chat", "text": "mesaj vechi"}); await settle()
        late = await self.connect("u1")
        self.assertEqual(late.of("chat_info")[0]["mode"], "under_18")
        self.assertEqual([m["text"] for m in late.of("chat_history")[0]["messages"]], ["mesaj vechi"])

    async def test_adult_mode_free_minor_mode_filtered_with_shipped_rules(self):
        adult1, adult2, minor = await self.connect("u3"), await self.connect("u4"), await self.connect("u1")
        adult1.push({"t": "chat", "text": "this is a shit game"}); await settle()
        self.assertEqual(adult2.of("chat")[0]["text"], "this is a shit game")
        self.assertEqual(minor.of("chat")[0]["text"], "this is a #### game")
        self.assertNotIn("shit", json.dumps(minor.sent))

    async def test_minor_cannot_claim_adult_status(self):
        minor, adult = await self.connect("u1"), await self.connect("u3")
        minor.push({"t": "chat", "text": "fuck this", "age_category": "adult_18", "adult": True, "mode": "adult_18"}); await settle()
        self.assertEqual(adult.of("chat")[0]["text"], "#### this")

    async def test_age_change_in_db_applies_immediately_without_reconnect(self):
        adult1, adult2 = await self.connect("u3"), await self.connect("u4")
        next(d for d in self.db.users.docs if d["user_id"] == "u4")["age_category"] = "under_18"
        adult1.push({"t": "chat", "text": "fuck"}); await settle()
        self.assertEqual(adult2.of("chat")[0]["text"], "####")

    async def test_new_rule_without_touching_chat_code(self):  # cerinta 6/8
        minor, adult = await self.connect("u1"), await self.connect("u3")
        adult.push({"t": "chat", "text": "a zorbaword here"}); await settle()
        self.assertEqual(minor.of("chat")[-1]["text"], "a zorbaword here")   # inca nu e interzis
        store = cf.shared_store(self.db)
        rules = await store.get_rules()
        await store.save_rules(rules + cf.parse_rules([{"term": "zorbaword", "action": "mask_sentence"}]))
        adult.chat_dummy = None
        adult.push({"t": "chat", "text": "another zorbaword here"}); await settle(); await asyncio.sleep(0.4)
        adult.push({"t": "chat", "text": "yet a zorbaword here"}); await settle()
        last = minor.of("chat")[-1]["text"]
        self.assertEqual(set(last), {"#"})

    async def test_blocked_user_messages_not_delivered(self):
        a, b, c = await self.connect("u3"), await self.connect("u4"), await self.connect("u1")
        self.db.blocks.docs.append({"blocker_id": "u4", "blocked_id": "u3"})
        a.push({"t": "chat", "text": "hello"}); await settle()
        self.assertEqual(b.of("chat"), [])
        self.assertEqual(len(c.of("chat")), 1)

    async def test_spam_and_oversize_and_empty(self):
        a, b = await self.connect("u3"), await self.connect("u4")
        a.push({"t": "chat", "text": ""}); a.push({"t": "chat", "text": "x" * 300})
        a.push('{"t":"chat","text":"' + "y" * 5000 + '"}')
        for i in range(8): a.push({"t": "chat", "text": f"spam{i}"})
        await settle(30)
        self.assertLessEqual(len(b.of("chat")), 4)
        codes = [e["code"] for e in a.of("chat_error")]
        self.assertEqual(codes[:3], ["empty", "too_long", "too_long"])
        self.assertIn("rate_limited", codes)

    async def test_garbage_frames_do_not_kill_the_connection(self):
        a, b = await self.connect("u3"), await self.connect("u4")
        for junk in ("not json", "[]", '{"t":"chat"}', '{"t":"chat","text":{"a":1}}', '{"t":"chat_report"}', '{"t":"nope"}'):
            a.push(junk)
        await settle(20)
        a.push({"t": "chat", "text": "still alive"}); await settle()
        self.assertEqual(b.of("chat")[-1]["text"], "still alive")

    async def test_report_flow_writes_report_from_server_side_data(self):
        a, b = await self.connect("u3"), await self.connect("u4")
        a.push({"t": "chat", "text": "rude words"}); await settle()
        mid = b.of("chat")[0]["id"]
        b.push({"t": "chat_report", "id": mid, "reason": "harassment"}); await settle()
        self.assertEqual(len(self.db.reports.docs), 1)
        rep = self.db.reports.docs[0]
        self.assertEqual((rep["reporter_id"], rep["target_type"], rep["context"]["sender_id"], rep["context"]["text"]),
                         ("u4", "message", "u3", "rude words"))
        self.assertEqual(b.of("chat_report_ack")[0]["id"], mid)

    async def test_nothing_is_written_to_db_by_normal_chat(self):
        a, b = await self.connect("u3"), await self.connect("u4")
        a.push({"t": "chat", "text": "no persistence"}); await settle()
        self.assertEqual(self.db.reports.docs, [])
        for col in ("users", "blocks", "instance_players", "game_instances", "platform_config"):
            self.assertNotIn("no persistence", json.dumps(getattr(self.db, col).docs, default=str))

    async def test_position_sync_still_works(self):  # regresie: functionalitatea existenta
        a, b = await self.connect("u3"), await self.connect("u4")
        a.push({"t": "state", "x": 1.5, "y": 0, "z": -2, "ry": 0.3, "anim": "walk"}); await settle()
        st = b.of("state")[0]
        self.assertEqual((st["user_id"], st["x"], st["z"], st["anim"]), ("u3", 1.5, -2.0, "walk"))
        self.assertEqual(len(b.of("join")) + len(a.of("join")), 1)

    async def test_connection_security_unchanged(self):
        bad = await self.connect("u3", token="wrong")
        self.assertEqual(bad.closed, 4401)
        outsider = await self.connect("u3", instance="other")
        self.assertEqual(outsider.closed, 4403)

    async def test_room_and_buffer_dropped_when_everyone_leaves(self):
        a = await self.connect("u3")
        a.push({"t": "chat", "text": "bye"}); await settle()
        self.assertEqual(len(realtime.hub.rooms["i1"].chat_buffer), 1)
        a.inbox.put_nowait(None); await settle()  # deconectare
        self.assertNotIn("i1", realtime.hub.rooms)

    async def test_remote_chat_via_hub_is_delivered_per_recipient(self):  # cale Redis, cu bridge fals
        minor, adult = await self.connect("u1"), await self.connect("u3")
        await realtime.hub.relay_remote("i1", {"t": "chat_x", "id": "rr1", "ts": 1, "uid": "far", "un": "far",
                                                "adult": True, "raw": "fuck yes", "safe": "#### yes"})
        self.assertEqual(adult.of("chat")[-1]["text"], "fuck yes")
        self.assertEqual(minor.of("chat")[-1]["text"], "#### yes")
        await realtime.hub.relay_remote("i1", {"t": "state", "user_id": "far", "x": 1})   # state: relay brut, ca inainte
        self.assertEqual(adult.of("state")[-1]["x"], 1)


if __name__ == "__main__":
    unittest.main()