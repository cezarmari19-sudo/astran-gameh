"""Endpoint-uri FastAPI pentru sandbox-ul Luau.

Se ataseaza din server.py:
    api.include_router(make_sandbox_router(get_current_user, db))
(dependintele sunt primite ca argumente, ca sa nu existe import circular)
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .runner import MAX_SOURCE_CHARS, SandboxUnavailable, run_cached, run_script

log = logging.getLogger("astran.sandbox")

RUNS_PER_MINUTE = 30


class RunBody(BaseModel):
    source: str = Field(default="", max_length=MAX_SOURCE_CHARS)


def make_sandbox_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/sandbox", tags=["sandbox"])
    recent_runs: dict[str, deque] = defaultdict(deque)

    def check_rate_limit(user_id: str) -> None:
        now = time.monotonic()
        hits = recent_runs[user_id]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= RUNS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Too many script runs, try again in a minute")
        hits.append(now)

    def sandbox_error(exc: Exception) -> HTTPException:
        if isinstance(exc, SandboxUnavailable):
            return HTTPException(status_code=503, detail=str(exc))
        if isinstance(exc, ValueError):
            return HTTPException(status_code=400, detail=str(exc))
        log.exception("sandbox failure")
        return HTTPException(status_code=500, detail="Sandbox failure")

    @router.post("/run")
    async def run_source(body: RunBody, current=Depends(get_current_user)):
        """Testeaza un script din editor (proprietarul vede output-ul si erorile)."""
        check_rate_limit(current["user_id"])
        if not body.source.strip():
            return {"ok": True, "output": [], "errors": [], "ops": [], "duration": 0.0, "truncated": False}
        try:
            return await run_script(body.source)
        except Exception as exc:  # noqa: BLE001
            raise sandbox_error(exc)

    @router.post("/games/{game_id}/run")
    async def run_game_script(game_id: str, current=Depends(get_current_user)):
        """Ruleaza scriptul unui joc si intoarce operatiile pe care clientul le reda in scena."""
        g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
        if not g:
            raise HTTPException(status_code=404, detail="Game not found")
        if g.get("age_category") == "adult_18" and current.get("age_category") == "under_18":
            raise HTTPException(status_code=403, detail="Age-restricted content")

        is_owner = g["owner_id"] == current["user_id"] or bool(current.get("is_platform_admin"))
        if not g.get("is_public", True) and not is_owner:
            raise HTTPException(status_code=403, detail="Game is private")

        script = g.get("script") or ""
        if not script.strip():
            return {"ok": True, "ops": [], "duration": 0.0, "truncated": False}

        check_rate_limit(current["user_id"])
        try:
            result = await run_cached(script)
        except Exception as exc:  # noqa: BLE001
            raise sandbox_error(exc)

        if is_owner:
            return result
        # jucatorii primesc doar efectele, nu output-ul si erorile autorului
        return {
            "ok": result["ok"],
            "ops": result["ops"],
            "duration": result["duration"],
            "truncated": result["truncated"],
        }

    return router