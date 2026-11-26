"""Rute /api/moderation/* si /api/contact/* (DSA). Logica e in moderation_service.py.

Autorizare (SERVER, niciodata frontend):
  * /moderation/me/*      -> orice utilizator autentificat, DOAR asupra datelor proprii (filtru pe user_id din sesiune; 404 la ID straine)
  * /moderation/admin/*   -> require_staff: rolul se citeste din documentul utilizatorului incarcat din DB (is_platform_admin / is_moderator)
  * /moderation/admin/moderators si decizia de suspendare permanenta -> doar platform admin (verificat si in service)
Corpurile cererilor interzic campuri necunoscute (extra=forbid). Fiecare scriere are limitare de rata.
"""
from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import moderation_service as MS
from .ratelimit import SlidingWindowLimiter

_notice_limiter = SlidingWindowLimiter(10, 3600)
_write_limiter = SlidingWindowLimiter(60, 3600)
_staff_limiter = SlidingWindowLimiter(1200, 3600)
_read_limiter = SlidingWindowLimiter(600, 3600)
_contact_limiter = SlidingWindowLimiter(5, 3600)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoticeBody(_Strict):
    target_type: Literal["user", "game", "message"]
    target_id: str = Field(min_length=1, max_length=128)
    category: Literal[tuple(MS.CATEGORIES)] = "illegal_content"   # type: ignore[valid-type]
    explanation: str = Field(min_length=1, max_length=MS.MAX_TEXT)
    alleged_legal_basis: Optional[str] = Field(default=None, max_length=500)
    content_location: Optional[str] = Field(default=None, max_length=500)
    reporter_name: Optional[str] = Field(default=None, max_length=120)
    reporter_email: Optional[str] = Field(default=None, max_length=320)
    identity_exempt: bool = False
    good_faith_declaration: bool = False


class AppealBody(_Strict):
    grounds: str = Field(min_length=1, max_length=MS.MAX_TEXT)
    evidence_text: Optional[str] = Field(default=None, max_length=MS.MAX_TEXT)
    evidence_urls: list[str] = Field(default_factory=list, max_length=MS.MAX_URLS)


class AssignBody(_Strict):
    assignee_id: str = Field(min_length=1, max_length=64)


class StatusBody(_Strict):
    status: Literal["pending", "under_review", "resolved", "rejected"]
    reasoning: Optional[str] = Field(default=None, max_length=MS.MAX_TEXT)


class NoteBody(_Strict):
    text: str = Field(min_length=1, max_length=MS.MAX_TEXT)


class AssessBody(_Strict):
    state: Literal["sufficient", "insufficient"]
    reasoning: str = Field(min_length=1, max_length=MS.MAX_TEXT)


class DecisionBody(_Strict):
    action: Literal[tuple(MS.ACTIONS)]    # type: ignore[valid-type]
    reasoning: str = Field(min_length=1, max_length=MS.MAX_TEXT)
    notifier_reasoning: Optional[str] = Field(default=None, max_length=2000)
    legal_ground: Optional[str] = Field(default=None, max_length=500)
    tos_provision: Optional[str] = Field(default=None, max_length=300)
    days: Optional[int] = Field(default=None, ge=1, le=365)
    scope: Optional[Literal["chat", "full"]] = None
    automated_detection: bool = False


class CorrectionBody(_Strict):
    text: str = Field(min_length=1, max_length=MS.MAX_TEXT)


class ModificationBody(_Strict):
    days: int = Field(ge=1, le=365)
    scope: Literal["chat", "full"] = "full"


class ResolveAppealBody(_Strict):
    outcome: Literal["maintain", "reverse", "modify"]
    reasoning: str = Field(min_length=1, max_length=MS.MAX_TEXT)
    modification: Optional[ModificationBody] = None      # doar pentru outcome="modify" (restrictii/suspendari de cont)


class ModeratorBody(_Strict):
    user_id: str = Field(min_length=1, max_length=64)
    enabled: bool


class ContactBody(_Strict):
    channel: Literal[tuple(MS.CONTACT_CHANNELS)]   # type: ignore[valid-type]
    name: Optional[str] = Field(default=None, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=10, max_length=MS.MAX_TEXT)


def _http(e: MS.ModerationError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _wait(limiter: SlidingWindowLimiter, key: str) -> None:
    w = limiter.hit(key)
    if w is not None:
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "retry_after": w}, headers={"Retry-After": str(w)})


