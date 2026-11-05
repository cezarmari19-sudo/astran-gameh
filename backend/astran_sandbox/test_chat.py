"""Teste pentru chatul din joc: filtrul (chat_filter.py) si nucleul (chat_core.py).
Rulare din folderul backend:   python -m unittest astran_sandbox.test_chat -v
(stdlib only - ca restul testelor din proiect; accesul la DB/Redis/WebSocket e simulat).

Ce NU acopera: FastAPI/Mongo/Redis reale (vezi test_chat_realtime.py pentru wiring-ul din
realtime.py cu un WebSocket si o baza de date false, si lista din raportul final pentru ce
ramane de verificat in runtime)."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

from astran_sandbox import chat_core as cc
from astran_sandbox import chat_filter as cf


def rules(*items):
    return cf.compile_rules(cf.parse_rules(list(items)))


WORD = {"term": "badword", "action": "mask_word"}
SENT = {"term": "another badword", "action": "mask_sentence"}
EXPR = {"term": "another expression", "action": "mask_word"}


class FilterBehaviour(unittest.TestCase):
    def test_word_only_keeps_rest_of_sentence(self):  # cerinta 4A
        r = cf.filter_text("You are a badword", rules(WORD))
        self.assertEqual(r.text, "You are a #######")
        self.assertTrue(r.changed and not r.sentence_blocked)

    def test_whole_sentence_hidden(self):  # cerinta 4B
        r = cf.filter_text("You are a badword", rules({"term": "badword", "action": "mask_sentence"}))
        self.assertEqual(r.text, "#" * len("You are a badword"))
        self.assertTrue(r.sentence_blocked)

    def test_expression_only(self):
        r = cf.filter_text("this is another expression, ok?", rules(EXPR))
        self.assertEqual(r.text, "this is " + "#" * len("another expression") + ", ok?")

    def test_multiple_terms_apply_each_rule_individually(self):  # nu blocam tot mesajul degeaba
        r = cf.filter_text("badword and another expression stay calm", rules(WORD, EXPR))
        self.assertEqual(r.text, "####### and ################## stay calm")
        self.assertEqual(set(r.matched_rule_ids), {"r_badword", "r_another_expression"})

    def test_sentence_rule_wins_over_word_rules(self):
        r = cf.filter_text("badword and another badword", rules(WORD, SENT))
        self.assertTrue(r.sentence_blocked)
        self.assertEqual(set(r.text), {"#"})

    def test_clean_message_untouched(self):
        r = cf.filter_text("hello there, good game!", rules(WORD, SENT, EXPR))
        self.assertEqual(r.text, "hello there, good game!")
        self.assertFalse(r.changed)

    def test_case_variants(self):
        for t in ("BADWORD", "BaDwOrD", "badWORD"):
            self.assertTrue(cf.filter_text(t, rules(WORD)).changed, t)

    def test_obfuscation_variants_are_caught(self):  # "ocolirea filtrului"
        for t in ("b4dw0rd", "b a d w o r d", "b.a.d.w.o.r.d", "baaadword", "b_a_d_w_o_r_d", "bаdword", "b\u200badword", "bàdword", "B-A-D-W-O-R-D"):
            r = cf.filter_text(f"x {t} y", rules(WORD))
            self.assertTrue(r.changed, t)
            self.assertTrue(r.text.startswith("x ") and r.text.endswith(" y"), (t, r.text))
            self.assertNotIn("bad", r.text.lower().replace("bаd", ""), t)

    def test_no_false_positive_inside_other_words(self):
        for t in ("badwords", "xbadword", "badwordy", "a bad world"):
            self.assertFalse(cf.filter_text(t, rules(WORD)).changed, t)

    def test_contains_mode_matches_inside_words(self):
        r = cf.filter_text("superbadwordy", rules({"term": "badword", "match": "contains"}))
        self.assertTrue(r.changed)

    def test_obfuscation_off_is_exact(self):
        rr = rules({"term": "badword", "obfuscation": False})
        self.assertTrue(cf.filter_text("badword", rr).changed)
        self.assertFalse(cf.filter_text("b4dw0rd", rr).changed)
        self.assertFalse(cf.filter_text("b a d w o r d", rr).changed)

    def test_disabled_rule_is_ignored(self):
        self.assertFalse(cf.filter_text("badword", rules({"term": "badword", "enabled": False})).changed)

    def test_short_terms_do_not_use_separator_tolerance(self):
        rr = rules({"term": "fut"})
        self.assertTrue(cf.filter_text("fut", rr).changed)
        self.assertFalse(cf.filter_text("f u t", rr).changed)  # prea multe false pozitive pe termeni scurti

    def test_unicode_romanian(self):
        rr = rules({"term": "căcat"})
        self.assertTrue(cf.filter_text("ce cacat", rr).changed)
        self.assertTrue(cf.filter_text("ce CĂCAT", rr).changed)

    def test_mask_maps_back_to_original_text(self):
        r = cf.filter_text("Ťhe BÀDWORD rocks", rules(WORD))
        self.assertEqual(r.text, "Ťhe ####### rocks")

    def test_overlapping_matches_merge(self):
        r = cf.filter_text("badword", rules(WORD, {"term": "dword", "match": "contains"}))
        self.assertEqual(r.text, "#######")

    def test_pathological_input_is_fast(self):  # fara backtracking exponential
        adv = rules({"term": "asasasasasasasasasasasas"}, {"term": "a" * 80}, {"term": "s a s a s a s a s a"})
        t0 = time.perf_counter()
        for txt in ("@$" * 100, "a" * 200, "a s " * 60, "$" * 200, "a@" * 100):
            cf.filter_text(txt, adv)
        self.assertLess(time.perf_counter() - t0, 1.0)

    def test_many_rules_still_fast(self):
        big = rules(*[{"term": f"term{i}x", "id": f"r{i}"} for i in range(1500)])
        t0 = time.perf_counter()
        for _ in range(20):
            cf.filter_text("hello this is a normal chat message with nothing bad", big)
        self.assertLess(time.perf_counter() - t0, 2.0)


class RuleValidation(unittest.TestCase):
    def test_defaults(self):
        r = cf.rule_from_dict({"term": "Bad Phrase"})
        self.assertEqual((r.action, r.match, r.kind, r.obfuscation, r.enabled), ("mask_word", "whole_word", "expression", True, True))

    def test_bad_inputs_rejected(self):
        for bad in ({}, {"term": ""}, {"term": "!!!"}, {"term": "x", "action": "delete"}, {"term": "x", "match": "?"},
                    {"term": "x", "enabled": "yes"}, {"term": "x" * 81}, {"term": "x", "id": "bad id!"}, "str"):
            with self.assertRaises(cf.RuleError, msg=repr(bad)):
                cf.rule_from_dict(bad)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(cf.RuleError):
            cf.parse_rules([{"term": "a1", "id": "x"}, {"term": "b1", "id": "x"}])

    def test_shipped_rules_file_is_valid(self):
        data = json.loads(cf.DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
        parsed = cf.parse_rules(data)
        self.assertGreater(len(parsed), 5)
        self.assertEqual({r.action for r in parsed} <= set(cf.ACTIONS), True)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class RuleStoreConfigWithoutCodeChanges(unittest.IsolatedAsyncioTestCase):
    async def test_adding_a_rule_to_the_file_takes_effect_without_code_changes(self):  # cerinta 6
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rules.json"
            path.write_text(json.dumps({"rules": [{"term": "badword"}]}), encoding="utf-8")
            clock = FakeClock()
            store = cf.RuleStore(None, path, ttl=15, clock=clock)
            self.assertTrue(cf.filter_text("newword here", await store.get_compiled()).text == "newword here")
            path.write_text(json.dumps({"rules": [{"term": "badword"}, {"term": "newword", "action": "mask_sentence"}]}), encoding="utf-8")
            clock.t += 16
            out = cf.filter_text("newword here", await store.get_compiled())
            self.assertTrue(out.sentence_blocked)
            self.assertEqual(store.source, "file")

    async def test_invalid_file_keeps_last_good_rules(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rules.json"
            path.write_text(json.dumps({"rules": [{"term": "badword"}]}), encoding="utf-8")
            clock = FakeClock()
            store = cf.RuleStore(None, path, ttl=1, clock=clock)
            await store.get_compiled()
            path.write_text("{ not json", encoding="utf-8")
            clock.t += 5
            self.assertTrue(cf.filter_text("badword", await store.get_compiled()).changed)
            path.write_text(json.dumps({"rules": [{"term": ""}]}), encoding="utf-8")
            clock.t += 5
            self.assertTrue(cf.filter_text("badword", await store.get_compiled()).changed)

    async def test_db_rules_take_priority_and_save_applies_immediately(self):
        class Col:
            def __init__(self): self.doc = None
            async def find_one(self, q, p=None): return self.doc
            async def update_one(self, q, u, upsert=False): self.doc = dict(u["$set"])
        class Db: platform_config = Col()
        db = Db()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "rules.json"
            path.write_text(json.dumps({"rules": [{"term": "fromfile"}]}), encoding="utf-8")
            clock = FakeClock()
            store = cf.RuleStore(db, path, ttl=15, clock=clock)
            self.assertEqual(store.source if await store.get_compiled() is not None else "", "file")
            await store.save_rules(cf.parse_rules([{"term": "fromdb", "action": "mask_sentence"}]))
            self.assertEqual(store.source, "db")
            compiled = await store.get_compiled()
            self.assertTrue(cf.filter_text("fromdb", compiled).sentence_blocked)
            self.assertFalse(cf.filter_text("fromfile", compiled).changed)  # DB inlocuieste fisierul
            # un alt proces vede regula noua dupa expirarea TTL
            other = cf.RuleStore(db, path, ttl=15, clock=clock)
            self.assertTrue(cf.filter_text("fromdb", await other.get_compiled()).sentence_blocked)


class SanitizeAndLimits(unittest.TestCase):
    def test_empty_and_whitespace_only(self):
        for raw in ("", "   ", "\n\t ", "\u200b\u200d", "\u202e", "\x00\x07"):
            self.assertEqual(cc.sanitize_message(raw), (None, "empty"), repr(raw))

    def test_non_string_invalid(self):
        for raw in (None, 5, [], {"a": 1}, True):
            self.assertEqual(cc.sanitize_message(raw), (None, "invalid"))

    def test_too_long_rejected_not_truncated(self):
        self.assertEqual(cc.sanitize_message("a" * (cc.MAX_CHAT_LEN + 1)), (None, "too_long"))
        self.assertEqual(cc.sanitize_message("a" * cc.MAX_CHAT_LEN)[1], None)
        self.assertEqual(cc.sanitize_message("a" * 5000), (None, "too_long"))

    def test_control_and_bidi_chars_removed_and_whitespace_collapsed(self):
        text, err = cc.sanitize_message("  he\u202ello\n\n  wor\x00ld\t! ")
        self.assertIsNone(err)
        self.assertEqual(text, "hello world !")

    def test_emoji_and_diacritics_preserved(self):
        self.assertEqual(cc.sanitize_message("Salut ă î ș 😀")[0], "Salut ă î ș 😀")


class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


class LimiterTests(unittest.TestCase):
    def test_burst_limit_then_recovers(self):
        c = Clock(); lim = cc.ChatLimiter(mono=c)
        for i in range(4):
            self.assertIsNone(lim.check_send(f"m{i}")); c.t += 0.5
        code, retry = lim.check_send("m5")
        self.assertEqual(code, "rate_limited"); self.assertGreater(retry, 0)
        c.t += 7
        self.assertIsNone(lim.check_send("after wait"))

    def test_too_fast_is_blocked(self):
        c = Clock(); lim = cc.ChatLimiter(mono=c)
        self.assertIsNone(lim.check_send("a")); c.t += 0.05
        self.assertEqual(lim.check_send("b")[0], "rate_limited")

    def test_duplicate_blocked_case_insensitive(self):
        c = Clock(); lim = cc.ChatLimiter(mono=c)
        self.assertIsNone(lim.check_send("Hello")); c.t += 1
        self.assertEqual(lim.check_send("hello")[0], "duplicate")
        c.t += 20
        self.assertIsNone(lim.check_send("hello"))

    def test_repeated_violations_lock_the_sender(self):
        c = Clock(); lim = cc.ChatLimiter(mono=c)
        lim.check_send("a")
        verdicts = []
        for i in range(3):
            c.t += 0.01
            verdicts.append(lim.check_send(f"spam{i}"))
        self.assertEqual(verdicts[-1][1], lim.lock_secs)
        c.t += 5
        self.assertEqual(lim.check_send("still locked")[0], "rate_limited")
        c.t += 20
        self.assertIsNone(lim.check_send("free again"))


# ------------------------------------------------------------------------------------------
# Nucleul chatului, cu membri/DB falsi
# ------------------------------------------------------------------------------------------
class FakeWS:
    def __init__(self): self.sent = []; self.fail = False
    async def send_text(self, text):
        if self.fail: raise RuntimeError("closed")
        self.sent.append(json.loads(text))
    def of(self, t): return [m for m in self.sent if m["t"] == t]


class Member:
    def __init__(self, uid, username=None, adult=False, mono=None):
        self.user_id, self.username, self.is_adult = uid, username or uid, adult
        self.ws = FakeWS()
        self.chat_limiter = cc.ChatLimiter(mono=mono or Clock())


class Room:
    def __init__(self, *members, instance_id="inst1", game_id="game1"):
        self.members = {m.user_id: m for m in members}
        self.chat_buffer = deque(maxlen=cc.ROOM_BUFFER)
        self.instance_id, self.game_id = instance_id, game_id


class World:
    """DB fals: varsta/mute per user, blocari, rapoarte."""
    def __init__(self):
        self.age = {}            # uid -> "adult_18"/"under_18" (sursa de adevar, ca users.age_category in DB)
        self.muted = {}          # uid -> epoch
        self.blocks = set()      # (blocker, blocked)
        self.reports = []
        self.published = []
        self.fail_state = False
        self.fail_blocks = False
        self.clock = Clock(); self.clock.t = 1_700_000_000.0

    async def user_state(self, uid):
        if self.fail_state: raise RuntimeError("db down")
        if uid not in self.age: return None
        return {"age_category": self.age[uid], "chat_muted_until": self.muted.get(uid)}

    async def recipient_ages(self, ids): return {i: self.age.get(i) == "adult_18" for i in ids}

    async def blocked_with(self, uid, ids):
        if self.fail_blocks: raise RuntimeError("db down")
        return {o for o in ids if (uid, o) in self.blocks or (o, uid) in self.blocks}

    async def report_sink(self, doc): self.reports.append(doc)
    async def publish(self, iid, payload): self.published.append((iid, payload))


def make_dispatcher(world, rule_items=(WORD, SENT, EXPR), publish=True):
    compiled = rules(*rule_items)
    async def provider(): return compiled
    return cc.ChatDispatcher(
        rules_provider=provider, user_state=world.user_state, recipient_ages=world.recipient_ages,
        blocked_with=world.blocked_with, report_sink=world.report_sink,
        publish=world.publish if publish else None, clock=world.clock,
    )


def setup(*specs):
    w = World(); members = []
    for uid, age in specs:
        w.age[uid] = age
        members.append(Member(uid, adult=(age == "adult_18")))
    return w, make_dispatcher(w), Room(*members), members


ADULT, MINOR = "adult_18", "under_18"


class ChatDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_normal_message_between_two_players(self):
        w, d, room, (a, b) = setup(("a", MINOR), ("b", MINOR))
        await d.handle_chat(room, a, {"t": "chat", "text": "salut!", "cid": "c1"})
        got = b.ws.of("chat")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["user_id"], got[0]["username"], got[0]["text"]), ("a", "a", "salut!"))
        self.assertNotIn("cid", got[0])
        echo = a.ws.of("chat")[0]
        self.assertEqual((echo["cid"], echo["id"]), ("c1", got[0]["id"]))  # acelasi mesaj, expeditorul isi recunoaste ecoul

    async def test_messages_keep_server_order(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        for i in range(3):
            w.clock.t += 1; a.chat_limiter = cc.ChatLimiter(mono=Clock())
            await d.handle_chat(room, a, {"text": f"msg {i}"})
        ts = [m["ts"] for m in b.ws.of("chat")]
        self.assertEqual([m["text"] for m in b.ws.of("chat")], ["msg 0", "msg 1", "msg 2"])
        self.assertEqual(ts, sorted(ts))

    async def test_adult_to_adult_is_unfiltered(self):  # cerinta 2
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        await d.handle_chat(room, a, {"text": "you are a badword"})
        self.assertEqual(b.ws.of("chat")[0]["text"], "you are a badword")
        self.assertEqual(a.ws.of("chat")[0]["text"], "you are a badword")

    async def test_minor_never_receives_unfiltered_text(self):  # cerinta 5
        w, d, room, (a, m) = setup(("a", ADULT), ("m", MINOR))
        await d.handle_chat(room, a, {"text": "you are a badword"})
        self.assertEqual(m.ws.of("chat")[0]["text"], "you are a #######")
        self.assertNotIn("badword", json.dumps(m.ws.sent))

    async def test_minor_sender_is_filtered_for_everyone_including_adults(self):
        w, d, room, (m, a) = setup(("m", MINOR), ("a", ADULT))
        await d.handle_chat(room, m, {"text": "you are a badword"})
        self.assertEqual(a.ws.of("chat")[0]["text"], "you are a #######")
        self.assertEqual(m.ws.of("chat")[0]["text"], "you are a #######")  # si el isi vede mesajul filtrat

    async def test_sentence_rule_for_minor_hides_whole_message(self):
        w, d, room, (a, m) = setup(("a", ADULT), ("m", MINOR))
        await d.handle_chat(room, a, {"text": "say another badword now"})
        self.assertEqual(set(m.ws.of("chat")[0]["text"]), {"#"})
        self.assertEqual(a.ws.of("chat")[0]["text"], "say another badword now")

    async def test_multiple_banned_words_each_rule_applied(self):
        w, d, room, (a, m) = setup(("a", ADULT), ("m", MINOR))
        await d.handle_chat(room, a, {"text": "badword plus another expression, fine"})
        self.assertEqual(m.ws.of("chat")[0]["text"], "####### plus ##################, fine")

    async def test_client_cannot_forge_identity_or_age(self):  # cerinta 5
        w, d, room, (a, b) = setup(("a", MINOR), ("b", ADULT))
        await d.handle_chat(room, a, {"t": "chat", "text": "you are a badword", "user_id": "b", "username": "ADMIN",
                                      "age_category": ADULT, "is_adult": True, "adult": True, "ts": 1, "id": "x", "safe": "raw"})
        got = b.ws.of("chat")[0]
        self.assertEqual((got["user_id"], got["username"]), ("a", "a"))
        self.assertEqual(got["text"], "you are a #######")  # varsta = cea din DB (minor), nu cea pretinsa
        self.assertNotEqual(got["ts"], 1); self.assertNotEqual(got["id"], "x")

    async def test_age_is_rechecked_from_db_not_cached_connect_value(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        w.age["b"] = MINOR   # b a trecut pe sub 18 dupa conectare; Member.is_adult e inca True
        self.assertTrue(b.is_adult)
        await d.handle_chat(room, a, {"text": "badword"})
        self.assertEqual(b.ws.of("chat")[0]["text"], "#######")
        w.age["a"] = MINOR   # si expeditorul e reverificat la fiecare mesaj
        a.chat_limiter = cc.ChatLimiter(mono=Clock())
        w.age["b"] = ADULT
        await d.handle_chat(room, a, {"text": "badword again"})
        self.assertEqual(b.ws.of("chat")[-1]["text"], "####### again")

    async def test_unknown_or_missing_age_is_treated_as_minor(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        w.age["b"] = None
        await d.handle_chat(room, a, {"text": "badword"})
        self.assertEqual(b.ws.of("chat")[0]["text"], "#######")

    async def test_blocked_users_do_not_exchange_messages(self):
        w, d, room, (a, b, c) = setup(("a", ADULT), ("b", ADULT), ("c", ADULT))
        w.blocks.add(("b", "a"))   # b l-a blocat pe a
        await d.handle_chat(room, a, {"text": "hi all"})
        self.assertEqual(len(b.ws.of("chat")), 0)
        self.assertEqual(len(c.ws.of("chat")), 1)
        self.assertEqual(len(a.ws.of("chat")), 1)
        await d.handle_chat(room, b, {"text": "hi from b"})   # si invers (a il are blocat pe b prin blocarea lui b)
        self.assertEqual([m["text"] for m in a.ws.of("chat")], ["hi all"])

    async def test_empty_and_long_messages_rejected_with_error(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        await d.handle_chat(room, a, {"text": "   "})
        await d.handle_chat(room, a, {"text": "x" * 500, "cid": "k"})
        await d.handle_chat(room, a, {"text": 123})
        codes = [(e["code"], e.get("cid")) for e in a.ws.of("chat_error")]
        self.assertEqual(codes, [("empty", None), ("too_long", "k"), ("invalid", None)])
        self.assertEqual(b.ws.of("chat"), [])

    async def test_spam_is_limited_and_not_delivered(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        for i in range(10):
            await d.handle_chat(room, a, {"text": f"spam {i}"})
        self.assertLessEqual(len(b.ws.of("chat")), 4)
        errs = a.ws.of("chat_error")
        self.assertTrue(errs and all(e["code"] == "rate_limited" and e["retry_ms"] > 0 for e in errs))

    async def test_muted_user_cannot_chat(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        w.muted["a"] = w.clock.t + 600
        await d.handle_chat(room, a, {"text": "hello"})
        self.assertEqual(a.ws.of("chat_error")[0]["code"], "muted")
        self.assertEqual(b.ws.of("chat"), [])
        w.muted["a"] = w.clock.t - 1   # mute expirat
        a.chat_limiter = cc.ChatLimiter(mono=Clock())
        await d.handle_chat(room, a, {"text": "hello"})
        self.assertEqual(len(b.ws.of("chat")), 1)

    async def test_db_failure_is_an_error_not_an_unfiltered_delivery(self):  # erori de retea/DB
        w, d, room, (a, m) = setup(("a", ADULT), ("m", MINOR))
        w.fail_state = True
        await d.handle_chat(room, a, {"text": "badword", "cid": "z"})
        self.assertEqual(a.ws.of("chat_error")[0], {"t": "chat_error", "code": "unavailable", "cid": "z"})
        self.assertEqual(m.ws.of("chat"), [])
        w.fail_state = False; w.fail_blocks = True
        a.chat_limiter = cc.ChatLimiter(mono=Clock())
        await d.handle_chat(room, a, {"text": "badword 2"})
        self.assertEqual(m.ws.of("chat"), [])
        self.assertEqual(len(room.chat_buffer), 0)

    async def test_unknown_user_in_db_cannot_chat(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        del w.age["a"]
        await d.handle_chat(room, a, {"text": "hi"})
        self.assertEqual(a.ws.of("chat_error")[0]["code"], "unavailable")

    async def test_dead_socket_of_one_member_does_not_break_delivery(self):
        w, d, room, (a, b, c) = setup(("a", ADULT), ("b", ADULT), ("c", ADULT))
        b.ws.fail = True
        await d.handle_chat(room, a, {"text": "hi"})
        self.assertEqual(len(c.ws.of("chat")), 1)

    async def test_messages_are_not_persisted_anywhere_except_memory_buffer(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        await d.handle_chat(room, a, {"text": "private talk"})
        self.assertEqual(w.reports, [])           # nimic scris in DB
        self.assertEqual(len(room.chat_buffer), 1)  # doar buffer in memorie, limitat
        for i in range(cc.ROOM_BUFFER + 20):
            room.chat_buffer.append(cc.ChatRecord(str(i), i, "a", "a", True, "x", "x"))
        self.assertEqual(len(room.chat_buffer), cc.ROOM_BUFFER)

    async def test_redis_payload_never_carries_raw_text_of_a_minor(self):
        w, d, room, (m, a) = setup(("m", MINOR), ("a", ADULT))
        await d.handle_chat(room, m, {"text": "badword"})
        payload = w.published[0][1]
        self.assertEqual((payload["t"], payload["raw"], payload["safe"]), ("chat_x", None, "#######"))
        a.chat_limiter = cc.ChatLimiter(mono=Clock())
        await d.handle_chat(room, a, {"text": "badword"})
        self.assertEqual(w.published[1][1]["raw"], "badword")


class ChatHistoryAndRemote(unittest.IsolatedAsyncioTestCase):
    async def test_history_on_join_is_per_recipient_and_respects_blocks(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        await d.handle_chat(room, a, {"text": "old badword message"})
        w.age["m"] = MINOR; w.age["x"] = ADULT; w.age["blocker"] = ADULT
        minor = Member("m"); adult = Member("x", adult=True); blocker = Member("blocker", adult=True)
        w.blocks.add(("blocker", "a"))
        for mem in (minor, adult, blocker):
            await d.on_join(room, mem)
        self.assertEqual(minor.ws.of("chat_info")[0]["mode"], MINOR)
        self.assertEqual(adult.ws.of("chat_info")[0]["mode"], ADULT)
        self.assertEqual(minor.ws.of("chat_history")[0]["messages"][0]["text"], "old ####### message")
        self.assertEqual(adult.ws.of("chat_history")[0]["messages"][0]["text"], "old badword message")
        self.assertEqual(blocker.ws.of("chat_history")[0]["messages"], [])
        self.assertNotIn("badword", json.dumps(minor.ws.sent))

    async def test_remote_message_is_delivered_per_recipient(self):
        w, d, room, (a, m) = setup(("a", ADULT), ("m", MINOR))
        payload = {"t": "chat_x", "id": "r1", "ts": 5, "uid": "far", "un": "far", "adult": True, "raw": "badword", "safe": "#######"}
        await d.relay_remote(room, payload)
        self.assertEqual(a.ws.of("chat")[0]["text"], "badword")
        self.assertEqual(m.ws.of("chat")[0]["text"], "#######")
        await d.relay_remote(room, payload)            # duplicat ignorat
        self.assertEqual(len(a.ws.of("chat")), 1)

    async def test_remote_message_from_minor_never_has_raw(self):
        w, d, room, (a,) = setup(("a", ADULT))
        await d.relay_remote(room, {"t": "chat_x", "id": "r2", "ts": 5, "uid": "far", "un": "far", "adult": False, "raw": "badword", "safe": "#######"})
        self.assertEqual(a.ws.of("chat")[0]["text"], "#######")

    async def test_malformed_remote_payloads_are_dropped(self):
        w, d, room, (a,) = setup(("a", ADULT))
        for bad in ({}, {"id": 1}, {"id": "x", "ts": "5", "uid": "u", "un": "n", "safe": "s"},
                    {"id": "x", "ts": 5, "uid": "u", "un": "n", "safe": "s" * 999}):
            await d.relay_remote(room, bad)
        self.assertEqual(a.ws.sent, [])


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_is_stored_once_with_server_side_context(self):
        w, d, room, (a, b) = setup(("a", MINOR), ("b", ADULT))
        await d.handle_chat(room, a, {"text": "you badword"})
        mid = b.ws.of("chat")[0]["id"]
        await d.handle_report(room, b, {"t": "chat_report", "id": mid, "reason": "harassment", "text": "FORGED", "sender_id": "zzz"})
        await d.handle_report(room, b, {"t": "chat_report", "id": mid, "reason": "spam"})   # a doua oara: idempotent
        self.assertEqual(len(w.reports), 1)
        rep = w.reports[0]
        self.assertEqual((rep["target_type"], rep["target_id"], rep["reporter_id"], rep["reason"], rep["status"]),
                         ("message", mid, "b", "harassment", "open"))
        self.assertEqual(rep["context"]["sender_id"], "a")
        self.assertEqual(rep["context"]["text"], "you badword")          # din buffer-ul serverului, nu din client
        self.assertEqual(len(b.ws.of("chat_report_ack")), 2)

    async def test_cannot_report_own_or_unknown_message(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        await d.handle_chat(room, a, {"text": "hello"})
        await d.handle_report(room, a, {"id": a.ws.of("chat")[0]["id"]})
        await d.handle_report(room, b, {"id": "nope"})
        await d.handle_report(room, b, {"id": 5})
        self.assertEqual(w.reports, [])
        self.assertEqual([e["code"] for e in a.ws.of("chat_error") + b.ws.of("chat_error")], ["invalid"] * 3)

    async def test_invalid_reason_becomes_other_and_report_rate_limited(self):
        w, d, room, (a, b) = setup(("a", ADULT), ("b", ADULT))
        ids = []
        for i in range(5):
            w.clock.t += 1; a.chat_limiter = cc.ChatLimiter(mono=Clock())
            await d.handle_chat(room, a, {"text": f"m{i}"})
        ids = [m["id"] for m in b.ws.of("chat")]
        for i in ids:
            await d.handle_report(room, b, {"id": i, "reason": "<script>"})
        self.assertEqual(len(w.reports), b.chat_limiter.reports_per_min)
        self.assertEqual({r["reason"] for r in w.reports}, {"other"})
        self.assertEqual(b.ws.of("chat_error")[-1]["code"], "rate_limited")


if __name__ == "__main__":
    unittest.main()