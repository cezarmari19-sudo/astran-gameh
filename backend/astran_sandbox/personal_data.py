"""Corectarea datelor personale (GDPR Art. 16): schimbarea adresei de e-mail + reautentificare + jurnal.

Fluxul de schimbare a e-mailului:
  1. Reautentificare: serviciul EXISTENT din privacy.py (`privacy.verify_reauth`): parola contului SAU cod trimis pe e-mailul
     curent (conturi Google, fara parola), cu rate limiting si jurnal de securitate din project_security. Nu exista un mecanism propriu.
  2. Utilizatorul cere noua adresa. Daca adresa apartine altui cont, raspunsul extern e IDENTIC (fara enumerare de conturi)
     si nu se trimite nimic; incercarea ramane in jurnal. O adresa deja asociata altui cont nu poate fi preluata niciodata
     (verificare la cerere SI la confirmare + index unic in DB).
  3. Un link cu token de 256 biti (stocat doar ca hash, single-use, expira) se trimite la NOUA adresa; pe adresa VECHE
     pleaca o notificare cu un link de anulare/revenire (valabil si dupa confirmare, `REVERT_WINDOW_DAYS`) = mecanismul de
     recuperare daca cererea nu a fost facuta de proprietar.
  4. `users.email` se schimba DOAR dupa confirmarea de la noua adresa; apoi sesiunile existente sunt invalidate.
Jurnalul `personal_data_audit` contine adrese MASCATE, niciodata parole, coduri sau token-uri.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from . import email_verification as EV

C_REQUESTS = "email_change_requests"
C_AUDIT = "personal_data_audit"
LINK_TTL_MINUTES = int(os.environ.get("ASTRAN_EMAIL_CHANGE_TTL_MINUTES", "60"))
REVERT_WINDOW_DAYS = int(os.environ.get("ASTRAN_EMAIL_CHANGE_REVERT_DAYS", "7"))
REQUEST_COOLDOWN_SECONDS = 60
MAX_REQUESTS_PER_DAY = 5
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")

Notifier = Callable[[str, str, str], Awaitable[bool]]


class PersonalDataError(Exception):
    def __init__(self, status: int, code: str, message: str = "", retry_after: Optional[int] = None):
        super().__init__(message or code)
        self.status, self.code, self.message, self.retry_after = status, code, message or code, retry_after

    def detail(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.retry_after:
            d["retry_after"] = self.retry_after
        return d


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


def _h(token: str) -> str:
    return hashlib.sha256(("email-change|" + token).encode()).hexdigest()


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    return (local[:1] + "***@" + domain[:1] + "***" + ("." + domain.rsplit(".", 1)[-1] if "." in domain else ""))


def normalize_email(raw: Any) -> str:
    if not isinstance(raw, str):
        raise PersonalDataError(422, "invalid_email", "Invalid e-mail address")
    e = raw.strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        raise PersonalDataError(422, "invalid_email", "Invalid e-mail address")
    return e


async def audit(db, user_id: str, action: str, details: Optional[dict] = None) -> None:
    safe = {k: v for k, v in (details or {}).items() if k in {"old_email_masked", "new_email_masked", "request_id", "method", "result",
                                                              "reason", "status", "direction", "old_band", "new_band", "required_threshold"}}
    await db[C_AUDIT].insert_one({"event_id": "pda_" + uuid.uuid4().hex[:16], "user_id": user_id, "action": action,
                                  "ts": now_utc(), "details": safe})


async def ensure_indexes(db) -> None:
    await db[C_REQUESTS].create_index("request_id", unique=True)
    await db[C_REQUESTS].create_index("confirm_hash", unique=True)
    await db[C_REQUESTS].create_index("revert_hash", unique=True)
    await db[C_REQUESTS].create_index([("user_id", 1), ("created_at", -1)])
    await db[C_AUDIT].create_index([("user_id", 1), ("ts", -1)])


# ----------------------------------------------------------------------------------------------- schimbare e-mail
async def _email_in_use(db, email: str, except_user: str) -> bool:
    other = await db.users.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}, "user_id": {"$ne": except_user}},
                                    {"_id": 0, "user_id": 1})
    return other is not None


async def request_email_change(db, user: dict, new_email_raw: Any, *, reauth_method: str, notifier: Optional[Notifier],
                               confirm_url_base: str) -> dict:
    """Presupune reautentificarea deja facuta (`reauth_method`). Raspunsul e acelasi indiferent daca adresa e libera."""
    if notifier is None:
        raise PersonalDataError(503, "email_not_configured", "E-mail service is not configured")
    new_email = normalize_email(new_email_raw)
    old_email = (user.get("email") or "").lower()
    if new_email == old_email:
        raise PersonalDataError(409, "same_email", "This is already your e-mail")
    now = now_utc()
    last = await db[C_REQUESTS].find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).limit(MAX_REQUESTS_PER_DAY).to_list(MAX_REQUESTS_PER_DAY)
    if last and (now - _aware(last[0]["created_at"])).total_seconds() < REQUEST_COOLDOWN_SECONDS:
        wait = REQUEST_COOLDOWN_SECONDS - int((now - _aware(last[0]["created_at"])).total_seconds())
        raise PersonalDataError(429, "too_many_requests", "Wait before requesting again", retry_after=max(1, wait))
    if len(last) >= MAX_REQUESTS_PER_DAY and (now - _aware(last[-1]["created_at"])) < timedelta(days=1):
        raise PersonalDataError(429, "too_many_requests", "Daily limit reached", retry_after=3600)
    generic = {"status": "pending_verification", "expires_in_minutes": LINK_TTL_MINUTES, "new_email_masked": mask_email(new_email)}
    if await _email_in_use(db, new_email, user["user_id"]):
        await audit(db, user["user_id"], "email_change_blocked_in_use", {"new_email_masked": mask_email(new_email), "method": reauth_method})
        return generic          # fara dezvaluire; nu se trimite nimic catre adresa straina
    await db[C_REQUESTS].update_many({"user_id": user["user_id"], "status": "pending"}, {"$set": {"status": "superseded", "closed_at": now}})
    confirm_token, revert_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    req = {"request_id": "ecr_" + uuid.uuid4().hex[:16], "user_id": user["user_id"], "old_email": old_email, "new_email": new_email,
           "confirm_hash": _h(confirm_token), "revert_hash": _h(revert_token), "status": "pending", "reauth_method": reauth_method,
           "created_at": now, "expires_at": now + timedelta(minutes=LINK_TTL_MINUTES), "confirmed_at": None, "revert_until": None}
    await db[C_REQUESTS].insert_one(req)
    base = confirm_url_base.rstrip("/")
    sent_new = await notifier(new_email, "Confirma noua adresa de e-mail - Astran Game / Confirm your new e-mail",
                              f"Pentru a confirma noua adresa de e-mail a contului, deschide linkul (expira in {LINK_TTL_MINUTES} minute):\n"
                              f"{base}/confirm-email?token={confirm_token}\n\nDaca nu ai cerut tu, ignora acest mesaj.\n\n"
                              f"To confirm your new e-mail address open the link (expires in {LINK_TTL_MINUTES} minutes):\n"
                              f"{base}/confirm-email?token={confirm_token}\nIf you didn't request this, ignore this message.")
    if not sent_new:
        await db[C_REQUESTS].update_one({"request_id": req["request_id"]}, {"$set": {"status": "failed_send", "closed_at": now_utc()}})
        raise PersonalDataError(502, "email_send_failed", "Could not send the confirmation e-mail")
    if old_email:
        await notifier(old_email, "Cerere de schimbare a e-mailului - Astran Game / E-mail change requested",
                       f"S-a cerut schimbarea e-mailului contului tau in {mask_email(new_email)}. Daca nu tu ai cerut-o, anuleaza aici "
                       f"(si dupa confirmare, timp de {REVERT_WINDOW_DAYS} zile): {base}/revert-email?token={revert_token}\n\n"
                       f"A change of your account e-mail to {mask_email(new_email)} was requested. If this wasn't you, cancel it here "
                       f"(also possible for {REVERT_WINDOW_DAYS} days after confirmation): {base}/revert-email?token={revert_token}")
    await audit(db, user["user_id"], "email_change_requested", {"request_id": req["request_id"], "old_email_masked": mask_email(old_email),
                                                                 "new_email_masked": mask_email(new_email), "method": reauth_method})
    return generic


async def confirm_email_change(db, token: Any, *, on_email_changed: Optional[Callable[[str], Awaitable[None]]] = None) -> dict:
    if not isinstance(token, str) or not (20 <= len(token) <= 200):
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    now = now_utc()
    req = await db[C_REQUESTS].find_one({"confirm_hash": _h(token)}, {"_id": 0})
    if not req or req["status"] != "pending" or _aware(req["expires_at"]) < now:
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    if await _email_in_use(db, req["new_email"], req["user_id"]):
        await db[C_REQUESTS].update_one({"request_id": req["request_id"], "status": "pending"}, {"$set": {"status": "rejected_in_use", "closed_at": now}})
        raise PersonalDataError(409, "email_unavailable", "This e-mail address cannot be used")
    claim = await db[C_REQUESTS].update_one({"request_id": req["request_id"], "status": "pending"},
                                            {"$set": {"status": "confirmed", "confirmed_at": now, "revert_until": now + timedelta(days=REVERT_WINDOW_DAYS)}})
    if claim.matched_count != 1:
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    try:
        res = await db.users.update_one({"user_id": req["user_id"], "email": req["old_email"]},
                                        {"$set": {"email": req["new_email"], "email_verified": True, "email_verified_at": now,
                                                  "email_changed_at": now, "updated_at": now}})
    except Exception as e:  # noqa: BLE001 - index unic pe email (cursa): adresa a fost luata intre timp
        if "Duplicate" in type(e).__name__ or "E11000" in str(e):
            await db[C_REQUESTS].update_one({"request_id": req["request_id"]}, {"$set": {"status": "rejected_in_use", "closed_at": now}})
            raise PersonalDataError(409, "email_unavailable", "This e-mail address cannot be used")
        raise
    if res.matched_count != 1:       # emailul contului s-a schimbat intre timp (alta cerere) sau contul nu mai exista
        await db[C_REQUESTS].update_one({"request_id": req["request_id"]}, {"$set": {"status": "stale", "closed_at": now}})
        raise PersonalDataError(409, "request_stale", "The request is no longer valid")
    await db[EV.COLLECTION].delete_many({"user_id": req["user_id"]})
    await audit(db, req["user_id"], "email_changed", {"request_id": req["request_id"], "old_email_masked": mask_email(req["old_email"]),
                                                      "new_email_masked": mask_email(req["new_email"])})
    if on_email_changed:
        await on_email_changed(req["user_id"])
    return {"status": "confirmed", "email": req["new_email"]}


async def revert_email_change(db, token: Any, *, on_email_changed: Optional[Callable[[str], Awaitable[None]]] = None) -> dict:
    """Linkul din notificarea trimisa pe adresa VECHE: anuleaza o cerere in asteptare sau readuce adresa veche (in fereastra)."""
    if not isinstance(token, str) or not (20 <= len(token) <= 200):
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    now = now_utc()
    req = await db[C_REQUESTS].find_one({"revert_hash": _h(token)}, {"_id": 0})
    if not req:
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    if req["status"] == "pending":
        await db[C_REQUESTS].update_one({"request_id": req["request_id"], "status": "pending"}, {"$set": {"status": "cancelled_by_owner", "closed_at": now}})
        await audit(db, req["user_id"], "email_change_cancelled", {"request_id": req["request_id"], "result": "cancelled_pending"})
        return {"status": "cancelled"}
    if req["status"] != "confirmed" or not req.get("revert_until") or _aware(req["revert_until"]) < now:
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    claim = await db[C_REQUESTS].update_one({"request_id": req["request_id"], "status": "confirmed"}, {"$set": {"status": "reverted", "closed_at": now}})
    if claim.matched_count != 1:
        raise PersonalDataError(400, "invalid_token", "Invalid or expired link")
    try:
        res = await db.users.update_one({"user_id": req["user_id"], "email": req["new_email"]},
                                        {"$set": {"email": req["old_email"], "email_verified": True, "email_changed_at": now, "email_reverted_at": now}})
    except Exception as e:  # noqa: BLE001
        if "Duplicate" in type(e).__name__ or "E11000" in str(e):
            raise PersonalDataError(409, "manual_recovery_required", "The previous address is no longer available; contact support")
        raise
    if res.matched_count != 1:
        raise PersonalDataError(409, "request_stale", "The request is no longer valid")
    await audit(db, req["user_id"], "email_change_reverted", {"request_id": req["request_id"], "old_email_masked": mask_email(req["new_email"]),
                                                              "new_email_masked": mask_email(req["old_email"]), "result": "reverted_after_confirmation"})
    if on_email_changed:
        await on_email_changed(req["user_id"])
    return {"status": "reverted"}


async def cancel_pending(db, user: dict) -> dict:
    r = await db[C_REQUESTS].update_many({"user_id": user["user_id"], "status": "pending"}, {"$set": {"status": "cancelled_by_user", "closed_at": now_utc()}})
    if r.modified_count:
        await audit(db, user["user_id"], "email_change_cancelled", {"result": "cancelled_by_user"})
    return {"cancelled": r.modified_count}


async def pending_summary(db, user_id: str) -> Optional[dict]:
    req = await db[C_REQUESTS].find_one({"user_id": user_id, "status": "pending"}, {"_id": 0})
    if not req or _aware(req["expires_at"]) < now_utc():
        return None
    return {"new_email_masked": mask_email(req["new_email"]), "expires_at": req["expires_at"]}
