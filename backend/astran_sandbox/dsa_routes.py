"""Rute DSA suplimentare: notificari publice (Art. 16), raport de transparenta (Art. 15), Baza de date de transparenta (Art. 24(5)),
livrari, cazuri din initiativa proprie si ordine ale autoritatilor (Art. 9/10). Logica e in module; autorizarea se reverifica si acolo.

Acces:
  * public (fara cont): /moderation/public/*, /transparency/*, /authority/intake, /authority/contact   (limitare de rata pe IP)
  * personal (moderator/admin): /moderation/admin/own-initiative, /moderation/admin/deliveries*, /dsa/applicability, /dsa/transparency/preview
  * admin: /dsa/transparency/publish, /dsa/tdb/*, /moderation/admin/flags
  * juridic (is_legal_officer sau admin): /dsa/orders/*  - moderatorii obisnuiti NU au acces
"""
from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import authority_orders as AO
from . import dsa_applicability as APPL
from . import dsa_delivery as DL
from . import dsa_public_notices as PN
from . import dsa_tdb as TDB
from . import dsa_transparency as TR
from . import moderation_service as MS
from .moderation_routes import _http, _wait
from .ratelimit import SlidingWindowLimiter

_public_notice_limiter = SlidingWindowLimiter(5, 3600)
_public_status_limiter = SlidingWindowLimiter(30, 3600)
_public_read_limiter = SlidingWindowLimiter(120, 600)
_authority_limiter = SlidingWindowLimiter(5, 3600)
_staff_limiter = SlidingWindowLimiter(1200, 3600)
_legal_limiter = SlidingWindowLimiter(600, 3600)
MAX_B64 = int(AO.MAX_FILE * 1.4) + 64


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicNoticeBody(_Strict):
    content_url: Optional[str] = Field(default=None, max_length=600)
    target_type: Optional[Literal["user", "game"]] = None
    target_id: Optional[str] = Field(default=None, max_length=64)
    account_username: Optional[str] = Field(default=None, max_length=64)
    category: Literal[tuple(MS.CATEGORIES)] = "illegal_content"   # type: ignore[valid-type]
    explanation: str = Field(min_length=1, max_length=MS.MAX_TEXT)
    alleged_legal_basis: Optional[str] = Field(default=None, max_length=500)
    content_location: Optional[str] = Field(default=None, max_length=500)
    reporter_name: Optional[str] = Field(default=None, max_length=120)
    reporter_email: Optional[str] = Field(default=None, max_length=320)
    identity_exempt: bool = False
    good_faith_declaration: bool = False
    hp: Optional[str] = Field(default=None, max_length=200)     # camp-capcana anti-bot (ascuns in UI)


class PublicStatusBody(_Strict):
    report_id: str = Field(min_length=3, max_length=64)
    token: str = Field(min_length=10, max_length=200)


class OwnInitiativeBody(_Strict):
    target_type: Literal["user", "game", "message"]
    target_id: str = Field(min_length=1, max_length=128)
    category: Literal[tuple(MS.CATEGORIES)]   # type: ignore[valid-type]
    summary: str = Field(min_length=10, max_length=MS.MAX_TEXT)
    automated_detection: bool = False


class FlagBody(_Strict):
    user_id: str = Field(min_length=1, max_length=64)
    flag: Literal["legal_officer", "trusted_flagger"]
    enabled: bool


class PublishBody(_Strict):
    start: datetime
    end: datetime


class ManualBody(_Strict):
    external_reference: str = Field(min_length=4, max_length=300)


class CategoryBody(_Strict):
    category: str = Field(min_length=10, max_length=100)


class ExportBody(_Strict):
    sor_ids: Optional[list[str]] = Field(default=None, max_length=500)


class _Authority(_Strict):
    name: Optional[str] = Field(default=None, max_length=200)
    member_state: Optional[str] = Field(default=None, max_length=2)
    contact_email: Optional[str] = Field(default=None, max_length=320)
    their_reference: Optional[str] = Field(default=None, max_length=120)


class _Target(_Strict):
    url: Optional[str] = Field(default=None, max_length=600)
    target_type: Optional[Literal["user", "game", "message"]] = None
    target_id: Optional[str] = Field(default=None, max_length=64)
    account_username: Optional[str] = Field(default=None, max_length=64)


