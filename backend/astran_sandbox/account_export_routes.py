"""Endpointuri GDPR Art. 15 / 20: /api/account/data-export  (export ZIP asincron). Logica e in privacy_export.py.

Se ataseaza din server.py, langa routerul /privacy:
    api.include_router(make_account_export_router(get_current_user, db, verify_pw=verify_pw_async,
                                                  client_ip=_client_ip, psec=psec, emailver=emailver))

  GET  /api/account/data-export            starea ultimului export + informatii (fara date personale in afara de emailul mascat)
  POST /api/account/data-export            cere un export nou (RE-AUTENTIFICARE obligatorie); 202, jobul ruleaza in fundal
  GET  /api/account/data-export/download   descarca arhiva ZIP gata a UTILIZATORULUI DIN SESIUNE

Securitate (toate pe server):
  * toate rutele cer utilizator autentificat; actiunea se aplica DOAR contului din token. Nicio ruta nu accepta un
    `user_id`/`export_id` din URL sau body -> nu exista ce sa fie modificat pentru IDOR; descarcarea cauta jobul dupa
    `user_id` din sesiune. Orice esec la descarcare (alt cont, expirat, epuizat, lipsa, corupt) = acelasi 404;
  * re-autentificare (parola / cod pe email pentru Google) la cerere, cu blocare la parole gresite (acelasi mecanism ca la
    stergere); rate limiting pe utilizator; corpul e validat de pydantic (lungimi maxime, fara campuri extra relevante);
  * arhiva: director privat, nume aleator, expirare, numar maxim de descarcari, stergere automata (vezi privacy_export.py);
  * audit: se logheaza doar actiunea, rezultatul si un cod (niciodata continutul exportului).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import privacy, privacy_export as PX
from .ratelimit import SlidingWindowLimiter

log = logging.getLogger("astran.privacy.account_export")

_request_limiter = SlidingWindowLimiter(5, 3600)        # cereri de export / ora / utilizator
_download_limiter = SlidingWindowLimiter(20, 3600)      # descarcari / ora / utilizator
_status_limiter = SlidingWindowLimiter(120, 60)         # polling stare / minut / utilizator

_BG_TASKS: set = set()                                  # referinte tari: un task fara referinta poate fi colectat de GC


class ExportRequestBody(BaseModel):
    password: Optional[str] = Field(default=None, max_length=128)
    code: Optional[str] = Field(default=None, max_length=12)
    confirm: bool = False                               # confirmarea explicita a utilizatorului (UI-ul o cere)


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    name, _, dom = email.partition("@")
    return (name[:1] + "***@" + dom) if name else None


def make_account_export_router(get_current_user: Callable, db, *, verify_pw: Callable, client_ip: Callable,
                               psec, emailver) -> APIRouter:
    router = APIRouter(prefix="/account", tags=["account-export"])

    def _too_many(wait: int) -> HTTPException:
        return HTTPException(status_code=429, detail="Prea multe cereri. Incearca din nou mai tarziu.",
                             headers={"Retry-After": str(wait)})

    async def _full_user(uid: str) -> dict:
        u = await db.users.find_one({"user_id": uid}, {"_id": 0})
        if not u:
            raise HTTPException(status_code=401, detail="Invalid token")
        return u

    async def _session_age(authorization: Optional[str]) -> Optional[float]:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        sess = await db.user_sessions.find_one({"session_token": authorization.split(" ", 1)[1].strip()},
                                               {"_id": 0, "created_at": 1})
        created = (sess or {}).get("created_at")
        if not isinstance(created, datetime):
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds()

    async def _log(action: str, result: str, uid: str, detail: Optional[str], request: Optional[Request] = None) -> None:
        try:
            await psec.log_security_event(db, action=action, result=result, user_id=uid, detail=detail,
                                          ip=client_ip(request) if request is not None else None)
        except Exception:  # noqa: BLE001
            log.warning("account_export: audit log esuat", exc_info=True)

    async def _reauth(current: dict, body: ExportRequestBody, authorization: Optional[str], request: Request) -> None:
        uid = current["user_id"]
        purpose = "data_export"
        await psec.ensure_rate_limit_or_raise(db, user_id=uid, scope_id="account", purpose=purpose)
        full = await _full_user(uid)
        try:
            await privacy.verify_reauth(
                db, full, password=body.password, code=body.code, verify_pw=verify_pw, emailver=emailver,
                session_age_seconds=await _session_age(authorization), email_is_configured=psec.email_is_configured())
        except privacy.ReauthError as e:
            if e.code in ("BAD_PASSWORD", "BAD_CODE"):
                await psec.record_failed_attempt(db, user_id=uid, scope_id="account", purpose=purpose)
            await _log(purpose, "denied", uid, e.code, request)
            raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.detail})
        await psec.reset_attempts(db, user_id=uid, scope_id="account", purpose=purpose)

    async def _log_event(**kw) -> None:                 # pentru worker-ul de generare (fara request/IP)
        await psec.log_security_event(db, **kw)

    @router.get("/data-export")
    async def data_export_status(current=Depends(get_current_user)):
        uid = current["user_id"]
        wait = _status_limiter.hit(uid)
        if wait is not None:
            raise _too_many(wait)
        full = await _full_user(uid)
        job = await PX.latest_job(db, uid)
        return {
            "export": PX.public_view(job),
            "reauth": {"has_password": bool(full.get("password_hash")), "email_masked": _mask_email(full.get("email")),
                       "email_service_available": bool(psec.email_is_configured())},
            "info": {
                "archive_name": PX.ARCHIVE_NAME,
                "archive_ttl_seconds": PX.archive_ttl_seconds(),
                "max_downloads": PX.MAX_DOWNLOADS,
                "files": list(PX._JSON_FILES) + ["README.txt"],
                "legal_response_days": PX.LEGAL_RESPONSE_DAYS,
                "legal_extension_months": PX.LEGAL_EXTENSION_MONTHS,
                "retention": privacy.retention_policy(),
                "contact_email": os.environ.get("PRIVACY_CONTACT_EMAIL") or None,
            },
        }

    @router.post("/data-export")
    async def data_export_request(body: ExportRequestBody, request: Request, current=Depends(get_current_user),
                                  authorization: Optional[str] = Header(None)):
        uid = current["user_id"]
        wait = _request_limiter.hit(uid)
        if wait is not None:
            raise _too_many(wait)
        if body.confirm is not True:
            raise HTTPException(status_code=400, detail={"code": "CONFIRMATION_REQUIRED",
                                                         "message": "Confirmare explicita necesara"})
        await _reauth(current, body, authorization, request)
        job, created = await PX.request_job(db, uid)
        if created:
            await _log("data_export", "requested", uid, "job_created", request)
            task = asyncio.create_task(PX.run_job(db, job["export_id"], _log_event))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
        else:
            await _log("data_export", "success", uid, "existing_job_returned", request)
        return JSONResponse(status_code=202 if created else 200,
                            content={"created": created, "export": PX.public_view(job)})

    @router.get("/data-export/download")
    async def data_export_download(request: Request, current=Depends(get_current_user)):
        uid = current["user_id"]
        wait = _download_limiter.hit(uid)
        if wait is not None:
            raise _too_many(wait)
        try:
            data, _job = await PX.open_download(db, uid)
        except PX.DownloadError:
            await _log("data_export", "denied", uid, "download_unavailable", request)
            raise HTTPException(status_code=404, detail="Arhiva nu este disponibila sau a expirat")
        await _log("data_export", "success", uid, "downloaded", request)
        return Response(content=data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{PX.ARCHIVE_NAME}"',
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store, max-age=0", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"})

    return router