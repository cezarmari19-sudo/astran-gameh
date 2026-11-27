"""Endpointuri: /api/age/* (cont propriu) si /api/parental/* (parinte). Strat subtire peste age_service / parental_consent.

Securitate:
  * Autentificarea API-ului e prin antetul `Authorization: Bearer` (fara cookie-uri) => nu exista autoritate ambientala
    pe care sa o exploateze un CSRF. Endpointurile pentru parinti FARA cont primesc tokenul in CORPUL cererii (POST),
    nu in URL/query (nu ajunge in loguri/Referer); tokenul e single-use, cu expirare (vezi parental_consent.py);
  * replay: tokenuri single-use consumate atomic, sesiuni de verificare single-use, atestari cu fereastra de timp;
  * brute-force: tokenuri de 256 biti + limitatoare pe IP / utilizator; cereri cu campuri necunoscute sunt respinse;
  * autorizare: pe backend la fiecare cerere; un utilizator NU devine parinte printr-un request (vezi _authorize);
  * rutele de callback (provider) accepta doar corpuri semnate HMAC; fara secret configurat raspund 503.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import age_audit as AU
from . import age_entitlement_service as ENT
from . import age_policy as AP
from . import age_service as AS
from . import parental_consent as PC
from .age_assurance import AgeAssuranceProvider, AssuranceError, get_provider as get_assurance_provider
from .parental_verification import ParentalVerifier, VerifierError, get_verifier
from .ratelimit import SlidingWindowLimiter

_ip_limiter = SlidingWindowLimiter(40, 600)          # rute publice (token) / 10 min / IP
_token_fail_limiter = SlidingWindowLimiter(10, 600)  # esecuri de token / 10 min / IP
_user_limiter = SlidingWindowLimiter(60, 3600)
_cb_limiter = SlidingWindowLimiter(120, 600)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssuranceStartBody(_Strict):
    threshold: Optional[int] = Field(default=None, description="16 sau 18; validat de server fata de starea contului")


class DobBody(_Strict):
    date_of_birth: str = Field(max_length=32)


class ParentEmailBody(_Strict):
    parent_email: str = Field(max_length=320)
    language: Optional[str] = Field(default="ro", max_length=8)


class OwnConsentBody(_Strict):
    language: Optional[str] = Field(default="ro", max_length=8)


class TokenBody(_Strict):
    token: str = Field(max_length=200)
    language: Optional[str] = Field(default=None, max_length=8)


class DecisionBody(_Strict):
    token: str = Field(max_length=200)
    decision: str = Field(max_length=10)
    acknowledgements: dict[str, bool] = Field(default_factory=dict)


class PanelDecisionBody(_Strict):
    decision: str = Field(max_length=10)
    acknowledgements: dict[str, bool] = Field(default_factory=dict)


class ClaimBody(_Strict):
    claim_token: str = Field(max_length=200)


class ScopeBody(_Strict):
    scope: str = Field(max_length=20)
    granted: bool


class DataRequestBody(_Strict):
    kind: str = Field(max_length=10)
    confirm: bool = False


def _wait(limiter: SlidingWindowLimiter, key: str) -> None:
    w = limiter.hit(key)
    if w is not None:
        raise HTTPException(status_code=429, detail={"code": "too_many_requests", "retry_after": w}, headers={"Retry-After": str(w)})


def _err(e: Exception) -> HTTPException:
    if isinstance(e, PC.ParentalError):
        return HTTPException(status_code=e.status, detail=e.detail())
    if isinstance(e, AP.AgeError):
        return HTTPException(status_code=e.status, detail=e.detail())
    if isinstance(e, (AssuranceError, VerifierError)):
        return HTTPException(status_code=e.status, detail={"code": e.code})
    raise e


def make_age_router(get_current_user, db, *, client_ip: Callable[[Request], str],
                    notifier: Optional[PC.Notifier] = None, app_base_url: Optional[str] = None,
                    on_parent_approved: Optional[Callable[[str], Awaitable[None]]] = None,
                    on_withdrawn: Optional[Callable[[str], Awaitable[None]]] = None,
                    erase_child: Optional[Callable[[str], Awaitable[Any]]] = None,
                    assurance_provider: Optional[AgeAssuranceProvider] = None,
                    verifier: Optional[ParentalVerifier] = None) -> APIRouter:
    router = APIRouter(tags=["age", "parental"])
    prov = lambda: assurance_provider or get_assurance_provider()  # noqa: E731 - citit la fiecare cerere (mediu)
    ver = lambda: verifier or get_verifier()  # noqa: E731

    # ------------------------------------------------------------------ cont propriu
    @router.get("/age/me")
    async def age_me(current=Depends(get_current_user)):
        return AS.public_state(current, await PC.child_status(db, current)) | {
            "age_assurance_available": prov().configured(), "parental_verification_available": ver().configured(),
            "email_delivery_available": notifier is not None and bool(app_base_url)}

    @router.get("/age/entitlements")
    async def age_entitlements(current=Depends(get_current_user)):
        """Matricea de acces calculata de server pentru contul curent: ce functii sunt disponibile / restrictionate si de ce.
        Doar informativ pentru UI; fiecare cerere reaplica aceleasi reguli pe backend (age_entitlement_service)."""
        return ENT.entitlements(current) | {
            "declared_age_band": AP.evaluate_access(current).band,
            "age_assurance_available": prov().configured(), "parental_verification_available": ver().configured()}

    @router.post("/age/date-of-birth")
    async def age_set_dob(body: DobBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "dob:" + current["user_id"])
        try:
            user = await AS.set_date_of_birth_once(db, current, body.date_of_birth)
        except Exception as e:  # noqa: BLE001
            raise _err(e)
        return AS.public_state(user, await PC.child_status(db, user))

    @router.post("/age/parental/request")
    async def age_parent_request(body: ParentEmailBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "preq:" + current["user_id"])
        try:
            r = await PC.request_consent(db, current, body.parent_email, lang=body.language or "ro", notifier=notifier, app_base_url=app_base_url)
        except Exception as e:  # noqa: BLE001
            raise _err(e)
        return r

    @router.post("/age/own-consent")
    async def age_own_consent(body: OwnConsentBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "own:" + current["user_id"])
        try:
            user = await AS.confirm_own_consent(db, current, lang=body.language or "ro")
        except Exception as e:  # noqa: BLE001
            raise _err(e)
        return AS.public_state(user, await PC.child_status(db, user))

    @router.post("/age/assurance/start")
    async def assurance_start(body: Optional[AssuranceStartBody] = None, current=Depends(get_current_user)):
        _wait(_user_limiter, "as:" + current["user_id"])
        try:
            # pragul il valideaza SERVERUL fata de starea contului (16-17 => 16; 18+ => 18); clientul poate doar sa il
            # restranga la o valoare permisa, niciodata sa o extinda
            return await AS.start_assurance(db, current, prov(), threshold=body.threshold if body else None)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/age/assurance/callback")
    async def assurance_callback(request: Request):
        _wait(_cb_limiter, "ascb:" + client_ip(request))
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(status_code=413, detail={"code": "payload_too_large"})
        try:
            return await AS.handle_assurance_callback(db, prov(), body, request.headers.get("x-astran-signature"))
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    # ------------------------------------------------------------------ parinte fara cont (token in corp)
    @router.post("/parental/notice")
    async def parental_notice(body: TokenBody, request: Request):
        ip = client_ip(request)
        _wait(_ip_limiter, "pn:" + ip)
        try:
            return await PC.peek_notice(db, body.token, body.language)
        except PC.ParentalError as e:
            _wait(_token_fail_limiter, "pnf:" + ip)
            raise _err(e)

    @router.post("/parental/decision")
    async def parental_decision(body: DecisionBody, request: Request):
        ip = client_ip(request)
        _wait(_ip_limiter, "pd:" + ip)
        try:
            return await PC.decide(db, body.token, body.decision, body.acknowledgements, verifier=ver())
        except PC.ParentalError as e:
            if e.code in ("token_invalid", "token_used", "token_expired"):
                _wait(_token_fail_limiter, "pdf:" + ip)
            raise _err(e)

    @router.post("/parental/withdraw-by-token")
    async def parental_withdraw_token(body: TokenBody, request: Request):
        ip = client_ip(request)
        _wait(_ip_limiter, "pw:" + ip)
        try:
            return await PC.withdraw_by_token(db, body.token, on_withdrawn=on_withdrawn)
        except PC.ParentalError as e:
            _wait(_token_fail_limiter, "pwf:" + ip)
            raise _err(e)

    @router.post("/parental/verification/callback")
    async def parental_verification_callback(request: Request):
        _wait(_cb_limiter, "pvcb:" + client_ip(request))
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(status_code=413, detail={"code": "payload_too_large"})
        v = ver()
        try:
            result = v.parse_callback(body, request.headers.get("x-astran-signature"))
            return await PC.complete_verification(db, result, notifier=notifier, app_base_url=app_base_url, on_approved=on_parent_approved)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    # ------------------------------------------------------------------ panou parental (cont logat)
    @router.get("/parental/children")
    async def parental_children(current=Depends(get_current_user)):
        _wait(_user_limiter, "pc:" + current["user_id"])
        try:
            return await PC.list_children(db, current)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/parental/claim")
    async def parental_claim(body: ClaimBody, current=Depends(get_current_user)):
        _wait(_token_fail_limiter, "claim:" + current["user_id"])
        try:
            return await PC.claim_link(db, current, body.claim_token)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/parental/children/{consent_id}/decision")
    async def parental_panel_decision(consent_id: str, body: PanelDecisionBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "pcd:" + current["user_id"])
        try:
            return await PC.panel_decide(db, current, consent_id, body.decision, body.acknowledgements, verifier=ver())
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/parental/children/{consent_id}/verification/restart")
    async def parental_restart_verification(consent_id: str, current=Depends(get_current_user)):
        _wait(_user_limiter, "pcv:" + current["user_id"])
        try:
            return await PC.panel_restart_verification(db, current, consent_id, ver())
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/parental/children/{consent_id}/withdraw")
    async def parental_panel_withdraw(consent_id: str, current=Depends(get_current_user)):
        _wait(_user_limiter, "pcw:" + current["user_id"])
        try:
            return await PC.panel_withdraw(db, current, consent_id, on_withdrawn=on_withdrawn)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/parental/children/{consent_id}/scopes")
    async def parental_scope(consent_id: str, body: ScopeBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "pcs:" + current["user_id"])
        try:
            return await PC.set_scope(db, current, consent_id, body.scope, body.granted)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    @router.post("/parental/children/{consent_id}/data-request")
    async def parental_data_request(consent_id: str, body: DataRequestBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "pcr:" + current["user_id"])
        try:
            return await PC.create_data_request(db, current, consent_id, body.kind, confirm=body.confirm, erase_child=erase_child)
        except Exception as e:  # noqa: BLE001
            raise _err(e)

    return router
