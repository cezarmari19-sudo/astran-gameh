"""Teste export ZIP GDPR (privacy_export.py + account_export_routes.py). Ruleaza fara MongoDB / FastAPI:
    cd backend && python -m unittest astran_sandbox.test_account_export -v

Reutilizeaza DB-ul simulat si datele de test din test_privacy.py. Handler-ele rutelor se apeleaza direct (ca in
test_privacy.RouteTests); arhivele se scriu intr-un director temporar real (permisiuni, stergere, corupere = reale).
"""
import asyncio
import inspect
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

from astran_sandbox import test_support_fakeweb as _fw

_KEYS = ("fastapi", "fastapi.responses", "pydantic")
_SAVED = {k: sys.modules.get(k) for k in _KEYS}
_fw.install()
from astran_sandbox import privacy as P  # noqa: E402
from astran_sandbox import privacy_export as PX  # noqa: E402
from astran_sandbox import account_export_routes as AR  # noqa: E402
from astran_sandbox import email_verification as emailver  # noqa: E402
from astran_sandbox.test_privacy import (A, B, C, D, NOW, SERVER_PY, FakePsec, _gcu, _vp, new_db, run)  # noqa: E402
from astran_sandbox.test_support_fakedb import FakeDB  # noqa: E402
from fastapi import HTTPException  # noqa: E402

for _k, _v in _SAVED.items():
    if _v is None:
        sys.modules.pop(_k, None)
    else:
        sys.modules[_k] = _v

EXPECTED_FILES = set(PX._JSON_FILES) | {"README.txt"}
FORBIDDEN = ("pw:secret", "password_hash", "argon2$x", "SECRETJOINTOKEN", "tokA1", "tokA2", "pi_123", "provider_ref",
             "_money_ops", "bob@example.com", "carol@example.com", "hi from bob", "bob to carol", "bob says hi",
             "sessions_valid_after", "credential_id", "project_password_hash")


def open_zip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        return {n: z.read(n) for n in z.namelist()}


def js(files: dict, name: str):
    return json.loads(files[name].decode("utf-8"))


