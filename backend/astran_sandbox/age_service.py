"""Orchestrare varsta: inregistrare cu data nasterii, DOB o singura data, confirmare proprie 16+, age assurance 18+,
migrarea conturilor existente. Singurul loc in care `users.age_category` poate deveni "adult_18" este
`apply_assurance_result` (dupa o atestare semnata de provider) - invariant verificat de teste statice.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import age_audit as AU
from . import age_policy as AP
from .age_assurance import AgeAssuranceProvider, AssuranceError, AssuranceResult

C_ASSURANCE = "age_assurance_sessions"
ASSURANCE_SESSION_TTL = timedelta(minutes=30)
START_COOLDOWN = timedelta(seconds=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    await db[C_ASSURANCE].create_index("session_hash", unique=True)
    await db[C_ASSURANCE].create_index("expires_at", expireAfterSeconds=60 * 60 * 24)
    await AU.ensure_indexes(db)


AGE_FIELDS = ("age_category", "age_band", "age_locked", "age_assurance", "age_info_required", "date_of_birth", "dob",
              "dob_enc", "parental", "age_source")


def strip_age_fields(provided: dict) -> tuple[dict, list[str]]:
    """Pentru PATCH /users/me: scoate ORICE camp de varsta trimis de client si intoarce lista celor ignorate (pentru audit).
    Vechiul client mai trimite `age_category`: il ignoram fara eroare (compatibilitate), dar niciodata nu are efect."""
    clean = {k: v for k, v in provided.items() if k not in AGE_FIELDS}
    ignored = [k for k in provided if k in AGE_FIELDS]
    return clean, ignored


# ----------------------------------------------------------------------------- inregistrare / DOB
def registration_fields(raw_dob: Any, *, today=None) -> dict:
    """Campurile de varsta ale unui cont NOU, calculate de server. `age_category` este INTOTDEAUNA "under_18" la
    creare (adult doar prin age assurance). Ridica AgeError pentru date invalide/viitoare/imposibile."""
    res = AP.evaluate_dob(raw_dob, today)
    doc = {
        "dob_enc": AP.encrypt_dob(res.dob), "age_band": res.band, "age_category": AP.CAT_MINOR, "age_locked": True,
        "age_assurance": {"status": AP.ASSURANCE_NONE}, "age_info_required": False, "age_source": "declared_at_registration",
    }
    if res.band == AP.BAND_UNDER_16:
        doc["parental"] = {"state": AP.PARENTAL_PENDING, "consent_id": None, "updated_at": _now(), "self_consent_at": None}
    return doc


def unknown_age_fields() -> dict:
    """Cont fara data nasterii (ex. creat prin Google): fail-closed pana cand utilizatorul o introduce."""
    return {"age_category": AP.CAT_MINOR, "age_locked": True, "age_assurance": {"status": AP.ASSURANCE_NONE},
            "age_info_required": True, "age_source": "unknown"}


async def audit_registration(db, user_id: str, band: str, age_years: Optional[int], source: str, now: Optional[datetime] = None) -> None:
    await AU.record(db, AU.DOB_REGISTERED, subject_user_id=user_id, actor="user", details={"source": source}, now=now)
    await AU.record(db, AU.AGE_BAND_COMPUTED, subject_user_id=user_id, actor="system", details={"band": band, "source": source}, now=now)


async def set_date_of_birth_once(db, user: dict, raw_dob: Any, *, now: Optional[datetime] = None) -> dict:
    """Pentru conturi FARA data nasterii (existente / Google). Se poate seta o SINGURA data: dupa aceea nicio cerere API
    nu o mai schimba (corectii doar prin suport, cu verificare). Race-safe: update conditionat pe lipsa `dob_enc`."""
    now = now or _now()
    if user.get("dob_enc"):
        raise AP.AgeError("date_of_birth_locked", 409, "Data nasterii nu mai poate fi modificata din aplicatie. Contacteaza suportul.")
    res = AP.evaluate_dob(raw_dob, now.date())
    patch = {"dob_enc": AP.encrypt_dob(res.dob), "age_band": res.band, "age_locked": True, "age_info_required": False,
             "age_source": "declared_legacy_update", "age_category": AP.CAT_MINOR if (user.get("age_assurance") or {}).get("status") != AP.ASSURANCE_VERIFIED else user.get("age_category", AP.CAT_MINOR)}
    if res.band == AP.BAND_UNDER_16:
        patch["parental"] = {"state": AP.PARENTAL_PENDING, "consent_id": None, "updated_at": now, "self_consent_at": None}
    r = await db.users.update_one({"user_id": user["user_id"], "dob_enc": {"$exists": False}}, {"$set": patch})
    if r.matched_count != 1:
        raise AP.AgeError("date_of_birth_locked", 409, "Data nasterii nu mai poate fi modificata din aplicatie. Contacteaza suportul.")
    await audit_registration(db, user["user_id"], res.band, res.age, "legacy_update", now)
    return await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})


async def confirm_own_consent(db, user: dict, *, lang: str = "ro", now: Optional[datetime] = None) -> dict:
    """Utilizator de 16+ cu stare parentala neaprobata (a implinit 16 ani / a ramas in asteptare): isi da SINGUR
    consimtamantul (GDPR art. 8). Inregistreaza acceptarea versiunilor curente Terms/Privacy in registrul consent_events."""
    now = now or _now()
    info = AP.evaluate_access(user, now.date())
    if info.band not in (AP.BAND_16_17, AP.BAND_ADULT):
        raise AP.AgeError("own_consent_not_allowed", 403, "Consimtamantul propriu e posibil de la 16 ani.")
    from . import consent as C
    for doc_type in ("terms_of_service", "privacy_policy"):
        await C.record_document_acceptance(db, user["user_id"], doc_type, lang=lang, source="reaccept")
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"parental.self_consent_at": now}})
    await AU.record(db, AU.SELF_CONSENT, subject_user_id=user["user_id"], actor="user", details={"band": info.band}, now=now)
    return await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})


# ----------------------------------------------------------------------------- age assurance 18+
def _hash(raw: str) -> str:
    return AP.token_hash("age-assurance|" + raw)


async def start_assurance(db, user: dict, provider: AgeAssuranceProvider, *, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    info = AP.evaluate_access(user, now.date())
    if info.restricted:
        raise AssuranceError("account_restricted", 403)
    if info.band in (AP.BAND_UNDER_16, AP.BAND_16_17):
        raise AssuranceError("not_eligible_for_age_assurance", 403)
    if info.assurance_status == AP.ASSURANCE_VERIFIED:
        return {"status": AP.ASSURANCE_VERIFIED, "redirect_url": None}
    if not provider.configured():
        await AU.record(db, AU.AGE_ASSURANCE_STARTED, subject_user_id=user["user_id"], actor="user",
                        details={"provider": "none", "status": "not_configured"}, now=now)
        raise AssuranceError("age_assurance_not_configured", 503)
    last = await db[C_ASSURANCE].find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).limit(1).to_list(1)
    if last and now - last[0]["created_at"].replace(tzinfo=last[0]["created_at"].tzinfo or timezone.utc) < START_COOLDOWN:
        raise AssuranceError("too_many_requests", 429)
    sid = secrets.token_urlsafe(24)
    start = provider.start(sid)
    await db[C_ASSURANCE].insert_one({"session_hash": _hash(sid), "user_id": user["user_id"], "provider": provider.name,
                                      "created_at": now, "expires_at": now + ASSURANCE_SESSION_TTL, "used_at": None})
    await db.users.update_one({"user_id": user["user_id"], "age_assurance.status": {"$ne": AP.ASSURANCE_VERIFIED}},
                              {"$set": {"age_assurance": {"status": AP.ASSURANCE_PENDING, "provider": provider.name, "started_at": now}}})
    await AU.record(db, AU.AGE_ASSURANCE_STARTED, subject_user_id=user["user_id"], actor="user",
                    details={"provider": provider.name, "status": "pending"}, now=now)
    return {"status": AP.ASSURANCE_PENDING, "redirect_url": start.redirect_url, "provider": provider.name}


async def handle_assurance_callback(db, provider: AgeAssuranceProvider, body: bytes, signature: Optional[str], *,
                                    now: Optional[datetime] = None, now_ts: Optional[float] = None) -> dict:
    """Atestare semnata de provider. Valideaza semnatura + fereastra de timp (parse_callback), apoi consuma sesiunea
    single-use. Doar `over_18 == true` (boolean strict) poate duce la `age_category = adult_18`."""
    now = now or _now()
    res: AssuranceResult = provider.parse_callback(body, signature, now_ts)
    h = _hash(res.session_id)
    sess = await db[C_ASSURANCE].find_one({"session_hash": h}, {"_id": 0})
    if not sess:
        raise AssuranceError("unknown_session", 404)
    claim = await db[C_ASSURANCE].update_one({"session_hash": h, "used_at": None, "expires_at": {"$gt": now}},
                                             {"$set": {"used_at": now, "nonce": res.nonce}})
    if claim.matched_count != 1:
        raise AssuranceError("session_used_or_expired", 409)
    user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0})
    if not user:
        raise AssuranceError("unknown_session", 404)
    return await apply_assurance_result(db, user, res, now=now)


async def apply_assurance_result(db, user: dict, res: AssuranceResult, *, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    info = AP.evaluate_access(user, now.date())
    ref = AP.pseudonym(res.session_id, "assurance")
    if not res.threshold_met or info.band in (AP.BAND_UNDER_16, AP.BAND_16_17):
        reason = "dob_conflict" if (res.threshold_met and info.band in (AP.BAND_UNDER_16, AP.BAND_16_17)) else "threshold_not_met"
        await db.users.update_one({"user_id": user["user_id"], "age_assurance.status": {"$ne": AP.ASSURANCE_VERIFIED}},
                                  {"$set": {"age_assurance": {"status": AP.ASSURANCE_FAILED, "provider": res.provider, "at": now, "reason": reason}}})
        await AU.record(db, AU.AGE_ASSURANCE_FAILED, subject_user_id=user["user_id"], actor="provider",
                        details={"provider": res.provider, "method": res.method, "reason": reason}, now=now)
        return {"status": AP.ASSURANCE_FAILED}
    patch = {"age_assurance": {"status": AP.ASSURANCE_VERIFIED, "provider": res.provider, "method": res.method,
                               "threshold": AP.ADULT_AGE, "verified_at": now, "ref": ref},
             "age_category": AP.CAT_ADULT, "age_band": AP.BAND_ADULT, "age_info_required": user.get("age_info_required", False)}
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": patch})
    await AU.record(db, AU.AGE_ASSURANCE_VERIFIED, subject_user_id=user["user_id"], actor="provider",
                    details={"provider": res.provider, "method": res.method}, now=now)
    return {"status": AP.ASSURANCE_VERIFIED}


# ----------------------------------------------------------------------------- migrare conturi existente
async def migrate_existing_accounts(db, now: Optional[datetime] = None) -> dict:
    """Idempotenta, fara stergeri, fara date de nastere inventate. La FIECARE pornire:
      1. orice cont cu age_category == adult_18 FARA age assurance verificat e coborat la under_18 (declaratia veche
         nu e verificare); declaratia veche se pastreaza in `legacy_declared_age_category`;
      2. orice valoare lipsa/necunoscuta de age_category devine under_18 (fail-closed);
      3. conturile fara `dob_enc` primesc `age_info_required=True` (flux de actualizare a datelor despre varsta).
    Intoarce numarul de conturi atinse la fiecare pas."""
    now = now or _now()
    r1 = await db.users.update_many(
        {"age_category": AP.CAT_ADULT, "age_assurance.status": {"$ne": AP.ASSURANCE_VERIFIED}},
        {"$set": {"age_category": AP.CAT_MINOR, "legacy_declared_age_category": AP.CAT_ADULT, "age_info_required": True,
                  "age_locked": True, "age_migrated_at": now}})
    r2 = await db.users.update_many(
        {"age_category": {"$nin": [AP.CAT_MINOR, AP.CAT_ADULT]}},
        {"$set": {"age_category": AP.CAT_MINOR, "age_migrated_at": now}})
    r3 = await db.users.update_many(
        {"dob_enc": {"$exists": False}, "age_info_required": {"$ne": True}},
        {"$set": {"age_info_required": True, "age_locked": True}})
    counts = {"adult_declarations_demoted": r1.modified_count, "unknown_category_fixed": r2.modified_count,
              "age_info_required_set": r3.modified_count}
    if any(counts.values()):
        await AU.record(db, AU.LEGACY_MIGRATED, actor="system", details={"status": "done", "requested_value": counts["adult_declarations_demoted"]}, now=now)
    return counts


def public_state(user: dict, parental: Optional[dict] = None) -> dict:
    info = AP.evaluate_access(user)
    out = info.public()
    out["parental"] = parental
    return out
