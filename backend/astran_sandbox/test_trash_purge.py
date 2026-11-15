"""Teste pentru trash_purge.py (stergere automata dupa 30 de zile).

Ruleaza fara MongoDB si fara pytest:  python -m unittest astran_sandbox.test_trash_purge
"""
import asyncio
import copy
import os
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astran_sandbox import trash_purge as tp

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
HERE = Path(__file__).resolve().parent.parent


def _match(doc, flt):
    for k, cond in flt.items():
        if k == "$or":
            if not any(_match(doc, c) for c in cond):
                return False
            continue
        val = doc.get(k)
        if isinstance(cond, dict) and any(c.startswith("$") for c in cond):
            for op, arg in cond.items():
                if op == "$lte" and not (val is not None and val <= arg):
                    return False
                if op == "$lt" and not (val is not None and val < arg):
                    return False
                if op == "$in" and val not in arg:
                    return False
                if op == "$type" and not (arg == "date" and isinstance(val, datetime)):
                    return False
                if op == "$exists" and (k in doc) != bool(arg):
                    return False
        elif val != cond:
            return False
    return True


class _Res:
    def __init__(self, matched=0, modified=0, deleted=0):
        self.matched_count, self.modified_count, self.deleted_count = matched, modified, deleted


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n):
        return [copy.deepcopy(d) for d in self.docs[:n]]


class _Coll:
    def __init__(self):
        self.docs = []

    async def insert_one(self, d):
        self.docs.append(copy.deepcopy(d))

    def find(self, flt=None, proj=None):
        return _Cursor([d for d in self.docs if _match(d, flt or {})])

    async def find_one(self, flt, proj=None):
        for d in self.docs:
            if _match(d, flt):
                return copy.deepcopy(d)
        return None

    async def create_index(self, *a, **k):
        return None

    def _apply(self, d, upd):
        d.update(upd.get("$set", {}))
        for k in upd.get("$unset", {}):
            d.pop(k, None)

    async def update_one(self, flt, upd):
        for d in self.docs:
            if _match(d, flt):
                self._apply(d, upd)
                return _Res(1, 1)
        return _Res()

    async def update_many(self, flt, upd):
        n = 0
        for d in self.docs:
            if _match(d, flt):
                self._apply(d, upd)
                n += 1
        return _Res(n, n)

    async def find_one_and_update(self, flt, upd, projection=None):
        for d in self.docs:
            if _match(d, flt):
                self._apply(d, upd)
                return copy.deepcopy(d)
        return None

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _match(d, flt):
                del self.docs[i]
                return _Res(deleted=1)
        return _Res()

    async def delete_many(self, flt):
        keep = [d for d in self.docs if not _match(d, flt)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        return _Res(deleted=n)


class FakeDb:
    def __init__(self):
        self._c = {}

    def __getattr__(self, name):
        return self._c.setdefault(name, _Coll())

    def __getitem__(self, name):
        return getattr(self, name)


def run(coro):
    return asyncio.run(coro)


def seed_game(db, gid, *, deleted, deleted_at, owner="u1"):
    run(db.games.insert_one({"game_id": gid, "owner_id": owner, "title": gid, "deleted": deleted, "deleted_at": deleted_at}))
    for coll in tp.GAME_ID_COLLECTIONS:
        run(db[coll].insert_one({"game_id": gid}))
    run(db.security_attempts.insert_one({"scope_id": gid, "user_id": owner, "purpose": "project_delete"}))
    run(db.game_instances.insert_one({"instance_id": f"i_{gid}", "game_id": gid, "status": "open"}))
    run(db.instance_players.insert_one({"instance_id": f"i_{gid}", "user_id": "p1"}))


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()

    def games(self):
        return {d["game_id"] for d in self.db.games.docs}

    def test_expired_game_is_purged_with_all_data(self):
        seed_game(self.db, "old", deleted=True, deleted_at=NOW - timedelta(days=31))
        seed_game(self.db, "keep", deleted=False, deleted_at=None)  # alt joc, neatins
        n = run(tp.purge_expired(self.db, now=NOW, days=30))
        self.assertEqual(n, 1)
        self.assertEqual(self.games(), {"keep"})
        for coll in tp.GAME_ID_COLLECTIONS:
            self.assertEqual([d["game_id"] for d in self.db[coll].docs], ["keep"], coll)
        self.assertEqual([d["scope_id"] for d in self.db.security_attempts.docs], ["keep"])
        self.assertEqual([d["game_id"] for d in self.db.game_instances.docs], ["keep"])
        self.assertEqual([d["instance_id"] for d in self.db.instance_players.docs], ["i_keep"])
        audit = self.db.security_audit_log.docs
        self.assertEqual(len(audit), 1)
        self.assertEqual((audit[0]["action"], audit[0]["game_id"]), ("project_purge", "old"))

    def test_not_expired_is_kept(self):
        seed_game(self.db, "g29", deleted=True, deleted_at=NOW - timedelta(days=29, hours=23))
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 0)
        self.assertEqual(self.games(), {"g29"})
        self.assertEqual(len(self.db.game_scripts.docs), 1)

    def test_boundary_exactly_30_days_is_purged(self):
        seed_game(self.db, "g30", deleted=True, deleted_at=NOW - timedelta(days=30))
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 1)

    def test_restored_game_is_never_purged(self):
        # deleted=False + deleted_at=None = starea dupa recuperare (server.py recover_confirm)
        seed_game(self.db, "restored", deleted=False, deleted_at=None)
        # si un caz "ciudat": data veche ramasa, dar deleted=False
        seed_game(self.db, "weird", deleted=False, deleted_at=NOW - timedelta(days=90))
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 0)
        self.assertEqual(self.games(), {"restored", "weird"})

    def test_undated_trashed_game_gets_clock_not_deleted(self):
        seed_game(self.db, "nodate", deleted=True, deleted_at=None)
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 0)
        self.assertEqual(self.games(), {"nodate"})
        self.assertEqual(self.db.games.docs[0]["deleted_at"], NOW)
        # peste 31 de zile de la pornirea ceasului -> se sterge
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW + timedelta(days=31), days=30)), 1)

    def test_idempotent_second_run(self):
        seed_game(self.db, "old", deleted=True, deleted_at=NOW - timedelta(days=40))
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 1)
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 0)

    def test_claimed_game_is_skipped_and_restore_filter_blocks(self):
        seed_game(self.db, "busy", deleted=True, deleted_at=NOW - timedelta(days=40))
        self.db.games.docs[0]["purging_at"] = NOW - timedelta(seconds=10)  # alta instanta il sterge acum
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 0)
        self.assertEqual(self.games(), {"busy"})
        # filtrul de restaurare din server.py nu potriveste un joc revendicat
        res = run(self.db.games.update_one({"game_id": "busy", "deleted": True, "purging_at": None}, {"$set": {"deleted": False}}))
        self.assertEqual(res.matched_count, 0)
        self.assertTrue(self.db.games.docs[0]["deleted"])

    def test_stale_claim_is_retried_after_crash(self):
        seed_game(self.db, "crashed", deleted=True, deleted_at=NOW - timedelta(days=40))
        self.db.games.docs[0]["purging_at"] = NOW - timedelta(seconds=tp.CLAIM_LEASE_SECONDS + 5)
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 1)
        self.assertEqual(self.games(), set())

    def test_restore_before_claim_wins(self):
        seed_game(self.db, "r", deleted=True, deleted_at=NOW - timedelta(days=40))
        res = run(self.db.games.update_one(
            {"game_id": "r", "deleted": True, "purging_at": None},
            {"$set": {"deleted": False, "deleted_at": None}, "$unset": {"purging_at": "", "purging_token": ""}},
        ))
        self.assertEqual(res.matched_count, 1)
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 0)
        self.assertEqual(self.games(), {"r"})

    def test_failure_in_one_game_does_not_stop_others(self):
        seed_game(self.db, "bad", deleted=True, deleted_at=NOW - timedelta(days=40))
        seed_game(self.db, "good", deleted=True, deleted_at=NOW - timedelta(days=41))
        orig = tp.purge_game_data

        async def flaky(db, gid):
            if gid == "bad":
                raise RuntimeError("boom")
            return await orig(db, gid)

        tp.purge_game_data = flaky
        try:
            self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=30)), 1)
        finally:
            tp.purge_game_data = orig
        self.assertEqual(self.games(), {"bad"})  # "bad" ramane (cu claim) si se reia dupa lease

    def test_retention_cannot_be_zero(self):
        seed_game(self.db, "fresh", deleted=True, deleted_at=NOW - timedelta(hours=1))
        self.assertEqual(run(tp.purge_expired(self.db, now=NOW, days=0)), 0)  # minim 1 zi
        os.environ["TRASH_RETENTION_DAYS"] = "0"
        try:
            self.assertEqual(tp.retention_days(), 1)
            os.environ["TRASH_RETENTION_DAYS"] = "abc"
            self.assertEqual(tp.retention_days(), 30)
        finally:
            os.environ.pop("TRASH_RETENTION_DAYS", None)
        self.assertEqual(tp.retention_days(), 30)

    def test_purge_at_helper(self):
        d = NOW - timedelta(days=1)
        self.assertEqual(tp.purge_at(d, 30), d + timedelta(days=30))
        self.assertEqual(tp.purge_at(d.replace(tzinfo=None), 30), d + timedelta(days=30))
        self.assertIsNone(tp.purge_at(None))


