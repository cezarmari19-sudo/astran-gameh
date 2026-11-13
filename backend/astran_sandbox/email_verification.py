"""Verificarea proprietatii emailului (cod de 6 cifre trimis pe email).

PROBLEMA: emailul din /auth/register nu era verificat nicaieri. Consecinte: (1) pre-hijacking -
cineva inregistra emailul victimei cu parola lui, iar cand victima intra cu Google ajungea in contul
pe care atacatorul il putea accesa in continuare; (2) conturi-farm: 100 Astrans gratis per cont, cu
orice adresa inventata; (3) codurile de recuperare ale proiectelor mergeau la un email neconfirmat.

Codul se stocheaza DOAR ca hash (salt = user_id + email), expira, are limita de incercari si
cooldown la retrimitere. Doar stdlib; db-ul e injectat (testabil fara MongoDB).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

CODE_TTL_MINUTES = 15
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
COLLECTION = "email_verifications"


class VerificationError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(user_id: str, email: str, code: str) -> str:
    return hashlib.sha256(f"{user_id}\x00{email.lower()}\x00{code}".encode()).hexdigest()


async def issue(db, user_id: str, email: str, now: datetime | None = None,
                collection: str = COLLECTION) -> str:
    """Genereaza si stocheaza un cod nou; ridica VerificationError(429) in cooldown."""
    now = now or _now()
    coll = db[collection]
    cur = await coll.find_one({"user_id": user_id}, {"_id": 0})
    if cur:
        created = _aware(cur.get("created_at"))
        if created and (now - created).total_seconds() < RESEND_COOLDOWN_SECONDS:
            wait = RESEND_COOLDOWN_SECONDS - int((now - created).total_seconds())
            raise VerificationError(429, "Wait before requesting another code", retry_after=max(1, wait))
        await coll.delete_one({"user_id": user_id})
    code = new_code()
    await coll.insert_one({
        "user_id": user_id, "email": email.lower(), "code_hash": hash_code(user_id, email, code),
        "attempts": 0, "created_at": now, "expires_at": now + timedelta(minutes=CODE_TTL_MINUTES),
    })
    return code


async def verify(db, user_id: str, email: str, code: str, now: datetime | None = None,
                 collection: str = COLLECTION) -> None:
    """Ridica VerificationError daca codul e gresit / expirat / blocat. La succes consuma codul."""
    now = now or _now()
    coll = db[collection]
    cur = await coll.find_one({"user_id": user_id}, {"_id": 0})
    if not cur or cur.get("email") != email.lower():
        raise VerificationError(400, "No active verification code. Request a new one.")
    exp = _aware(cur.get("expires_at"))
    if exp is None or exp < now:
        await coll.delete_one({"user_id": user_id})
        raise VerificationError(400, "The code has expired. Request a new one.")
    # Incrementam INAINTE de comparatie si atomic cu conditia `attempts < MAX`: cereri paralele nu
    # pot depasi limita (1.000.000 de coduri posibile; fara limita ar fi fortabil prin brute force).
    res = await coll.update_one({"user_id": user_id, "attempts": {"$lt": MAX_ATTEMPTS}}, {"$inc": {"attempts": 1}})
    if res.matched_count != 1:
        await coll.delete_one({"user_id": user_id})
        raise VerificationError(429, "Too many wrong attempts. Request a new code.")
    ok = isinstance(code, str) and hmac.compare_digest(cur["code_hash"], hash_code(user_id, email, code.strip()))
    if not ok:
        raise VerificationError(400, "Incorrect code")
    await coll.delete_one({"user_id": user_id})