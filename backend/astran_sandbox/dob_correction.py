"""Corectarea datei de nastere (GDPR Art. 16) FARA a deschide o cale de ocolire a restrictiilor de varsta.

Reguli (toate pe backend; pragurile sunt configurabile prin variabile de mediu):
  * Doar un cont care ARE deja o data a nasterii (prima data se seteaza prin /age/date-of-birth). Reautentificare obligatorie.
  * Limite anti-ocolire: maximum `ASTRAN_DOB_CORRECTION_MAX_APPLIED` corectii aplicate (implicit 1) pe cont, o singura cerere
    deschisa, pauza `ASTRAN_DOB_CORRECTION_COOLDOWN_DAYS` (implicit 30) intre cereri si maximum
    `ASTRAN_DOB_CORRECTION_MAX_PER_YEAR` cereri/an. Peste limita: corectia se face doar prin suport/verificare umana.
  * Sensul schimbarii decide ce se intampla (comparatie intre benzile de varsta):
      - mai RESTRICTIV (utilizatorul devine mai tanar): se aplica IMEDIAT; regulile de siguranta se aplica pe loc (cerere de
        acord parental sub 16, pierderea accesului 18+/a atestarii care nu mai e valida);
      - aceeasi banda: se aplica dupa reautentificare;
      - mai PUTIN restrictiv (16->16-17, ->18+): NU se aplica pana nu exista o atestare de varsta de la furnizorul existent
        (`age_service` / age assurance, prag 16 sau 18). Pana atunci data efectiva si accesul raman neschimbate. Daca
        furnizorul nu e configurat, cererea ramane in asteptare si accesul 18+ ramane blocat (fail-closed).
  * Se pastreaza: data veche, data noua (criptate, ca si `users.dob_enc`), momentul, motivul, metoda de reautentificare.
    NU se cer si NU se stocheaza copii ale documentelor de identitate: confirmarea identitatii/varstei se face prin furnizorul
    de verificare si pastram doar rezultatul sigilat (referinta pseudonimizata), nu documentul.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import age_audit as AU
from . import age_policy as AP

C_CORRECTIONS = "dob_corrections"
REASON_CODES = ("typo", "wrong_year_or_day", "entered_by_someone_else", "other")
BAND_RANK = {AP.BAND_UNDER_16: 0, AP.BAND_16_17: 1, AP.BAND_ADULT: 2}
EV_REQUESTED, EV_APPLIED, EV_CANCELLED, EV_EXPIRED = ("dob_correction_requested", "dob_correction_applied",
                                                       "dob_correction_cancelled", "dob_correction_expired")


class DobCorrectionError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra: Any):
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


def policy() -> dict:
    return {"max_applied": _int_env("ASTRAN_DOB_CORRECTION_MAX_APPLIED", 1, 0, 10),
            "cooldown_days": _int_env("ASTRAN_DOB_CORRECTION_COOLDOWN_DAYS", 30, 0, 365),
            "max_per_year": _int_env("ASTRAN_DOB_CORRECTION_MAX_PER_YEAR", 3, 1, 20),
            "pending_ttl_days": _int_env("ASTRAN_DOB_CORRECTION_PENDING_TTL_DAYS", 30, 1, 180)}


def required_threshold(target_band: str) -> int:
    return AP.ADULT_AGE if target_band == AP.BAND_ADULT else AP.DIGITAL_CONSENT_AGE


def pending_target(user: Optional[dict]) -> Optional[dict]:
    """Cererea care asteapta atestarea de varsta (marcata pe documentul utilizatorului de acest serviciu), daca nu a expirat."""
    p = (user or {}).get("dob_correction_pending")
    if not isinstance(p, dict) or p.get("target_band") not in BAND_RANK:
        return None
    exp = _aware(p.get("expires_at"))
    return p if exp is None or exp > _now() else None


async def ensure_indexes(db) -> None:
    await db[C_CORRECTIONS].create_index("request_id", unique=True)
    await db[C_CORRECTIONS].create_index([("user_id", 1), ("requested_at", -1)])


def _clean_reason(code: Any, text: Any) -> tuple[str, Optional[str]]:
    if code not in REASON_CODES:
        raise DobCorrectionError(422, "invalid_reason", "Invalid reason")
    if text is not None and not isinstance(text, str):
        raise DobCorrectionError(422, "invalid_reason", "Invalid reason text")
    text = (text or "").strip() or None
    if text and len(text) > 500:
        raise DobCorrectionError(422, "invalid_reason", "Reason text is too long")
    if code == "other" and (not text or len(text) < 10):
        raise DobCorrectionError(422, "reason_required", "Please describe the reason (at least 10 characters)")
    return code, text


async def status(db, user: dict, *, provider_configured: bool, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    pol = policy()
    rows = await db[C_CORRECTIONS].find({"user_id": user["user_id"]}, {"_id": 0, "request_id": 1, "status": 1, "requested_at": 1,
                                                                        "direction": 1, "applied_at": 1, "required_threshold": 1}).sort("requested_at", -1).limit(20).to_list(20)
    applied = sum(1 for r in rows if r["status"] == "applied")
    last = _aware(rows[0]["requested_at"]) if rows else None
    next_at = last + timedelta(days=pol["cooldown_days"]) if last and pol["cooldown_days"] else None
    pend = pending_target(user)
    return {"has_date_of_birth": bool(user.get("dob_enc")), "applied_count": applied, "max_applied": pol["max_applied"],
            "can_request": bool(user.get("dob_enc")) and applied < pol["max_applied"] and not pend and (next_at is None or next_at <= now),
            "next_request_at": next_at if next_at and next_at > now else None,
            "pending": ({"request_id": pend["request_id"], "required_threshold": pend["required_threshold"],
                         "expires_at": pend.get("expires_at"), "provider_available": provider_configured,
                         "next_action": f"verify_age_{pend['required_threshold']}"} if pend else None),
            "history": [{"request_id": r["request_id"], "status": r["status"], "direction": r.get("direction"),
                         "requested_at": r["requested_at"], "applied_at": r.get("applied_at")} for r in rows]}


async def request_correction(db, user: dict, raw_dob: Any, reason_code: Any, reason_text: Any, *, reauth_method: str,
                             provider_configured: bool, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    pol = policy()
    code, text = _clean_reason(reason_code, reason_text)
    if not user.get("dob_enc"):
        raise DobCorrectionError(409, "date_of_birth_not_set", "Set your date of birth first")
    old_dob = AP.decrypt_dob(user["dob_enc"])
    if old_dob is None:
        raise DobCorrectionError(409, "manual_review_required", "Your date of birth could not be read; contact support")
    new = AP.evaluate_dob(raw_dob, now.date())          # AgeError pentru date invalide/viitoare/imposibile
    if new.dob == old_dob:
        raise DobCorrectionError(422, "no_change", "The date is identical to the current one")
    if pending_target(user):
        raise DobCorrectionError(409, "request_already_open", "A correction is already waiting for age verification")
    st = await status(db, user, provider_configured=provider_configured, now=now)
    if st["applied_count"] >= pol["max_applied"]:
        raise DobCorrectionError(409, "correction_limit_reached", "The correction limit was reached; contact support for a manual review")
    if st["next_request_at"]:
        raise DobCorrectionError(429, "correction_cooldown", "Please wait before submitting another correction", retry_at=st["next_request_at"].isoformat())
    year_ago = now - timedelta(days=365)
    if await db[C_CORRECTIONS].count_documents({"user_id": user["user_id"], "requested_at": {"$gt": year_ago}}) >= pol["max_per_year"]:
        raise DobCorrectionError(429, "correction_limit_reached", "Too many correction requests; contact support")
    old_band = AP.evaluate_dob(old_dob.isoformat(), now.date()).band
    direction = ("more_restrictive" if BAND_RANK[new.band] < BAND_RANK[old_band]
                 else "less_restrictive" if BAND_RANK[new.band] > BAND_RANK[old_band] else "same_band")
    req = {"request_id": "dobc_" + uuid.uuid4().hex[:16], "user_id": user["user_id"], "old_dob_enc": user["dob_enc"],
           "new_dob_enc": AP.encrypt_dob(new.dob), "old_band": old_band, "new_band": new.band, "direction": direction,
           "reason_code": code, "reason_text": text, "reauth_method": reauth_method, "requested_at": now, "applied_at": None,
           "status": "requested", "required_threshold": None}
    await db[C_CORRECTIONS].insert_one(req)
    await AU.record(db, EV_REQUESTED, subject_user_id=user["user_id"], actor="user",
                    details={"from_state": old_band, "to_state": new.band, "reason": code, "status": direction}, now=now)
    if direction != "less_restrictive":
        await _apply(db, user, req, new, now)
        return {"request_id": req["request_id"], "status": "applied", "direction": direction, "new_band": new.band, "next_action": None}
    thr = required_threshold(new.band)
    await db[C_CORRECTIONS].update_one({"request_id": req["request_id"]}, {"$set": {"status": "awaiting_age_verification", "required_threshold": thr}})
    await db.users.update_one({"user_id": user["user_id"], "dob_enc": user["dob_enc"]},
                              {"$set": {"dob_correction_pending": {"request_id": req["request_id"], "target_band": new.band, "required_threshold": thr,
                                                                    "created_at": now, "expires_at": now + timedelta(days=pol["pending_ttl_days"])}}})
    return {"request_id": req["request_id"], "status": "awaiting_age_verification", "direction": direction, "new_band": new.band,
            "required_threshold": thr, "next_action": f"verify_age_{thr}", "provider_available": provider_configured}


async def _apply(db, user: dict, req: dict, new: AP.AgeResult, now: datetime) -> None:
    """Aplica data noua si regulile de siguranta. Update conditionat pe valoarea veche (cursa-safe)."""
    patch: dict = {"dob_enc": req["new_dob_enc"], "age_band": new.band, "age_source": "corrected_by_user", "age_info_required": False,
                   "age_locked": True, "age_corrected_at": now}
    if new.band != AP.BAND_ADULT:
        patch["age_category"] = AP.CAT_MINOR                           # 18+ numai prin atestare valida
    vt = AP.verified_threshold(user)
    if vt is not None and (new.band == AP.BAND_UNDER_16 or (vt == AP.ADULT_AGE and new.band != AP.BAND_ADULT)):
        patch["age_assurance"] = {"status": AP.ASSURANCE_NONE, "reason": "dob_corrected_below_verified_threshold"}
    if new.band == AP.BAND_UNDER_16 and (req["old_band"] != AP.BAND_UNDER_16 or not user.get("parental")):
        patch["parental"] = {"state": AP.PARENTAL_PENDING, "consent_id": None, "updated_at": now, "self_consent_at": None}
    res = await db.users.update_one({"user_id": user["user_id"], "dob_enc": req["old_dob_enc"]},
                                    {"$set": patch, "$unset": {"dob_correction_pending": ""}})
    if res.matched_count != 1:
        await db[C_CORRECTIONS].update_one({"request_id": req["request_id"]}, {"$set": {"status": "stale", "closed_at": now}})
        raise DobCorrectionError(409, "request_stale", "Your date of birth changed in the meantime; retry")
    await db[C_CORRECTIONS].update_one({"request_id": req["request_id"]}, {"$set": {"status": "applied", "applied_at": now}})
    await AU.record(db, EV_APPLIED, subject_user_id=user["user_id"], actor="system",
                    details={"from_state": req["old_band"], "to_state": new.band, "reason": req["reason_code"], "status": req["direction"]}, now=now)


async def finalize_after_assurance(db, user_id: str, *, now: Optional[datetime] = None) -> Optional[dict]:
    """Apelata de age_service dupa o atestare REUSITA: daca exista o cerere in asteptare iar pragul atestat o acopera, data noua devine efectiva."""
    now = now or _now()
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    pend = pending_target(user)
    if not user or not pend:
        return None
    vt = AP.verified_threshold(user)
    if vt is None or vt < pend["required_threshold"]:
        return None
    req = await db[C_CORRECTIONS].find_one({"request_id": pend["request_id"], "user_id": user_id, "status": "awaiting_age_verification"}, {"_id": 0})
    if not req or req["old_dob_enc"] != user.get("dob_enc"):
        return None
    new = AP.evaluate_dob(AP.decrypt_dob(req["new_dob_enc"]).isoformat(), now.date())
    if new.band != pend["target_band"]:
        return None
    await _apply(db, user, req, new, now)
    return {"request_id": req["request_id"], "status": "applied", "new_band": new.band}


async def cancel(db, user: dict, *, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    pend = (user or {}).get("dob_correction_pending")
    if not isinstance(pend, dict):
        return {"cancelled": False}
    await db[C_CORRECTIONS].update_one({"request_id": pend.get("request_id"), "user_id": user["user_id"], "status": "awaiting_age_verification"},
                                       {"$set": {"status": "cancelled", "closed_at": now}})
    await db.users.update_one({"user_id": user["user_id"]}, {"$unset": {"dob_correction_pending": ""}})
    await AU.record(db, EV_CANCELLED, subject_user_id=user["user_id"], actor="user", details={"status": "cancelled"}, now=now)
    return {"cancelled": True}
