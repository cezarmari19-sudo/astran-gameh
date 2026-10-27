"""
Astran Game - Secure Project Deletion & Recovery
=================================================

Acest modul contine TOATA logica de securitate server-side pentru:
  - parola de securitate a proiectului (hash Argon2id, niciodata in clar)
  - rate limiting / anti brute-force pe incercari (persistat in Mongo, nu in memorie,
    ca sa supravietuiasca unui restart al backendului)
  - jurnal de securitate (audit log) pentru fiecare operatiune sensibila
  - tokenuri de recuperare cu expirare si folosire unica (email code)
  - WebAuthn / passkeys ca al doilea factor biometric, pentru recuperare si reset parola

Nimic din acest fisier nu "simuleaza" o verificare - daca o dependinta (argon2-cffi sau
webauthn) lipseste din mediul de rulare, functiile relevante arunca explicit o eroare
(RuntimeError / HTTPException 501), NU trec verificarea implicit. Vezi backend/requirements.txt.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

from fastapi import HTTPException

log = logging.getLogger("astran.security")

# ---------------------------------------------------------------------------
# Argon2id - hash-ul parolei de securitate a proiectului
# ---------------------------------------------------------------------------
# NU folosim bcrypt aici (desi e deja folosit pentru parola de cont) pentru ca cerinta
# explicita e Argon2id. argon2-cffi implementeaza Argon2id by default din versiunea 19+.
try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError
    _ph = PasswordHasher(
        time_cost=3,       # iteratii
        memory_cost=65536, # 64 MB
        parallelism=4,
        hash_len=32,
        salt_len=16,       # salt unic, generat random la fiecare hash (inclus in output)
    )
    ARGON2_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 - lipsa dependintei nu trebuie sa pice tot serverul
    _ph = None
    ARGON2_AVAILABLE = False
    log.warning("argon2-cffi not installed - project-password hashing is DISABLED: %s", exc)


PROJECT_PASSWORD_MIN = 6
PROJECT_PASSWORD_MAX = 30


def validate_project_password_shape(pw: str, pw_confirm: str) -> None:
    """Validare de forma (lungime + confirmare) - NU restrictioneaza setul de caractere
    (litere mari/mici, cifre, caractere speciale sunt toate permise), si NU limiteaza
    parola exclusiv la cifre: orice parola care respecta doar lungimea e acceptata."""
    if pw != pw_confirm:
        raise HTTPException(status_code=400, detail="Parolele introduse nu coincid")
    if not (PROJECT_PASSWORD_MIN <= len(pw) <= PROJECT_PASSWORD_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"Parola proiectului trebuie sa aiba intre {PROJECT_PASSWORD_MIN} si {PROJECT_PASSWORD_MAX} caractere",
        )
    if not pw.isprintable() or "\n" in pw or "\r" in pw:
        raise HTTPException(status_code=400, detail="Parola contine caractere nepermise")


def hash_project_password(pw: str) -> str:
    if not ARGON2_AVAILABLE:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: argon2-cffi not installed (see backend/requirements.txt)",
        )
    return _ph.hash(pw)


def verify_project_password(pw: str, pw_hash: Optional[str]) -> bool:
    """Intotdeauna 'constant-shape': daca nu exista hash, tot facem un hash dummy ca sa nu
    scurgem prin timing daca proiectul are sau nu parola setata."""
    if not ARGON2_AVAILABLE:
        raise HTTPException(status_code=500, detail="Server misconfigured: argon2-cffi not installed")
    if not pw_hash:
        try:
            _ph.verify(_ph.hash(secrets.token_urlsafe(16)), pw)
        except Exception:
            pass
        return False
    try:
        _ph.verify(pw_hash, pw)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001
        return False


def project_password_needs_rehash(pw_hash: str) -> bool:
    if not ARGON2_AVAILABLE:
        return False
    try:
        return _ph.check_needs_rehash(pw_hash)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Rate limiting / anti brute-force - persistat in Mongo (collection: security_attempts)
# ---------------------------------------------------------------------------
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_MINUTES = 15
LOCKOUT_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_rate_limit_or_raise(db, *, user_id: str, scope_id: str, purpose: str) -> None:
    """scope_id = de regula game_id (sau user_id pentru actiuni care nu tin de un joc anume).
    Ridica 429 daca actorul e blocat temporar dupa prea multe incercari gresite recente."""
    doc = await db.security_attempts.find_one({"user_id": user_id, "scope_id": scope_id, "purpose": purpose})
    if not doc:
        return
    now = _now()
    locked_until = doc.get("locked_until")
    if locked_until:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > now:
            retry_after = int((locked_until - now).total_seconds())
            raise HTTPException(
                status_code=429,
                detail=f"Prea multe incercari esuate. Reincearca peste {max(retry_after // 60, 1)} minute.",
                headers={"Retry-After": str(retry_after)},
            )


async def record_failed_attempt(db, *, user_id: str, scope_id: str, purpose: str) -> None:
    now = _now()
    window_start = now - timedelta(minutes=ATTEMPT_WINDOW_MINUTES)
    doc = await db.security_attempts.find_one({"user_id": user_id, "scope_id": scope_id, "purpose": purpose})
    if not doc or (doc.get("window_start") and doc["window_start"].replace(tzinfo=timezone.utc) < window_start):
        count = 1
        new_window = now
    else:
        count = doc.get("count", 0) + 1
        new_window = doc.get("window_start", now)
    update = {
        "user_id": user_id, "scope_id": scope_id, "purpose": purpose,
        "count": count, "window_start": new_window, "updated_at": now,
    }
    if count >= MAX_ATTEMPTS:
        update["locked_until"] = now + timedelta(minutes=LOCKOUT_MINUTES)
    await db.security_attempts.update_one(
        {"user_id": user_id, "scope_id": scope_id, "purpose": purpose},
        {"$set": update},
        upsert=True,
    )


async def reset_attempts(db, *, user_id: str, scope_id: str, purpose: str) -> None:
    await db.security_attempts.delete_one({"user_id": user_id, "scope_id": scope_id, "purpose": purpose})


# ---------------------------------------------------------------------------
# Jurnal de securitate (audit log) - niciodata parole/tokenuri/secrete in clar
# ---------------------------------------------------------------------------
async def log_security_event(
    db, *, action: str, result: str, user_id: Optional[str], game_id: Optional[str] = None,
    detail: Optional[str] = None, ip: Optional[str] = None,
) -> None:
    await db.security_audit_log.insert_one({
        "event_id": secrets.token_hex(12),
        "action": action,           # ex: "project_delete", "project_recover", "password_reset"
        "result": result,           # "success" | "denied" | "failed"
        "user_id": user_id,
        "game_id": game_id,
        "detail": detail,           # text non-sensibil (ex: "not_owner", "bad_password")
        "ip": ip,
        "created_at": _now(),
    })


# ---------------------------------------------------------------------------
# Recovery tokens (email code) - folosite pentru: stergere/recuperare proiect, reset
# parola proiect. Codul propriu-zis NU e stocat in clar, doar hash-ul lui (HMAC-SHA256
# cu un secret de server) - acelasi principiu ca pentru parole: serverul nu pastreaza
# secretul recuperabil in clar nicaieri.
# ---------------------------------------------------------------------------
RECOVERY_TOKEN_TTL_MINUTES = 15
RECOVERY_CODE_LEN = 6

TOKEN_SECRET = os.environ.get("JWT_SECRET", "astran-dev-secret-change-in-prod").encode()


def _code_hash(code: str, token_id: str) -> str:
    # token_id e amestecat in HMAC ca "salt" per-token, ca doi coduri identice intamplator
    # generate pentru doi useri sa nu aiba acelasi hash stocat.
    return hmac.new(TOKEN_SECRET, (token_id + ":" + code).encode(), hashlib.sha256).hexdigest()


@dataclass
class RecoveryToken:
    token_id: str
    code: str  # doar pentru a fi trimis prin email ACUM - nu se stocheaza niciodata


async def create_recovery_token(
    db, *, user_id: str, game_id: Optional[str], purpose: str,
) -> RecoveryToken:
    token_id = secrets.token_urlsafe(18)
    code = f"{secrets.randbelow(10 ** RECOVERY_CODE_LEN):0{RECOVERY_CODE_LEN}d}"
    now = _now()
    await db.recovery_tokens.insert_one({
        "token_id": token_id,
        "code_hash": _code_hash(code, token_id),
        "user_id": user_id,
        "game_id": game_id,
        "purpose": purpose,  # "project_delete" | "project_recover" | "project_password_reset"
        "stage": "created",  # -> "email_verified" -> "biometric_verified" (daca necesar) -> "used"
        "attempts": 0,
        "used": False,
        "created_at": now,
        "expires_at": now + timedelta(minutes=RECOVERY_TOKEN_TTL_MINUTES),
    })
    return RecoveryToken(token_id=token_id, code=code)


async def get_valid_token(db, *, token_id: str, user_id: str, game_id: Optional[str], purpose: str) -> dict:
    doc = await db.recovery_tokens.find_one({"token_id": token_id}, {"_id": 0})
    if not doc or doc["user_id"] != user_id or doc["purpose"] != purpose or doc.get("game_id") != game_id:
        raise HTTPException(status_code=400, detail="Token de recuperare invalid")
    if doc.get("used"):
        raise HTTPException(status_code=400, detail="Acest cod a fost deja folosit")
    expires_at = doc["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < _now():
        raise HTTPException(status_code=400, detail="Codul de verificare a expirat")
    return doc


async def verify_recovery_code(db, *, token_id: str, user_id: str, game_id: Optional[str], purpose: str, code: str) -> dict:
    doc = await get_valid_token(db, token_id=token_id, user_id=user_id, game_id=game_id, purpose=purpose)
    if doc.get("attempts", 0) >= 5:
        raise HTTPException(status_code=429, detail="Prea multe incercari - solicita un cod nou")
    if not hmac.compare_digest(doc["code_hash"], _code_hash(code, token_id)):
        await db.recovery_tokens.update_one({"token_id": token_id}, {"$inc": {"attempts": 1}})
        raise HTTPException(status_code=401, detail="Cod de verificare incorect")
    await db.recovery_tokens.update_one({"token_id": token_id}, {"$set": {"stage": "email_verified"}})
    doc["stage"] = "email_verified"
    return doc


async def mark_token_stage(db, *, token_id: str, stage: str) -> None:
    await db.recovery_tokens.update_one({"token_id": token_id}, {"$set": {"stage": stage}})


async def consume_token(db, *, token_id: str) -> None:
    await db.recovery_tokens.update_one({"token_id": token_id}, {"$set": {"used": True, "stage": "used"}})


async def invalidate_other_tokens(db, *, user_id: str, game_id: Optional[str], purpose: str, keep_token_id: str) -> None:
    await db.recovery_tokens.update_many(
        {"user_id": user_id, "game_id": game_id, "purpose": purpose, "token_id": {"$ne": keep_token_id}, "used": False},
        {"$set": {"used": True, "stage": "invalidated"}},
    )


# ---------------------------------------------------------------------------
# Email - trimitere reala prin SMTP daca e configurat (SMTP_HOST / SMTP_PORT / SMTP_USER /
# SMTP_PASS / SMTP_FROM in .env). Daca NU e configurat, codul NU e expus nicaieri in
# raspunsul API si NU e scris in log in productie - doar un warning ca emailul nu a putut
# fi livrat. Singura exceptie e un flag explicit de dezvoltare (vezi mai jos), dezactivat
# implicit, pentru ca fluxul sa poata fi testat local fara un provider real de email.
# ---------------------------------------------------------------------------
SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_FROM = os.environ.get("SMTP_FROM", "security@astran.game")
DEV_EXPOSE_RECOVERY_CODES = os.environ.get("ASTRAN_DEV_EXPOSE_RECOVERY_CODES", "false").lower() == "true"


def email_is_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def send_recovery_code_email(to_email: str, code: str, *, purpose: str, game_title: Optional[str] = None) -> bool:
    subject_map = {
        "project_delete": "Confirmare stergere proiect - Astran Game Studio",
        "project_recover": "Cod de recuperare proiect - Astran Game Studio",
        "project_password_reset": "Resetare parola de securitate - Astran Game Studio",
    }
    subject = subject_map.get(purpose, "Cod de securitate - Astran Game Studio")
    body = (
        f"Codul tau de verificare este: {code}\n\n"
        f"Acest cod expira in {RECOVERY_TOKEN_TTL_MINUTES} minute si poate fi folosit o singura data.\n"
        + (f"Proiect: {game_title}\n" if game_title else "")
        + "Daca nu ai initiat aceasta actiune in Astran Game Studio, ignora acest email si "
          "verifica securitatea contului tau."
    )
    if not email_is_configured():
        log.warning("SMTP not configured - recovery email NOT sent to %s (purpose=%s)", to_email, purpose)
        if DEV_EXPOSE_RECOVERY_CODES:
            log.warning("[DEV ONLY, ASTRAN_DEV_EXPOSE_RECOVERY_CODES=true] code=%s", code)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send recovery email: %s", exc)
        return False


# ---------------------------------------------------------------------------
# WebAuthn / Passkeys - al doilea factor biometric pentru recuperare si reset.
# Foloseste mecanismul securizat al platformei (Touch ID / Face ID / Windows Hello /
# amprenta Android) prin API-ul standard WebAuthn - serverul nu vede si nu stocheaza
# NICIODATA date biometrice brute, doar o cheie publica si un contor de semnaturi.
# ---------------------------------------------------------------------------
RP_ID = os.environ.get("ASTRAN_RP_ID", "localhost")
RP_NAME = "Astran Game Studio"
RP_ORIGIN = os.environ.get("ASTRAN_RP_ORIGIN", "http://localhost:8081")

try:
    import webauthn
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        UserVerificationRequirement,
        AuthenticatorSelectionCriteria,
        ResidentKeyRequirement,
    )
    WEBAUTHN_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    webauthn = None  # type: ignore
    WEBAUTHN_AVAILABLE = False
    log.warning("webauthn package not installed - biometric/passkey recovery is DISABLED: %s", exc)


def require_webauthn() -> None:
    if not WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="Biometric recovery is not configured on this server (webauthn package missing). "
                   "Account-password + email verification remain required and are not bypassed.",
        )


async def webauthn_registration_options(db, *, user_id: str, username: str):
    require_webauthn()
    existing = await db.webauthn_credentials.find({"user_id": user_id}, {"_id": 0, "credential_id": 1}).to_list(50)
    options = webauthn.generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_id.encode(),
        user_name=username,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=bytes.fromhex(c["credential_id"])) for c in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,  # forteaza biometrie/PIN pe device
        ),
    )
    challenge_id = secrets.token_urlsafe(16)
    await db.webauthn_challenges.insert_one({
        "challenge_id": challenge_id, "user_id": user_id, "challenge": options.challenge.hex(),
        "purpose": "register", "created_at": _now(), "expires_at": _now() + timedelta(minutes=5), "used": False,
    })
    return challenge_id, webauthn.options_to_json(options)


async def webauthn_verify_registration(db, *, user_id: str, challenge_id: str, credential_json: str, label: Optional[str] = None):
    require_webauthn()
    ch = await db.webauthn_challenges.find_one({"challenge_id": challenge_id, "user_id": user_id, "used": False})
    if not ch:
        raise HTTPException(status_code=400, detail="Provocare WebAuthn invalida sau expirata")
    verification = webauthn.verify_registration_response(
        credential=credential_json,
        expected_challenge=bytes.fromhex(ch["challenge"]),
        expected_origin=RP_ORIGIN,
        expected_rp_id=RP_ID,
    )
    await db.webauthn_challenges.update_one({"challenge_id": challenge_id}, {"$set": {"used": True}})
    await db.webauthn_credentials.insert_one({
        "credential_id": verification.credential_id.hex(),
        "public_key": verification.credential_public_key.hex(),
        "sign_count": verification.sign_count,
        "user_id": user_id,
        "label": label or "Passkey",
        "created_at": _now(),
    })
    return True


async def webauthn_authentication_options(db, *, user_id: str):
    require_webauthn()
    creds = await db.webauthn_credentials.find({"user_id": user_id}, {"_id": 0, "credential_id": 1}).to_list(50)
    if not creds:
        raise HTTPException(status_code=404, detail="Niciun passkey/biometrie inregistrat pentru acest cont")
    options = webauthn.generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=bytes.fromhex(c["credential_id"])) for c in creds],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = secrets.token_urlsafe(16)
    await db.webauthn_challenges.insert_one({
        "challenge_id": challenge_id, "user_id": user_id, "challenge": options.challenge.hex(),
        "purpose": "authenticate", "created_at": _now(), "expires_at": _now() + timedelta(minutes=5), "used": False,
    })
    return challenge_id, webauthn.options_to_json(options)


async def webauthn_verify_authentication(db, *, user_id: str, challenge_id: str, credential_json: str) -> bool:
    require_webauthn()
    ch = await db.webauthn_challenges.find_one({"challenge_id": challenge_id, "user_id": user_id, "used": False})
    if not ch:
        raise HTTPException(status_code=400, detail="Provocare WebAuthn invalida sau expirata")
    import json as _json
    cred_struct = _json.loads(credential_json)
    raw_id_hex = webauthn.base64url_to_bytes(cred_struct["rawId"]).hex()
    stored = await db.webauthn_credentials.find_one({"user_id": user_id, "credential_id": raw_id_hex})
    if not stored:
        raise HTTPException(status_code=400, detail="Credential biometric necunoscut")
    verification = webauthn.verify_authentication_response(
        credential=credential_json,
        expected_challenge=bytes.fromhex(ch["challenge"]),
        expected_origin=RP_ORIGIN,
        expected_rp_id=RP_ID,
        credential_public_key=bytes.fromhex(stored["public_key"]),
        credential_current_sign_count=stored["sign_count"],
        require_user_verification=True,
    )
    await db.webauthn_challenges.update_one({"challenge_id": challenge_id}, {"$set": {"used": True}})
    await db.webauthn_credentials.update_one(
        {"credential_id": raw_id_hex}, {"$set": {"sign_count": verification.new_sign_count}}
    )
    return True


async def user_has_webauthn(db, *, user_id: str) -> bool:
    return bool(await db.webauthn_credentials.find_one({"user_id": user_id}, {"_id": 1}))


async def create_security_indexes(db) -> None:
    await db.security_attempts.create_index([("user_id", 1), ("scope_id", 1), ("purpose", 1)], unique=True)
    await db.security_audit_log.create_index([("game_id", 1), ("created_at", -1)])
    await db.security_audit_log.create_index([("user_id", 1), ("created_at", -1)])
    await db.recovery_tokens.create_index("token_id", unique=True)
    await db.recovery_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.webauthn_credentials.create_index("credential_id", unique=True)
    await db.webauthn_credentials.create_index("user_id")
    await db.webauthn_challenges.create_index("challenge_id", unique=True)
    await db.webauthn_challenges.create_index("expires_at", expireAfterSeconds=0)