async def drain():
    if AR._BG_TASKS:
        await asyncio.gather(*list(AR._BG_TASKS))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="astran_exp_test_")
        self._env = os.environ.get("PRIVACY_EXPORT_DIR")
        os.environ["PRIVACY_EXPORT_DIR"] = self.tmp
        self.db = new_db()
        run(PX.ensure_indexes(self.db))             # in productie: lifespan din server.py (testat static mai jos)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PRIVACY_EXPORT_DIR", None)
        else:
            os.environ["PRIVACY_EXPORT_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_ready(self, uid=A):
        job, created = run(PX.request_job(self.db, uid))
        self.assertTrue(created)
        self.assertEqual(run(PX.run_job(self.db, job["export_id"])), "ready")
        return run(self.db[PX.JOBS_COLLECTION].find_one({"export_id": job["export_id"]}, {"_id": 0}))

    def archive(self, uid=A) -> dict:
        data, _ = run(PX.open_download(self.db, uid))
        return open_zip(data)


# =========================================================================== contine datele corecte
class ContentTests(Base):
    def test_zip_has_all_files_valid_utf8_json(self):
        self.make_ready()
        files = self.archive()
        self.assertEqual(set(files), EXPECTED_FILES)
        for n in PX._JSON_FILES:
            files[n].decode("utf-8")
            self.assertIsInstance(js(files, n), dict, n)
        self.assertIn("astran_game_data_export", PX.ARCHIVE_NAME)

    def test_data_matches_database(self):
        self.make_ready()
        f = self.archive()
        self.assertEqual(js(f, "profile.json")["account"]["user_id"], A)
        self.assertEqual(js(f, "profile.json")["account"]["email"], "alice@example.com")
        self.assertEqual({g["game_id"] for g in js(f, "games.json")["games"]}, {"gA1", "gA2"})
        self.assertEqual({m["model_id"] for m in js(f, "models.json")["studio_models"]}, {"mA"})
        self.assertEqual({t["tx_id"] for t in js(f, "transactions.json")["transactions"]}, {"t1", "t2", "t3"})
        self.assertEqual(js(f, "astrans.json")["balance"], 50)
        self.assertEqual(js(f, "account_settings.json")["preferences"], {"volume": 7})
        self.assertEqual([x["user_id"] for x in js(f, "friends.json")["friends"]], [B])
        self.assertEqual({m["group_id"] for m in js(f, "groups.json")["memberships"]}, {"grp_A1", "grp_A2", "grp_B1"})
        self.assertEqual({m["message_id"] for m in js(f, "messages.json")["group_messages_sent"]}, {"gm1", "gm2", "gm4"})
        self.assertEqual({i["item_id"] for i in js(f, "shop.json")["shop_items_published"]}, {"sh_sold", "sh_unsold"})
        sent = {m["message_id"] for c in js(f, "messages.json")["direct_messages"] for m in c["messages_sent_by_you"]}
        self.assertEqual(sent, {"m1", "m3"})

    def test_no_secrets_and_no_other_users_data(self):
        self.make_ready()
        blob = b"".join(self.archive().values()).decode("utf-8")
        for bad in FORBIDDEN:
            self.assertNotIn(bad, blob, bad)

    def test_dates_are_iso_8601_and_dm_timestamp_converted(self):
        self.make_ready()
        f = self.archive()
        iso = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        self.assertRegex(js(f, "profile.json")["generated_at"], iso)
        self.assertRegex(js(f, "profile.json")["account"]["created_at"], iso)
        for c in js(f, "messages.json")["direct_messages"]:
            for m in c["messages_sent_by_you"]:
                self.assertRegex(m["sent_at"], iso)

    def test_honest_about_missing_categories(self):
        self.make_ready()
        f = self.archive()
        c = js(f, "comments.json")
        self.assertFalse(c["available"])
        self.assertEqual((c["comments"], c["reviews"]), ([], []))
        self.assertFalse(js(f, "consents.json")["terms_acceptance"]["recorded"])
        self.assertIsNone(js(f, "processing_information.json")["recipients"]["value"])

    def test_moderation_hides_reporter_identity(self):
        run(self.db.users.update_one({"user_id": A}, {"$set": {"chat_muted_until": NOW + timedelta(days=1)}}))
        run(self.db.group_members.update_one({"group_id": "grp_B1", "user_id": A}, {"$set": {"muted": True}}))
        self.make_ready()
        m = js(self.archive(), "moderation.json")
        self.assertIsNotNone(m["chat_muted_until"])
        self.assertEqual([r["report_id"] for r in m["reports_about_your_account"]], ["r2"])
        self.assertNotIn(B, json.dumps(m))                       # r2 a fost depus de Bob
        self.assertEqual([x["group_id"] for x in m["group_memberships_with_restrictions"]], ["grp_B1"])

    def test_romanian_characters_roundtrip(self):
        txt = "Ștefan Țăranu — ăâîșț ĂÂÎȘȚ „test” ✓"
        run(self.db.users.update_one({"user_id": A}, {"$set": {"display_name": txt}}))
        run(self.db.group_chat_messages.insert_one({"message_id": "gmRO", "group_id": "grp_A1", "user_id": A, "text": txt}))
        self.make_ready()
        f = self.archive()
        self.assertEqual(js(f, "profile.json")["account"]["display_name"], txt)
        self.assertIn(txt.encode("utf-8"), f["messages.json"])            # scris real UTF-8, nu \\uXXXX
        self.assertIn("Generat la".encode("utf-8"), f["README.txt"])

    def test_readme_explains_every_file(self):
        self.make_ready()
        readme = self.archive()["README.txt"].decode("utf-8")
        for n in PX._JSON_FILES:
            self.assertIn(n, readme)
        self.assertIn("Art. 15", readme)
        self.assertIn("Art. 20", readme)
        self.assertIn("Art. 12(3)", readme)

    def test_account_without_extra_data(self):
        run(self.db.users.insert_one({"user_id": "user_empty", "email": "e@example.com", "username": "empty", "created_at": NOW}))
        self.make_ready("user_empty")
        f = self.archive("user_empty")
        self.assertEqual(js(f, "games.json")["games"], [])
        self.assertEqual(js(f, "transactions.json")["transactions"], [])
        self.assertEqual(js(f, "friends.json")["friends"], [])
        self.assertEqual(js(f, "messages.json")["direct_messages"], [])
        self.assertEqual(js(f, "profile.json")["account"]["username"], "empty")
        self.assertEqual(set(f), EXPECTED_FILES)                          # nu se inventeaza si nu lipsesc fisiere

    def test_account_with_many_games_and_transactions_is_complete(self):
        n_tx, n_games = 12000, 300                                        # peste vechiul plafon de 10000
        for i in range(n_games):
            run(self.db.games.insert_one({"game_id": f"gx{i}", "owner_id": A, "title": f"G{i}"}))
        for i in range(n_tx):
            self.db.transactions_seed = None
            run(self.db.astran_ledger.insert_one({"tx_id": f"tx{i}", "user_id": A, "type": "shop_sale", "amount": 1,
                                                  "timestamp": NOW}))
        self.make_ready()
        f = self.archive()
        self.assertEqual(len(js(f, "transactions.json")["transactions"]), n_tx + 3)
        self.assertEqual(len(js(f, "games.json")["games"]), n_games + 2)

    def test_too_large_fails_instead_of_truncating(self):
        old = P.EXPORT_ROW_LIMIT
        P.EXPORT_ROW_LIMIT = 2
        try:
            job, _ = run(PX.request_job(self.db, A))
            self.assertEqual(run(PX.run_job(self.db, job["export_id"])), "failed")
        finally:
            P.EXPORT_ROW_LIMIT = old
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["error_code"], "TOO_LARGE")
        self.assertEqual(os.listdir(self.tmp), [])


