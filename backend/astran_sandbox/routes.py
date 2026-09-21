"""Endpoint-uri FastAPI pentru sandbox-ul Luau (scripturi pe mai multe fisiere).

Se ataseaza din server.py:
    api.include_router(make_sandbox_router(get_current_user, db))
(dependintele sunt primite ca argumente, ca sa nu existe import circular)

Fisierele scriptului unui joc se tin in colectia `game_scripts`
({game_id, files: [{name, source}], updated_at}), separat de documentul jocului.
Jocurile vechi, care au doar campul `script`, sunt citite ca fisierul "main".
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .runner import (
    MAX_NAME_CHARS,
    MAX_SOURCE_CHARS,
    SandboxUnavailable,
    files_from_source,
    run_cached_files,
    run_files,
    validate_files,
)

log = logging.getLogger("astran.sandbox")

RUNS_PER_MINUTE = 30


class ScriptFileBody(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    source: str = Field(default="", max_length=MAX_SOURCE_CHARS)


class RunBody(BaseModel):
    files: Optional[List[ScriptFileBody]] = None
    source: Optional[str] = Field(default=None, max_length=MAX_SOURCE_CHARS)  # compatibilitate: un singur script


class SaveFilesBody(BaseModel):
    files: List[ScriptFileBody] = Field(default_factory=list)


def _plain(files: List[ScriptFileBody]) -> list[dict]:
    return [{"name": f.name, "source": f.source} for f in files]


def make_sandbox_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/sandbox", tags=["sandbox"])
    recent_runs: dict[str, deque] = defaultdict(deque)
    index_ready = False

    def check_rate_limit(user_id: str) -> None:
        now = time.monotonic()
        hits = recent_runs[user_id]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= RUNS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Too many script runs, try again in a minute")
        hits.append(now)

    def sandbox_error(exc: Exception) -> HTTPException:
        if isinstance(exc, HTTPException):
            return exc
        if isinstance(exc, SandboxUnavailable):
            return HTTPException(status_code=503, detail=str(exc))
        if isinstance(exc, ValueError):
            return HTTPException(status_code=400, detail=str(exc))
        log.exception("sandbox failure")
        return HTTPException(status_code=500, detail="Sandbox failure")

    async def ensure_index() -> None:
        nonlocal index_ready
        if index_ready:
            return
        try:
            await db.game_scripts.create_index("game_id", unique=True)
        except Exception:  # noqa: BLE001
            log.warning("could not create game_scripts index", exc_info=True)
        index_ready = True

    async def get_game(game_id: str) -> dict:
        g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
        if not g:
            raise HTTPException(status_code=404, detail="Game not found")
        return g

    def is_owner(g: dict, current: dict) -> bool:
        return g["owner_id"] == current["user_id"] or bool(current.get("is_platform_admin"))

    async def load_files(game_id: str, g: dict) -> list[dict]:
        doc = await db.game_scripts.find_one({"game_id": game_id}, {"_id": 0})
        if doc is not None and isinstance(doc.get("files"), list):
            return doc["files"]
        legacy = g.get("script") or ""
        if isinstance(legacy, str) and legacy.strip():
            return files_from_source(legacy)
        return []

    @router.post("/run")
    async def run_source(body: RunBody, current=Depends(get_current_user)):
        """Testeaza scripturile din editor (proprietarul vede output-ul si erorile)."""
        check_rate_limit(current["user_id"])
        try:
            if body.files is not None:
                files = _plain(body.files)
            elif body.source is not None:
                files = files_from_source(body.source)
            else:
                files = []
            if not any(f["source"].strip() for f in files):
                validate_files(files)
                return {"ok": True, "output": [], "errors": [], "ops": [], "duration": 0.0, "truncated": False}
            return await run_files(files)
        except Exception as exc:  # noqa: BLE001
            raise sandbox_error(exc)

    @router.get("/games/{game_id}/files")
    async def get_files(game_id: str, current=Depends(get_current_user)):
        """Fisierele scriptului unui joc (doar proprietarul)."""
        g = await get_game(game_id)
        if not is_owner(g, current):
            raise HTTPException(status_code=403, detail="Not the owner")
        return {"files": await load_files(game_id, g)}

    @router.put("/games/{game_id}/files")
    async def save_files(game_id: str, body: SaveFilesBody, current=Depends(get_current_user)):
        """Salveaza toate fisierele scriptului unui joc (inlocuieste lista veche)."""
        g = await get_game(game_id)
        if not is_owner(g, current):
            raise HTTPException(status_code=403, detail="Not the owner")
        try:
            files = validate_files(_plain(body.files))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        await ensure_index()
        await db.game_scripts.update_one(
            {"game_id": game_id},
            {"$set": {"game_id": game_id, "files": files, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"ok": True, "files": files}

    @router.post("/games/{game_id}/run")
    async def run_game_script(game_id: str, current=Depends(get_current_user)):
        """Ruleaza scriptul unui joc si intoarce operatiile pe care clientul le reda in scena."""
        g = await get_game(game_id)
        if g.get("age_category") == "adult_18" and current.get("age_category") == "under_18":
            raise HTTPException(status_code=403, detail="Age-restricted content")

        owner = is_owner(g, current)
        if not g.get("is_public", True) and not owner:
            raise HTTPException(status_code=403, detail="Game is private")

        files = await load_files(game_id, g)
        if not any(isinstance(f, dict) and str(f.get("source", "")).strip() for f in files):
            return {"ok": True, "ops": [], "duration": 0.0, "truncated": False}

        check_rate_limit(current["user_id"])
        try:
            result = await run_cached_files(files)
        except Exception as exc:  # noqa: BLE001
            raise sandbox_error(exc)

        if owner:
            return result
        # jucatorii primesc doar efectele, nu output-ul si erorile autorului
        return {
            "ok": result["ok"],
            "ops": result["ops"],
            "duration": result["duration"],
            "truncated": result["truncated"],
        }

    return router