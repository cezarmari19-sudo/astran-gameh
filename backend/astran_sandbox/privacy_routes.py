"""Endpointuri GDPR: /api/privacy/*  (stergere cont, export date). Logica e in privacy.py.

Se ataseaza din server.py, ca celelalte routere:
    api.include_router(make_privacy_router(get_current_user, db, verify_pw=verify_pw_async,
                                           client_ip=_client_ip, psec=psec, emailver=emailver))

Securitate (toate verificarile sunt pe server):
  * toate rutele, cu exceptia GET /deletion/{id}, cer utilizator autentificat; actiunea se aplica DOAR contului
    din token - nu exista niciun parametru `user_id` care sa poata indica alt cont;
  * stergerea si exportul cer RE-AUTENTIFICARE (parola contului / cod pe email pentru conturile Google);
  * stergerea cere confirmare explicita (`confirm: true` + textul "DELETE" tastat de utilizator);
  * limitare de rate + blocare temporara la parole gresite (acelasi mecanism ca la stergerea proiectelor).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import privacy
from .ratelimit import SlidingWindowLimiter

log = logging.getLogger("astran.privacy.routes")

_export_limiter = SlidingWindowLimiter(5, 3600)        # cereri de export / ora / utilizator
_download_limiter = SlidingWindowLimiter(20, 3600)     # incercari de descarcare / ora / utilizator
_delete_limiter = SlidingWindowLimiter(10, 3600)       # cereri de stergere / ora / utilizator
_code_limiter = SlidingWindowLimiter(5, 3600)          # coduri de re-autentificare / ora / utilizator
_status_ip_limiter = SlidingWindowLimiter(120, 60)     # polling stare stergere / minut / IP


class ReauthBody(BaseModel):
    password: Optional[str] = Field(default=None, max_length=128)
    code: Optional[str] = Field(default=None, max_length=12)


class DeleteAccountBody(ReauthBody):
    confirm: bool = False
    confirmation_text: str = Field(default="", max_length=32)


class DownloadBody(BaseModel):
    ticket: str = Field(min_length=20, max_length=200)


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return None
    name, _, dom = email.partition("@")
    return (name[:1] + "***@" + dom) if name else None


def make_privacy_router(get_current_user: Callable, db, *, verify_pw: Callable, client_ip: Callable,
                        psec, emailver) -> APIRouter:
    router = APIRouter(prefix="/privacy", tags=["privacy"])

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

    async def _reauth(current: dict, body: ReauthBody, authorization: Optional[str], purpose: str, request: Request):
        uid = current["user_id"]
        ip = client_ip(request)
        await psec.ensure_rate_limit_or_raise(db, user_id=uid, scope_id="account", purpose=purpose)
        full = await _full_user(uid)
        try:
            await privacy.verify_reauth(
                db, full, password=body.password, code=body.code, verify_pw=verify_pw, emailver=emailver,
                session_age_seconds=await _session_age(authorization), email_is_configured=psec.email_is_configured())
        except privacy.ReauthError as e:
            if e.code in ("BAD_PASSWORD", "BAD_CODE"):
                await psec.record_failed_attempt(db, user_id=uid, scope_id="account", purpose=purpose)
            await psec.log_security_event(db, action=purpose, result="denied", user_id=uid, detail=e.code, ip=ip)
            raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.detail})
        await psec.reset_attempts(db, user_id=uid, scope_id="account", purpose=purpose)
        return full

    # ------------------------------------------------------------------ stare / informatii
    @router.get("/status")
    async def privacy_status(current=Depends(get_current_user)):
        full = await _full_user(current["user_id"])
        return {
            "has_password": bool(full.get("password_hash")),
            "email_masked": _mask_email(full.get("email")),
            "email_service_available": bool(psec.email_is_configured()),
            "confirmation_text": privacy.CONFIRMATION_TEXT,
            "export_ttl_seconds": privacy.export_ttl_seconds(),
            "retention": privacy.retention_policy(),
            "can_delete": not bool(full.get("is_platform_owner")),
        }

    @router.post("/reauth/code")
    async def send_reauth_code(current=Depends(get_current_user)):
        """Cod de 6 cifre pe emailul contului - re-autentificare pentru conturile fara parola (Google)."""
        uid = current["user_id"]
        wait = _code_limiter.hit(uid)
        if wait is not None:
            raise _too_many(wait)
        full = await _full_user(uid)
        if full.get("password_hash"):
            raise HTTPException(status_code=409, detail="Acest cont are parola: foloseste parola contului")
        if not psec.email_is_configured():
            raise HTTPException(status_code=503, detail="Email service is not configured")
        if not full.get("email"):
            raise HTTPException(status_code=409, detail="Contul nu are o adresa de email")
        try:
            code = await emailver.issue(db, uid, full["email"], collection=privacy.REAUTH_CODE_COLLECTION)
        except emailver.VerificationError as e:
            raise HTTPException(status_code=e.status, detail=e.detail,
                                headers={"Retry-After": str(e.retry_after)} if e.retry_after else None)
        sent = await asyncio.to_thread(
            psec.send_plain_email, full["email"], "Confirmare actiune cont - Astran Game",
            f"Codul tau de confirmare este: {code}\n\nExpira in {emailver.CODE_TTL_MINUTES} minute si poate fi folosit o "
            "singura data. Daca nu ai cerut tu aceasta actiune (export sau stergere de date), nu introduce codul nicaieri "
            "si schimba-ti parola/contul Google.")
        if not sent:
            raise HTTPException(status_code=503, detail="Could not send the email")
        return {"ok": True, "email_masked": _mask_email(full["email"])}

    # ------------------------------------------------------------------ export (Art. 15 / 20)
    @router.post("/export/request")
    async def export_request(body: ReauthBody, request: Request, current=Depends(get_current_user),
                             authorization: Optional[str] = Header(None)):
        wait = _export_limiter.hit(current["user_id"])
        if wait is not None:
            raise _too_many(wait)
        await _reauth(current, body, authorization, "data_export", request)
        t = await privacy.create_export_ticket(db, current["user_id"])
        await psec.log_security_event(db, action="data_export", result="success", user_id=current["user_id"],
                                      detail="ticket_issued", ip=client_ip(request))
        return {"ticket": t["ticket"], "expires_at": t["expires_at"], "ttl_seconds": t["ttl_seconds"], "format": "json"}

    @router.post("/export/download")
    async def export_download(body: DownloadBody, request: Request, current=Depends(get_current_user)):
        """Descarcarea cere ATAT sesiunea utilizatorului, CAT si tichetul (de unica folosinta, expira). Orice
        motiv de esec (altcineva, expirat, deja folosit, inexistent) intoarce acelasi 404."""
        uid = current["user_id"]
        wait = _download_limiter.hit(uid)
        if wait is not None:
            raise _too_many(wait)
        if not await privacy.redeem_export_ticket(db, uid, body.ticket):
            await psec.log_security_event(db, action="data_export", result="denied", user_id=uid,
                                          detail="bad_or_expired_ticket", ip=client_ip(request))
            raise HTTPException(status_code=404, detail="Linkul de descarcare nu este valid sau a expirat")
        try:
            data = await privacy.build_export(db, uid)
        except LookupError:
            raise HTTPException(status_code=401, detail="Invalid token")
        payload = json.dumps(data, default=privacy.json_default, ensure_ascii=False, indent=2)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(content=payload, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="astran-data-export-{stamp}.json"',
            "Cache-Control": "no-store, max-age=0", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"})

    # ------------------------------------------------------------------ stergere cont (Art. 17)
    @router.post("/delete-account")
    async def delete_account(body: DeleteAccountBody, request: Request, current=Depends(get_current_user),
                             authorization: Optional[str] = Header(None)):
        uid = current["user_id"]
        wait = _delete_limiter.hit(uid)
        if wait is not None:
            raise _too_many(wait)
        if body.confirm is not True or body.confirmation_text.strip().upper() != privacy.CONFIRMATION_TEXT:
            raise HTTPException(status_code=400, detail={"code": "CONFIRMATION_REQUIRED",
                                "message": f'Confirmare explicita necesara: tasteaza "{privacy.CONFIRMATION_TEXT}"'})
        if current.get("is_platform_owner"):
            raise HTTPException(status_code=403, detail={"code": "OWNER_NOT_DELETABLE",
                                "message": "Contul de proprietar al platformei nu poate fi sters din aplicatie"})
        full = await _reauth(current, body, authorization, "account_delete", request)
        await psec.log_security_event(db, action="account_delete", result="started", user_id=uid, ip=client_ip(request))

        state, rec = await privacy.begin_deletion(db, uid)
        if state == "not_found":
            raise HTTPException(status_code=401, detail="Invalid token")
        if state == "in_progress":
            return JSONResponse(status_code=202, content={"status": rec.get("status", "processing"),
                                                          "deletion_id": rec.get("deletion_id")})
        email = full.get("email")
        status = await privacy.run_deletion(db, uid, rec["deletion_id"], first_run=True)
        if status == "completed" and email:
            # confirmare de stergere: emailul se foloseste o singura data si NU se mai stocheaza
            asyncio.create_task(asyncio.to_thread(
                psec.send_plain_email, email, "Contul tau Astran Game a fost sters",
                "Contul tau si datele personale asociate au fost sterse. Anumite inregistrari pentru care exista o "
                "obligatie legala sau un interes legitim (ex: achizitii cu bani reali, rapoarte de moderare, jurnale de "
                "securitate) sunt pastrate pseudonimizat, restrictionat, pentru perioadele din politica de confidentialitate."))
        code = 200 if status == "completed" else 202
        return JSONResponse(status_code=code, content={"status": status, "deletion_id": rec["deletion_id"]})

    @router.get("/deletion/{deletion_id}")
    async def deletion_state(deletion_id: str, request: Request):
        """Starea unei cereri de stergere. Nu cere token (sesiunile se invalideaza la inceputul stergerii); id-ul e un
        secret aleator de 192 de biti, iar raspunsul nu contine date personale."""
        wait = _status_ip_limiter.hit(client_ip(request))
        if wait is not None:
            raise _too_many(wait)
        st = await privacy.deletion_status(db, deletion_id)
        if st is None:
            raise HTTPException(status_code=404, detail="Not found")
        return st

    return router