class _NonDisclosure(_Strict):
    requested: bool = False
    basis: Optional[str] = Field(default=None, max_length=500)


class OrderBody(_Strict):
    order_type: Literal["art9_illegal_content", "art10_information"]
    authority: _Authority
    legal_basis: Optional[str] = Field(default=None, max_length=1000)
    statement_of_reasons: Optional[str] = Field(default=None, max_length=MS.MAX_TEXT)
    issued_at: Optional[datetime] = None
    response_deadline: Optional[datetime] = None
    territorial_scope: Optional[str] = Field(default=None, max_length=300)
    authority_to_inform: Optional[str] = Field(default=None, max_length=300)
    redress_information: bool = False
    non_disclosure: _NonDisclosure = _NonDisclosure()
    targets: list[_Target] = Field(default_factory=list, max_length=50)
    requested_items: list[str] = Field(default_factory=list, max_length=30)
    illegal_content_category: Optional[Literal[tuple(MS.CATEGORIES)]] = None   # type: ignore[valid-type]


class StaffOrderBody(OrderBody):
    received_at: Optional[datetime] = None
    received_via: Literal["email", "post", "dsc_transmission", "contact_point_web", "other"] = "email"


class SupplementBody(_Strict):
    authority: Optional[_Authority] = None
    legal_basis: Optional[str] = Field(default=None, max_length=1000)
    statement_of_reasons: Optional[str] = Field(default=None, max_length=MS.MAX_TEXT)
    issued_at: Optional[datetime] = None
    response_deadline: Optional[datetime] = None
    territorial_scope: Optional[str] = Field(default=None, max_length=300)
    authority_to_inform: Optional[str] = Field(default=None, max_length=300)
    redress_information: Optional[bool] = None
    non_disclosure: Optional[_NonDisclosure] = None
    targets: Optional[list[_Target]] = Field(default=None, max_length=50)
    requested_items: Optional[list[str]] = Field(default=None, max_length=30)
    illegal_content_category: Optional[Literal[tuple(MS.CATEGORIES)]] = None   # type: ignore[valid-type]


class AuthenticityBody(_Strict):
    verified: bool
    method: str = Field(min_length=10, max_length=500)


class LegalReviewBody(_Strict):
    decision: Literal["approve", "reject", "request_information"]
    reasoning: str = Field(min_length=20, max_length=MS.MAX_TEXT)
    confirm_non_disclosure: bool = False


class NoteBody(_Strict):
    note: str = Field(min_length=10, max_length=500)


class ReasonBody(_Strict):
    reason: str = Field(min_length=10, max_length=500)


class ResponseBody(_Strict):
    kind: Literal["receipt", "effect", "rejection"]


class AttachmentBody(_Strict):
    filename: str = Field(min_length=1, max_length=200)
    data_b64: str = Field(min_length=4, max_length=MAX_B64)
    kind: Literal["order_document", "supporting"] = "order_document"


def _dump(m: BaseModel) -> dict:
    return m.model_dump(exclude_unset=True, mode="python")


