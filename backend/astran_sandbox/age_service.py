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
MAX_STARTS_PER_24H = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    await db[C_ASSURANCE].create_index("session_hash", unique=True)
    await db[C_ASSURANCE].create_index("expires_at", expireAfterSeconds=60 * 60 * 24)
    await AU.ensure_indexes(db)


AGE_FIELDS = ("age_category", "age_band", "age_locked", "age_assurance", "age_info_required", "date_of_birth", "dob",
              "dob_enc", "parental", "age_source", "consent_eligibility", "limited", "age_seal",
              "legacy_declared_age_category", "dob_correction_pending")


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
    if info.band not in (AP.BAND_16_17, AP.BAND_ADULT) or info.consent_eligibility != AP.ELIG_VERIFIED:
        # data declarata NU e suficienta: consimtamantul propriu cere varsta >=16 atestata de provider (sigilat)
        raise AP.AgeError("own_consent_not_allowed", 403, "Consimtamantul propriu cere verificarea varstei (16+).")
    from . import consent as C
    for doc_type in ("terms_of_service", "privacy_policy"):
        await C.record_document_acceptance(db, user["user_id"], doc_type, lang=lang, source="reaccept")
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {"parental.self_consent_at": now}})
    await AU.record(db, AU.SELF_CONSENT, subject_user_id=user["user_id"], actor="user", details={"band": info.band}, now=now)
    return await db.users.find_one({"user_id": user["user_id"]}, {"_id": 0})


# ----------------------------------------------------------------------------- age assurance 18+
def _hash(raw: str) -> str:
    return AP.token_hash("age-assurance|" + raw)


def allowed_thresholds(info: AP.AccessInfo, user: dict) -> tuple:
    """Pragurile pe care utilizatorul poate cere sa le verifice, decise DE SERVER din starea contului (nu din cerere):
      * 16-17 declarat -> 16;  * 18+ declarat -> 18 (si 16 daca atestarea de 18 a esuat: poate fi minor);
      * sub 16 declarat / varsta necunoscuta -> nimic (acord parental, respectiv introducerea datei)."""
    # Corectie a datei de nastere in asteptare (GDPR Art. 16, vezi dob_correction.py): pragul care trebuie atestat decurge din
    # banda DORITA, ca sa se poata verifica varsta chiar daca data declarata curenta e mai restrictiva.
    from . import dob_correction as DC
    pend = DC.pending_target(user)
    if pend is not None:
        return (pend["required_threshold"],)
    if info.band == AP.BAND_16_17:
        return (16,)
    if info.band == AP.BAND_ADULT:
        failed18 = (user.get("age_assurance") or {}).get("status") == AP.ASSURANCE_FAILED
        return (18, 16) if failed18 else (18,)
    return ()


async def start_assurance(db, user: dict, provider: AgeAssuranceProvider, *, threshold: Optional[int] = None,
                          now: Optional[datetime] = None) -> dict:
    now = now or _now()
    info = AP.evaluate_access(user, now.date())
    from . import dob_correction as DC
    if info.restricted and DC.pending_target(user) is None:
        raise AssuranceError("account_restricted", 403)
    allowed = allowed_thresholds(info, user)
    if not allowed:
        raise AssuranceError("not_eligible_for_age_assurance", 403)
    thr = allowed[0] if threshold is None else threshold
    if isinstance(thr, bool) or thr not in allowed:
        raise AssuranceError("threshold_not_allowed", 403)
    if info.verified_threshold is not None and info.verified_threshold >= thr:
        return {"status": AP.ASSURANCE_VERIFIED, "redirect_url": None, "threshold": info.verified_threshold}
    if not provider.configured():
        await AU.record(db, AU.AGE_ASSURANCE_STARTED, subject_user_id=user["user_id"], actor="user",
                        details={"provider": "none", "status": "not_configured"}, now=now)
        raise AssuranceError("age_assurance_not_configured", 503)
    last = await db[C_ASSURANCE].find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).limit(1).to_list(1)
    if last and now - last[0]["created_at"].replace(tzinfo=last[0]["created_at"].tzinfo or timezone.utc) < START_COOLDOWN:
        raise AssuranceError("too_many_requests", 429)
    day_ago = now - timedelta(hours=24)
    if await db[C_ASSURANCE].count_documents({"user_id": user["user_id"], "created_at": {"$gt": day_ago}}) >= MAX_STARTS_PER_24H:
        raise AssuranceError("too_many_requests", 429)
    sid = secrets.token_urlsafe(24)
    start = provider.start(sid, thr)
    await db[C_ASSURANCE].insert_one({"session_hash": _hash(sid), "user_id": user["user_id"], "provider": provider.name,
                                      "threshold": thr, "created_at": now, "expires_at": now + ASSURANCE_SESSION_TTL, "used_at": None})
    # Nu suprascriem o atestare VERIFICATA (cu sigiliu) cu `pending`: un esec/restart nu retrogradeaza un cont deja verificat.
    await db.users.update_one({"user_id": user["user_id"], "age_assurance.status": {"$ne": AP.ASSURANCE_VERIFIED}},
                              {"$set": {"age_assurance": {"status": AP.ASSURANCE_PENDING, "provider": provider.name,
                                                          "threshold": thr, "started_at": now}}})
    await AU.record(db, AU.AGE_ASSURANCE_STARTED, subject_user_id=user["user_id"], actor="user",
                    details={"provider": provider.name, "status": "pending", "threshold": thr}, now=now)
    return {"status": AP.ASSURANCE_PENDING, "redirect_url": start.redirect_url, "provider": provider.name, "threshold": thr}


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
    # Pragul atestat TREBUIE sa fie cel cerut la start (sesiunea il pastreaza pe server): o atestare de 16 nu poate
    # fi prezentata drept 18, iar clientul nu poate alege pragul.
    if sess.get("threshold") != res.threshold:
        await AU.record(db, AU.AGE_ASSURANCE_FAILED, subject_user_id=user["user_id"], actor="provider",
                        details={"provider": res.provider, "method": res.method, "reason": "threshold_mismatch"}, now=now)
        raise AssuranceError("threshold_mismatch", 409)
    return await apply_assurance_result(db, user, res, now=now)