# =========================================================================== joburi / ciclu de viata / securitate fisier
class LifecycleTests(Base):
    def test_concurrent_requests_create_one_job_and_one_archive(self):
        async def go():
            res = await asyncio.gather(*[PX.request_job(self.db, A) for _ in range(8)])
            ids = {j["export_id"] for j, _ in res}
            created = [c for _, c in res]
            await asyncio.gather(*[PX.run_job(self.db, i) for i in ids])
            return ids, created
        ids, created = run(go())
        self.assertEqual(len(ids), 1)
        self.assertEqual(created.count(True), 1)
        self.assertEqual(run(self.db[PX.JOBS_COLLECTION].count_documents({"user_id": A})), 1)
        self.assertEqual(len([f for f in os.listdir(self.tmp) if f.endswith(".zip")]), 1)

    def test_existing_ready_job_is_returned_not_regenerated(self):
        j1 = self.make_ready()
        job, created = run(PX.request_job(self.db, A))
        self.assertFalse(created)
        self.assertEqual(job["export_id"], j1["export_id"])

    def test_archive_is_private_on_disk(self):
        self.make_ready()
        d = PX.storage_dir()
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        for fn in os.listdir(d):
            self.assertEqual(stat.S_IMODE(os.stat(d / fn).st_mode), 0o600)
            self.assertRegex(fn, r"^exp_[0-9a-f]{32}\.zip$")              # nume aleator, fara user_id
            self.assertNotIn(A, fn)

    def test_public_view_exposes_no_internal_fields(self):
        job = self.make_ready()
        v = PX.public_view(job)
        self.assertEqual(v["status"], "ready")
        self.assertGreater(v["size_bytes"], 0)
        self.assertEqual(v["downloads_left"], PX.MAX_DOWNLOADS)
        blob = json.dumps(v)
        for k in ("file_name", "sha256", "active_key", job["file_name"], job["sha256"], A):
            self.assertNotIn(k, blob)

    def test_expired_archive_cannot_be_downloaded_and_file_is_deleted(self):
        job = self.make_ready()
        run(self.db[PX.JOBS_COLLECTION].update_one({"export_id": job["export_id"]},
                                                   {"$set": {"expires_at": NOW - timedelta(seconds=1)}}))
        with self.assertRaises(PX.DownloadError):
            run(PX.open_download(self.db, A))
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["status"], "expired")
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(".zip")], [])

    def test_new_export_possible_after_expiry(self):
        job = self.make_ready()
        run(self.db[PX.JOBS_COLLECTION].update_one({"export_id": job["export_id"]},
                                                   {"$set": {"expires_at": NOW - timedelta(seconds=1)}}))
        new, created = run(PX.request_job(self.db, A))
        self.assertTrue(created)
        self.assertNotEqual(new["export_id"], job["export_id"])

    def test_sweep_expires_and_removes_files_and_orphans(self):
        job = self.make_ready()
        orphan = PX.storage_dir() / ("exp_" + "0" * 32 + ".zip")
        orphan.write_bytes(b"x")
        old = (NOW - timedelta(hours=2)).timestamp()
        os.utime(orphan, (old, old))
        run(self.db[PX.JOBS_COLLECTION].update_one({"export_id": job["export_id"]},
                                                   {"$set": {"expires_at": NOW - timedelta(seconds=5)}}))
        self.assertGreaterEqual(run(PX.sweep(self.db)), 2)
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["status"], "expired")

    def test_download_limit_then_archive_deleted(self):
        self.make_ready()
        for _ in range(PX.MAX_DOWNLOADS):
            self.assertEqual(set(self.archive()), EXPECTED_FILES)
        with self.assertRaises(PX.DownloadError):
            run(PX.open_download(self.db, A))
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(".zip")], [])
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["status"], "expired")

    def test_concurrent_downloads_respect_the_limit(self):
        self.make_ready()

        async def one():
            try:
                await PX.open_download(self.db, A)
                return 1
            except PX.DownloadError:
                return 0
        async def many():
            return await asyncio.gather(*[one() for _ in range(10)])
        ok = sum(run(many()))
        self.assertLessEqual(ok, PX.MAX_DOWNLOADS)
        self.assertGreaterEqual(ok, 1)

    def test_corrupted_archive_is_rejected(self):
        job = self.make_ready()
        path = PX.storage_dir() / job["file_name"]
        raw = path.read_bytes()
        path.write_bytes(raw[: len(raw) // 2])                           # arhiva trunchiata
        with self.assertRaises(PX.DownloadError):
            run(PX.open_download(self.db, A))
        v = PX.public_view(run(PX.latest_job(self.db, A)))
        self.assertEqual((v["status"], v["error_code"]), ("failed", "ARCHIVE_CORRUPT"))
        self.assertFalse(path.exists())
        _, created = run(PX.request_job(self.db, A))                      # utilizatorul poate cere din nou
        self.assertTrue(created)

    def test_bitflip_with_valid_zip_structure_detected_by_hash(self):
        job = self.make_ready()
        path = PX.storage_dir() / job["file_name"]
        other = PX.build_zip({n: {"x": 1} for n in PX._JSON_FILES}, {}, NOW, "someone_else")   # ZIP valid, alt continut
        path.write_bytes(other)
        with self.assertRaises(PX.DownloadError):
            run(PX.open_download(self.db, A))
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["error_code"], "ARCHIVE_CORRUPT")

    def test_missing_file_marks_failed(self):
        job = self.make_ready()
        (PX.storage_dir() / job["file_name"]).unlink()
        with self.assertRaises(PX.DownloadError):
            run(PX.open_download(self.db, A))
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["error_code"], "FILE_MISSING")

    def test_generation_failure_sets_failed_and_allows_retry(self):
        orig = P.build_export

        async def boom(*a, **k):
            raise RuntimeError("db down")
        P.build_export = boom
        try:
            job, _ = run(PX.request_job(self.db, A))
            self.assertEqual(run(PX.run_job(self.db, job["export_id"])), "failed")
        finally:
            P.build_export = orig
        v = PX.public_view(run(PX.latest_job(self.db, A)))
        self.assertEqual((v["status"], v["error_code"]), ("failed", "GENERATION_FAILED"))
        self.assertEqual(os.listdir(self.tmp), [])
        job2, created = run(PX.request_job(self.db, A))
        self.assertTrue(created)
        self.assertEqual(run(PX.run_job(self.db, job2["export_id"])), "ready")

    def test_stalled_processing_job_is_failed_by_sweep(self):
        job, _ = run(PX.request_job(self.db, A))
        run(self.db[PX.JOBS_COLLECTION].update_one({"export_id": job["export_id"]},
                                                   {"$set": {"lease_until": NOW - timedelta(minutes=1)}}))
        run(PX.sweep(self.db))
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, A)))["error_code"], "GENERATION_STALLED")
        self.assertTrue(run(PX.request_job(self.db, A))[1])

    def test_unknown_user_fails_cleanly(self):
        job, _ = run(PX.request_job(self.db, "user_ghost"))
        self.assertEqual(run(PX.run_job(self.db, job["export_id"])), "failed")
        self.assertEqual(PX.public_view(run(PX.latest_job(self.db, "user_ghost")))["error_code"], "USER_NOT_FOUND")

    def test_safe_path_rejects_traversal(self):
        for bad in ("../etc/passwd", "exp_/../x.zip", "/abs/exp_x.zip", "other.zip", None, "exp_" + "a" * 200 + ".zip"):
            self.assertIsNone(PX._safe_path(bad), bad)