def make_moderation_router(get_current_user, db, *, target_exists: Callable[[str, str, dict], Awaitable[bool]]) -> APIRouter:
    router = APIRouter(prefix="/moderation", tags=["moderation"])

    async def staff(current: dict = Depends(get_current_user)) -> dict:
        if not MS.role_of(current):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Moderator access required"})
        _wait(_staff_limiter, current["user_id"])
        return current

    async def run(coro):
        try:
            return await coro
        except MS.ModerationError as e:
            raise _http(e)

    # ------------------------------------------------------------------ utilizator: notificari Art. 16
    @router.post("/notices", status_code=201)
    async def create_notice(body: NoticeBody, current: dict = Depends(get_current_user)):
        _wait(_notice_limiter, current["user_id"])
        if not await target_exists(body.target_type, body.target_id, current):
            raise HTTPException(status_code=404, detail={"code": "target_not_found"})
        rid = MS.new_id("rep_")
        doc = {"report_id": rid, "reporter_id": current["user_id"], "target_type": body.target_type, "target_id": body.target_id,
               "reason": "illegal_content", "details": None, "status": "open", "created_at": MS.now_utc()}
        notice = body.model_dump(include={"explanation", "alleged_legal_basis", "content_location", "reporter_name", "reporter_email",
                                          "identity_exempt", "good_faith_declaration"})
        receipt = await run(MS.register_report(db, doc, kind=MS.KIND_NOTICE, category=body.category, notice=notice))
        return {"report_id": rid, "receipt": receipt}

    @router.get("/me/reports")
    async def my_reports(current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return {"reports": await MS.my_reports(db, current["user_id"])}

    @router.get("/me/reports/{report_id}")
    async def my_report(report_id: str, current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return await run(MS.my_report(db, current["user_id"], report_id))

    @router.get("/me/notifications")
    async def my_notifications(current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return {"notifications": await MS.list_notifications(db, current["user_id"])}

    @router.post("/me/notifications/{notification_id}/read")
    async def read_notification(notification_id: str, current: dict = Depends(get_current_user)):
        _wait(_write_limiter, current["user_id"])
        await run(MS.mark_notification_read(db, current["user_id"], notification_id))
        return {"ok": True}

    # ------------------------------------------------------------------ utilizator: decizii (Art. 17) + plangeri (Art. 20)
    @router.get("/me/decisions")
    async def my_decisions(current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return {"decisions": await MS.my_decisions(db, current["user_id"])}

    @router.get("/me/decisions/{decision_id}")
    async def my_decision(decision_id: str, current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return await run(MS.my_decision(db, current["user_id"], decision_id))

    @router.post("/me/decisions/{decision_id}/appeals", status_code=201)
    async def submit_appeal(decision_id: str, body: AppealBody, current: dict = Depends(get_current_user)):
        _wait(_write_limiter, current["user_id"])
        return await run(MS.submit_appeal(db, current, decision_id, grounds=body.grounds, evidence_text=body.evidence_text,
                                          evidence_urls=body.evidence_urls))

    @router.get("/me/appeals")
    async def my_appeals(current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return {"appeals": await MS.my_appeals(db, current["user_id"])}

    @router.get("/me/appeals/{appeal_id}")
    async def my_appeal(appeal_id: str, current: dict = Depends(get_current_user)):
        _wait(_read_limiter, current["user_id"])
        return await run(MS.my_appeal(db, current["user_id"], appeal_id))

    @router.get("/me/role")
    async def my_role(current: dict = Depends(get_current_user)):
        """Doar pentru a afisa/ascunde meniul in UI; autorizarea reala se face la fiecare ruta /admin."""
        from . import authority_orders as _AO   # doar afiseaza/ascunde intrarea in UI; ordinele raman protejate pe server
        return {"role": MS.role_of(current), "orders": bool(_AO.order_role(current))}

    # ------------------------------------------------------------------ personal autorizat
    @router.get("/admin/reports")
    async def admin_reports(_s: dict = Depends(staff), status: Optional[str] = None, category: Optional[str] = None,
                            target_type: Optional[Literal["user", "game", "message"]] = None,
                            kind: Optional[Literal["rule_violation", "illegal_content_notice", "own_initiative", "authority_order"]] = None,
                            assigned_to: Optional[str] = Query(default=None, max_length=64),
                            date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                            q: Optional[str] = Query(default=None, max_length=100),
                            page: int = Query(default=1, ge=1, le=10000), page_size: int = Query(default=25, ge=1, le=100)):
        return await run(MS.list_reports(db, page=page, page_size=page_size, status=status, category=category, target_type=target_type,
                                         kind=kind, assigned_to=assigned_to, date_from=date_from, date_to=date_to, q=q))

    @router.get("/admin/reports/{report_id}")
    async def admin_report(report_id: str, _s: dict = Depends(staff)):
        return await run(MS.report_detail(db, report_id))

    @router.post("/admin/reports/{report_id}/assign")
    async def admin_assign(report_id: str, body: AssignBody, s: dict = Depends(staff)):
        return await run(MS.assign_report(db, s, report_id, body.assignee_id))

    @router.post("/admin/reports/{report_id}/status")
    async def admin_status(report_id: str, body: StatusBody, s: dict = Depends(staff)):
        return await run(MS.set_status(db, s, report_id, body.status, body.reasoning))

    @router.post("/admin/reports/{report_id}/notes", status_code=201)
    async def admin_note(report_id: str, body: NoteBody, s: dict = Depends(staff)):
        return await run(MS.add_note(db, s, report_id, body.text))

    @router.post("/admin/reports/{report_id}/assessment")
    async def admin_assess(report_id: str, body: AssessBody, s: dict = Depends(staff)):
        return await run(MS.assess_notice(db, s, report_id, body.state, body.reasoning))

    @router.post("/admin/reports/{report_id}/decisions", status_code=201)
    async def admin_decide(report_id: str, body: DecisionBody, s: dict = Depends(staff)):
        return await run(MS.decide(db, s, report_id, body.model_dump()))

    @router.post("/admin/decisions/{decision_id}/corrections", status_code=201)
    async def admin_correction(decision_id: str, body: CorrectionBody, s: dict = Depends(staff)):
        return await run(MS.add_correction(db, s, decision_id, body.text))

    @router.get("/admin/audit")
    async def admin_audit(_s: dict = Depends(staff), report_id: Optional[str] = Query(default=None, max_length=64),
                          decision_id: Optional[str] = Query(default=None, max_length=64)):
        return {"events": await MS.audit_history(db, report_id=report_id, decision_id=decision_id)}

    @router.get("/admin/audit/verify")
    async def admin_audit_verify(_s: dict = Depends(staff)):
        return await MS.verify_audit_chain(db)

    @router.get("/admin/appeals")
    async def admin_appeals(_s: dict = Depends(staff), status: Optional[str] = Query(default=None, max_length=30)):
        return {"appeals": await MS.list_appeals(db, status=status)}

    @router.get("/admin/appeals/{appeal_id}")
    async def admin_appeal(appeal_id: str, _s: dict = Depends(staff)):
        return await run(MS.appeal_detail(db, appeal_id))

    @router.post("/admin/appeals/{appeal_id}/start-review")
    async def admin_start_review(appeal_id: str, s: dict = Depends(staff)):
        return await run(MS.start_appeal_review(db, s, appeal_id))

    @router.post("/admin/appeals/{appeal_id}/resolve")
    async def admin_resolve(appeal_id: str, body: ResolveAppealBody, s: dict = Depends(staff)):
        return await run(MS.resolve_appeal(db, s, appeal_id, outcome=body.outcome, reasoning=body.reasoning,
                                                  modification=body.modification.model_dump() if body.modification else None))

    @router.post("/admin/moderators")
    async def admin_set_moderator(body: ModeratorBody, s: dict = Depends(staff)):
        return await run(MS.set_moderator(db, s, body.user_id, body.enabled))

    @router.get("/admin/contact-messages")
    async def admin_contact_messages(_s: dict = Depends(staff)):
        return {"messages": await MS.list_contact_messages(db)}

    return router


def make_contact_router(db, *, client_ip: Callable[[Request], str]) -> APIRouter:
    router = APIRouter(prefix="/contact", tags=["contact"])

    @router.get("/info")
    async def info():
        """Public. Campurile neconfigurate sunt intoarse ca null + `missing_fields` (NU se inventeaza date)."""
        return MS.contact_info()

    @router.post("/messages", status_code=201)
    async def send(body: ContactBody, request: Request):
        _wait(_contact_limiter, client_ip(request))
        try:
            return await MS.submit_contact_message(db, channel=body.channel, name=body.name, email=body.email,
                                                   subject=body.subject, message=body.message)
        except MS.ModerationError as e:
            raise _http(e)

    return router
