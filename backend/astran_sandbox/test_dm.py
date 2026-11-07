"""Teste pentru mesageria privata (dm_core.py). Rulare din folderul backend:
    python -m unittest astran_sandbox.test_dm -v
(stdlib only. Filtrul (chat_filter), limiter-ul si text_for (chat_core) sunt cele REALE; doar
stocarea, utilizatorii, prieteniile, blocarile si WebSocket-ul sunt simulate in memorie.)

Ce NU acopera: MongoDB real (dm_store.py - interogari/indexuri), FastAPI/Starlette real
(dm_routes.py - validarea Pydantic, dependinta get_current_user, handler-ul WebSocket), Redis real.
Vezi raportul final pentru ce trebuie verificat in runtime."""
import asyncio
import json
import unittest

from astran_sandbox import chat_filter as cf
from astran_sandbox import dm_core as dm

ADULT, MINOR = "adult_18", "under_18"


def run(coro):
    return asyncio.run(coro)


class FakeStore:
    """Implementeaza aceeasi interfata ca dm_store.MongoDmStore, in memorie."""

    def __init__(self):
        self.convs, self.msgs, self.reports = {}, [], []
        self.fail_insert = False

    async def get_or_create(self, a, b, cid, now):
        for c in self.convs.values():
            if (c["user_a"], c["user_b"]) == (a, b):
                return dict(c)
        c = {"conversation_id": cid, "user_a": a, "user_b": b, "created_at": now, "last_message_id": None,
             "last_message_at": None, "unread_a": 0, "unread_b": 0}
        self.convs[cid] = c
        return dict(c)

    async def get_conversation(self, cid):
        c = self.convs.get(cid)
        return dict(c) if c else None

    async def list_conversations(self, uid, limit):
        rows = [dict(c) for c in self.convs.values() if uid in (c["user_a"], c["user_b"]) and c["last_message_id"]]
        rows.sort(key=lambda c: c["last_message_at"], reverse=True)
        return rows[:limit]

    async def unread_conversations(self, uid):
        out = []
        for c in self.convs.values():
            if (c["user_a"] == uid and c["unread_a"] > 0) or (c["user_b"] == uid and c["unread_b"] > 0):
                out.append(dict(c))
        return out

    async def insert_message(self, msg):
        if self.fail_insert:
            raise RuntimeError("db down")
        self.msgs.append(dict(msg))

    async def bump_conversation(self, cid, mid, ts, slot):
        c = self.convs[cid]
        c["unread_" + slot] += 1
        if c["last_message_at"] is None or c["last_message_at"] <= ts:
            c["last_message_id"], c["last_message_at"] = mid, ts

    async def mark_read(self, cid, slot):
        self.convs[cid]["unread_" + slot] = 0

    async def page_messages(self, cid, before, limit):
        rows = [m for m in self.msgs if m["conversation_id"] == cid]
        if before is not None:
            ts, mid = before
            rows = [m for m in rows if m["ts"] < ts or (m["ts"] == ts and m["message_id"] < mid)]
        rows.sort(key=lambda m: (m["ts"], m["message_id"]), reverse=True)
        return [dict(m) for m in rows[:limit]]

    async def get_messages_by_ids(self, ids):
        return {m["message_id"]: dict(m) for m in self.msgs if m["message_id"] in ids}

    async def get_message(self, mid):
        return next((dict(m) for m in self.msgs if m["message_id"] == mid), None)

    async def insert_report(self, doc):
        self.reports.append(doc)


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class Env:
    """Doua conturi adulte (alice, bob), doua minore (mia, max), prieteni intre toti, fara blocari."""

    def __init__(self, rules=None):
        self.now = 1_700_000_000.0
        self.users = {"alice": ADULT, "bob": ADULT, "mia": MINOR, "max": MINOR, "carol": ADULT}
        self.muted = {}
        self.friends = {dm.pair(a, b) for a in ("alice", "bob", "mia", "max") for b in ("alice", "bob", "mia", "max") if a != b}
        self.blocks = set()  # (blocker, blocked)
        self.rules = cf.compile_rules(cf.parse_rules(rules if rules is not None else [
            {"term": "badword", "action": "mask_word"},
            {"term": "awful phrase", "action": "mask_sentence"},
        ]))
        self.rules_fail = False
        self.ages_fail = False
        self.store = FakeStore()
        self.hub = dm.DmHub()
        self.published = []
        self.svc = dm.DmService(
            store=self.store, hub=self.hub,
            rules_provider=self._rules, user_state=self._state, user_ages=self._ages,
            users_public=self._public, are_friends=self._friends, friend_ids=self._friend_ids,
            blocked_between=self._blocked_between, blocked_among=self._blocked_among,
            publish=self._publish, clock=lambda: self.now, mono=lambda: self.now,
        )

    async def _rules(self):
        if self.rules_fail:
            raise RuntimeError("rules unavailable")
        return self.rules

    async def _state(self, uid):
        if uid not in self.users:
            return None
        return {"age_category": self.users[uid], "chat_muted_until": self.muted.get(uid)}

    async def _ages(self, ids):
        if self.ages_fail:
            raise RuntimeError("db down")
        return {u: self.users[u] == ADULT for u in ids if u in self.users}

    async def _public(self, ids):
        return {u: {"user_id": u, "username": u, "display_name": u.title(), "avatar_url": None, "online": False}
                for u in ids if u in self.users}

    async def _friends(self, a, b):
        return dm.pair(a, b) in self.friends

    async def _friend_ids(self, uid):
        return {b if a == uid else a for a, b in self.friends if uid in (a, b)}

    async def _blocked_among(self, uid, ids):
        return {i for i in ids if (uid, i) in self.blocks or (i, uid) in self.blocks}

    async def _blocked_between(self, a, b):
        return (a, b) in self.blocks or (b, a) in self.blocks

    async def _publish(self, uid, payload):
        self.published.append((uid, payload))

    # scurtaturi
    def open(self, a, b):
        return run(self.svc.open_conversation(a, b))["conversation_id"]

    def send(self, uid, cid, text, tick=1.0):
        self.now += tick  # ca sa nu declansam limiter-ul in testele care nu il vizeaza
        return run(self.svc.send(uid, cid, text))

    def adult(self, uid):
        return self.users[uid] == ADULT


