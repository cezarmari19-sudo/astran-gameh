"""/api/seller/* (onboarding propriu) si /api/admin/merchants/* (verificare, personal autorizat) - DSA Art. 30-32.

Datele comerciantului sunt accesibile doar: (a) proprietarului (GET/PUT /seller/status), (b) personalului (admin / responsabil
juridic) si (c) public, in forma redusa din `merchants.public_seller_info`, doar pentru comercianti.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import age_policy as AP
from . import merchants as M
from .ratelimit import SlidingWindowLimiter

_user_limiter = SlidingWindowLimiter(40, 3600)
_staff_limiter = SlidingWindowLimiter(200, 60)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddressBody(_Strict):
    line1: str = Field(max_length=200)
    line2: Optional[str] = Field(default=None, max_length=200)
    city: str = Field(max_length=100)
    postal_code: str = Field(max_length=30)
    country: str = Field(max_length=2)


class RegistryBody(_Strict):
    register_name: Optional[str] = Field(default=None, max_length=160)
    number: Optional[str] = Field(default=None, max_length=80)
    not_applicable_reason: Optional[str] = Field(default=None, max_length=400)


class TraderBody(_Strict):
    trader_type: str = Field(max_length=20)
    name: str = Field(max_length=200)
    address: AddressBody
    phone: str = Field(max_length=40)
    contact_email: str = Field(max_length=300)
    registry: Optional[RegistryBody] = None
    vat_id: Optional[str] = Field(default=None, max_length=40)
    eu_compliance_statement: bool = False


class AssessmentBody(_Strict):
    regular_sales: bool
    for_profit_or_business: bool
    professional_or_company: bool
    hired_staff_or_resells_goods: bool


class DeclareBody(_Strict):
    seller_status: str = Field(max_length=16)
    assessment: AssessmentBody
    trader: Optional[TraderBody] = None


class ReviewBody(_Strict):
    decision: str = Field(max_length=16)
    note: Optional[str] = Field(default=None, max_length=1200)


class ReclassifyBody(_Strict):
    reason: str = Field(max_length=1200)


class PolicyBody(_Strict):
    enforce_seller_status: Optional[bool] = None
    min_verification_to_sell: Optional[str] = Field(default=None, max_length=16)
    grace_until: Optional[str] = Field(default=None, max_length=32)
    signal_sales_count_12m: Optional[int] = None
    signal_revenue_astrans_12m: Optional[int] = None


def _err(e: M.MerchantError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail())


def make_merchant_router(get_current_user, db) -> APIRouter:
    router = APIRouter(tags=["merchants"])

    def _wait(limiter: SlidingWindowLimiter, key: str) -> None:
        w = limiter.hit(key)
        if w is not None:
            raise HTTPException(status_code=429, detail={"code": "too_many_requests", "retry_after": w}, headers={"Retry-After": str(w)})

    async def staff(current: dict = Depends(get_current_user)) -> dict:
        if not M.reviewer_role(current):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Not authorized"})
        _wait(_staff_limiter, "ms:" + current["user_id"])
        return current

    # ------------------------------------------------------------------ vanzator
    @router.get("/seller/status")
    async def my_status(current=Depends(get_current_user)):
        _wait(_user_limiter, "sl:" + current["user_id"])
        prof = await M.get_profile(db, current["user_id"])
        pol = await M.get_policy(db)
        return {"profile": M.own_view(prof), "eligibility": await M.sale_eligibility(db, current["user_id"], policy=pol),
                "public_preview": M.public_seller_info(prof),
                "questions": list(M.ASSESSMENT_QUESTIONS), "statement_version": M.STATEMENT_VERSION,
                "min_verification_to_sell": pol["min_verification_to_sell"], "adult_verified": AP.can_access_adult_content(current)}

    @router.put("/seller/status")
    async def declare(body: DeclareBody, current=Depends(get_current_user)):
        _wait(_user_limiter, "sl:" + current["user_id"])
        if not AP.can_access_adult_content(current):
            # vanzarea (monetizarea) e rezervata adultilor verificati: nu colectam date de comerciant de la alte conturi
            raise HTTPException(status_code=403, detail={"code": "age_verification_required", "message": "Selling requires a verified adult account"})
        try:
            prof = await M.declare_status(db, current, body.model_dump(exclude_none=False))
        except M.MerchantError as e:
            raise _err(e)
        return {"profile": M.own_view(prof), "eligibility": await M.sale_eligibility(db, current["user_id"]),
                "public_preview": M.public_seller_info(prof)}

    # ------------------------------------------------------------------ personal
    @router.get("/admin/merchants/policy")
    async def get_policy(_s: dict = Depends(staff)):
        return {"policy": await M.get_policy(db)}

    @router.put("/admin/merchants/policy")
    async def put_policy(body: PolicyBody, s: dict = Depends(staff)):
        try:
            return {"policy": await M.set_policy(db, s, body.model_dump(exclude_none=True))}
        except M.MerchantError as e:
            raise _err(e)

    @router.get("/admin/merchants")
    async def list_merchants(state: Optional[str] = None, limit: int = 100, s: dict = Depends(staff)):
        try:
            rows = await M.admin_list(db, s, state=state, limit=limit)
        except M.MerchantError as e:
            raise _err(e)
        return {"merchants": rows}

    @router.get("/admin/merchants/{user_id}")
    async def get_merchant(user_id: str, s: dict = Depends(staff)):
        try:
            return await M.admin_get(db, s, user_id)
        except M.MerchantError as e:
            raise _err(e)

    @router.post("/admin/merchants/{user_id}/review")
    async def review(user_id: str, body: ReviewBody, s: dict = Depends(staff)):
        try:
            return {"profile": await M.review(db, s, user_id, body.decision, body.note)}
        except M.MerchantError as e:
            raise _err(e)

    @router.post("/admin/merchants/{user_id}/reclassify")
    async def reclassify(user_id: str, body: ReclassifyBody, s: dict = Depends(staff)):
        try:
            return {"profile": await M.reclassify_as_trader(db, s, user_id, body.reason)}
        except M.MerchantError as e:
            raise _err(e)

    return router
