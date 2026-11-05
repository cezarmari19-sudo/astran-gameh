"""Administrarea chatului din joc: reguli de filtrare (cuvinte/expresii interzise) si mute.
Doar pentru platform admin (users.is_platform_admin) - aceeasi verificare ca in server.py.

Reguli (vezi formatul in chat_filter.py), prin HTTP - efect imediat pe acest proces si in
maxim RULES_TTL_SECONDS (~15s) pe celelalte, FARA redeploy:
    GET    /api/admin/chat/rules                -> {"source": "db"|"file", "rules": [...]}
    PUT    /api/admin/chat/rules                -> inlocuieste TOATA lista   body: {"rules": [...]}
    POST   /api/admin/chat/rules                -> adauga/modifica O regula (dupa "id")  body: regula
    DELETE /api/admin/chat/rules/{rule_id}      -> sterge o regula
Alternativa fara API: editezi backend/config/chat_filter_rules.json (cat timp nu exista reguli
salvate prin API in DB - acelea au prioritate).

Mute de chat (nu afecteaza restul jocului; se aplica la urmatorul mesaj, pe orice instanta):
    POST   /api/admin/chat/mute    body: {"user_id": "...", "minutes": 60}
    POST   /api/admin/chat/unmute  body: {"user_id": "..."}
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from astran_sandbox.chat_filter import Rule, RuleError, RuleStore, parse_rules, rule_from_dict, shared_store

log = logging.getLogger("astran.chat_admin")

MAX_MUTE_MINUTES = 60 * 24 * 365


class RulesBody(BaseModel):
    rules: List[dict] = Field(default_factory=list)


class MuteBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    minutes: int = Field(default=60, ge=1, le=MAX_MUTE_MINUTES)


class UnmuteBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


def make_chat_admin_router(get_current_user, db, store: RuleStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/admin/chat", tags=["chat-admin"])
    rules_store = store or shared_store(db)

    async def require_admin(current: dict = Depends(get_current_user)) -> dict:
        if not current.get("is_platform_admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        return current

    async def _save(rules: list[Rule]) -> dict:
        try:
            await rules_store.save_rules(rules)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception:  # noqa: BLE001
            log.warning("chat_admin: salvarea regulilor a esuat", exc_info=True)
            raise HTTPException(status_code=503, detail="Could not save rules")
        return {"ok": True, "source": rules_store.source, "count": len(rules)}

    @router.get("/rules")
    async def list_rules(_admin: dict = Depends(require_admin)):
        rules_store.invalidate()
        rules = await rules_store.get_rules()
        return {"source": rules_store.source, "rules": [r.to_dict() for r in rules]}

    @router.put("/rules")
    async def replace_rules(body: RulesBody, _admin: dict = Depends(require_admin)):
        try:
            rules = parse_rules(body.rules)
        except RuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return await _save(rules)

    @router.post("/rules")
    async def upsert_rule(raw: dict = Body(...), _admin: dict = Depends(require_admin)):
        try:
            new_rule = rule_from_dict(raw)
        except RuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rules_store.invalidate()
        current_rules = await rules_store.get_rules()
        merged = [new_rule if r.id == new_rule.id else r for r in current_rules]
        if all(r.id != new_rule.id for r in current_rules):
            merged.append(new_rule)
        try:
            parse_rules([r.to_dict() for r in merged])  # re-valideaza lista finala (limita de reguli etc.)
        except RuleError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        out = await _save(merged)
        return {**out, "rule": new_rule.to_dict()}

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: str, _admin: dict = Depends(require_admin)):
        rules_store.invalidate()
        current_rules = await rules_store.get_rules()
        remaining = [r for r in current_rules if r.id != rule_id]
        if len(remaining) == len(current_rules):
            raise HTTPException(status_code=404, detail="Rule not found")
        return await _save(remaining)

    @router.post("/mute")
    async def mute(body: MuteBody, _admin: dict = Depends(require_admin)):
        until = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)
        res = await db.users.update_one({"user_id": body.user_id}, {"$set": {"chat_muted_until": until}})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "user_id": body.user_id, "chat_muted_until": until}

    @router.post("/unmute")
    async def unmute(body: UnmuteBody, _admin: dict = Depends(require_admin)):
        res = await db.users.update_one({"user_id": body.user_id}, {"$unset": {"chat_muted_until": ""}})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "user_id": body.user_id}

    return router