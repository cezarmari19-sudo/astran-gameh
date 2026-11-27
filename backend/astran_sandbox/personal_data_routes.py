"""/api/account/personal-data, /api/account/email-change/*, /api/account/dob-correction/* (GDPR Art. 16).

Strat subtire peste personal_data.py / dob_correction.py. Toate rutele de modificare cer autentificare + reautentificare
(parola sau cod pe e-mailul curent); confirmarea si revenirea la e-mail se fac cu token single-use din CORPUL cererii.
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import age_policy as AP
from . import dob_correction as DC
from . import email_verification as EV
from . import personal_data as PD
from . import privacy
from . import project_security as psec
from .age_assurance import get_provider as get_assurance_provider
from .ratelimit import SlidingWindowLimiter

_user_limiter = SlidingWindowLimiter(30, 3600)
_ip_limiter = SlidingWindowLimiter(30, 600)            # rute cu token (publice) / 10 min / IP
_token_fail_limiter = SlidingWindowLimiter(10, 600)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailChangeBody(_Strict):
    new_email: str = Field(max_length=320)
    password: Optional[str] = Field(default=None, max_length=128)
    reauth_code: Optional[str] = Field(default=None, max_length=12)


class TokenBody(_Strict):
    token: str = Field(max_length=200)


class DobCorrectionBody(_Strict):
    date_of_birth: str = Field(max_length=32)
    reason_code: str = Field(max_length=40)
    reason_text: Optional[str] = Field(default=None, max_length=600)
    password: Optional[str] = Field(default=None, max_length=128)
    reauth_code: Optional[str] = Field(default=None, max_length=12)


def _wait(limiter: SlidingWindowLimiter, key: str) -> None:
    w = limiter.hit(key)
    if w is not None:
        raise HTTPException(status_code=429, detail={"code": "too_many_requests", "retry_after": w}, headers={"Retry-After": str(w)})


def _err(e: Exception) -> HTTPException:
    if isinstance(e, (PD.PersonalDataError, DC.DobCorrectionError)):
        return HTTPException(status_code=e.status, detail=e.detail(),
                             headers={"Retry-After": str(e.retry_after)} if getattr(e, "retry_after", None) else None)
    if isinstance(e, AP.AgeError):
        return HTTPException(status_code=e.status, detail=e.detail())
    raise e


def make_personal_data_router(get_current_user, db, *, verify_pw: Callable[[str, str], Awaitable[bool]], client_ip: Callable[[Request], str],
                              notifier: Optional[PD.Notifier] = None, app_base_url: Optional[str] = None,
                              on_email_changed: Optional[Callable[[str], Awaitable[None]]] = None) -> APIRouter:
    router = APIRouter(prefix="/account", tags=["personal-data"])
    base = lambda: (app_base_url or os.environ.get("ASTRAN_PUBLIC_APP_URL") or "").rstrip("/")  # noqa: E731

    async def _reauth(user: dict, password: Optional[str], code: Optional[str], purpose: str, request: Request) -> str:
        """Reautentificare prin serviciul EXISTENT (privacy.verify_reauth) + rate limiting / jurnal de securitate existente (psec)."""
        uid = user["user_id"]
        await psec.ensure_rate_limit_or_raise(db, user_id=uid, scope_id="account", purpose=purpose)
        full = await db.users.find_one({"user_id": uid}, {"_id": 0}) or user
        try:
            await privacy.verify_reauth(db, full, password=password, code=code, verify_pw=verify_pw, emailver=EV,
                                        session_age_seconds=None,      # fail-closed: fara scurtatura "sesiune Google proaspata"
                                        email_is_configured=psec.email_is_configured())
        except privacy.ReauthError as e:
            if e.code in ("BAD_PASSWORD", "BAD_CODE"):
                await psec.record_failed_attempt(db, user_id=uid, scope_id="account", purpose=purpose)
            await psec.log_security_event(db, action=purpose, result="denied", user_id=uid, detail=e.code, ip=client_ip(request))
            raise HTTPException(status_code=e.status, detail={"code": "reauth_failed" if e.code != "REAUTH_REQUIRED" else "reauth_required", "message": e.detail})
        await psec.reset_attempts(db, user_id=uid, scope_id="account", purpose=purpose)
        return "password" if full.get("password_hash") else "email_code"

    @router.get("/personal-data")
    async def overview(current=Depends(get_current_user)):
        _wait(_user_limiter, "pd:" + current["user_id"])
        prov = get_assurance_provider()
        st = await DC.status(db, current, provider_configured=prov.configured())
        info = AP.evaluate_access(current)
        return {"email": current.get("email"), "email_verified": bool(current.get("email_verified")),
                "reauth_method": "password" if current.get("has_password") else "email_code",   # codul se cere de la POST /privacy/reauth/code (existent)
                "pending_email_change": await PD.pending_summary(db, current["user_id"]),
                "date_of_birth": {"set": st["has_date_of_birth"], "age_band": info.band, "correction": st},
                "email_service_available": notifier is not None}

    # ------------------------------------------------------------------ e-mail
    @router.post("/email-change/request", status_code=202)
    async def email_change_request(body: EmailChangeBody, request: Request, current=Depends(get_current_user)):
        _wait(_user_limiter, "pd:" + current["user_id"])
        try:
            method = await _reauth(current, body.password, body.reauth_code, "email_change", request)
            return await PD.request_email_change(db, current, body.new_email, reauth_method=method, notifier=notifier, confirm_url_base=base())
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/email-change/cancel")
    async def email_change_cancel(current=Depends(get_current_user)):
        _wait(_user_limiter, "pd:" + current["user_id"])
        return await PD.cancel_pending(db, current)

    @router.post("/email-change/confirm")
    async def email_change_confirm(body: TokenBody, request: Request):
        ip = client_ip(request)
        _wait(_ip_limiter, "ecc:" + ip)
        try:
            return await PD.confirm_email_change(db, body.token, on_email_changed=on_email_changed)
        except PD.PersonalDataError as e:
            if e.code == "invalid_token":
                _wait(_token_fail_limiter, "ecf:" + ip)
            raise _err(e)

    @router.post("/email-change/revert")
    async def email_change_revert(body: TokenBody, request: Request):
        ip = client_ip(request)
        _wait(_ip_limiter, "ecr:" + ip)
        try:
            return await PD.revert_email_change(db, body.token, on_email_changed=on_email_changed)
        except PD.PersonalDataError as e:
            if e.code == "invalid_token":
                _wait(_token_fail_limiter, "ecf:" + ip)
            raise _err(e)

    # ------------------------------------------------------------------ data nasterii
    @router.get("/dob-correction")
    async def dob_status(current=Depends(get_current_user)):
        _wait(_user_limiter, "pd:" + current["user_id"])
        return await DC.status(db, current, provider_configured=get_assurance_provider().configured())

    @router.post("/dob-correction", status_code=202)
    async def dob_request(body: DobCorrectionBody, request: Request, current=Depends(get_current_user)):
        _wait(_user_limiter, "pd:" + current["user_id"])
        try:
            method = await _reauth(current, body.password, body.reauth_code, "dob_correction", request)
            return await DC.request_correction(db, current, body.date_of_birth, body.reason_code, body.reason_text, reauth_method=method,
                                               provider_configured=get_assurance_provider().configured())
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/dob-correction/cancel")
    async def dob_cancel(current=Depends(get_current_user)):
        _wait(_user_limiter, "pd:" + current["user_id"])
        return await DC.cancel(db, current)

    return router
