"""GDPR: stergerea contului (Art. 17) si exportul datelor personale (Art. 15 / 20).

Modul FARA dependente de FastAPI / MongoDB: `db` este injectat (la fel ca in money.py, trash_purge.py),
deci tot ce e aici se testeaza cu o baza de date simulata (vezi test_privacy.py).

PRINCIPII
---------
* Stergerea se executa EXCLUSIV pe server. Ruta (privacy_routes.py) cere re-autentificare si confirmare
  explicita; aici e doar motorul.
* Blocare atomica: `users.deletion_state` se seteaza cu find_one_and_update conditionat. Doua cereri
  simultane -> una singura porneste executia, cealalta primeste starea existenta (nu se sterge de doua ori).
* Din momentul blocarii, contul NU mai poate fi folosit: resolve_user_from_token (server.py) respinge
  orice token al unui utilizator cu `deletion_state`, iar sesiunile din `user_sessions` sunt sterse.
* Pasii sunt IDEMPOTENTI si se reiau dupa cadere: un worker (`resume_stalled`) reia stergerile ramase in
  starea "processing" cu lease expirat. Documentul `users` se sterge ULTIMUL.
* Datele altor utilizatori NU se sterg. Ce nu poate fi sters imediat (temei legal) se pseudonimizeaza si
  primeste `retain_until`; `sweep_retention` il elimina la expirare.

DECIZII DE RETENTIE (valori implicite - de CONFIRMAT cu un jurist, vezi PRIVACY_GDPR.md)
  * astran_ledger: DOAR achizitiile cu bani reali ("purchase", "purchase_reversal") - obligatii
    contabile/fiscale si anti-frauda (provider_ref unic impiedica re-creditarea aceleiasi plati).
  * reports / security_audit_log / mesaje de grup raportate: dovezi de moderare si securitate.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("astran.privacy")

# --------------------------------------------------------------------------- configurare
DELETION_COLLECTION = "account_deletions"
EXPORT_COLLECTION = "privacy_exports"
REAUTH_CODE_COLLECTION = "privacy_codes"

CONFIRMATION_TEXT = "DELETE"            # textul pe care utilizatorul il tasteaza explicit
DELETION_LEASE_SECONDS = 300            # un "processing" mai vechi de atat e considerat intrerupt
STALL_SWEEP_INTERVAL_SECONDS = 60
MAX_DELETION_ATTEMPTS = 20

FINANCIAL_LEDGER_TYPES = ("purchase", "purchase_reversal")
EXPORT_FORMAT_VERSION = 1

_DELETED_USERNAME = "[deleted]"
_DELETED_TEXT = ""


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# Plafon de siguranta (memorie) pentru UN set de randuri din export. Exportul trebuie sa fie COMPLET: daca un set ar atinge
# plafonul, privacy_export.py esueaza explicit (TOO_LARGE) in loc sa trunchieze in tacere. Valoarea veche era 10000.
EXPORT_ROW_LIMIT = _env_int("PRIVACY_EXPORT_ROW_CAP", 1_000_000)


def financial_retention_days() -> int:
    return _env_int("PRIVACY_FINANCIAL_RETENTION_DAYS", 1825)      # 5 ani (de confirmat juridic)


def moderation_retention_days() -> int:
    return _env_int("PRIVACY_MODERATION_RETENTION_DAYS", 365)


def audit_retention_days() -> int:
    return _env_int("PRIVACY_AUDIT_RETENTION_DAYS", 365)


def export_ttl_seconds() -> int:
    return _env_int("PRIVACY_EXPORT_TTL_SECONDS", 600, minimum=30)


def retention_policy() -> dict:
    """Politica de retentie curenta (afisata utilizatorului in Settings, fara date personale)."""
    return {
        "financial_records_days": financial_retention_days(),
        "moderation_records_days": moderation_retention_days(),
        "security_log_days": audit_retention_days(),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


def pseudonym_key() -> bytes:
    explicit = os.environ.get("PRIVACY_PSEUDONYM_KEY")
    if explicit:
        return explicit.encode()
    base = os.environ.get("JWT_SECRET", "astran-dev-secret-change-in-prod")
    return hmac.new(base.encode(), b"astran-privacy-pseudonym-v1", hashlib.sha256).digest()


def pseudonym(user_id: str, key: Optional[bytes] = None) -> str:
    """Identificator ireversibil (fara cheie) si stabil: aceleasi randuri pastrate raman corelabile intre ele
    (ex: ledger + reversal), dar nu mai indica o persoana. Cheia NU se stocheaza in baza de date."""
    digest = hmac.new(key or pseudonym_key(), user_id.encode(), hashlib.sha256).hexdigest()
    return "deleted_" + digest[:24]


# --------------------------------------------------------------------------- deconectare sockets
_DISCONNECTORS: list[Callable[[str], Awaitable[None]]] = []


def register_disconnector(fn: Callable[[str], Awaitable[None]]) -> None:
    """realtime.py / dm_routes.py isi inregistreaza functia care inchide WebSocket-urile unui utilizator."""
    if fn not in _DISCONNECTORS:
        _DISCONNECTORS.append(fn)


async def disconnect_user_sockets(user_id: str) -> None:
    for fn in list(_DISCONNECTORS):
        try:
            await fn(user_id)
        except Exception:  # noqa: BLE001 - un socket greu de inchis nu opreste stergerea
            log.warning("privacy: deconectarea socket-urilor a esuat", exc_info=True)


# --------------------------------------------------------------------------- indexuri
async def ensure_indexes(db) -> None:
    await db[DELETION_COLLECTION].create_index("deletion_id", unique=True)
    await db[DELETION_COLLECTION].create_index([("status", 1), ("lease_until", 1)])
    await db[EXPORT_COLLECTION].create_index("token_hash", unique=True)
    await db[EXPORT_COLLECTION].create_index("expires_at", expireAfterSeconds=0)
    await db[EXPORT_COLLECTION].create_index([("user_id", 1), ("created_at", -1)])
    await db[REAUTH_CODE_COLLECTION].create_index("user_id", unique=True)
    await db[REAUTH_CODE_COLLECTION].create_index("expires_at", expireAfterSeconds=0)
    for coll in ("astran_ledger", "reports", "security_audit_log", "group_chat_messages"):
        await db[coll].create_index([("account_deleted", 1), ("retain_until", 1)])


# =========================================================================== STERGERE
class DeletionDeferred(Exception):
    """Ceva (ex: o mutare de Astrans in curs) impiedica momentan finalizarea; se reia automat."""


async def begin_deletion(db, user_id: str) -> tuple[str, dict]:
    """Blocheaza ATOMIC contul pentru stergere.

    Intoarce ("started", rec) daca ACEASTA cerere a castigat blocarea, ("in_progress", rec) daca exista deja
    o stergere pornita (cerere dubla/concurenta) sau ("not_found", {}) daca utilizatorul nu mai exista."""
    now = _now()
    deletion_id = "del_" + secrets.token_urlsafe(24)
    locked = await db.users.find_one_and_update(
        {"user_id": user_id, "deletion_state": {"$exists": False}},
        {"$set": {"deletion_state": "processing", "deletion_id": deletion_id, "deletion_requested_at": now}},
        projection={"_id": 0, "user_id": 1},
    )
    if locked is None:
        cur = await db.users.find_one({"user_id": user_id}, {"_id": 0, "deletion_id": 1, "deletion_state": 1})
        if not cur or not cur.get("deletion_id"):
            return "not_found", {}
        rec = await db[DELETION_COLLECTION].find_one({"deletion_id": cur["deletion_id"]}, {"_id": 0})
        return "in_progress", rec or {"deletion_id": cur["deletion_id"], "status": "processing"}
    rec = {
        "deletion_id": deletion_id, "user_id": user_id, "pseudonym": pseudonym(user_id),
        "status": "processing", "requested_at": now, "updated_at": now, "completed_at": None,
        "lease_until": now + timedelta(seconds=DELETION_LEASE_SECONDS), "attempts": 0,
        "steps_done": [], "last_error": None,
    }
    await db[DELETION_COLLECTION].insert_one(dict(rec))
    return "started", rec


async def _claim_lease(db, deletion_id: str) -> Optional[dict]:
    """Un singur executor ruleaza pasii la un moment dat (lease)."""
    now = _now()
    return await db[DELETION_COLLECTION].find_one_and_update(
        {"deletion_id": deletion_id, "status": "processing", "lease_until": {"$lt": now}},
        {"$set": {"lease_until": now + timedelta(seconds=DELETION_LEASE_SECONDS), "updated_at": now},
         "$inc": {"attempts": 1}},
        projection={"_id": 0},
    )


async def _step_done(db, deletion_id: str, name: str) -> None:
    await db[DELETION_COLLECTION].update_one(
        {"deletion_id": deletion_id}, {"$addToSet": {"steps_done": name}, "$set": {"updated_at": _now()}})


async def run_deletion(db, user_id: str, deletion_id: str, *, first_run: bool = True) -> str:
    """Executa pasii. Intoarce "completed" | "processing" (de reluat) ."""
    if not first_run:
        if await _claim_lease(db, deletion_id) is None:
            return "processing"   # alt executor lucreaza sau a fost deja finalizat
    else:
        # prima rulare: lease-ul a fost setat la inserare; il consideram al nostru
        await db[DELETION_COLLECTION].update_one({"deletion_id": deletion_id}, {"$inc": {"attempts": 1}})
    key = pseudonym_key()
    pseud = pseudonym(user_id, key)
    steps: list[tuple[str, Callable[..., Awaitable[None]]]] = [
        ("revoke_access", _step_revoke_access),
        ("money_pending", _step_check_money_pending),
        ("social_graph", _step_social_graph),
        ("direct_messages", _step_direct_messages),
        ("groups", _step_groups),
        ("owned_games", _step_owned_games),
        ("participation", _step_participation),
        ("catalog", _step_catalog),
        ("profile_data", _step_profile_data),
        ("records", _step_records),
    ]
    try:
        for name, fn in steps:
            await fn(db, user_id, pseud)
            await _step_done(db, deletion_id, name)
        await _finalize(db, user_id, deletion_id)
        return "completed"
    except DeletionDeferred as e:
        await _record_error(db, deletion_id, f"deferred: {e}")
        return "processing"
    except Exception as e:  # noqa: BLE001 - raman "processing"; worker-ul reia pasii
        log.exception("privacy: stergerea %s a esuat la mijloc (se reia)", deletion_id)
        await _record_error(db, deletion_id, f"{type(e).__name__}")
        return "processing"


async def _record_error(db, deletion_id: str, msg: str) -> None:
    try:
        await db[DELETION_COLLECTION].update_one(
            {"deletion_id": deletion_id},
            {"$set": {"last_error": msg[:200], "updated_at": _now(), "lease_until": _now() + timedelta(seconds=15)}})
    except Exception:  # noqa: BLE001
        pass


async def _finalize(db, user_id: str, deletion_id: str) -> None:
    now = _now()
    await db.users.delete_one({"user_id": user_id})
    await db[DELETION_COLLECTION].update_one(
        {"deletion_id": deletion_id},
        {"$set": {"status": "completed", "completed_at": now, "updated_at": now,
                  "expires_at": now + timedelta(days=max(financial_retention_days(), moderation_retention_days()))},
         "$unset": {"user_id": "", "lease_until": ""}})
    # dovada stergerii (Art. 5(2) responsabilitate): DOAR pseudonim, fara IP / email
    await db.security_audit_log.insert_one({
        "event_id": secrets.token_hex(12), "action": "account_delete", "result": "success",
        "user_id": pseudonym(user_id), "game_id": None, "detail": deletion_id[:12], "ip": None, "created_at": now,
        "account_deleted": True, "retain_until": now + timedelta(days=audit_retention_days())})


async def deletion_status(db, deletion_id: str) -> Optional[dict]:
    """Starea unei cereri - fara date personale (id-ul e un secret aleator de 192 de biti)."""
    if not isinstance(deletion_id, str) or not deletion_id.startswith("del_") or len(deletion_id) > 80:
        return None
    rec = await db[DELETION_COLLECTION].find_one({"deletion_id": deletion_id}, {"_id": 0})
    if not rec:
        return None
    return {"deletion_id": rec["deletion_id"], "status": rec.get("status"),
            "requested_at": rec.get("requested_at"), "completed_at": rec.get("completed_at"),
            "retention": retention_policy()}


async def resume_stalled(db, limit: int = 20) -> int:
    """Reia stergerile ramase la jumatate (proces cazut / DeletionDeferred). Apelat periodic de worker."""
    resumed = 0
    now = _now()
    rows = await db[DELETION_COLLECTION].find(
        {"status": "processing", "lease_until": {"$lt": now}}, {"_id": 0}).to_list(limit)
    for rec in rows:
        uid = rec.get("user_id")
        if not uid:
            continue
        if int(rec.get("attempts", 0)) >= MAX_DELETION_ATTEMPTS:
            log.error("privacy: stergerea %s a depasit numarul maxim de reincercari - verificare manuala", rec["deletion_id"])
            continue
        if await db.users.find_one({"user_id": uid}, {"_id": 1}) is None:
            # users sters, dar nefinalizat in tombstone: toti pasii anteriori s-au terminat
            await db[DELETION_COLLECTION].update_one(
                {"deletion_id": rec["deletion_id"]},
                {"$set": {"status": "completed", "completed_at": now, "updated_at": now,
                          "expires_at": now + timedelta(days=max(financial_retention_days(), moderation_retention_days()))},
                 "$unset": {"user_id": "", "lease_until": ""}})
            resumed += 1
            continue
        if await run_deletion(db, uid, rec["deletion_id"], first_run=False) == "completed":
            resumed += 1
    return resumed


async def deletion_worker(db) -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await resume_stalled(db)
            await sweep_retention(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("privacy worker error")
        await asyncio.sleep(STALL_SWEEP_INTERVAL_SECONDS)


# --------------------------------------------------------------------------- pasii stergerii
async def _step_revoke_access(db, uid: str, pseud: str) -> None:
    await db.user_sessions.delete_many({"user_id": uid})
    await db.password_resets.delete_many({"user_id": uid})
    await db.email_verifications.delete_many({"user_id": uid})
    await db[REAUTH_CODE_COLLECTION].delete_many({"user_id": uid})
    await db[EXPORT_COLLECTION].delete_many({"user_id": uid})
    from . import privacy_export        # import tardiv: privacy_export importa privacy
    await privacy_export.purge_user(db, uid)    # arhivele ZIP de export (fisiere + metadate) dispar odata cu contul
    await db.recovery_tokens.delete_many({"user_id": uid})
    await db.webauthn_credentials.delete_many({"user_id": uid})
    await db.webauthn_challenges.delete_many({"user_id": uid})
    await db.security_attempts.delete_many({"user_id": uid})
    user = await db.users.find_one({"user_id": uid}, {"_id": 0, "email": 1})
    email = (user or {}).get("email")
    if email:   # incercarile de login sunt cheiate "<ip>|<email>" (date personale)
        await db.security_attempts.delete_many({"scope_id": {"$regex": re.escape("|" + email.lower()) + "$"}})
    await disconnect_user_sockets(uid)


async def _step_check_money_pending(db, uid: str, pseud: str) -> None:
    pend = await db.astran_money_journal.find_one(
        {"state": "pending", "$or": [{"payer_id": uid}, {"credits.user_id": uid}]}, {"_id": 1})
    if pend is not None:
        raise DeletionDeferred("o operatiune de Astrans este in curs; se reia automat")


async def _step_social_graph(db, uid: str, pseud: str) -> None:
    await db.friendships.delete_many({"$or": [{"user_a": uid}, {"user_b": uid}]})
    await db.friend_requests.delete_many({"$or": [{"from_id": uid}, {"to_id": uid}]})
    await db.blocks.delete_many({"$or": [{"blocker_id": uid}, {"blocked_id": uid}]})


async def _step_direct_messages(db, uid: str, pseud: str) -> None:
    """Mesajele TRIMISE de utilizator isi pierd continutul si identitatea; mesajele celuilalt participant
    raman ale lui. Conversatia nu mai arata participantul sters (dm_core sare peste utilizatori lipsa)."""
    convs = await db.dm_conversations.find(
        {"$or": [{"user_a": uid}, {"user_b": uid}]}, {"_id": 0, "conversation_id": 1, "user_a": 1, "user_b": 1}).to_list(100000)
    cids = [c["conversation_id"] for c in convs]
    if cids:
        await db.dm_messages.update_many(
            {"conversation_id": {"$in": cids}, "sender_id": uid},
            {"$set": {"sender_id": pseud, "raw": None, "safe": _DELETED_TEXT, "removed": True}})
    await db.dm_conversations.update_many({"user_a": uid}, {"$set": {"user_a": pseud}})
    await db.dm_conversations.update_many({"user_b": uid}, {"$set": {"user_b": pseud}})
    # conversatii in care nu a ramas niciun participant real (ambii sterse): nu mai are cine sa le citeasca
    for c in convs:
        other = c["user_b"] if c["user_a"] == uid else c["user_a"]
        if isinstance(other, str) and other.startswith("deleted_"):
            await db.dm_messages.delete_many({"conversation_id": c["conversation_id"]})
            await db.dm_conversations.delete_one({"conversation_id": c["conversation_id"]})


def _successor(members: list[dict]) -> Optional[dict]:
    def key(m):
        j = m.get("joined_at")
        return _aware(j) if isinstance(j, datetime) else datetime.max.replace(tzinfo=timezone.utc)
    admins = sorted([m for m in members if m.get("role") == "admin"], key=key)
    if admins:
        return admins[0]
    plain = sorted([m for m in members if m.get("role") != "owner"], key=key)
    return plain[0] if plain else None


async def _open_report_message_ids(db) -> set:
    rows = await db.reports.find(
        {"target_type": "message", "status": {"$nin": ["resolved", "closed", "dismissed"]}},
        {"_id": 0, "target_id": 1}).to_list(100000)
    return {r["target_id"] for r in rows if r.get("target_id")}


async def _step_groups(db, uid: str, pseud: str) -> None:
    retain = _now() + timedelta(days=moderation_retention_days())
    # 1) grupul detinut: transfer catre un admin / cel mai vechi membru activ, altfel se sterge
    for g in await db.groups.find({"owner_id": uid}, {"_id": 0, "group_id": 1}).to_list(1000):
        gid = g["group_id"]
        members = await db.group_members.find(
            {"group_id": gid, "status": "active", "user_id": {"$ne": uid}}, {"_id": 0}).to_list(100000)
        succ = _successor(members)
        if succ is not None:
            await db.group_members.update_one({"group_id": gid, "user_id": succ["user_id"]}, {"$set": {"role": "owner"}})
            await db.groups.update_one({"group_id": gid}, {"$set": {"owner_id": succ["user_id"], "updated_at": _now()}})
        else:
            await db.games.update_many({"group_id": gid}, {"$unset": {"group_id": ""}})
            await db.group_chat_messages.delete_many({"group_id": gid})
            await db.group_members.delete_many({"group_id": gid})
            await db.groups.delete_one({"group_id": gid})
    # 2) apartenenta in grupuri
    await db.group_members.delete_many({"user_id": uid})
    # 3) mesajele de grup: sterse, mai putin cele cu raport deschis (dovada) - acelea se pseudonimizeaza
    keep_ids = await _open_report_message_ids(db)
    mine = await db.group_chat_messages.find({"user_id": uid}, {"_id": 0, "message_id": 1}).to_list(1000000)
    ids_keep = [m["message_id"] for m in mine if m["message_id"] in keep_ids]
    if ids_keep:
        await db.group_chat_messages.update_many(
            {"user_id": uid, "message_id": {"$in": ids_keep}},
            {"$set": {"user_id": pseud, "username": _DELETED_USERNAME, "account_deleted": True, "retain_until": retain}})
    await db.group_chat_messages.delete_many({"user_id": uid})


async def _step_owned_games(db, uid: str, pseud: str) -> None:
    from . import trash_purge   # refolosim cascada existenta (scripturi, assets, colaboratori, instante, ...)
    for g in await db.games.find({"owner_id": uid}, {"_id": 0, "game_id": 1}).to_list(100000):
        await trash_purge.purge_game_data(db, g["game_id"])
        await db.game_instances.delete_many({"game_id": g["game_id"]})
        await db.games.delete_one({"game_id": g["game_id"], "owner_id": uid})
    await db.recovery_tokens.delete_many({"user_id": uid})


async def _step_participation(db, uid: str, pseud: str) -> None:
    for row in await db.instance_players.find({"user_id": uid}, {"_id": 0, "instance_id": 1}).to_list(10000):
        await db.game_instances.update_one({"instance_id": row["instance_id"], "player_count": {"$gt": 0}},
                                           {"$inc": {"player_count": -1}})
    await db.instance_players.delete_many({"user_id": uid})
    await db.game_collaborators.delete_many({"user_id": uid})
    await db.game_testers.delete_many({"user_id": uid})
    await db.recently_played.delete_many({"user_id": uid})


async def _step_catalog(db, uid: str, pseud: str) -> None:
    """Itemele cu cumparatori NU se sterg (cumparatorii au platit; contractul lor ramane valabil): se retrag din
    Shop si se anonimizeaza. Cele fara cumparatori se sterg. Modelele din Studio sunt sterse."""
    for coll, purchases in (("shop_items", "shop_purchases"), ("clothes_items", "clothes_purchases")):
        for it in await db[coll].find({"owner_id": uid}, {"_id": 0, "item_id": 1}).to_list(100000):
            sold = await db[purchases].find_one({"item_id": it["item_id"], "user_id": {"$ne": uid}}, {"_id": 1})
            if sold is None:
                await db[coll].delete_one({"item_id": it["item_id"]})
            else:
                await db[coll].update_one({"item_id": it["item_id"]}, {"$set": {
                    "owner_id": pseud, "owner_username": _DELETED_USERNAME, "delisted": True,
                    "is_public": False, "delisted_at": _now(), "updated_at": _now()}})
        await db[purchases].delete_many({"user_id": uid})
    await db.studio_models.delete_many({"owner_id": uid})


async def _step_profile_data(db, uid: str, pseud: str) -> None:
    await db.user_settings.delete_many({"user_id": uid})
    await db.user_avatars.delete_many({"user_id": uid})


async def _step_records(db, uid: str, pseud: str) -> None:
    """Date cu temei legal de pastrare: pseudonimizate, cu retain_until (sweep_retention le elimina)."""
    now = _now()
    fin = now + timedelta(days=financial_retention_days())
    mod = now + timedelta(days=moderation_retention_days())
    aud = now + timedelta(days=audit_retention_days())
    # Ledger: randurile fara bani reali (bonus, transferuri, cumparaturi in Shop) se sterg; achizitiile reale se pastreaza.
    await db.astran_ledger.delete_many({"user_id": uid, "type": {"$nin": list(FINANCIAL_LEDGER_TYPES)}})
    await db.astran_ledger.update_many(
        {"user_id": uid, "type": {"$in": list(FINANCIAL_LEDGER_TYPES)}},
        {"$set": {"user_id": pseud, "account_deleted": True, "retain_until": fin}})
    # randurile ALTOR utilizatori raman intacte; doar referinta catre contul sters devine pseudonim
    await db.astran_ledger.update_many({"counterparty_id": uid}, {"$set": {"counterparty_id": pseud}})
    # Rapoarte (moderare): reporter / tinta / contextul copiat al mesajelor
    await db.reports.update_many({"reporter_id": uid}, {"$set": {"reporter_id": pseud, "account_deleted": True, "retain_until": mod}})
    await db.reports.update_many({"target_type": "user", "target_id": uid},
                                 {"$set": {"target_id": pseud, "account_deleted": True, "retain_until": mod}})
    await db.reports.update_many({"context.sender_id": uid},
                                 {"$set": {"context.sender_id": pseud, "context.sender_username": _DELETED_USERNAME,
                                           "account_deleted": True, "retain_until": mod}})
    # Jurnal de securitate: fara IP, cu user pseudonimizat
    await db.security_audit_log.update_many(
        {"user_id": uid}, {"$set": {"user_id": pseud, "ip": None, "account_deleted": True, "retain_until": aud}})


async def sweep_retention(db, now: Optional[datetime] = None) -> int:
    """Elimina definitiv datele pseudonimizate al caror termen de pastrare a expirat."""
    now = now or _now()
    total = 0
    for coll in ("astran_ledger", "reports", "security_audit_log", "group_chat_messages"):
        res = await db[coll].delete_many({"account_deleted": True, "retain_until": {"$lte": now}})
        total += getattr(res, "deleted_count", 0) or 0
    res = await db[DELETION_COLLECTION].delete_many({"status": "completed", "expires_at": {"$lte": now}})
    total += getattr(res, "deleted_count", 0) or 0
    return total


# =========================================================================== EXPORT
_USER_EXPORT_FIELDS = (
    "user_id", "username", "display_name", "email", "email_verified", "age_category", "language",
    "avatar_url", "astrans_balance", "is_platform_owner", "is_platform_admin", "created_at", "updated_at",
    "email_verified_at", "chat_muted_until",
)
_LEDGER_EXPORT_FIELDS = ("tx_id", "type", "amount", "fee", "net", "status", "timestamp", "reference",
                         "counterparty_id", "reversed_at")
_GAME_DROP = ("project_password_hash", "_id", "purging_token", "purging_at")


def _clean_doc(doc: dict, drop: tuple = ("_id",)) -> dict:
    return {k: v for k, v in doc.items() if k not in drop}


def _pick(doc: dict, fields: tuple) -> dict:
    return {k: doc[k] for k in fields if k in doc}


async def _rows(db, coll: str, flt: dict, projection: Optional[dict] = None, sort: Optional[tuple] = None) -> list[dict]:
    cur = db[coll].find(flt, projection or {"_id": 0})
    if sort:
        cur = cur.sort(*sort)
    return await cur.to_list(EXPORT_ROW_LIMIT)


async def _usernames(db, ids: list) -> dict:
    ids = [i for i in set(ids) if isinstance(i, str)]
    if not ids:
        return {}
    rows = await db.users.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "username": 1}).to_list(len(ids))
    return {r["user_id"]: r.get("username") for r in rows}


async def build_export(db, user_id: str) -> dict:
    """Copia datelor personale ale UNUI utilizator (cel autentificat; apelantul garanteaza asta).
    Whitelist pentru cont si ledger; fara parole, hash-uri, tokenuri, secrete sau continutul altor utilizatori."""
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if not u:
        raise LookupError("user not found")
    out: dict[str, Any] = {
        "export_format_version": EXPORT_FORMAT_VERSION,
        "generated_at": _now(),
        "legal_basis": "GDPR Art. 15 (acces) si Art. 20 (portabilitate)",
        "account": _pick(u, _USER_EXPORT_FIELDS),
    }
    out["account"]["has_password"] = bool(u.get("password_hash"))
    out["account"]["sign_in_method"] = "password" if u.get("password_hash") else "google"

    st = await db.user_settings.find_one({"user_id": user_id}, {"_id": 0})
    out["preferences"] = (st or {}).get("settings", {})
    av = await db.user_avatars.find_one({"user_id": user_id}, {"_id": 0})
    out["avatar"] = _clean_doc(av) if av else None

    games = await _rows(db, "games", {"owner_id": user_id})
    game_ids = [g["game_id"] for g in games]
    out["games"] = []
    for g in games:
        entry = _clean_doc(g, _GAME_DROP)
        entry["files"] = [_clean_doc(s) for s in await _rows(db, "game_scripts", {"game_id": g["game_id"]})]
        assets = await db.game_assets.find_one({"game_id": g["game_id"]}, {"_id": 0})
        entry["assets"] = _clean_doc(assets) if assets else None
        out["games"].append(entry)
    collab = await _rows(db, "game_collaborators", {"user_id": user_id})
    testers = await _rows(db, "game_testers", {"user_id": user_id})
    titles = {}
    gids = list({r["game_id"] for r in collab + testers if r.get("game_id")})
    if gids:
        for g in await db.games.find({"game_id": {"$in": gids}}, {"_id": 0, "game_id": 1, "title": 1}).to_list(len(gids)):
            titles[g["game_id"]] = g.get("title")
    out["collaborations"] = {
        "as_collaborator": [{**r, "game_title": titles.get(r.get("game_id"))} for r in collab],
        "as_tester": [{**r, "game_title": titles.get(r.get("game_id"))} for r in testers],
    }
    out["recently_played"] = await _rows(db, "recently_played", {"user_id": user_id})

    out["studio_models"] = await _rows(db, "studio_models", {"owner_id": user_id})
    out["shop_items_published"] = await _rows(db, "shop_items", {"owner_id": user_id})
    out["clothes_items_created"] = await _rows(db, "clothes_items", {"owner_id": user_id})
    out["purchases"] = {
        "shop": await _rows(db, "shop_purchases", {"user_id": user_id}),
        "clothes": await _rows(db, "clothes_purchases", {"user_id": user_id}),
    }
    ledger = await _rows(db, "astran_ledger", {"user_id": user_id}, sort=("timestamp", -1))
    out["transactions"] = [_pick(r, _LEDGER_EXPORT_FIELDS) for r in ledger]

    edges = await db.friendships.find({"$or": [{"user_a": user_id}, {"user_b": user_id}]}, {"_id": 0}).to_list(EXPORT_ROW_LIMIT)
    reqs = await db.friend_requests.find({"$or": [{"from_id": user_id}, {"to_id": user_id}]}, {"_id": 0}).to_list(EXPORT_ROW_LIMIT)
    blocks = await _rows(db, "blocks", {"blocker_id": user_id})
    other_ids = [e["user_b"] if e["user_a"] == user_id else e["user_a"] for e in edges]
    other_ids += [r["to_id"] if r.get("from_id") == user_id else r.get("from_id") for r in reqs]
    other_ids += [b.get("blocked_id") for b in blocks]
    names = await _usernames(db, other_ids)
    out["social"] = {
        "friends": [{"user_id": o, "username": names.get(o), "since": e.get("since")}
                    for e in edges for o in [e["user_b"] if e["user_a"] == user_id else e["user_a"]]],
        "friend_requests": [{"direction": "sent" if r.get("from_id") == user_id else "received",
                             "user_id": (r["to_id"] if r.get("from_id") == user_id else r.get("from_id")),
                             "username": names.get(r["to_id"] if r.get("from_id") == user_id else r.get("from_id")),
                             "status": r.get("status"), "created_at": r.get("created_at")} for r in reqs],
        "blocked_users": [{"user_id": b.get("blocked_id"), "username": names.get(b.get("blocked_id")),
                           "created_at": b.get("created_at")} for b in blocks],
    }

    members = await _rows(db, "group_members", {"user_id": user_id})
    owned_groups = await _rows(db, "groups", {"owner_id": user_id})
    gm_ids = list({m["group_id"] for m in members} | {g["group_id"] for g in owned_groups})
    gnames = {}
    if gm_ids:
        for g in await db.groups.find({"group_id": {"$in": gm_ids}}, {"_id": 0, "group_id": 1, "name": 1}).to_list(len(gm_ids)):
            gnames[g["group_id"]] = g.get("name")
    out["groups"] = {
        "owned": [_clean_doc(g, ("_id", "token")) for g in owned_groups],     # `token` = secret de aderare
        "memberships": [{**m, "group_name": gnames.get(m["group_id"])} for m in members],
        "messages_sent": await _rows(db, "group_chat_messages", {"user_id": user_id},
                                     {"_id": 0, "message_id": 1, "group_id": 1, "text": 1, "created_at": 1}),
    }

    convs = await db.dm_conversations.find(
        {"$or": [{"user_a": user_id}, {"user_b": user_id}]}, {"_id": 0}).to_list(EXPORT_ROW_LIMIT)
    dm_others = [c["user_b"] if c["user_a"] == user_id else c["user_a"] for c in convs]
    dm_names = await _usernames(db, dm_others)
    dms = []
    for c in convs:
        other = c["user_b"] if c["user_a"] == user_id else c["user_a"]
        msgs = await db.dm_messages.find({"conversation_id": c["conversation_id"], "sender_id": user_id}, {"_id": 0}).to_list(EXPORT_ROW_LIMIT)
        dms.append({"conversation_id": c["conversation_id"], "with_user_id": other, "with_username": dm_names.get(other),
                    "messages_sent_by_you": [{"message_id": m["message_id"], "sent_at_ms": m.get("ts"),
                                              "text": m.get("raw") if m.get("raw") is not None else m.get("safe")}
                                             for m in msgs]})
    out["direct_messages"] = dms

    out["reports_submitted"] = await _rows(
        db, "reports", {"reporter_id": user_id},
        {"_id": 0, "report_id": 1, "target_type": 1, "target_id": 1, "reason": 1, "details": 1, "status": 1, "created_at": 1})
    creds = await _rows(db, "webauthn_credentials", {"user_id": user_id}, {"_id": 0, "created_at": 1, "name": 1})
    out["security"] = {
        "registered_biometric_credentials": creds,
        "security_events": await _rows(db, "security_audit_log", {"user_id": user_id},
                                       {"_id": 0, "action": 1, "result": 1, "detail": 1, "ip": 1, "created_at": 1}),
    }
    out["not_included"] = [
        "Parole, hash-uri de parola, tokenuri de sesiune/autentificare, coduri de verificare si chei (nu se exporta niciodata).",
        "Parola de securitate a proiectelor (doar hash intern) si tokenul secret de aderare al grupurilor.",
        "Datele personale ale altor utilizatori: mesajele primite de la altii, continutul lor, email-urile lor.",
        "Identificatori de plata ai furnizorilor (purchase token / payment intent) - sunt pastrati intern pentru prevenirea fraudei.",
        "Jurnalele de infrastructura (access log) si copiile de siguranta ale bazei de date - vezi politica de retentie.",
    ]
    out["retention_policy"] = retention_policy()
    return out


# --------------------------------------------------------------------------- tichete de descarcare
def _hash_ticket(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


async def create_export_ticket(db, user_id: str, now: Optional[datetime] = None) -> dict:
    """Tichet de descarcare: aleator (256 biti), stocat DOAR ca hash, valabil export_ttl_seconds, de unica folosinta,
    legat de utilizator (descarcarea cere si sesiunea lui)."""
    now = now or _now()
    ticket = secrets.token_urlsafe(32)
    exp = now + timedelta(seconds=export_ttl_seconds())
    await db[EXPORT_COLLECTION].insert_one({
        "export_id": "exp_" + secrets.token_hex(8), "user_id": user_id, "token_hash": _hash_ticket(ticket),
        "created_at": now, "expires_at": exp, "used_at": None})
    return {"ticket": ticket, "expires_at": exp, "ttl_seconds": export_ttl_seconds()}


async def redeem_export_ticket(db, user_id: str, ticket: str, now: Optional[datetime] = None) -> bool:
    """True DOAR daca tichetul exista, apartine utilizatorului, nu a expirat si nu a fost folosit (atomic)."""
    now = now or _now()
    if not isinstance(ticket, str) or not (20 <= len(ticket) <= 200):
        return False
    row = await db[EXPORT_COLLECTION].find_one_and_update(
        {"token_hash": _hash_ticket(ticket), "user_id": user_id, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}}, projection={"_id": 0, "export_id": 1})
    return row is not None


def json_default(o):
    if isinstance(o, datetime):
        return _aware(o).isoformat()
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


# =========================================================================== RE-AUTENTIFICARE
REAUTH_RECENT_SESSION_SECONDS = 300


class ReauthError(Exception):
    def __init__(self, status: int, detail: str, code: str = "REAUTH_FAILED"):
        super().__init__(detail)
        self.status, self.detail, self.code = status, detail, code


async def verify_reauth(db, user: dict, *, password: Optional[str], code: Optional[str],
                        verify_pw: Callable[[str, str], Awaitable[bool]], emailver: Any,
                        session_age_seconds: Optional[float], email_is_configured: bool) -> None:
    """Dovada ca cel care cere actiunea este titularul contului, NU doar detinatorul unui token furat.

    * cont cu parola  -> parola contului (verificata cu acelasi hash ca la login);
    * cont Google (fara parola) -> cod de 6 cifre trimis pe emailul verificat al contului; doar daca serverul NU
      poate trimite emailuri, se accepta o sesiune Google proaspata (< 5 minute).
    Ridica ReauthError; nu modifica nimic."""
    uid = user["user_id"]
    pw_hash = user.get("password_hash")
    if pw_hash:
        if not password or not await verify_pw(password, pw_hash):
            raise ReauthError(401, "Parola contului este incorecta", "BAD_PASSWORD")
        return
    if code:
        email = user.get("email") or ""
        try:
            await emailver.verify(db, uid, email, code, collection=REAUTH_CODE_COLLECTION)
        except emailver.VerificationError as e:
            raise ReauthError(e.status, e.detail, "BAD_CODE")
        return
    if not email_is_configured and session_age_seconds is not None and session_age_seconds <= REAUTH_RECENT_SESSION_SECONDS:
        return
    raise ReauthError(401, "Re-autentificare necesara: cere un cod pe email si introdu-l", "REAUTH_REQUIRED")