class AccountDeletionInteractionTests(Base):
    def test_account_deletion_removes_archives_and_job_metadata(self):
        self.make_ready(A)
        self.make_ready(B)
        self.assertEqual(len([f for f in os.listdir(self.tmp) if f.endswith(".zip")]), 2)
        state, rec = run(P.begin_deletion(self.db, A))
        self.assertEqual(state, "started")
        self.assertEqual(run(P.run_deletion(self.db, A, rec["deletion_id"], first_run=True)), "completed")
        self.assertEqual(run(self.db[PX.JOBS_COLLECTION].count_documents({"user_id": A})), 0)
        self.assertEqual(run(self.db[PX.JOBS_COLLECTION].count_documents({"user_id": B})), 1)   # al lui B ramane
        self.assertEqual(len([f for f in os.listdir(self.tmp) if f.endswith(".zip")]), 1)


# =========================================================================== rute
class RouteTests(Base):
    def setUp(self):
        super().setUp()
        AR._request_limiter = AR.SlidingWindowLimiter(5, 3600)
        AR._download_limiter = AR.SlidingWindowLimiter(20, 3600)
        AR._status_limiter = AR.SlidingWindowLimiter(120, 60)
        run(self.db.security_attempts.delete_many({"purpose": "data_export"}))
        self.psec = FakePsec(self.db)
        router = AR.make_account_export_router(_gcu, self.db, verify_pw=_vp, client_ip=lambda r: "9.9.9.9",
                                               psec=self.psec, emailver=emailver)
        self.r = router.routes

    def h(self, method, path=""):
        return self.r[(method, "/account/data-export" + path)]

    def cur(self, uid):
        return {"user_id": uid, "is_platform_owner": False}

    def request(self, uid=A, password="secret", confirm=True, **kw):
        body = AR.ExportRequestBody(password=password, confirm=confirm, **kw)
        res = run(self.h("POST")(body, object(), current=self.cur(uid), authorization="Bearer tokA1"))
        run(drain())                                                     # asteapta generarea in fundal
        return res

    def code_of(self, fn):
        try:
            return fn().status_code
        except HTTPException as e:
            return e.status_code

    def status(self, uid=A):
        return run(self.h("GET")(current=self.cur(uid)))

    def download(self, uid=A):
        return run(self.h("GET", "/download")(object(), current=self.cur(uid)))

    # --- autentificare / IDOR
    def test_exactly_three_routes_all_require_auth(self):
        self.assertEqual(set(self.r), {("GET", "/account/data-export"), ("POST", "/account/data-export"),
                                       ("GET", "/account/data-export/download")})
        for fn in self.r.values():
            p = inspect.signature(fn).parameters
            self.assertIn("current", p)
            self.assertIs(p["current"].default, _gcu)

    def test_no_route_takes_an_id_from_the_client(self):
        for path in (p for _, p in self.r):
            self.assertNotIn("{", path)
        for fn in self.r.values():
            for name in inspect.signature(fn).parameters:
                self.assertNotIn(name, ("user_id", "export_id", "id"))
        for f in ("user_id", "export_id"):
            self.assertFalse(hasattr(AR.ExportRequestBody(), f))

    def test_get_current_user_dependency_is_the_real_one_in_server(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn("make_account_export_router(get_current_user, db,", src)
        self.assertIn("privacy_export.export_worker(db)", src)
        self.assertIn("privacy_export.ensure_indexes(db)", src)
        self.assertIn("privacy_export_task", src)

    # --- cerere
    def test_request_requires_explicit_confirmation(self):
        self.assertEqual(self.code_of(lambda: self.request(confirm=False)), 400)
        self.assertEqual(run(self.db[PX.JOBS_COLLECTION].count_documents({})), 0)

    def test_request_requires_reauth(self):
        self.assertEqual(self.code_of(lambda: self.request(password=None)), 401)
        self.assertEqual(self.code_of(lambda: self.request(password="wrong")), 401)
        self.assertEqual(run(self.db[PX.JOBS_COLLECTION].count_documents({})), 0)

    def test_wrong_passwords_lock_the_export_after_five_attempts(self):
        for _ in range(5):
            self.assertEqual(self.code_of(lambda: self.request(password="nope")), 401)
        self.assertEqual(self.code_of(lambda: self.request(password="secret")), 429)

    def test_rate_limit_on_requests(self):
        for _ in range(5):
            self.request()
        self.assertEqual(self.code_of(lambda: self.request()), 429)

    def test_full_flow_request_status_download(self):
        st0 = self.status()
        self.assertIsNone(st0["export"])
        self.assertTrue(st0["reauth"]["has_password"])
        self.assertEqual(st0["info"]["legal_response_days"], 30)
        self.assertIn("astran_game_data_export.zip", st0["info"]["archive_name"])
        res = self.request()
        self.assertEqual(res.status_code, 202)
        body = json.loads(res.content) if isinstance(res.content, (str, bytes)) else res.content
        self.assertTrue(body["created"])
        st = self.status()["export"]
        self.assertEqual(st["status"], "ready")
        for k in ("requested_at", "generated_at", "expires_at", "size_bytes"):
            self.assertTrue(st[k], k)
        resp = self.download()
        self.assertEqual(resp.media_type, "application/zip")
        self.assertIn("astran_game_data_export.zip", resp.headers["Content-Disposition"])
        self.assertEqual(resp.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(int(resp.headers["Content-Length"]), len(resp.content))
        self.assertEqual(int(resp.headers["Content-Length"]), st["size_bytes"])
        self.assertEqual(set(open_zip(resp.content)), EXPECTED_FILES)

    def test_second_request_returns_existing_export(self):
        self.request()
        res = self.request()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(run(self.db[PX.JOBS_COLLECTION].count_documents({"user_id": A})), 1)

    def test_simultaneous_http_requests_single_archive(self):
        async def go():
            body = AR.ExportRequestBody(password="secret", confirm=True)
            res = await asyncio.gather(*[self.h("POST")(body, object(), current=self.cur(A), authorization="Bearer tokA1")
                                         for _ in range(5)])
            await asyncio.gather(*list(AR._BG_TASKS))
            return res
        res = run(go())
        self.assertEqual(sorted(r.status_code for r in res), [200, 200, 200, 200, 202])
        self.assertEqual(len([f for f in os.listdir(self.tmp) if f.endswith(".zip")]), 1)

    # --- acces la exportul altcuiva
    def test_user_cannot_see_or_download_someone_elses_export(self):
        self.request(A)
        self.assertIsNone(self.status(B)["export"])
        self.assertEqual(self.code_of(lambda: self.download(B)), 404)
        self.assertEqual(self.code_of(lambda: self.download(C)), 404)
        self.assertEqual(self.status(A)["export"]["status"], "ready")      # al lui A nu a fost consumat de incercari straine
        self.assertEqual(self.status(A)["export"]["downloads_left"], PX.MAX_DOWNLOADS)

    def test_each_user_gets_only_their_own_data(self):
        self.request(A, "secret")
        self.request(B, "bobpw")
        fa, fb = open_zip(self.download(A).content), open_zip(self.download(B).content)
        self.assertEqual(js(fa, "profile.json")["account"]["user_id"], A)
        self.assertEqual(js(fb, "profile.json")["account"]["user_id"], B)
        self.assertNotIn("alice@example.com", b"".join(fb.values()).decode())
        self.assertNotIn("bob@example.com", b"".join(fa.values()).decode())
        self.assertEqual({g["game_id"] for g in js(fb, "games.json")["games"]}, {"gB1"})

    def test_download_without_ready_export_is_404(self):
        self.assertEqual(self.code_of(self.download), 404)

    def test_download_after_expiry_is_404_and_status_expired(self):
        self.request()
        run(self.db[PX.JOBS_COLLECTION].update_one({"user_id": A}, {"$set": {"expires_at": NOW - timedelta(seconds=1)}}))
        self.assertEqual(self.code_of(self.download), 404)
        self.assertEqual(self.status()["export"]["status"], "expired")
        self.assertEqual(self.request().status_code, 202)                 # se poate cere din nou

    def test_failed_state_reported(self):
        orig = P.build_export

        async def boom(*a, **k):
            raise RuntimeError("x")
        P.build_export = boom
        try:
            self.request()
        finally:
            P.build_export = orig
        st = self.status()["export"]
        self.assertEqual((st["status"], st["error_code"]), ("failed", "GENERATION_FAILED"))

    def test_google_account_reauth_with_email_code(self):
        run(self.db.user_sessions.insert_one({"session_token": "tokD", "user_id": D, "created_at": NOW}))
        self.assertEqual(self.code_of(lambda: self.request(D, password=None)), 401)

    # --- audit
    def test_audit_log_has_actions_but_never_content(self):
        self.request()
        self.download()
        self.code_of(lambda: self.download(B))
        rows = [d for n, d in self.db.all_docs() if n == "security_audit_log" and d.get("action", "").startswith("data_export")]
        self.assertTrue(rows)
        blob = json.dumps(rows, default=str)
        for secret in ("secret hello", "alice@example.com", "hello group", "Alice game", "pw:secret"):
            self.assertNotIn(secret, blob)
        self.assertIn("downloaded", blob)
        self.assertIn("download_unavailable", blob)

    def test_job_metadata_has_no_exported_content(self):
        self.request()
        blob = json.dumps([d for n, d in self.db.all_docs() if n == PX.JOBS_COLLECTION], default=str)
        for secret in ("secret hello", "alice@example.com", "hello group", "Alice game"):
            self.assertNotIn(secret, blob)


if __name__ == "__main__":
    unittest.main()