def err(fn, *a, **k):
    try:
        fn(*a, **k)
    except dm.DmError as e:
        return e
    raise AssertionError("trebuia sa arunce DmError")


class Conversation(unittest.TestCase):
    def test_same_pair_always_same_conversation(self):  # nu se creeaza conversatii noi la fiecare apasare
        e = Env()
        c1 = e.open("alice", "bob")
        c2 = e.open("alice", "bob")
        c3 = e.open("bob", "alice")  # si din partea celuilalt
        self.assertEqual(c1, c2)
        self.assertEqual(c1, c3)
        self.assertEqual(len(e.store.convs), 1)

    def test_cannot_open_with_non_friend_self_or_blocked(self):
        e = Env()
        self.assertEqual(err(e.open, "alice", "carol").code, "not_allowed")
        self.assertEqual(err(e.open, "alice", "alice").code, "invalid")
        self.assertEqual(err(e.open, "alice", "nobody").code, "not_allowed")
        e.blocks.add(("bob", "alice"))
        self.assertEqual(err(e.open, "alice", "bob").code, "not_allowed")

    def test_empty_conversation_not_in_list(self):  # nicio conversatie "fictiva" in lista
        e = Env()
        e.open("alice", "bob")
        self.assertEqual(run(e.svc.list_conversations("alice", True)), [])

    def test_send_and_history_roundtrip_persistent(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "salut")
        e.send("bob", cid, "hei")
        h = run(e.svc.history("bob", True, cid, None, 30))
        self.assertEqual([m["text"] for m in h["messages"]], ["hei", "salut"])  # cele mai noi primele
        self.assertEqual([m["mine"] for m in h["messages"]], [True, False])
        self.assertFalse(h["has_more"])
        # "repornire": un serviciu NOU peste ACELASI store vede istoricul
        e2 = Env()
        e2.store = e.store
        e2.svc.store = e.store
        self.assertEqual(len(run(e2.svc.history("alice", True, cid, None, 30))["messages"]), 2)


