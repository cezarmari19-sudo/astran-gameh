"""Rute /api/consumer/* : informatii precontractuale (checkout), confirmari, comenzi, cereri de rambursare si panou admin.

Autorizare pe SERVER:
  * rutele utilizatorului lucreaza DOAR cu `user_id` din sesiune (fara parametru user_id; ID straine => 404);
  * rutele /consumer/admin/* cer `is_platform_admin` citit din DB la fiecare cerere (nu din token/frontend);
  * corpurile cererilor interzic campuri necunoscute; confirmarile sunt booleene stricte;
  * limitare de rata pe scrieri.
"""
from __future__ import annotations

from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:   # stub-urile de test nu au mereu aceste nume; pydantic real le are
    from pydantic import ConfigDict, StrictBool
except ImportError:
    ConfigDict = dict  # type: ignore[misc,assignment]
    StrictBool = bool  # type: ignore[misc,assignment]

from . import consumer as C
from . import refunds as R
from .ratelimit import SlidingWindowLimiter

_write = SlidingWindowLimiter(60, 3600)
_read = SlidingWindowLimiter(600, 3600)
_admin = SlidingWindowLimiter(1200, 3600)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutBody(_Strict):
    product_type: Literal["astran_package", "shop_item", "clothes_item"]
    product_id: str = Field(min_length=1, max_length=64)
    language: Optional[str] = Field(default=None, max_length=8)


class ConfirmBody(_Strict):
    accept_immediate_performance: StrictBool = False
    acknowledge_loss_of_withdrawal: StrictBool = False
    presented_sha256: dict = Field(default_factory=dict)


class RefundBody(_Strict):
    tx_id: str = Field(min_length=1, max_length=64)
    reason: Literal[tuple(R.REASONS)]   # type: ignore[valid-type]
    explanation: Optional[str] = Field(default=None, max_length=R.EXPLANATION_MAX)


class ReviewDecisionBody(_Strict):
    decision: Literal["approve", "reject"]
    reasoning: str = Field(min_length=3, max_length=2000)
    override: bool = False


class ExternalBody(_Strict):
    external_ref: str = Field(min_length=3, max_length=200)


