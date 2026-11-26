"""Migrari versionate pentru modificarile DSA (MongoDB nu are DDL: migrarea = indexuri + registru `schema_migrations`).

  * `migrate_up(db)`   - aplica migrarile neaplicate (idempotent; poate fi rulata la fiecare pornire).
  * `migrate_down(db)` - anuleaza ultima migrare: elimina DOAR indexurile ei. Datele NU se sterg implicit; `destroy_data=True` sterge
                         colectiile noi (distructiv - doar pentru rollback pe un mediu fara date de productie).
Colectiile noi: moderation_deliveries, tdb_submissions, authority_orders, authority_order_attachments, transparency_reports.
Nu se modifica documente existente (deciziile mai vechi fara `source` sunt tratate la citire din tipul raportului).
"""
from __future__ import annotations

from typing import Optional

from . import moderation_service as MS

MIGRATIONS_COLL = "schema_migrations"
NEW_COLLECTIONS = ("moderation_deliveries", "tdb_submissions", "authority_orders", "authority_order_attachments", "transparency_reports")
# (colectie, chei, unic, nume)
M0001_INDEXES = [
    ("moderation_deliveries", "delivery_id", True, "dsa_delivery_id"),
    ("moderation_deliveries", "dedupe_key", True, "dsa_delivery_dedupe"),
    ("moderation_deliveries", [("status", 1), ("next_attempt_at", 1)], False, "dsa_delivery_due"),
    ("tdb_submissions", "submission_id", True, "dsa_tdb_id"),
    ("tdb_submissions", "sor_id", True, "dsa_tdb_sor"),
    ("tdb_submissions", [("status", 1), ("next_attempt_at", 1)], False, "dsa_tdb_due"),
    ("authority_orders", "order_id", True, "dsa_order_id"),
    ("authority_orders", "reference", True, "dsa_order_ref"),
    ("authority_orders", [("status", 1), ("received_at", -1)], False, "dsa_order_status"),
    ("authority_order_attachments", "attachment_id", True, "dsa_order_att_id"),
    ("authority_order_attachments", "order_id", False, "dsa_order_att_order"),
    ("transparency_reports", "report_id", True, "dsa_tr_id"),
    ("transparency_reports", [("period_start", 1), ("version", -1)], False, "dsa_tr_period"),
    ("reports", [("report_kind", 1), ("created_at", -1)], False, "dsa_reports_kind_created"),
    ("reports", "submitter.email_hash", False, "dsa_reports_email_hash"),
    ("moderation_decisions", [("source", 1), ("created_at", -1)], False, "dsa_decisions_source"),
    ("moderation_appeals", [("status", 1), ("created_at", 1)], False, "dsa_appeals_status"),
]


async def _up_0001(db) -> None:
    for coll, keys, unique, name in M0001_INDEXES:
        await db[coll].create_index(keys, unique=unique, name=name)


async def _down_0001(db) -> None:
    for coll, _k, _u, name in M0001_INDEXES:
        try:
            await db[coll].drop_index(name)
        except Exception as e:  # noqa: BLE001
            if "not found" not in str(e).lower() and "IndexNotFound" not in type(e).__name__ and "KeyError" not in type(e).__name__:
                raise


MIGRATIONS = [("0001_dsa_remediation_indexes", "Indexes and unique constraints for DSA deliveries, TDB queue, authority orders, transparency reports", _up_0001, _down_0001)]


async def applied(db) -> list:
    return [d["migration_id"] for d in await db[MIGRATIONS_COLL].find({}, {"_id": 0}).sort("applied_at", 1).to_list(100)]


async def migrate_up(db) -> list:
    done = set(await applied(db))
    out = []
    for mid, desc, up, _down in MIGRATIONS:
        if mid in done:
            continue
        await up(db)
        await db[MIGRATIONS_COLL].insert_one({"migration_id": mid, "description": desc, "applied_at": MS.now_utc()})
        out.append(mid)
    return out


async def migrate_down(db, steps: int = 1, *, destroy_data: bool = False) -> list:
    done = await applied(db)
    out = []
    by_id = {m[0]: m for m in MIGRATIONS}
    for mid in reversed(done[-steps:] if steps else []):
        await by_id[mid][3](db)
        if destroy_data:
            for c in NEW_COLLECTIONS:
                if hasattr(db, "drop_collection"):
                    await db.drop_collection(c)
                else:
                    await db[c].delete_many({})
        await db[MIGRATIONS_COLL].delete_one({"migration_id": mid})
        out.append(mid)
    return out
