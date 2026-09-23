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


MAX_GAME_ASSETS = 100


class RunBody(BaseModel):
    files: Optional[List[ScriptFileBody]] = None
    source: Optional[str] = Field(default=None, max_length=MAX_SOURCE_CHARS)  # compatibilitate: un singur script
    asset_ids: Optional[List[str]] = None  # id-uri de modele din Shop, pentru testare cu Assets.load in editor


class SaveFilesBody(BaseModel):
    files: List[ScriptFileBody] = Field(default_factory=list)


class SaveAssetsBody(BaseModel):
    asset_ids: List[str] = Field(default_factory=list, max_length=MAX_GAME_ASSETS)


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
            await db.game_assets.create_index("game_id", unique=True)
        except Exception:  # noqa: BLE001
            log.warning("could not create game_scripts/game_assets index", exc_info=True)
        index_ready = True

    async def resolve_assets(user_id: str, asset_ids: list[str]) -> list[dict]:
        """Din id-uri de modele din Shop, intoarce doar cele pe care userul chiar le detine
        (autor, cumparate, sau gratis+publice) — restul se ignora silentios, ca un id vechi
        (sters sau devenit privat intre timp) sa nu strice rularea jocului."""
        if not asset_ids:
            return []
        ids = list(dict.fromkeys(asset_ids))[:MAX_GAME_ASSETS]  # dedupe, pastrand ordinea
        items = await db.shop_items.find({"item_id": {"$in": ids}, "kind": "model"}, {"_id": 0}).to_list(MAX_GAME_ASSETS)
        by_id = {it["item_id"]: it for it in items}

        purchased_ids: set[str] = set()
        to_check = [i for i in ids if i in by_id and by_id[i]["owner_id"] != user_id and by_id[i]["price"] > 0]
        if to_check:
            purchases = await db.shop_purchases.find(
                {"user_id": user_id, "item_id": {"$in": to_check}}, {"_id": 0}
            ).to_list(len(to_check))
            purchased_ids = {p["item_id"] for p in purchases}

        resolved = []
        for aid in ids:
            it = by_id.get(aid)
            if it is None:
                continue
            owned = it["owner_id"] == user_id or (it["price"] == 0 and it["is_public"]) or aid in purchased_ids
            if not owned:
                continue
            resolved.append({"id": aid, "name": it.get("name", ""), "object": it.get("object", {})})
        return resolved

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
            assets = await resolve_assets(current["user_id"], body.asset_ids or [])
            return await run_files(files, assets=assets)
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

    @router.get("/games/{game_id}/assets")
    async def get_game_assets(game_id: str, current=Depends(get_current_user)):
        """Id-urile modelelor din Shop atasate unui joc (doar proprietarul)."""
        g = await get_game(game_id)
        if not is_owner(g, current):
            raise HTTPException(status_code=403, detail="Not the owner")
        await ensure_index()
        doc = await db.game_assets.find_one({"game_id": game_id}, {"_id": 0})
        asset_ids = doc["asset_ids"] if doc and isinstance(doc.get("asset_ids"), list) else []
        resolved = await resolve_assets(current["user_id"], asset_ids)
        # pastram si id-urile care nu s-au putut rezolva (ex: itemul a fost sters), ca sa le poata scoate din lista
        resolved_ids = {a["id"] for a in resolved}
        missing = [aid for aid in asset_ids if aid not in resolved_ids]
        return {"asset_ids": asset_ids, "assets": resolved, "missing_ids": missing}

    @router.put("/games/{game_id}/assets")
    async def save_game_assets(game_id: str, body: SaveAssetsBody, current=Depends(get_current_user)):
        """Salveaza lista de modele din Shop atasate unui joc (inlocuieste lista veche)."""
        g = await get_game(game_id)
        if not is_owner(g, current):
            raise HTTPException(status_code=403, detail="Not the owner")
        asset_ids = list(dict.fromkeys(body.asset_ids))[:MAX_GAME_ASSETS]  # dedupe, pastreaza ordinea
        await ensure_index()
        await db.game_assets.update_one(
            {"game_id": game_id},
            {"$set": {"game_id": game_id, "asset_ids": asset_ids, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"ok": True, "asset_ids": asset_ids}

    @router.post("/games/{game_id}/run")
    async def run_game_script(game_id: str, current=Depends(get_current_user)):
        """Ruleaza scriptul unui joc si intoarce operatiile