"""/api/admin/incidents/* - gestionarea incidentelor de securitate (GDPR Art. 33-34). DOAR personal autorizat.

Autorizarea se face pe backend la FIECARE cerere, din documentul utilizatorului incarcat din DB (nu din ce trimite
clientul): admin / responsabil juridic / responsabil de securitate. Un utilizator obisnuit primeste 403 generic si nu
afla nici macar ca exista incidente. Nicio ruta nu trimite ceva catre autoritati sau utilizatori; rutele pregatesc si
documenteaza (vezi incidents.py).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import incidents as INC
from .ratelimit import SlidingWindowLimiter

_read_limiter = SlidingWindowLimiter(120, 60)
_write_limiter = SlidingWindowLimiter(40, 60)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateBody(_Strict):
    title: str = Field(max_length=200)
    description: str = Field(max_length=5000)
    incident_type: str = Field(max_length=40)
    discovered_at: str = Field(max_length=40)
    occurred_at: Optional[str] = Field(default=None, max_length=40)
    breach_properties: list[str] = Field(default_factory=list, max_length=3)
    data_categories: list[str] = Field(default_factory=list, max_length=20)
    affected_users_estimate: Optional[int] = Field(default=None, ge=0)
    affected_users_unknown: bool = False
    affected_records_estimate: Optional[int] = Field(default=None, ge=0)


class FactsBody(_Strict):
    description: Optional[str] = Field(default=None, max_length=5000)
    discovered_at: Optional[str] = Field(default=None, max_length=40)
    breach_properties: Optional[list[str]] = Field(default=None, max_length=3)
    data_categories: Optional[list[str]] = Field(default=None, max_length=20)
    affected_users_estimate: Optional[int] = Field(default=None, ge=0)
    affected_records_estimate: Optional[int] = Field(default=None, ge=0)
    add_measure: Optional[str] = Field(default=None, max_length=1200)


class AssessBody(_Strict):
    severity: str = Field(max_length=16)
    risk_level: str = Field(max_length=16)
    reasoning: str = Field(max_length=5000)
    likely_consequences: Optional[str] = Field(default=None, max_length=2500)


class AuthorityDecisionBody(_Strict):
    decision: str = Field(max_length=16)
    reasons: str = Field(max_length=5000)


class DraftBody(_Strict):
    dpo_contact: str = Field(max_length=400)
    likely_consequences: Optional[str] = Field(default=None, max_length=2500)
    draft_text: Optional[str] = Field(default=None, max_length=12000)


class SubmissionBody(_Strict):
    submitted_at: str = Field(max_length=40)
    authority_reference: Optional[str] = Field(default=None, max_length=160)
    late_reason: Optional[str] = Field(default=None, max_length=2500)
    phased: bool = False


class SubjectsDecisionBody(_Strict):
    required: bool
    reasons: str = Field(max_length=5000)
    exemption: Optional[str] = Field(default=None, max_length=60)
    contact: Optional[str] = Field(default=None, max_length=400)
    likely_consequences: Optional[str] = Field(default=None, max_length=2500)
    advice: Optional[str] = Field(default=None, max_length=1800)


class SubjectsRecordBody(_Strict):
    communicated_at: str = Field(max_length=40)
    channel: str = Field(max_length=20)
    recipients_count: int = Field(ge=0)


class CloseBody(_Strict):
    lessons_learned: str = Field(max_length=5000)


class PolicyBody(_Strict):
    notify_deadline_hours: Optional[int] = None
    due_soon_hours: Optional[int] = None
    require_separate_approver: Optional[bool] = None
    min_reason_chars: Optional[int] = None
    child_data_forces_high_severity: Optional[bool] = None


def _wrap(e: INC.IncidentError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail())


def make_incident_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/admin/incidents", tags=["security-incidents"])

    async def staff(current: dict = Depends(get_current_user)) -> dict:
        if not INC.staff_role(current):
            # raspuns generic: nu dezvaluim existenta modulului/incidentelor
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Not authorized"})
        return current

    def _limit(actor: dict, write: bool) -> None:
        w = (_write_limiter if write else _read_limiter).hit(("w:" if write else "r:") + actor["user_id"])
        if w is not None:
            raise HTTPException(status_code=429, detail={"code": "too_many_requests", "retry_after": w}, headers={"Retry-After": str(w)})

    async def _run(coro):
        try:
            return await coro
        except INC.IncidentError as e:
            raise _wrap(e)

    @router.get("/me")
    async def me(actor: dict = Depends(staff)):
        return {"role": INC.staff_role(actor), "can_approve": INC.staff_role(actor) in ("admin", "legal"),
                "enums": {"incident_types": INC.INCIDENT_TYPES, "data_categories": INC.DATA_CATEGORIES, "severities": INC.SEVERITIES,
                          "risk_levels": INC.RISK_LEVELS, "art34_exemptions": INC.ART34_EXEMPTIONS, "channels": INC.CHANNELS,
                          "breach_properties": INC.BREACH_PROPERTIES}}

    @router.get("/summary")
    async def summary(actor: dict = Depends(staff)):
        _limit(actor, False)
        return await _run(INC.dashboard_summary(db, actor))

    @router.get("/policy")
    async def get_policy(actor: dict = Depends(staff)):
        _limit(actor, False)
        return {"policy": await INC.get_policy(db)}

    @router.put("/policy")
    async def put_policy(body: PolicyBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        res = await _run(INC.set_policy(db, actor, body.model_dump(exclude_none=True)))
        await INC.audit(db, "policy", "policy_changed", actor, {"keys": sorted(body.model_dump(exclude_none=True))})
        return {"policy": res}

    @router.get("")
    async def list_all(status: Optional[str] = None, limit: int = 100, actor: dict = Depends(staff)):
        _limit(actor, False)
        return {"incidents": await _run(INC.list_incidents(db, actor, status=status, limit=limit))}

    @router.post("", status_code=201)
    async def create(body: CreateBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.create_incident(db, actor, body.model_dump()))}

    @router.get("/{incident_id}")
    async def get_one(incident_id: str, actor: dict = Depends(staff)):
        _limit(actor, False)
        return {"incident": await _run(INC.get_incident(db, actor, incident_id))}

    @router.patch("/{incident_id}")
    async def patch(incident_id: str, body: FactsBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.update_facts(db, actor, incident_id, body.model_dump(exclude_none=True)))}

    @router.post("/{incident_id}/assessment")
    async def assess(incident_id: str, body: AssessBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.assess(db, actor, incident_id, body.model_dump()))}

    @router.post("/{incident_id}/authority/decision")
    async def authority_decision(incident_id: str, body: AuthorityDecisionBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.decide_authority_notification(db, actor, incident_id, body.model_dump()))}

    @router.post("/{incident_id}/authority/approve")
    async def authority_approve(incident_id: str, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.approve_authority_decision(db, actor, incident_id))}

    @router.post("/{incident_id}/authority/draft")
    async def authority_draft(incident_id: str, body: DraftBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.prepare_authority_notification(db, actor, incident_id, body.model_dump()))}

    @router.post("/{incident_id}/authority/submission")
    async def authority_submission(incident_id: str, body: SubmissionBody, actor: dict = Depends(staff)):
        """Doar INREGISTREAZA depunerea facuta de responsabil prin canalul oficial; nu trimite nimic."""
        _limit(actor, True)
        return {"incident": await _run(INC.record_authority_submission(db, actor, incident_id, body.model_dump()))}

    @router.post("/{incident_id}/data-subjects/decision")
    async def subjects_decision(incident_id: str, body: SubjectsDecisionBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.decide_data_subject_communication(db, actor, incident_id, body.model_dump()))}

    @router.post("/{incident_id}/data-subjects/communication")
    async def subjects_record(incident_id: str, body: SubjectsRecordBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.record_data_subject_communication(db, actor, incident_id, body.model_dump()))}

    @router.post("/{incident_id}/close")
    async def close(incident_id: str, body: CloseBody, actor: dict = Depends(staff)):
        _limit(actor, True)
        return {"incident": await _run(INC.close_incident(db, actor, incident_id, body.model_dump()))}

    @router.get("/{incident_id}/audit")
    async def audit_log(incident_id: str, actor: dict = Depends(staff)):
        _limit(actor, False)
        return await _run(INC.audit_history(db, actor, incident_id))

    return router
