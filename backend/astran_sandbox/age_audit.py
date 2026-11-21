"""Jurnal de audit pentru varsta / consimtamant parental / acces 18+ (ADAUGA-DOAR, inlantuit prin hash).

  * Singura operatie expusa e `record` (insert). Nu exista nicio functie de update/delete si nicio ruta HTTP pe
    aceasta colectie; in productie se recomanda si un rol MongoDB cu drept doar de `insert`/`find` pe `age_audit_log`.
  * Fiecare rand contine `seq`, `prev_hash` si `hash` (SHA-256 peste continut + hash-ul anterior): orice modificare
    sau stergere a unui rand din mijloc rupe lantul si e detectata de `verify_chain` (tamper-EVIDENT, nu tamper-proof:
    cine controleaza baza de date poate reface lantul - de aceea ancoreaza periodic ultimul hash in afara DB).
  * Minimizare: subiectul si actorul sunt PSEUDONIME (HMAC), detaliile trec printr-o lista alba de chei cu valori
    scalare scurte. Nu se pun niciodata data nasterii, emailuri, token-uri sau IP-uri in clar.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import age_policy as AP

log = logging.getLogger("astran.age.audit")
COLLECTION = "age_audit_log"

# evenimente (cerinta 10)
DOB_REGISTERED = "dob_registered"
AGE_BAND_COMPUTED = "age_band_computed"
PARENTAL_REQUESTED = "parental_consent_requested"
PARENTAL_APPROVED = "parental_consent_approved"
PARENTAL_REJECTED = "parental_consent_rejected"
PARENTAL_WITHDRAWN = "parental_consent_withdrawn"
PARENTAL_VERIFICATION_STARTED = "parental_verification_started"
PARENTAL_VERIFICATION_FAILED = "parental_verification_failed"
PARENTAL_LINKED = "parental_link_created"
PARENTAL_DATA_REQUEST = "parental_data_request"
AGE_ASSURANCE_STARTED = "age_assurance_started"
AGE_ASSURANCE_VERIFIED = "age_assurance_verified"
AGE_ASSURANCE_FAILED = "age_assurance_failed"
UNAUTHORIZED_ACCESS = "unauthorized_access_attempt"
AGE_CHANGE_IGNORED = "age_category_change_ignored"
LEGACY_MIGRATED = "legacy_account_migrated"
SELF_CONSENT = "self_consent_confirmed"

_ALLOWED_DETAIL_KEYS = {
    "band", "age_years", "source", "method", "provider", "state", "from_state", "to_state", "consent_id",
    "notice_version", "notice_sha256", "terms_version", "privacy_version", "resource", "resource_id", "reason",
    "delivery", "kind", "status", "requested_value", "path", "legacy_declared",
}
_FORBIDDEN_SUBSTR = ("dob", "birth", "email", "token", "password", "secret")


def _clean_details(details: Optional[dict]) -> dict:
    out: dict = {}
    for k, v in (details or {}).items():
        if k not in _ALLOWED_DETAIL_KEYS or any(s in k for s in _FORBIDDEN_SUBSTR):
            continue
        if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:120]
    return out


def _canon(doc: dict) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _hash(body: dict, prev_hash: str) -> str:
    return hashlib.sha256(prev_hash.encode() + b"|" + _canon(body)).hexdigest()


async def ensure_indexes(db) -> None:
    c = db[COLLECTION]
    await c.create_index("seq", unique=True)
    await c.create_index([("subject", 1), ("seq", 1)])
    await c.create_index("event")


async def record(db, event: str, *, subject_user_id: Optional[str] = None, actor: Optional[str] = None,
                 details: Optional[dict] = None, now: Optional[datetime] = None, max_retries: int = 8) -> dict:
    """Adauga un eveniment. `actor`: 'system' | 'user' | 'parent' | 'provider' | 'admin' (rol, nu identitate).
    Nu arunca niciodata exceptii spre apelant pentru a nu bloca fluxul de business: intoarce randul sau {} la esec
    (si logheaza). Concurenta: indexul unic pe `seq` + reincercare."""
    now = now or datetime.now(timezone.utc)
    for _ in range(max_retries):
        try:
            coll = db[COLLECTION]
            last_rows = await coll.find({}, {"_id": 0, "seq": 1, "hash": 1}).sort("seq", -1).limit(1).to_list(1)
            last = last_rows[0] if last_rows else None
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else "GENESIS"
            body = {
                "seq": seq, "event": event, "at": now.isoformat(),
                "subject": AP.pseudonym(subject_user_id, "audit") if subject_user_id else None,
                "actor": actor or "system", "details": _clean_details(details), "prev_hash": prev,
            }
            body["hash"] = _hash({k: v for k, v in body.items() if k != "hash"}, prev)
            await coll.insert_one(dict(body))
            return body
        except Exception as e:  # noqa: BLE001 - incl. DuplicateKeyError din driver sau din fake
            if type(e).__name__ != "DuplicateKeyError":
                log.exception("age audit write failed (%s)", event)
                return {}
    log.error("age audit write gave up after retries (%s)", event)
    return {}


async def verify_chain(db) -> dict:
    """Recalculeaza lantul. Intoarce {"ok": bool, "checked": n, "broken_at": seq|None}."""
    rows = await db[COLLECTION].find({}, {"_id": 0}).sort("seq", 1).to_list(None)
    prev, expected_seq = "GENESIS", 1
    for r in rows:
        body = {k: v for k, v in r.items() if k != "hash"}
        if r.get("seq") != expected_seq or r.get("prev_hash") != prev or _hash(body, prev) != r.get("hash"):
            return {"ok": False, "checked": expected_seq - 1, "broken_at": r.get("seq")}
        prev, expected_seq = r["hash"], expected_seq + 1
    return {"ok": True, "checked": len(rows), "broken_at": None}


async def history_for_user(db, user_id: str) -> list[dict]:
    return await db[COLLECTION].find({"subject": AP.pseudonym(user_id, "audit")}, {"_id": 0}).sort("seq", 1).to_list(None)