async def apply_assurance_result(db, user: dict, res: AssuranceResult, *, now: Optional[datetime] = None) -> dict:
    """SINGURUL loc care scrie o atestare `verified` (sigilata) si, pentru prag 18, `age_category = adult_18`."""
    now = now or _now()
    info = AP.evaluate_access(user, now.date())
    ref = AP.pseudonym(res.session_id, "assurance")
    allowed = allowed_thresholds(info, user)
    conflict = res.threshold_met and res.threshold not in allowed and info.verified_threshold is None
    if not res.threshold_met or conflict:
        reason = "dob_conflict" if conflict else "threshold_not_met"
        await db.users.update_one({"user_id": user["user_id"], "age_assurance.status": {"$ne": AP.ASSURANCE_VERIFIED}},
                                  {"$set": {"age_assurance": {"status": AP.ASSURANCE_FAILED, "provider": res.provider,
                                                              "threshold": res.threshold, "at": now, "reason": reason}}})
        await AU.record(db, AU.AGE_ASSURANCE_FAILED, subject_user_id=user["user_id"], actor="provider",
                        details={"provider": res.provider, "method": res.method, "reason": reason, "threshold": res.threshold}, now=now)
        return {"status": AP.ASSURANCE_FAILED}
    if info.verified_threshold is not None and info.verified_threshold >= res.threshold:
        return {"status": AP.ASSURANCE_VERIFIED}        # idempotent: nu se retrogradeaza o atestare mai puternica
    patch = {"age_assurance": AP.sealed_assurance(user["user_id"], threshold=res.threshold, provider=res.provider,
                                                  method=res.method, verified_at=now, ref=ref),
             "age_info_required": user.get("age_info_required", False)}
    if res.threshold == AP.ADULT_AGE:
        patch.update({"age_category": AP.CAT_ADULT, "age_band": AP.BAND_ADULT})
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": patch})
    await AU.record(db, AU.AGE_ASSURANCE_VERIFIED, subject_user_id=user["user_id"], actor="provider",
                    details={"provider": res.provider, "method": res.method, "threshold": res.threshold}, now=now)
    # Corectia datei de nastere care astepta aceasta atestare devine efectiva acum (vezi dob_correction.finalize_after_assurance).
    try:
        from . import dob_correction as DC
        await DC.finalize_after_assurance(db, user["user_id"], now=now)
    except Exception:  # noqa: BLE001 - atestarea ramane valida; corectia poate fi reluata de utilizator
        import logging
        logging.getLogger("astran.age").warning("dob correction finalize failed", exc_info=True)
    return {"status": AP.ASSURANCE_VERIFIED, "threshold": res.threshold}


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
    # 1b. `adult_18` + "verified" FARA sigiliu valid (scris direct in DB / de o versiune veche) nu e o atestare: se coboara.
    demoted_unsealed = 0
    for u in await db.users.find({"age_category": AP.CAT_ADULT}, {"_id": 0, "user_id": 1, "age_assurance": 1}).to_list(None):
        if AP.verified_threshold(u) != AP.ADULT_AGE:
            await db.users.update_one({"user_id": u["user_id"]},
                                      {"$set": {"age_category": AP.CAT_MINOR, "legacy_declared_age_category": AP.CAT_ADULT,
                                                "age_info_required": True, "age_migrated_at": now,
                                                "age_assurance": {"status": AP.ASSURANCE_NONE, "reason": "unsealed_attestation_ignored"}}})
            demoted_unsealed += 1
    counts = {"adult_declarations_demoted": r1.modified_count + demoted_unsealed, "unknown_category_fixed": r2.modified_count,
              "age_info_required_set": r3.modified_count}
    if any(counts.values()):
        await AU.record(db, AU.LEGACY_MIGRATED, actor="system", details={"status": "done", "requested_value": counts["adult_declarations_demoted"]}, now=now)
    return counts


def public_state(user: dict, parental: Optional[dict] = None) -> dict:
    info = AP.evaluate_access(user)
    out = info.public()
    out["parental"] = parental
    return out
