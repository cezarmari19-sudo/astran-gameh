"""Migrari versionate pentru remedierea GDPR (Art. 16, 33-34) si DSA (Art. 27, 30-32).

MongoDB nu are DDL: o migrare = colectii noi + indexuri + (daca e cazul) transformari idempotente de date, inregistrate in
`schema_migrations_compliance` (registru SEPARAT de cel al `dsa_migrations`, ca `migrate_down` al unuia sa nu intalneasca
identificatorii celuilalt).

  * `migrate_up(db)`   - aplica migrarile neaplicate (idempotent; ruleaza la fiecare pornire).
  * `migrate_down(db)` - anuleaza ultima migrare: elimina DOAR indexurile ei. Datele NU se sterg implicit; `destroy_data=True`
                         sterge colectiile noi (distructiv - doar pe un mediu fara date de productie).

Compatibilitate cu datele existente: nu se modifica si nu se sterg documente existente. Utilizatorii fara profil de vanzator sunt
tratati la citire ca `undeclared` (vezi merchants.sale_eligibility), iar setarea `personalized_recommendations` lipsa = `false`
(vezi user_settings_routes: cheile noi primesc default la citire).
"""
from __future__ import annotations

from datetime import datetime, timezone

MIGRATIONS_COLL = "schema_migrations_compliance"

# (colectie, chei, unic, nume, optiuni)
M0001_INDEXES = [
    ("security_incidents", "incident_id", True, "inc_id", {}),
    ("security_incidents", [("status", 1), ("deadline_at", 1)], False, "inc_status_deadline", {}),
    ("security_incident_audit", [("incident_id", 1), ("seq", 1)], True, "inc_audit_seq", {}),
    ("app_config", "key", True, "app_config_key", {}),
]
M0002_INDEXES = [
    ("email_change_requests", "request_id", True, "ecr_id", {}),
    ("email_change_requests", "confirm_hash", True, "ecr_confirm_hash", {}),
    ("email_change_requests", "revert_hash", True, "ecr_revert_hash", {}),
    ("email_change_requests", [("user_id", 1), ("created_at", -1)], False, "ecr_user_created", {}),
    ("personal_data_audit", [("user_id", 1), ("ts", -1)], False, "pda_user_ts", {}),
    ("dob_corrections", "request_id", True, "dobc_id", {}),
    ("dob_corrections", [("user_id", 1), ("requested_at", -1)], False, "dobc_user_requested", {}),
]
M0003_INDEXES = [
    ("merchant_profiles", "user_id", True, "merchant_user", {}),
    ("merchant_profiles", [("verification.state", 1), ("updated_at", -1)], False, "merchant_state", {}),
    ("merchant_audit", [("user_id", 1), ("ts", -1)], False, "merchant_audit_user_ts", {}),
]
# colectiile create de fiecare migrare (pentru `destroy_data`); `app_config` poate contine si alte configurari: nu se sterge
COLLECTIONS_BY_MIGRATION = {
    "0001_security_incidents": ("security_incidents", "security_incident_audit"),
    "0002_personal_data_corrections": ("email_change_requests", "personal_data_audit", "dob_corrections"),
    "0003_merchant_profiles": ("merchant_profiles", "merchant_audit"),
}


def _up(indexes):
    async def up(db) -> None:
        for coll, keys, unique, name, opts in indexes:
            await db[coll].create_index(keys, unique=unique, name=name, **opts)
    return up


def _down(indexes):
    async def down(db) -> None:
        for coll, _k, _u, name, _o in indexes:
            try:
                await db[coll].drop_index(name)
            except Exception as e:  # noqa: BLE001
                if "not found" not in str(e).lower() and "IndexNotFound" not in type(e).__name__ and "KeyError" not in type(e).__name__:
                    raise
    return down


MIGRATIONS = [
    ("0001_security_incidents", "GDPR Art. 33-34: security incident register, audit trail and incident policy config", _up(M0001_INDEXES), _down(M0001_INDEXES)),
    ("0002_personal_data_corrections", "GDPR Art. 16: e-mail change requests, date-of-birth corrections, personal data audit", _up(M0002_INDEXES), _down(M0002_INDEXES)),
    ("0003_merchant_profiles", "DSA Art. 30-32: seller status / trader profiles and merchant audit", _up(M0003_INDEXES), _down(M0003_INDEXES)),
]


async def applied(db) -> list:
    return [d["migration_id"] for d in await db[MIGRATIONS_COLL].find({}, {"_id": 0}).sort("applied_at", 1).to_list(100)]


async def migrate_up(db) -> list:
    done = set(await applied(db))
    out = []
    for mid, desc, up, _down_fn in MIGRATIONS:
        if mid in done:
            continue
        await up(db)
        await db[MIGRATIONS_COLL].insert_one({"migration_id": mid, "description": desc, "applied_at": datetime.now(timezone.utc)})
        out.append(mid)
    return out


async def migrate_down(db, steps: int = 1, *, destroy_data: bool = False) -> list:
    done = await applied(db)
    by_id = {m[0]: m for m in MIGRATIONS}
    out = []
    for mid in reversed(done[-steps:] if steps else []):
        await by_id[mid][3](db)
        if destroy_data and hasattr(db, "drop_collection"):
            for c in COLLECTIONS_BY_MIGRATION.get(mid, ()):
                await db.drop_collection(c)
        await db[MIGRATIONS_COLL].delete_one({"migration_id": mid})
        out.append(mid)
    return out
