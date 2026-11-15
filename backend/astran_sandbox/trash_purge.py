"""Stergerea DEFINITIVA, automata, a proiectelor din Cosul de gunoi dupa TRASH_RETENTION_DAYS (30).

Soft-delete-ul (server.py: DELETE /games/{id}) seteaza deleted=True + deleted_at. Acest modul
ruleaza periodic din procesul backend (task pornit in `startup`, vezi server.py) si nu depinde de
nicio actiune a utilizatorului.

Garantii:
  * Se sterg DOAR jocurile cu deleted=True si deleted_at (data reala) mai vechi de cutoff.
    Jocurile restaurate au deleted=False/deleted_at=None, deci nu mai corespund filtrului.
  * Fiecare joc e "revendicat" ATOMIC (find_one_and_update pe acelasi filtru) inainte de stergere:
    doua instante de server nu il sterg de doua ori, iar o restaurare concurenta (care cere
    purging_at absent) fie castiga inainte de revendicare, fie e refuzata dupa.
  * Documentul din `games` se sterge ULTIMUL, doar daca inca e deleted=True si purging_at e al
    nostru. Daca procesul cade la jumatate, revendicarea expira (lease) si urmatoarea rulare
    reia cascada (stergerile sunt idempotente).
  * Jocurile deleted=True fara deleted_at valid (date vechi) NU se sterg: li se porneste ceasul
    (deleted_at = acum) si primesc inca 30 de zile.
  * Valoarea retentiei se citeste din env si nu poate cobori sub 1 zi (o greseala de config gen
    "0" nu are voie sa goleasca coșul imediat).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

log = logging.getLogger("astran.trash_purge")

DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
DEFAULT_INTERVAL_SECONDS = 3600
MIN_INTERVAL_SECONDS = 60
INITIAL_DELAY_SECONDS = 30
CLAIM_LEASE_SECONDS = 3600      # dupa atat, o revendicare ramasa agatata (crash) se poate relua
BATCH_LIMIT = 50                # jocuri sterse per rulare (restul la urmatoarea)

# Colectii cu campul `game_id` (cascada). Pastrat explicit + verificat de test_trash_purge.
GAME_ID_COLLECTIONS = (
    "game_scripts",
    "game_assets",
    "game_collaborators",
    "game_testers",
    "recently_played",
    "recovery_tokens",
)
# NU se sterg intentionat: security_audit_log si reports (dovezi/audit), conform politicii de retentie.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        v = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, v)


def retention_days() -> int:
    return _env_int("TRASH_RETENTION_DAYS", DEFAULT_RETENTION_DAYS, MIN_RETENTION_DAYS)


def interval_seconds() -> int:
    return _env_int("TRASH_PURGE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS)


def purge_at(deleted_at, days: int | None = None):
    """Data la care un joc sters la `deleted_at` dispare definitiv (None daca data lipseste)."""
    if not isinstance(deleted_at, datetime):
        return None
    if deleted_at.tzinfo is None:
        deleted_at = deleted_at.replace(tzinfo=timezone.utc)
    return deleted_at + timedelta(days=days if days is not None else retention_days())


async def ensure_indexes(db) -> None:
    await db.games.create_index([("deleted", 1), ("deleted_at", 1)])
    await db.game_instances.create_index("game_id")
    for coll in GAME_ID_COLLECTIONS:
        if coll != "recovery_tokens":  # recovery_tokens are deja TTL; game_id nu e critic acolo
            try:
                await db[coll].create_index("game_id")
            except Exception:  # noqa: BLE001 - un index lipsa nu opreste pornirea
                log.warning("trash purge: index game_id lipsa pe %s", coll, exc_info=True)


async def start_clock_for_undated(db, now: datetime | None = None) -> int:
    """Jocuri deleted=True fara deleted_at valid: pornim ceasul acum (nu le stergem)."""
    now = now or _now()
    res = await db.games.update_many(
        {"deleted": True, "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}]},
        {"$set": {"deleted_at": now}},
    )
    n = getattr(res, "modified_count", 0) or 0
    if n:
        log.warning("trash purge: %d proiecte sterse fara deleted_at - ceasul de %d zile porneste acum", n, retention_days())
    return n


async def purge_game_data(db, game_id: str) -> None:
    """Sterge toate datele legate de un joc, MAI PUTIN documentul din `games`. Idempotent."""
    insts = await db.game_instances.find({"game_id": game_id}, {"_id": 0, "instance_id": 1}).to_list(10000)
    inst_ids = [i["instance_id"] for i in insts if i.get("instance_id")]
    if inst_ids:
        await db.instance_players.delete_many({"instance_id": {"$in": inst_ids}})
    await db.game_instances.delete_many({"game_id": game_id})
    for coll in GAME_ID_COLLECTIONS:
        await db[coll].delete_many({"game_id": game_id})
    # security_attempts folosesc scope_id = game_id (rate limit de stergere/recuperare)
    await db.security_attempts.delete_many({"scope_id": game_id})


async def purge_owner_games_data(db, owner_id: str) -> None:
    """Cascada pentru TOATE jocurile unui owner (fara documentul din `games`) - folosit la curatarea contului demo."""
    for g in await db.games.find({"owner_id": owner_id}, {"_id": 0, "game_id": 1}).to_list(10000):
        await purge_game_data(db, g["game_id"])


async def _claim_next(db, cutoff: datetime, now: datetime):
    lease_cutoff = now - timedelta(seconds=CLAIM_LEASE_SECONDS)
    token = secrets.token_hex(8)
    doc = await db.games.find_one_and_update(
        {
            "deleted": True,
            "deleted_at": {"$lte": cutoff, "$type": "date"},
            "$or": [
                {"purging_at": None},
                {"purging_at": {"$exists": False}},
                {"purging_at": {"$lt": lease_cutoff}},
            ],
        },
        {"$set": {"purging_at": now, "purging_token": token}},
        projection={"_id": 0, "game_id": 1, "owner_id": 1, "title": 1, "deleted_at": 1},
    )
    return doc, token


async def purge_expired(db, now: datetime | None = None, days: int | None = None, limit: int = BATCH_LIMIT) -> int:
    """O rulare: porneste ceasul pentru jocuri fara data, apoi sterge definitiv jocurile expirate.
    Intoarce numarul de jocuri sterse."""
    now = now or _now()
    days = retention_days() if days is None else max(MIN_RETENTION_DAYS, days)
    cutoff = now - timedelta(days=days)
    await start_clock_for_undated(db, now)
    purged = 0
    for _ in range(limit):
        doc, token = await _claim_next(db, cutoff, now)
        if not doc:
            break
        game_id = doc["game_id"]
        try:
            await purge_game_data(db, game_id)
            res = await db.games.delete_one({"game_id": game_id, "deleted": True, "purging_token": token})
            if getattr(res, "deleted_count", 0):
                purged += 1
                await db.security_audit_log.insert_one({
                    "event_id": secrets.token_hex(12),
                    "action": "project_purge",
                    "result": "success",
                    "user_id": doc.get("owner_id"),
                    "game_id": game_id,
                    "detail": f"auto_purge_after_{days}d",
                    "ip": None,
                    "created_at": now,
                })
                log.info("trash purge: proiect %s sters definitiv (sters la %s)", game_id, doc.get("deleted_at"))
        except Exception:  # noqa: BLE001 - un joc defect nu opreste restul; revendicarea expira si se reia
            log.exception("trash purge failed for %s", game_id)
    return purged


async def purge_loop(db) -> None:
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            await purge_expired(db)
            await asyncio.sleep(interval_seconds())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("trash purge loop error")
            await asyncio.sleep(interval_seconds())