def make_dsa_router(get_current_user, db, *, target_exists: Callable, client_ip: Callable[[Request], str]) -> APIRouter:
    router = APIRouter(tags=["dsa"])

    async def run(coro):
        try:
            return await coro
        except MS.ModerationError as e:
            raise _http(e)

    async def staff(current: dict = Depends(get_current_user)) -> dict:
        if not MS.role_of(current):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Moderator access required"})
        _wait(_staff_limiter, current["user_id"])
        return current

    async def admin(current: dict = Depends(staff)) -> dict:
        if MS.role_of(current) != "admin":
            raise HTTPException(status_code=403, detail={"code": "admin_required"})
        return current

    async def legal(current: dict = Depends(get_current_user)) -> dict:
        if not AO.order_role(current):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Legal access required"})
        _wait(_legal_limiter, current["user_id"])
        return current

    # ------------------------------------------------------------------ public: Art. 16 fara cont
    @router.post("/moderation/public/notices", status_code=201)
    async def public_notice(body: PublicNoticeBody, request: Request):
        ip = client_ip(request)
        _wait(_public_notice_limiter, ip)
        return await run(PN.submit_public_notice(db, body.model_dump(), ip=ip, target_exists=target_exists))

    @router.post("/moderation/public/notices/status")
    async def public_notice_status(body: PublicStatusBody, request: Request):
        _wait(_public_status_limiter, client_ip(request))
        return await run(PN.public_status(db, body.report_id, body.token))

    # ------------------------------------------------------------------ public: Art. 15
    @router.get("/transparency/reports")
    async def transparency_list(request: Request):
        _wait(_public_read_limiter, client_ip(request))
        return {"reports": await TR.list_public(db),
                "note": "Machine-readable (JSON/CSV) transparency reports published by Astran Game under Art. 15 DSA."}

    @router.get("/transparency/reports/{report_id}")
    async def transparency_get(report_id: str, request: Request):
        _wait(_public_read_limiter, client_ip(request))
        return await run(TR.get_public(db, report_id))

    @router.get("/transparency/reports/{report_id}/csv")
    async def transparency_csv(report_id: str, request: Request):
        _wait(_public_read_limiter, client_ip(request))
        text = await run(TR.get_public_csv(db, report_id))
        safe = re.sub(r"[^A-Za-z0-9_-]", "", report_id)[:40]
        return Response(content=text, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="astran-transparency-{safe}.csv"',
                                                                                  "X-Content-Type-Options": "nosniff"})

    # ------------------------------------------------------------------ public: punct de contact pentru autoritati (Art. 11)
    @router.get("/authority/contact")
    async def authority_contact():
        return MS.contact_info()

    @router.post("/authority/intake", status_code=201)
    async def authority_intake(body: OrderBody, request: Request):
        """Inregistreaza ordinul ca NEVERIFICAT; nu executa nimic. Documentele se trimit pe canalul securizat indicat in /authority/contact."""
        _wait(_authority_limiter, client_ip(request))
        return await run(AO.public_intake(db, _dump(body)))

    # ------------------------------------------------------------------ personal
    @router.post("/moderation/admin/own-initiative", status_code=201)
    async def own_initiative(body: OwnInitiativeBody, s: dict = Depends(staff)):
        return await run(MS.open_own_initiative_case(db, s, **body.model_dump()))

    @router.get("/moderation/admin/deliveries")
    async def deliveries(_s: dict = Depends(staff), status: Optional[str] = Query(default=None, max_length=30)):
        return await DL.admin_list(db, status=status)

    @router.post("/moderation/admin/deliveries/{delivery_id}/retry")
    async def delivery_retry(delivery_id: str, s: dict = Depends(staff)):
        return await run(DL.admin_retry(db, s, delivery_id))

    @router.get("/dsa/applicability")
    async def applicability(_s: dict = Depends(staff)):
        return {"obligations": APPL.obligations(), "orders_storage": AO.storage_info(), "tdb": TDB.public_config()}

    @router.get("/dsa/transparency/preview")
    async def transparency_preview(s: dict = Depends(staff), start: Optional[datetime] = None, end: Optional[datetime] = None,
                                   year: Optional[int] = Query(default=None, ge=2024, le=2100)):
        if year and not (start or end):
            start, end = TR.period_for_year(year)
        if not (start and end):
            raise HTTPException(status_code=422, detail={"code": "period_required"})
        return await run(TR.preview(db, s, start, end))

    # ------------------------------------------------------------------ admin
    @router.post("/moderation/admin/flags")
    async def set_flag(body: FlagBody, s: dict = Depends(admin)):
        return await run(MS.set_flag(db, s, body.user_id, body.flag, body.enabled))

    @router.post("/dsa/transparency/publish", status_code=201)
    async def transparency_publish(body: PublishBody, s: dict = Depends(admin)):
        return await run(TR.publish(db, s, body.start, body.end))

    @router.get("/dsa/tdb")
    async def tdb_overview(s: dict = Depends(admin), status: Optional[str] = Query(default=None, max_length=30)):
        return await run(TDB.overview(db, s, status=status))

    @router.post("/dsa/tdb/export")
    async def tdb_export(body: ExportBody, s: dict = Depends(admin)):
        return await run(TDB.export_statements(db, s, sor_ids=body.sor_ids))

    @router.post("/dsa/tdb/{submission_id}/confirm-manual")
    async def tdb_confirm(submission_id: str, body: ManualBody, s: dict = Depends(admin)):
        return await run(TDB.confirm_manual(db, s, submission_id, body.external_reference))

    @router.post("/dsa/tdb/{submission_id}/category")
    async def tdb_category(submission_id: str, body: CategoryBody, s: dict = Depends(admin)):
        return await run(TDB.set_category(db, s, submission_id, body.category))

    @router.post("/dsa/tdb/{submission_id}/retry")
    async def tdb_retry(submission_id: str, s: dict = Depends(admin)):
        return await run(TDB.retry(db, s, submission_id))

    # ------------------------------------------------------------------ juridic: ordine Art. 9 / 10
    @router.get("/dsa/orders")
    async def orders_list(a: dict = Depends(legal), status: Optional[str] = Query(default=None, max_length=40),
                          order_type: Optional[Literal["art9_illegal_content", "art10_information"]] = None):
        return await run(AO.list_orders(db, a, status=status, order_type=order_type))

    @router.post("/dsa/orders", status_code=201)
    async def orders_create(body: StaffOrderBody, a: dict = Depends(legal)):
        d = _dump(body)
        received_at, via = d.pop("received_at", None), d.pop("received_via", "email")
        return await run(AO.create_order(db, a, d, received_at=received_at, via=via))

    @router.get("/dsa/orders/{order_id}")
    async def orders_get(order_id: str, a: dict = Depends(legal)):
        return await run(AO.get_order(db, a, order_id))

    @router.post("/dsa/orders/{order_id}/supplement")
    async def orders_supplement(order_id: str, body: SupplementBody, a: dict = Depends(legal)):
        return await run(AO.supplement(db, a, order_id, _dump(body)))

    @router.post("/dsa/orders/{order_id}/authenticity")
    async def orders_authenticity(order_id: str, body: AuthenticityBody, a: dict = Depends(legal)):
        return await run(AO.set_authenticity(db, a, order_id, verified=body.verified, method=body.method))

    @router.post("/dsa/orders/{order_id}/legal-review")
    async def orders_review(order_id: str, body: LegalReviewBody, a: dict = Depends(legal)):
        return await run(AO.legal_review(db, a, order_id, decision=body.decision, reasoning=body.reasoning, confirm_non_disclosure=body.confirm_non_disclosure))

    @router.post("/dsa/orders/{order_id}/execute")
    async def orders_execute(order_id: str, a: dict = Depends(legal)):
        return await run(AO.execute_art9(db, a, order_id))

    @router.post("/dsa/orders/{order_id}/information")
    async def orders_information(order_id: str, a: dict = Depends(legal)):
        return await run(AO.prepare_information(db, a, order_id))

    @router.post("/dsa/orders/{order_id}/package-delivered")
    async def orders_package_delivered(order_id: str, body: NoteBody, a: dict = Depends(legal)):
        return await run(AO.confirm_package_delivered(db, a, order_id, body.note))

    @router.post("/dsa/orders/{order_id}/release-notice")
    async def orders_release(order_id: str, body: ReasonBody, a: dict = Depends(legal)):
        return await run(AO.release_withheld_notice(db, a, order_id, body.reason))

    @router.post("/dsa/orders/{order_id}/responses", status_code=201)
    async def orders_respond(order_id: str, body: ResponseBody, a: dict = Depends(legal)):
        return await run(AO.send_response(db, a, order_id, body.kind))

    @router.post("/dsa/orders/{order_id}/close")
    async def orders_close(order_id: str, body: NoteBody, a: dict = Depends(legal)):
        return await run(AO.close_order(db, a, order_id, body.note))

    @router.post("/dsa/orders/{order_id}/attachments", status_code=201)
    async def orders_attach(order_id: str, body: AttachmentBody, a: dict = Depends(legal)):
        try:
            data = base64.b64decode(body.data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=422, detail={"code": "attachment_encoding_invalid"})
        return await run(AO.add_attachment(db, a, order_id, filename=body.filename, data=data, kind=body.kind))

    @router.get("/dsa/orders/{order_id}/attachments/{attachment_id}")
    async def orders_download(order_id: str, attachment_id: str, a: dict = Depends(legal)):
        name, ctype, data = await run(AO.read_attachment(db, a, order_id, attachment_id))
        return Response(content=data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff",
                                                                  "Cache-Control": "no-store"})

    return router