def make_consumer_router(get_current_user: Callable, db, processors: Optional[dict] = None) -> APIRouter:
    router = APIRouter(prefix="/consumer", tags=["consumer"])

    def _lim(l: SlidingWindowLimiter, uid: str) -> None:
        wait = l.hit(uid)
        if wait is not None:
            raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Too many requests"}, headers={"Retry-After": str(wait)})

    def _http(e: C.ConsumerError) -> HTTPException:
        return HTTPException(status_code=e.status, detail=e.detail())

    async def _admin_user(current: dict) -> dict:
        fresh = await db.users.find_one({"user_id": current["user_id"]}, {"_id": 0, "user_id": 1, "is_platform_admin": 1, "deletion_state": 1})
        try:
            R.require_admin_doc(fresh)
        except C.ConsumerError as e:
            raise _http(e)
        return {**current, "is_platform_admin": True}

    # ---------------- public: documente + comerciant
    @router.get("/documents/{doc_type}")
    async def document(doc_type: str, version: Optional[str] = None, lang: Optional[str] = None):
        reg = C.docs()
        if not reg.has_doc(doc_type):
            raise HTTPException(status_code=404, detail={"code": "unknown_document", "message": "Unknown document"})
        lg = reg.normalize_lang(lang)
        ver = version or reg.current_version(doc_type)
        got = reg.text(doc_type, ver, lg)
        if got is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_version", "message": "Unknown version"})
        return {"doc_type": doc_type, "version": ver, "language": lg, "sha256": got[1], "text": got[0], "is_current": ver == reg.current_version(doc_type)}

    @router.get("/trader")
    async def trader():
        t = C.trader_info()
        return {k: v for k, v in t.items()}

    # ---------------- checkout
    @router.post("/checkout")
    async def create_checkout(body: CheckoutBody, current=Depends(get_current_user)):
        _lim(_write, current["user_id"])
        try:
            return await C.create_checkout(db, current, body.product_type, body.product_id, body.language)
        except C.ConsumerError as e:
            raise _http(e)

    @router.get("/checkout/{checkout_id}")
    async def get_checkout(checkout_id: str, current=Depends(get_current_user)):
        _lim(_read, current["user_id"])
        try:
            return await C.get_checkout(db, current, checkout_id)
        except C.ConsumerError as e:
            raise _http(e)

    @router.post("/checkout/{checkout_id}/confirm")
    async def confirm_checkout(checkout_id: str, body: ConfirmBody, current=Depends(get_current_user)):
        _lim(_write, current["user_id"])
        try:
            return await C.confirm_checkout(db, current, checkout_id, accept_immediate_performance=body.accept_immediate_performance,
                                            acknowledge_loss_of_withdrawal=body.acknowledge_loss_of_withdrawal,
                                            presented_sha256=body.presented_sha256)
        except C.ConsumerError as e:
            raise _http(e)

    # ---------------- comenzi
    @router.get("/orders")
    async def orders(current=Depends(get_current_user)):
        _lim(_read, current["user_id"])
        return {"orders": await C.list_orders(db, current["user_id"])}

    @router.get("/orders/{order_id}")
    async def order(order_id: str, current=Depends(get_current_user)):
        _lim(_read, current["user_id"])
        try:
            return await C.get_order(db, current["user_id"], order_id)
        except C.ConsumerError as e:
            raise _http(e)

    # ---------------- rambursari (utilizator)
    @router.get("/refunds/transactions")
    async def refund_transactions(current=Depends(get_current_user)):
        _lim(_read, current["user_id"])
        return {"transactions": await R.refundable_transactions(db, current["user_id"])}

    @router.post("/refunds")
    async def create_refund(body: RefundBody, current=Depends(get_current_user)):
        _lim(_write, current["user_id"])
        try:
            return await R.create_request(db, current, body.tx_id, body.reason, body.explanation)
        except C.ConsumerError as e:
            raise _http(e)

    @router.get("/refunds")
    async def my_refunds(current=Depends(get_current_user)):
        _lim(_read, current["user_id"])
        return {"requests": await R.list_requests(db, current["user_id"])}

    @router.get("/refunds/{refund_id}")
    async def my_refund(refund_id: str, current=Depends(get_current_user)):
        _lim(_read, current["user_id"])
        try:
            return await R.get_own_request(db, current["user_id"], refund_id)
        except C.ConsumerError as e:
            raise _http(e)

    # ---------------- admin
    @router.get("/admin/refunds")
    async def admin_list(status: Optional[str] = None, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        try:
            return {"requests": await R.admin_list(db, status)}
        except C.ConsumerError as e:
            raise _http(e)

    @router.get("/admin/refunds/{refund_id}")
    async def admin_detail(refund_id: str, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        try:
            return await R.admin_detail(db, actor, refund_id)
        except C.ConsumerError as e:
            raise _http(e)

    @router.post("/admin/refunds/{refund_id}/review")
    async def admin_review(refund_id: str, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        try:
            await R.start_review(db, actor, refund_id)
            return {"ok": True}
        except C.ConsumerError as e:
            raise _http(e)

    @router.post("/admin/refunds/{refund_id}/decision")
    async def admin_decision(refund_id: str, body: ReviewDecisionBody, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        try:
            return await R.decide(db, actor, refund_id, body.decision, body.reasoning, override=body.override)
        except C.ConsumerError as e:
            raise _http(e)

    @router.post("/admin/refunds/{refund_id}/execute")
    async def admin_execute(refund_id: str, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        try:
            return await R.execute(db, actor, refund_id, processors)
        except C.ConsumerError as e:
            raise _http(e)

    @router.post("/admin/refunds/{refund_id}/confirm-external")
    async def admin_confirm_external(refund_id: str, body: ExternalBody, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        try:
            return await R.confirm_external(db, actor, refund_id, body.external_ref)
        except C.ConsumerError as e:
            raise _http(e)

    @router.get("/admin/audit")
    async def admin_audit(refund_id: Optional[str] = None, current=Depends(get_current_user)):
        actor = await _admin_user(current)
        _lim(_admin, actor["user_id"])
        q = {"refund_id": refund_id} if refund_id else {}
        rows = await db[R.COL_AUDIT].find(q, {"_id": 0}).sort("ts", -1).limit(300).to_list(300)
        return {"events": rows}

    return router