class Security(unittest.TestCase):
    def test_stranger_cannot_read_send_read_or_report(self):  # nu poti accesa conversatia altora schimband un ID
        e = Env()
        cid = e.open("alice", "bob")
        m = e.send("alice", cid, "secret")
        for fn in (
            lambda: run(e.svc.history("carol", True, cid, None, 30)),
            lambda: run(e.svc.send("carol", cid, "intru")),
            lambda: run(e.svc.mark_read("carol", cid)),
            lambda: run(e.svc.report("carol", m["id"], "other")),
        ):
            self.assertEqual(err(fn).code, "not_found")  # acelasi raspuns ca la "nu exista"
        self.assertEqual(err(lambda: run(e.svc.history("alice", True, "dm_inexistent", None, 30))).code, "not_found")
        self.assertEqual(err(lambda: run(e.svc.history("alice", True, 123, None, 30))).code, "not_found")

    def test_sender_identity_comes_from_session_only(self):
        e = Env()
        cid = e.open("alice", "bob")
        msg = e.send("alice", cid, "hello")
        self.assertEqual(e.store.msgs[0]["sender_id"], "alice")
        self.assertEqual(msg["sender_id"], "alice")
        # nu exista nicio cale prin care textul sa schimbe expeditorul: send() primeste doar (uid, cid, text)
        e.send("bob", cid, "ca alice: hello")
        self.assertEqual(e.store.msgs[1]["sender_id"], "bob")

    def test_blocked_user_cannot_send_even_bypassing_ui(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.blocks.add(("bob", "alice"))  # bob l-a blocat pe alice DUPA ce conversatia exista
        self.assertEqual(err(e.send, "alice", cid, "hei").code, "not_allowed")
        self.assertEqual(err(e.send, "bob", cid, "hei").code, "not_allowed")  # si invers
        self.assertEqual(e.store.msgs, [])

    def test_blocked_cannot_read_history_and_conversation_hidden_from_list_and_unread(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "hei")
        self.assertEqual(run(e.svc.unread_total("bob")), 1)
        e.blocks.add(("bob", "alice"))
        self.assertEqual(err(lambda: run(e.svc.history("bob", True, cid, None, 30))).code, "not_allowed")
        self.assertEqual(run(e.svc.list_conversations("bob", True)), [])
        self.assertEqual(run(e.svc.unread_total("bob")), 0)

    def test_not_friends_anymore_cannot_send_but_history_readable_and_flagged(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "hei")
        e.friends.discard(dm.pair("alice", "bob"))
        self.assertEqual(err(e.send, "alice", cid, "inca aici?").code, "not_allowed")
        lst = run(e.svc.list_conversations("alice", True))
        self.assertFalse(lst[0]["can_send"])
        self.assertEqual(len(run(e.svc.history("alice", True, cid, None, 30))["messages"]), 1)

    def test_admin_chat_mute_applies(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.muted["alice"] = e.now + 3600
        x = err(e.send, "alice", cid, "hei")
        self.assertEqual(x.code, "muted")
        self.assertGreater(x.retry_ms, 0)
        e.muted["alice"] = e.now - 1  # mute expirat
        e.send("alice", cid, "hei")

    def test_validation_empty_long_and_types(self):
        e = Env()
        cid = e.open("alice", "bob")
        self.assertEqual(err(e.send, "alice", cid, "").code, "empty")
        self.assertEqual(err(e.send, "alice", cid, "   \n\t ").code, "empty")
        self.assertEqual(err(e.send, "alice", cid, "\u200b\u202e").code, "empty")  # doar caractere invizibile/bidi
        self.assertEqual(err(e.send, "alice", cid, "x" * (dm.MAX_DM_LEN + 1)).code, "too_long")
        self.assertEqual(err(e.send, "alice", cid, "x" * (dm.MAX_RAW_LEN + 1)).code, "too_long")
        self.assertEqual(err(e.send, "alice", cid, None).code, "invalid")
        self.assertEqual(err(e.send, "alice", cid, {"a": 1}).code, "invalid")
        e.send("alice", cid, "x" * dm.MAX_DM_LEN)  # exact limita: ok, NETRUNCHIAT
        self.assertEqual(len(e.store.msgs[0]["safe"]), dm.MAX_DM_LEN)
        self.assertEqual(len(e.store.msgs), 1)  # nimic din cele respinse nu a fost salvat

    def test_invisible_chars_stripped_before_storage(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "he\u200bllo\u202e world")
        self.assertEqual(e.store.msgs[0]["raw"], "hello world")

    def test_rate_limit_burst_duplicate_and_lock(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.now += 10
        e.send("alice", cid, "m1", tick=0)
        e.now += 0.5
        e.send("alice", cid, "m2", tick=0)
        e.now += 0.1
        self.assertEqual(err(lambda: run(e.svc.send("alice", cid, "m2"))).code, "duplicate")  # acelasi text in conversatie
        # prea repede (sub min_gap fata de m2, trimis acum 0.1s)
        self.assertEqual(err(lambda: run(e.svc.send("alice", cid, "m3"))).code, "rate_limited")
        # spam repetat => blocare temporara, chiar si pentru mesaje diferite
        for i in range(4):
            err(lambda i=i: run(e.svc.send("alice", cid, f"spam{i}")))
        self.assertEqual(err(lambda: run(e.svc.send("alice", cid, "dupa-blocare"))).code, "rate_limited")
        sent_before = len(e.store.msgs)
        e.now += 60  # dupa expirarea blocarii merge din nou
        e.send("alice", cid, "ok din nou", tick=0)
        self.assertEqual(len(e.store.msgs), sent_before + 1)
        # limita e PER UTILIZATOR: bob nu e afectat de spamul lui alice
        e.send("bob", cid, "bob e liber")

    def test_storage_failure_is_unavailable_not_silent(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.store.fail_insert = True
        self.assertEqual(err(e.send, "alice", cid, "hei").code, "unavailable")
        self.assertEqual(e.store.convs[cid]["unread_b"], 0)  # nu s-a numarat un mesaj nesalvat


class AgeAndFiltering(unittest.TestCase):
    def test_minor_sender_never_stores_raw(self):
        e = Env()
        cid = e.open("mia", "max")
        e.send("mia", cid, "you badword")
        stored = e.store.msgs[0]
        self.assertIsNone(stored["raw"])
        self.assertEqual(stored["safe"], "you #######")
        self.assertFalse(stored["sender_adult"])

    def test_adult_to_adult_gets_raw_unfiltered(self):  # 18+ nu e filtrat
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "you badword")
        h = run(e.svc.history("bob", True, cid, None, 30))
        self.assertEqual(h["messages"][0]["text"], "you badword")
        sent = run(e.svc.history("alice", True, cid, None, 30))
        self.assertEqual(sent["messages"][0]["text"], "you badword")

    def test_adult_to_minor_minor_sees_only_filtered_everywhere(self):
        e = Env()
        cid = e.open("alice", "mia")
        e.send("alice", cid, "you badword")
        # istoric
        h = run(e.svc.history("mia", False, cid, None, 30))
        self.assertEqual(h["messages"][0]["text"], "you #######")
        # lista (ultimul mesaj)
        lst = run(e.svc.list_conversations("mia", False))
        self.assertEqual(lst[0]["last_message"]["text"], "you #######")
        # nicaieri in raspunsurile catre minor nu apare textul brut
        self.assertNotIn("badword", json.dumps(h) + json.dumps(lst))

    def test_minor_to_adult_adult_sees_filtered_too(self):  # minorul nu "scapa" cuvinte spre adulti
        e = Env()
        cid = e.open("mia", "alice")
        e.send("mia", cid, "you badword")
        self.assertEqual(run(e.svc.history("alice", True, cid, None, 30))["messages"][0]["text"], "you #######")

    def test_mask_sentence_rule_hides_whole_message_for_minor_only(self):
        e = Env()
        cid = e.open("alice", "mia")
        e.send("alice", cid, "this is an awful phrase ok")
        self.assertEqual(run(e.svc.history("mia", False, cid, None, 30))["messages"][0]["text"], "#" * len("this is an awful phrase ok"))
        self.assertEqual(run(e.svc.history("alice", True, cid, None, 30))["messages"][0]["text"], "this is an awful phrase ok")

    def test_uses_the_same_rule_source_as_game_chat(self):  # nu exista o a doua lista: regulile vin din rules_provider
        e = Env(rules=[{"term": "zzz", "action": "mask_word"}])
        cid = e.open("alice", "mia")
        e.send("alice", cid, "badword zzz")
        self.assertEqual(run(e.svc.history("mia", False, cid, None, 30))["messages"][0]["text"], "badword ###")
        e.rules = cf.compile_rules(cf.parse_rules([{"term": "badword", "action": "mask_word"}]))  # regula schimbata, fara cod nou
        e.send("alice", cid, "badword zzz again")
        self.assertEqual(run(e.svc.history("mia", False, cid, None, 30))["messages"][0]["text"], "******* zzz again".replace("*", "#"))

    def test_rules_unavailable_refuses_instead_of_sending_unfiltered(self):
        e = Env()
        cid = e.open("alice", "mia")
        e.rules_fail = True
        self.assertEqual(err(e.send, "alice", cid, "badword").code, "unavailable")
        self.assertEqual(e.store.msgs, [])

    def test_age_is_read_from_db_at_read_time(self):  # nu o valoare "congelata" / de la client
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "you badword")
        e.users["bob"] = MINOR  # bob trece pe modul sub 18 (setare noua in DB)
        bob_now_adult = e.adult("bob")
        self.assertEqual(run(e.svc.history("bob", bob_now_adult, cid, None, 30))["messages"][0]["text"], "you #######")

    def test_unknown_or_missing_age_treated_as_minor(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.users["alice"] = None  # categorie lipsa/necunoscuta pentru expeditor
        e.send("alice", cid, "you badword")
        self.assertIsNone(e.store.msgs[0]["raw"])
        self.assertFalse(e.store.msgs[0]["sender_adult"])
        self.assertEqual(run(e.svc.history("bob", True, cid, None, 30))["messages"][0]["text"], "you #######")


class Pagination(unittest.TestCase):
    def test_pages_without_gaps_or_duplicates_with_same_timestamp(self):
        e = Env()
        cid = e.open("alice", "bob")
        for i in range(25):
            e.send("alice" if i % 2 == 0 else "bob", cid, f"m{i:02d}", tick=1.0)
        # si doua mesaje cu ACELASI ts (departajare dupa id)
        e.store.msgs.append({**e.store.msgs[-1], "message_id": "dmm_zzzz1", "safe": "same-ts-1", "raw": "same-ts-1"})
        e.store.msgs.append({**e.store.msgs[-1], "message_id": "dmm_zzzz2", "safe": "same-ts-2", "raw": "same-ts-2"})
        seen, before = [], None
        for _ in range(10):
            h = run(e.svc.history("alice", True, cid, before, 10))
            seen += [m["id"] for m in h["messages"]]
            if not h["has_more"]:
                break
            before = h["next_before"]
        self.assertEqual(len(seen), 27)
        self.assertEqual(len(set(seen)), 27)  # niciun duplicat
        self.assertEqual(len(e.store.msgs), 27)  # niciun mesaj sarit

    def test_bad_cursor_rejected_and_limit_clamped(self):
        e = Env()
        cid = e.open("alice", "bob")
        for bad in ("abc", "1", "x:y", "-5:id", "12:" + "a" * 100):
            self.assertEqual(err(lambda b=bad: run(e.svc.history("alice", True, cid, b, 10))).code, "invalid")
        for i in range(60):
            e.send("alice", cid, f"m{i}", tick=2.0)  # sub limita de burst, ca sa testam doar paginarea
        self.assertEqual(len(run(e.svc.history("alice", True, cid, None, 10_000))["messages"]), dm.MAX_PAGE)


class UnreadAndList(unittest.TestCase):
    def test_unread_counts_open_marks_read_and_isolation(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "1")
        e.send("alice", cid, "2")
        self.assertEqual(run(e.svc.unread_total("bob")), 2)
        self.assertEqual(run(e.svc.unread_total("alice")), 0)  # propriile mesaje nu sunt necitite
        self.assertEqual(run(e.svc.list_conversations("bob", True))[0]["unread"], 2)
        run(e.svc.mark_read("bob", cid))
        self.assertEqual(run(e.svc.unread_total("bob")), 0)
        e.send("bob", cid, "3")
        self.assertEqual(run(e.svc.unread_total("alice")), 1)
        self.assertEqual(run(e.svc.unread_total("bob")), 0)

    def test_list_sorted_by_last_message_and_moves_to_top(self):
        e = Env()
        c1, c2 = e.open("alice", "bob"), e.open("alice", "mia")
        e.send("alice", c1, "primul")
        e.send("alice", c2, "al doilea")
        order = [x["friend"]["user_id"] for x in run(e.svc.list_conversations("alice", True))]
        self.assertEqual(order, ["mia", "bob"])
        e.send("bob", c1, "bob scrie din nou")
        order = [x["friend"]["user_id"] for x in run(e.svc.list_conversations("alice", True))]
        self.assertEqual(order, ["bob", "mia"])
        top = run(e.svc.list_conversations("alice", True))[0]
        self.assertEqual(top["last_message"]["text"], "bob scrie din nou")
        self.assertFalse(top["last_message"]["mine"])
        self.assertTrue(top["can_send"])


class RealtimeDelivery(unittest.TestCase):
    def test_delivered_live_to_recipient_and_sender_other_devices_with_per_viewer_text(self):
        e = Env()
        cid = e.open("alice", "mia")
        w_mia, w_alice2 = FakeWs(), FakeWs()
        run(e.hub.add("mia", w_mia))
        run(e.hub.add("alice", w_alice2))
        e.send("alice", cid, "you badword")
        self.assertEqual(w_mia.sent[0]["t"], "dm")
        self.assertEqual(w_mia.sent[0]["message"]["text"], "you #######")  # minor: filtrat, NICIODATA raw
        self.assertFalse(w_mia.sent[0]["message"]["mine"])
        self.assertEqual(w_alice2.sent[0]["message"]["text"], "you badword")  # expeditorul adult isi vede textul
        self.assertTrue(w_alice2.sent[0]["message"]["mine"])
        self.assertNotIn("badword", json.dumps(w_mia.sent))

    def test_recipient_age_reread_at_delivery_and_failsafe_on_db_error(self):
        e = Env()
        cid = e.open("alice", "bob")
        w = FakeWs()
        run(e.hub.add("bob", w))
        e.ages_fail = True  # nu putem citi varsta destinatarului
        e.send("alice", cid, "you badword")
        self.assertEqual(w.sent[0]["message"]["text"], "you #######")  # fail-safe: sub 18

    def test_offline_recipient_gets_message_later_via_history_and_unread(self):
        e = Env()
        cid = e.open("alice", "bob")  # bob nu are nicio conexiune
        e.send("alice", cid, "cand revii")
        self.assertEqual(run(e.svc.unread_total("bob")), 1)
        self.assertEqual(run(e.svc.history("bob", True, cid, None, 30))["messages"][0]["text"], "cand revii")

    def test_cross_process_publish_never_carries_minor_raw_and_relay_revalidates(self):
        e = Env()
        cid = e.open("mia", "alice")
        e.send("mia", cid, "you badword")
        for _uid, payload in e.published:
            self.assertIsNone(payload["raw"])
            self.assertNotIn("badword", json.dumps(payload))
        # payload de la alt proces: se recitesc varsta/permisiunile din DB, se valideaza forma
        w = FakeWs()
        run(e.hub.add("bob", w))
        run(e.svc.relay_remote("bob", {"t": "dm_x", "cid": "c", "id": "m", "ts": 5, "sid": "alice", "adult": True,
                                       "raw": "you badword", "safe": "you #######"}))
        self.assertEqual(w.sent[0]["message"]["text"], "you badword")  # bob adult, expeditor adult
        e.users["bob"] = MINOR
        run(e.svc.relay_remote("bob", {"t": "dm_x", "cid": "c", "id": "m2", "ts": 6, "sid": "alice", "adult": True,
                                       "raw": "you badword", "safe": "you #######"}))
        self.assertEqual(w.sent[1]["message"]["text"], "you #######")
        n = len(w.sent)
        for bad in ({}, {"cid": 1}, {"cid": "c", "id": "m", "ts": True, "sid": "a", "safe": "x"},
                    {"cid": "c", "id": "m", "ts": 1, "sid": "a", "safe": "x" * 5000},
                    {"cid": "c", "id": "m", "ts": 1, "sid": "a", "safe": "x", "raw": 5}):
            run(e.svc.relay_remote("bob", bad))
        self.assertEqual(len(w.sent), n)  # nimic invalid nu ajunge la client

    def test_remote_adult_flag_false_ignores_raw(self):
        e = Env()
        w = FakeWs()
        run(e.hub.add("bob", w))
        run(e.svc.relay_remote("bob", {"cid": "c", "id": "m", "ts": 1, "sid": "x", "adult": False,
                                       "raw": "TREBUIE IGNORAT", "safe": "safe text"}))
        self.assertEqual(w.sent[0]["message"]["text"], "safe text")

    def test_mark_read_syncs_own_devices(self):
        e = Env()
        cid = e.open("alice", "bob")
        e.send("alice", cid, "x")
        w = FakeWs()
        run(e.hub.add("bob", w))
        run(e.svc.mark_read("bob", cid))
        self.assertEqual(w.sent[-1], {"t": "dm_sync"})


class Reports(unittest.TestCase):
    def test_participant_can_report_other_persons_message_once(self):
        e = Env()
        cid = e.open("alice", "mia")
        m = e.send("alice", cid, "you badword")
        run(e.svc.report("mia", m["id"], "harassment"))
        run(e.svc.report("mia", m["id"], "harassment"))  # idempotent
        self.assertEqual(len(e.store.reports), 1)
        r = e.store.reports[0]
        self.assertEqual((r["reporter_id"], r["target_type"], r["target_id"], r["reason"]), ("mia", "message", m["id"], "harassment"))
        self.assertEqual(r["context"]["sender_id"], "alice")
        self.assertEqual(r["context"]["source"], "private_dm")

    def test_cannot_report_own_message_or_unknown_or_bad_reason_normalised(self):
        e = Env()
        cid = e.open("alice", "bob")
        m = e.send("alice", cid, "hei")
        self.assertEqual(err(lambda: run(e.svc.report("alice", m["id"], "spam"))).code, "invalid")
        self.assertEqual(err(lambda: run(e.svc.report("bob", "dmm_nu_exista", "spam"))).code, "not_found")
        run(e.svc.report("bob", m["id"], "reason-inventat"))
        self.assertEqual(e.store.reports[0]["reason"], "other")


class Hub(unittest.TestCase):
    def test_subscribe_hooks_and_cleanup(self):
        events = []

        async def on_first(uid): events.append(("sub", uid))
        async def on_last(uid): events.append(("unsub", uid))

        hub = dm.DmHub()
        hub.on_first_connection, hub.on_last_disconnect = on_first, on_last
        w1, w2 = FakeWs(), FakeWs()
        run(hub.add("u", w1))
        run(hub.add("u", w2))
        run(hub.remove("u", w1))
        self.assertTrue(hub.has("u"))
        run(hub.remove("u", w2))
        self.assertFalse(hub.has("u"))
        self.assertEqual(events, [("sub", "u"), ("unsub", "u")])


if __name__ == "__main__":
    unittest.main()