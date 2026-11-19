"""Endpointuri: /api/legal/* (public, doar citire) si /api/consent/* (autentificat).

Atasare din server.py:
    api.include_router(make_legal_router())
    api.include_router(make_consent_router(get_current_user, db))

Securitate:
  * /consent/* actioneaza DOAR asupra contului din token; nu exista parametru user_id si nicio ruta de
    modificare/stergere a evenimentelor (registrul e adauga-doar);
  * corpurile cererilor interzic campuri necunoscute (extra=forbid): un `created_at` / `version` trimis de client
    este respins, nu ignorat in tacere; serverul oricum nu citeste niciodata data sau versiunea de la client;
  * limitare de rate pe utilizator; retragerea e idempotenta (dublu-click => un singur eveniment).
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import consent as C
from . import legal_docs as LD
from .ratelimit import SlidingWindowLimiter

_write_limiter = SlidingWindowLimiter(30, 3600)    # scrieri / ora / utilizator
_read_limiter = SlidingWindowLimiter(300, 3600)    # citiri / ora / utilizator


class AcceptDocumentsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[str] = Field(default_factory=list, max_length=8)
    presented_versions: dict[str, str] = Field(default_factory=dict)   # doar comparat cu versiunea curenta
    language: Optional[str] = Field(default=None, max_length=8)


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Optional[str] = Field(default=None, max_length=8)


class WithdrawBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    language: Optional[str] = Field(default=None, max_length=8)


def _http(e: C.ConsentError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail())


def make_legal_router() -> APIRouter:
    router = APIRouter(prefix="/legal", tags=["legal"])

    @router.get("/documents")
    async def list_documents(lang: Optional[str] = None):
        reg = LD.get_registry()
        lg = reg.normalize_lang(lang)
        out = []
        for t in reg.doc_types():
            cur = reg.current_version(t)
            meta = reg.version_meta(t, cur) or {}
            out.append({
                "doc_type": t, "legal_basis": reg.legal_basis(t), "current_version": cur,
                "effective_date": meta.get("effective_date"), "requires_reacceptance": bool(meta.get("requires_reacceptance")),
                "sha256": (reg.text(t, cur, lg) or (None, None))[1], "language": lg,
            })
        return {"documents": out}

    @router.get("/documents/{doc_type}")
    async def get_document(doc_type: str, version: Optional[str] = None, lang: Optional[str] = None):
        reg = LD.get_registry()
        if not reg.has_doc(doc_type):
            raise HTTPException(status_code=404, detail={"code": "unknown_subject", "message": "Unknown document"})
        lg = reg.normalize_lang(lang)
        ver = version or reg.current_version(doc_type)
        got = reg.text(doc_type, ver, lg)
        if got is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_version", "message": "Unknown version"})
        return {"doc_type": doc_type, "version": ver, "language": lg, "sha256": got[1], "text": got[0],
                "is_current": ver == reg.current_version(doc_type), "legal_basis": reg.legal_basis(doc_type)}

    return router


def make_consent_router(get_current_user: Callable, db) -> APIRouter:
    router = APIRouter(prefix="/consent", tags=["consent"])

    def _limit(lim: SlidingWindowLimiter, uid: str) -> None:
        wait = lim.hit(uid)
        if wait is not None:
            raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Too many requests"},
                                headers={"Retry-After": str(wait)})

    def _lang(requested: Optional[str], user: dict) -> str:
        return LD.get_registry().normalize_lang(requested or user.get("language"))

    @router.get("/me")
    async def my_consents(lang: Optional[str] = None, current=Depends(get_current_user)):
        _limit(_read_limiter, current["user_id"])
        return await C.overview(db, current, _lang(lang, current))

    @router.get("/history")
    async def my_history(current=Depends(get_current_user)):
        _limit(_read_limiter, current["user_id"])
        return {"events": await C.history(db, current["user_id"])}

    @router.post("/documents/accept")
    async def accept_documents(body: AcceptDocumentsBody, current=Depends(get_current_user)):
        _limit(_write_limiter, current["user_id"])
        try:
            await C.accept_documents(db, current["user_id"], list(body.documents), dict(body.presented_versions),
                                     lang=_lang(body.language, current), source="reaccept")
        except C.ConsentError as e:
            raise _http(e)
        return await C.overview(db, current, _lang(body.language, current))

    @router.post("/purposes/{purpose}/grant")
    async def grant(purpose: str, body: GrantBody, current=Depends(get_current_user)):
        _limit(_write_limiter, current["user_id"])
        try:
            await C.grant_purpose(db, current["user_id"], purpose, age_category=current.get("age_category"),
                                  lang=_lang(body.language, current), source="settings")
        except C.ConsentError as e:
            raise _http(e)
        return await C.overview(db, current, _lang(body.language, current))

    @router.post("/purposes/{purpose}/withdraw")
    async def withdraw(purpose: str, body: WithdrawBody, current=Depends(get_current_user)):
        _limit(_write_limiter, current["user_id"])
        try:
            await C.withdraw_purpose(db, current["user_id"], purpose, confirm=bool(body.confirm),
                                     lang=_lang(body.language, current), source="settings")
        except C.ConsentError as e:
            raise _http(e)
        return await C.overview(db, current, _lang(body.language, current))

    return router