class WiringStaticTests(unittest.TestCase):
    """Verifica ca jobul chiar e pornit si ca restaurarea/WebSocket-ul sunt protejate."""

    @classmethod
    def setUpClass(cls):
        cls.server = (HERE / "server.py").read_text(encoding="utf-8")
        cls.realtime = (HERE / "realtime.py").read_text(encoding="utf-8")

    def test_loop_started_and_cancelled(self):
        self.assertIn("asyncio.create_task(trash_purge.purge_loop(db))", self.server)
        self.assertIn('getattr(app.state, "trash_purge_task", None)', self.server)

    def test_restore_is_conditional_on_not_purging(self):
        body = self.server[self.server.index("async def recover_confirm"):self.server.index("async def webauthn_register_options")]
        self.assertIn('"purging_at": None', body)
        self.assertIn("matched_count", body)

    def test_websocket_rejects_trashed_games(self):
        self.assertIn('{"game_id": game_id, "deleted": True}', self.realtime)

    def test_cascade_covers_every_game_id_collection(self):
        """Orice colectie noua care foloseste `game_id` trebuie adaugata in cascada (sau aici, justificat)."""
        found = set()
        for f in [HERE / "server.py", HERE / "realtime.py", *(HERE / "astran_sandbox").glob("*.py")]:
            if f.name.startswith("test_"):
                continue
            for m in re.finditer(r"db\.([a-z_]+)\.(?:find_one|find|update_one|update_many|insert_one|delete_one|delete_many|count_documents|create_index)\([^\n]*game_id", f.read_text(encoding="utf-8")):
                found.add(m.group(1))
        # security_attempts: sters prin scope_id in purge_game_data; security_audit_log: pastrat intentionat (audit).
        handled = set(tp.GAME_ID_COLLECTIONS) | {"game_instances", "games", "security_attempts", "security_audit_log"}
        self.assertEqual(found - handled, set(), "colectii cu game_id care nu sunt sterse in cascada")


if __name__ == "__main__":
    unittest.main()