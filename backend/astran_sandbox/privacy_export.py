"""GDPR Art. 15 / 20: export de date ca arhiva ZIP, generata asincron (job), cu stari si stocare privata temporara.

Modul FARA dependente de FastAPI / MongoDB (db e injectat, ca in privacy.py / money.py) -> testabil cu DB simulat.
Rutele sunt in account_export_routes.py. Colectarea datelor reutilizeaza `privacy.build_export` (aceeasi whitelist de
campuri, aceleasi excluderi: parole/hash-uri/tokenuri/secrete/datele altor utilizatori); aici doar se completeaza
categoriile lipsa, se imparte in fisiere, se arhiveaza si se gestioneaza ciclul de viata al arhivei.

CICLUL DE VIATA AL UNUI JOB (colectia `privacy_export_jobs`)
    processing --> ready --> expired        (TTL scurt SAU descarcari epuizate; fisierul se sterge)
              \\--> failed                   (eroare la generare / arhiva corupta / fisier pierdut)
  * `active_key` (= user_id) are index UNIC si exista doar cat jobul e `processing` sau `ready`: un utilizator nu poate
    avea doua arhive simultan si nu se genereaza doua arhive identice (cererea a doua intoarce jobul existent).
  * Arhiva se scrie intr-un director privat (0700), fisier 0600, nume aleator; atomic (tmp + rename). Metadatele
    jobului NU contin continutul exportat, iar calea fisierului nu iese niciodata din backend.
  * Descarcarea: doar proprietarul (user_id din sesiune; nu exista niciun id in URL), maxim MAX_DOWNLOADS descarcari in
    perioada de valabilitate; integritatea (SHA-256 + ZIP testzip) se verifica la fiecare descarcare.
  * Metadatele (fara date personale) se pastreaza JOB_METADATA_RETENTION_DAYS ca dovada a respectarii termenului din
    Art. 12(3) GDPR (cererea + data raspunsului).

NOTA DEPLOY: arhiva sta pe discul instantei care a generat-o. Cu mai multe instante/containere efemere, pune
PRIVACY_EXPORT_DIR pe un volum comun sau ruleaza cu o singura instanta; daca fisierul lipseste, jobul devine `failed`
(FILE_MISSING) si utilizatorul poate cere din nou - nu se serveste niciodata continutul altcuiva.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import secrets
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import privacy

log = logging.getLogger("astran.privacy.export")

JOBS_COLLECTION = "privacy_export_jobs"
ARCHIVE_NAME = "astran_game_data_export.zip"
MAX_DOWNLOADS = 3
JOB_LEASE_SECONDS = 600
SWEEP_INTERVAL_SECONDS = 60
JOB_METADATA_RETENTION_DAYS = 365
LEGAL_RESPONSE_DAYS = 30                 # Art. 12(3): "fara intarzieri nejustificate si in termen de o luna"
LEGAL_EXTENSION_MONTHS = 2               # prelungibil cu inca 2 luni (cereri complexe / numeroase), cu notificare

# Starea pe care o vede clientul (mapare stricta; nu se expun campuri interne).
STATUS_PROCESSING, STATUS_READY, STATUS_EXPIRED, STATUS_FAILED = "processing", "ready", "expired", "failed"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def archive_ttl_seconds() -> int:
    return _env_int("PRIVACY_EXPORT_ARCHIVE_TTL_SECONDS", 86400, minimum=60)      # 24 h


def archive_max_bytes() -> int:
    return _env_int("PRIVACY_EXPORT_MAX_BYTES", 200 * 1024 * 1024, minimum=1024)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


def _iso(dt) -> Optional[str]:
    return _aware(dt).isoformat() if isinstance(dt, datetime) else None


# =========================================================================== stocare privata
def storage_dir() -> Path:
    base = os.environ.get("PRIVACY_EXPORT_DIR") or os.path.join(tempfile.gettempdir(), "astran_privacy_exports")
    p = Path(base)
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _safe_path(name: Optional[str]) -> Optional[Path]:
    """Numele de fisier vine din DB, dar nu se are incredere in el: doar nume simple generate de noi."""
    if not isinstance(name, str) or not name.startswith("exp_") or not name.endswith(".zip"):
        return None
    if os.sep in name or (os.altsep and os.altsep in name) or ".." in name or len(name) > 80:
        return None
    return storage_dir() / name


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _delete_file(name: Optional[str]) -> None:
    p = _safe_path(name)
    if p is not None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("privacy_export: nu s-a putut sterge o arhiva temporara", exc_info=True)


# =========================================================================== indexuri
async def ensure_indexes(db) -> None:
    c = db[JOBS_COLLECTION]
    await c.create_index("export_id", unique=True)
    # unic doar pentru jobul activ (`active_key` lipseste din documentele terminale -> indexul il ignora)
    await c.create_index("active_key", unique=True, partialFilterExpression={"active_key": {"$type": "string"}})
    await c.create_index([("user_id", 1), ("requested_at", -1)])
    await c.create_index([("status", 1), ("expires_at", 1)])
    await c.create_index("purge_at", expireAfterSeconds=0)


# =========================================================================== continut
class ExportTooLarge(Exception):
    pass


_README_FILES = {
    "profile.json": ("Contul si profilul tau: id, nume de utilizator, nume afisat, email, categoria de varsta, limba, data "
                     "inregistrarii, avatarul si personajul.", "Account and profile data.", "provided"),
    "account_settings.json": ("Setarile si preferintele contului, metoda de autentificare, credentialele biometrice "
                              "inregistrate (doar data/numele) si evenimentele de securitate ale contului (actiune, "
                              "rezultat, IP, data).", "Settings, preferences, sign-in method, security events.", "mixed"),
    "friends.json": ("Prietenii, cererile de prietenie trimise/primite si utilizatorii blocati de tine (doar id si nume "
                     "de utilizator ale celorlalti).", "Friends, friend requests, blocked users.", "provided"),
    "groups.json": ("Grupurile create de tine (fara tokenul secret de aderare), apartenentele tale si rolul/statusul "
                    "tau (inclusiv mute/ban) in grupuri.", "Groups you own or belong to.", "provided"),
    "messages.json": ("MESAJELE TRIMISE DE TINE: mesaje directe (per conversatie) si mesaje in chat-ul grupurilor, plus "
                      "rapoartele depuse de tine. Mesajele primite de la alti utilizatori NU sunt incluse (datele lor).",
                      "Messages you sent (DMs and group chat) and reports you filed.", "provided"),
    "games.json": ("Jocurile create de tine (setari, scripturi/fisiere, resurse), colaborari/testari si jocurile "
                   "jucate recent.", "Games you created, collaborations, recently played.", "provided"),
    "models.json": ("Modelele 3D din Studio si articolele de imbracaminte create de tine.",
                    "3D models and clothing items you created.", "provided"),
    "shop.json": ("Articolele publicate de tine in Shop si achizitiile tale (Shop si imbracaminte).",
                  "Shop items you published and your purchases.", "provided"),
    "transactions.json": ("Istoricul tranzactiilor in Astrans (achizitii, transferuri, taxe, rambursari) ale contului tau.",
                          "Your Astrans transaction ledger.", "provided"),
    "astrans.json": ("Soldul curent de Astrans la data exportului.", "Current Astrans balance.", "generated"),
    "comments.json": ("Comentarii si recenzii. Platforma nu are (inca) o functie de comentarii/recenzii: fisierul spune "
                      "explicit acest lucru; nu s-au inventat date.", "Comments/reviews: not a platform feature.",
                      "generated"),
    "consents.json": ("Datele de consimtamant/varsta/verificare stocate: categoria de varsta, blocarea ei, "
                      "verificarea emailului. Platforma NU stocheaza momentul acceptarii Termenilor (campul e declarat "
                      "lipsa, nu inventat).", "Stored consent/age/verification data.", "provided"),
    "moderation.json": ("Sanctiunile si masurile de moderare asociate contului tau (mute chat, mute/ban in grupuri) si "
                        "starea rapoartelor depuse la adresa ta, FARA identitatea celui care a raportat.",
                        "Moderation measures related to your account.", "generated"),
    "other_personal_data.json": ("Alte date personale gasite la audit (ex: setari ale personajului, metadate tehnice "
                                 "ale contului) si lista a ce NU este inclus si de ce.",
                                 "Other personal data + list of what is excluded and why.", "mixed"),
    "processing_information.json": ("Informatiile suplimentare cerute de Art. 15 GDPR: scopuri, categorii de date, "
                                    "destinatari, perioade de stocare, drepturile tale.",
                                    "Art. 15 information about the processing.", "generated"),
}
_JSON_FILES = tuple(_README_FILES)


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, default=privacy.json_default, ensure_ascii=False, indent=2).encode("utf-8")


def _check_not_truncated(obj: Any, cap: int) -> None:
    """Un set de randuri la plafon inseamna ca exportul ar putea fi incomplet -> esec explicit, nu trunchiere tacuta."""
    if isinstance(obj, list):
        if len(obj) >= cap:
            raise ExportTooLarge()
        for x in obj:
            _check_not_truncated(x, cap)
    elif isinstance(obj, dict):
        for v in obj.values():
            _check_not_truncated(v, cap)


def _ms_to_iso(ms) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


async def _moderation(db, uid: str, user: dict, groups: dict) -> dict:
    """Masuri de moderare asupra contului. Rapoartele la adresa ta: doar categorie/stare/data, FARA reporter_id/detalii
    (datele personale ale altei persoane - Art. 15(4))."""
    targeting = await db.reports.find({"target_type": "user", "target_id": uid},
                                      {"_id": 0, "report_id": 1, "reason": 1, "status": 1, "created_at": 1}
                                      ).to_list(privacy.EXPORT_ROW_LIMIT)
    return {
        "chat_muted_until": user.get("chat_muted_until"),
        "group_memberships_with_restrictions": [
            {k: m.get(k) for k in ("group_id", "group_name", "role", "status", "muted", "joined_at")}
            for m in groups.get("memberships", []) if m.get("status") == "banned" or m.get("muted")],
        "reports_about_your_account": [
            {"report_id": r.get("report_id"), "category": r.get("reason"), "status": r.get("status"),
             "created_at": r.get("created_at")} for r in targeting],
        "note": "Sunt incluse doar masurile stocate pe contul tau. Identitatea persoanelor care au raportat nu se divulga.",
    }


def _processing_information() -> dict:
    ctrl = os.environ.get("PRIVACY_CONTROLLER_NAME") or None
    mail = os.environ.get("PRIVACY_CONTACT_EMAIL") or None
    return {
        "legal_basis_of_this_export": "GDPR Art. 15 (dreptul de acces) si Art. 20 (portabilitate)",
        "controller": {"name": ctrl, "privacy_contact_email": mail,
                       "note": None if (ctrl and mail) else
                       "Datele operatorului nu sunt configurate pe server (PRIVACY_CONTROLLER_NAME / PRIVACY_CONTACT_EMAIL); "
                       "vezi Politica de confidentialitate. De completat de operator."},
        "purposes_and_categories": {
            "see": "Politica de confidentialitate din aplicatie (sectiunile 'Date pe care le putem colecta' si 'Cum folosim datele').",
            "categories_in_this_archive": [f for f in _JSON_FILES if f != "processing_information.json"],
        },
        "recipients": {"value": None, "note": "Lista destinatarilor/categoriilor de destinatari trebuie completata de operator "
                                              "din Politica de confidentialitate (furnizori de plata, email, hosting)."},
        "retention": privacy.retention_policy(),
        "your_rights": ["acces (Art. 15)", "rectificare (Art. 16)", "stergere (Art. 17) - din Setari > Privacy & Personal Data",
                        "restrictionare (Art. 18)", "portabilitate (Art. 20)", "opozitie (Art. 21)",
                        "retragerea consimtamantului, oricand", "plangere la autoritatea de supraveghere "
                        "(in Romania: ANSPDCP, www.dataprotection.ro)"],
        "response_deadline": {"days": LEGAL_RESPONSE_DAYS, "extension_months": LEGAL_EXTENSION_MONTHS,
                              "basis": "GDPR Art. 12(3)"},
        "automated_decision_making": {"value": None, "note": "De confirmat de operator (Art. 15(1)(h))."},
    }


async def build_files(db, user_id: str) -> tuple[dict[str, Any], dict]:
    """Intoarce ({nume_fisier: obiect JSON}, rezumat cu numarul de inregistrari pe fisier). Ridica LookupError /
    ExportTooLarge."""
    d = await privacy.build_export(db, user_id)            # LookupError daca utilizatorul nu exista
    _check_not_truncated(d, privacy.EXPORT_ROW_LIMIT)
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise LookupError("user not found")
    generated = d["generated_at"]
    acct = dict(d["account"])
    balance = acct.pop("astrans_balance", None)
    stamp = {"export_format_version": privacy.EXPORT_FORMAT_VERSION, "generated_at": generated}

    dms = d["direct_messages"]
    for c in dms:
        for m in c["messages_sent_by_you"]:
            m["sent_at"] = _ms_to_iso(m.get("sent_at_ms"))
    g = d["groups"]
    files: dict[str, Any] = {
        "profile.json": {**stamp, "account": acct, "avatar": d["avatar"]},
        "account_settings.json": {**stamp, "preferences": d["preferences"], "security": d["security"]},
        "friends.json": {**stamp, **d["social"]},
        "groups.json": {**stamp, "owned": g["owned"], "memberships": g["memberships"]},
        "messages.json": {**stamp, "direct_messages": dms, "group_messages_sent": g["messages_sent"],
                          "reports_you_submitted": d["reports_submitted"]},
        "games.json": {**stamp, "games": d["games"], "collaborations": d["collaborations"],
                       "recently_played": d["recently_played"]},
        "models.json": {**stamp, "studio_models": d["studio_models"], "clothes_items_created": d["clothes_items_created"]},
        "shop.json": {**stamp, "shop_items_published": d["shop_items_published"], "purchases": d["purchases"]},
        "transactions.json": {**stamp, "currency": "ASTRANS", "transactions": d["transactions"]},
        "astrans.json": {**stamp, "currency": "ASTRANS", "balance": balance, "balance_as_of": generated},
        "comments.json": {**stamp, "available": False, "comments": [], "reviews": [],
                          "note": "Platforma nu are functie de comentarii sau recenzii; nu exista date de exportat."},
        "consents.json": {**stamp, "age_category": acct.get("age_category"), "age_category_locked": bool(u.get("age_locked")),
                          "email_verified": acct.get("email_verified"), "email_verified_at": acct.get("email_verified_at"),
                          "terms_acceptance": {"recorded": False, "note": "Platforma nu stocheaza momentul acceptarii "
                                               "Termenilor/Politicii; nu se inventeaza o data."}},
        "moderation.json": {**stamp, **await _moderation(db, user_id, u, g)},
        "other_personal_data.json": {**stamp, "has_password": acct.get("has_password"),
                                     "sign_in_method": acct.get("sign_in_method"),
                                     "platform_roles": {"is_platform_owner": acct.get("is_platform_owner"),
                                                        "is_platform_admin": acct.get("is_platform_admin")},
                                     "not_included": d["not_included"], "retention_policy": d["retention_policy"]},
        "processing_information.json": {**stamp, **_processing_information()},
    }
    counts = {}
    for name, obj in files.items():
        n = 0
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, list):
                    n += len(v)
        counts[name] = n
    return files, counts


def _readme(counts: dict, generated: datetime, user_id: str) -> str:
    L = ["ASTRAN GAME - COPIE A DATELOR TALE PERSONALE / COPY OF YOUR PERSONAL DATA",
         "=" * 72, "",
         f"Generat la / Generated at: {_iso(generated)} (UTC, ISO 8601)",
         f"Cont / Account ID: {user_id}",
         f"Format: arhiva ZIP; fisiere JSON valide, UTF-8; date si ore ISO 8601 / JSON, UTF-8, ISO 8601 dates.", "",
         "TEMEI / LEGAL BASIS",
         "- Art. 15 GDPR (acces): toate fisierele + processing_information.json (scopuri, destinatari, retentie, drepturi).",
         "- Art. 20 GDPR (portabilitate): datele FURNIZATE DE TINE si prelucrate automat (marcate 'provided' / 'mixed' mai jos),",
         "  intr-un format structurat, uzual, care poate fi citit automat.", "",
         "FISIERE / FILES", "-" * 72]
    for name, (ro, en, kind) in _README_FILES.items():
        L.append(f"{name}  [{kind}]  ({counts.get(name, 0)} inregistrari / records in lists)")
        L.append(f"  RO: {ro}")
        L.append(f"  EN: {en}")
        L.append("")
    L += ["NU SUNT INCLUSE / NOT INCLUDED", "-" * 72,
          "RO: parole si hash-uri, tokenuri de sesiune/autentificare, coduri de verificare, chei/secrete, datele personale ale",
          "    altor utilizatori (inclusiv mesajele primite de la altii), date interne de securitate, identificatori ai",
          "    furnizorilor de plata, jurnale de infrastructura si copii de siguranta (vezi retentia in processing_information.json).",
          "EN: passwords/hashes, session/auth tokens, verification codes, keys/secrets, other users' personal data (incl.",
          "    messages received from others), internal security data, payment-provider identifiers, infrastructure logs/backups.", "",
          "TERMEN / DEADLINE",
          f"RO: raspundem in cel mult o luna ({LEGAL_RESPONSE_DAYS} zile), prelungibil conform Art. 12(3) GDPR.",
          f"EN: we respond within one month ({LEGAL_RESPONSE_DAYS} days), extendable under Art. 12(3) GDPR.", "",
          "Aceasta arhiva contine date personale: pastreaz-o in siguranta si nu o trimite altora.",
          "This archive contains personal data: keep it safe and do not share it.", ""]
    return "\n".join(L)


def build_zip(files: dict[str, Any], counts: dict, generated: datetime, user_id: str) -> bytes:
    buf = io.BytesIO()
    ts = (generated.year, generated.month, generated.day, generated.hour, generated.minute, generated.second)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in _JSON_FILES:
            zi = zipfile.ZipInfo(name, date_time=ts)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            z.writestr(zi, _dump(files[name]))
        zi = zipfile.ZipInfo("README.txt", date_time=ts)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o600 << 16
        z.writestr(zi, _readme(counts, generated, user_id).encode("utf-8"))
    return buf.getvalue()


def verify_zip_bytes(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.testzip() is not None:
                return False
            names = set(z.namelist())
            return "README.txt" in names and all(n in names for n in _JSON_FILES)
    except (zipfile.BadZipFile, OSError, ValueError):
        return False


# =========================================================================== joburi
LogEvent = Optional[Callable[..., Awaitable[None]]]


def public_view(job: Optional[dict], now: Optional[datetime] = None) -> Optional[dict]:
    """Singurele campuri expuse clientului. Fara cai de fisier, hash-uri sau alte campuri interne."""
    if not job:
        return None
    now = now or _now()
    status = job.get("status")
    exp = _aware(job.get("expires_at"))
    if status == STATUS_READY and isinstance(exp, datetime) and exp <= now:
        status = STATUS_EXPIRED
    return {
        "export_id": job.get("export_id"),
        "status": status,
        "requested_at": _iso(job.get("requested_at")),
        "generated_at": _iso(job.get("generated_at")),
        "expires_at": _iso(job.get("expires_at")),
        "size_bytes": job.get("size_bytes") if status == STATUS_READY else None,
        "downloads_left": max(0, MAX_DOWNLOADS - int(job.get("download_count") or 0)) if status == STATUS_READY else None,
        "error_code": job.get("error_code") if status == STATUS_FAILED else None,
        "legal_deadline_at": _iso(_aware(job.get("requested_at")) + timedelta(days=LEGAL_RESPONSE_DAYS))
        if isinstance(job.get("requested_at"), datetime) else None,
    }


async def _finish(db, export_id: str, fields: dict, *, unset_active: bool = True) -> None:
    upd: dict[str, Any] = {"$set": {**fields, "updated_at": _now()}}
    if unset_active:
        upd["$unset"] = {"active_key": ""}
    await db[JOBS_COLLECTION].update_one({"export_id": export_id}, upd)


async def _expire_job(db, job: dict, status: str = STATUS_EXPIRED, error_code: Optional[str] = None) -> None:
    _delete_file(job.get("file_name"))
    f: dict[str, Any] = {"status": status, "file_name": None, "expired_at": _now()}
    if error_code:
        f["error_code"] = error_code
    await _finish(db, job["export_id"], f)


async def latest_job(db, user_id: str) -> Optional[dict]:
    """Cel mai recent job al utilizatorului; jobul gata dar expirat se inchide (si fisierul se sterge) la citire."""
    cur = db[JOBS_COLLECTION].find({"user_id": user_id}, {"_id": 0}).sort("requested_at", -1).limit(1)
    rows = await cur.to_list(1)
    job = rows[0] if rows else None
    if job and job.get("status") == STATUS_READY:
        exp = _aware(job.get("expires_at"))
        if isinstance(exp, datetime) and exp <= _now():
            await _expire_job(db, job)
            job = await db[JOBS_COLLECTION].find_one({"export_id": job["export_id"]}, {"_id": 0})
    return job


async def request_job(db, user_id: str) -> tuple[dict, bool]:
    """Creeaza un job NOU sau intoarce jobul activ existent. Intoarce (job, created). Atomic prin indexul unic pe
    `active_key`: doua cereri simultane -> un singur job (si o singura arhiva)."""
    for _ in range(2):
        await latest_job(db, user_id)                       # inchide un `ready` expirat, ca sa se poata cere din nou
        now = _now()
        job = {"export_id": "exp_" + secrets.token_hex(12), "user_id": user_id, "active_key": user_id,
               "status": STATUS_PROCESSING, "requested_at": now, "updated_at": now, "generated_at": None,
               "expires_at": None, "size_bytes": None, "sha256": None, "file_name": None, "download_count": 0,
               "error_code": None, "lease_until": now + timedelta(seconds=JOB_LEASE_SECONDS),
               "purge_at": now + timedelta(days=JOB_METADATA_RETENTION_DAYS)}
        try:
            await db[JOBS_COLLECTION].insert_one(dict(job))
            return job, True
        except Exception as e:  # noqa: BLE001 - DuplicateKeyError (pymongo / fake) -> exista deja un job activ
            if type(e).__name__ != "DuplicateKeyError":
                raise
            existing = await db[JOBS_COLLECTION].find_one({"active_key": user_id}, {"_id": 0})
            if existing:
                return existing, False
    raise RuntimeError("could not create or find export job")


async def run_job(db, export_id: str, log_event: LogEvent = None) -> str:
    """Genereaza arhiva pentru job. Intoarce starea finala. Nu logheaza si nu stocheaza continutul datelor."""
    job = await db[JOBS_COLLECTION].find_one({"export_id": export_id, "status": STATUS_PROCESSING}, {"_id": 0})
    if not job:
        return "skipped"
    uid = job["user_id"]

    async def audit(result: str, detail: str) -> None:
        if log_event:
            try:
                await log_event(action="data_export_generate", result=result, user_id=uid, detail=detail)
            except Exception:  # noqa: BLE001 - jurnalul nu trebuie sa strice exportul
                log.warning("privacy_export: audit log esuat", exc_info=True)

    name: Optional[str] = None
    try:
        files, counts = await build_files(db, uid)
        generated = _now()
        data = await asyncio.to_thread(build_zip, files, counts, generated, uid)
        if len(data) > archive_max_bytes():
            raise ExportTooLarge()
        if not verify_zip_bytes(data):
            raise RuntimeError("zip verification failed")
        name = f"exp_{secrets.token_hex(16)}.zip"
        await asyncio.to_thread(_write_private, storage_dir() / name, data)
        await _finish(db, export_id, {
            "status": STATUS_READY, "generated_at": generated, "expires_at": generated + timedelta(seconds=archive_ttl_seconds()),
            "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "file_name": name, "error_code": None},
            unset_active=False)
        await audit("success", "archive_ready")
        return STATUS_READY
    except LookupError:
        code = "USER_NOT_FOUND"
    except ExportTooLarge:
        code = "TOO_LARGE"
    except Exception:  # noqa: BLE001
        log.error("privacy_export: generarea a esuat (export_id=%s)", export_id, exc_info=True)   # fara date personale
        code = "GENERATION_FAILED"
    _delete_file(name)
    await _finish(db, export_id, {"status": STATUS_FAILED, "error_code": code, "file_name": None})
    await audit("failed", code)
    return STATUS_FAILED


class DownloadError(Exception):
    """Orice motiv (alt utilizator, lipsa, expirat, epuizat, corupt) -> acelasi raspuns 404 in ruta."""


async def open_download(db, user_id: str) -> tuple[bytes, dict]:
    """Rezerva atomic o descarcare pentru ULTIMUL export gata al utilizatorului si intoarce (bytes, job). Nu exista
    parametru de tip id: proprietarul e mereu `user_id` din sesiune. Rezervarea inainte de citire face ca limita de
    descarcari sa fie respectata si la cereri simultane."""
    now = _now()
    job = await db[JOBS_COLLECTION].find_one_and_update(
        {"user_id": user_id, "status": STATUS_READY, "expires_at": {"$gt": now},
         "download_count": {"$lt": MAX_DOWNLOADS}},
        {"$inc": {"download_count": 1}, "$set": {"last_download_at": now}}, projection={"_id": 0})
    if not job:
        raise DownloadError("not available")
    path = _safe_path(job.get("file_name"))
    data: Optional[bytes] = None
    if path is not None:
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            data = None
    if data is None:
        await _expire_job(db, job, STATUS_FAILED, "FILE_MISSING")
        raise DownloadError("file missing")
    if hashlib.sha256(data).hexdigest() != job.get("sha256") or not verify_zip_bytes(data):
        await _expire_job(db, job, STATUS_FAILED, "ARCHIVE_CORRUPT")
        raise DownloadError("corrupt")
    if int(job.get("download_count") or 0) + 1 >= MAX_DOWNLOADS:
        await _expire_job(db, job)                          # ultima descarcare permisa: arhiva se sterge
    return data, job


async def purge_user(db, user_id: str) -> None:
    """La stergerea contului: se sterg arhivele de export (fisierele de pe disc) si metadatele joburilor."""
    for job in await db[JOBS_COLLECTION].find({"user_id": user_id}, {"_id": 0, "file_name": 1}).to_list(10000):
        _delete_file(job.get("file_name"))
    await db[JOBS_COLLECTION].delete_many({"user_id": user_id})


# =========================================================================== intretinere
async def sweep(db, now: Optional[datetime] = None) -> int:
    """Expira arhivele ajunse la termen (si le sterge fisierul), marcheaza `failed` joburile blocate si sterge fisierele
    orfane. Idempotent; se apeleaza periodic din worker."""
    now = now or _now()
    n = 0
    for job in await db[JOBS_COLLECTION].find({"status": STATUS_READY, "expires_at": {"$lte": now}}, {"_id": 0}).to_list(1000):
        await _expire_job(db, job)
        n += 1
    for job in await db[JOBS_COLLECTION].find({"status": STATUS_PROCESSING, "lease_until": {"$lt": now}}, {"_id": 0}).to_list(1000):
        await _finish(db, job["export_id"], {"status": STATUS_FAILED, "error_code": "GENERATION_STALLED", "file_name": None})
        n += 1
    try:
        live = {j.get("file_name") for j in await db[JOBS_COLLECTION].find(
            {"status": STATUS_READY}, {"_id": 0, "file_name": 1}).to_list(100000)}
        grace = now - timedelta(seconds=JOB_LEASE_SECONDS + 60)
        for p in storage_dir().iterdir():
            if p.name in live or not p.is_file():
                continue
            if datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) < grace:
                p.unlink(missing_ok=True)
                n += 1
    except OSError:
        log.warning("privacy_export: curatarea fisierelor orfane a esuat", exc_info=True)
    return n


async def export_worker(db) -> None:
    while True:
        try:
            await sweep(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("privacy_export: sweep esuat", exc_info=True)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)