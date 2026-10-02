"""Game Collaboration: acces de colaborator (Editor) si Tester pentru un joc, separat
complet de orice Group (vezi group_routes.py) - cele doua sisteme de permisiuni NU se
amesteca niciodata (un Group Admin NU primeste automat acces la Code Editor-ul unui joc,
si un Game Editor NU trebuie sa fie membru al niciunui Group - vezi item 13 din cerinta).

Se ataseaza din server.py:
    api.include_router(make_game_permissions_router(get_current_user, db, _user_public))

Functiile simple (get_game_access, path_allowed, filter_project_to_scope) sunt importate
DIRECT (fara factory) de server.py SI de astran_sandbox/routes.py, ca regulile de acces
sa fie EXACT aceleasi peste tot - un singur loc de adevar, nu doua implementari care pot
diverge. Asta e si raspunsul la item 15: verificarea e in acest modul, apelat din backend
la fiecare actiune, niciodata doar in frontend.

Colectii Mongo:
    game_collaborators
        {game_id, user_id, granted_by, granted_at, level,
         can_create_files, can_edit_files, can_delete_files, can_delete_folders,
         can_change_settings, can_publish,
         allowed_paths: [str],   # gol = acces la tot proiectul (in limitele flagurilor)
         denied_paths: [str]}    # exceptii, chiar si in interiorul allowed_paths

    game_testers
        {game_id, user_id, granted_by, granted_at}
        - NU are nicio permisiune de cod; doar acces de a INTRA IN JOC (can_play).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("astran.game_access")

Level = Literal["view", "code_edit", "full"]
MAX_PATHS = 50
MAX_PATH_CHARS = 240

LEVEL_PRESETS: dict[str, dict] = {
    "view": dict(can_create_files=False, can_edit_files=False, can_delete_files=False,
                 can_delete_folders=False, can_change_settings=False, can_publish=False),
    "code_edit": dict(can_create_files=True, can_edit_files=True, can_delete_files=False,
                       can_delete_folders=False, can_change_settings=False, can_publish=False),
    "full": dict(can_create_files=True, can_edit_files=True, can_delete_files=True,
                 can_delete_folders=True, can_change_settings=True, can_publish=True),
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class GameAccess:
    role: Literal["owner", "editor", "tester", "none"] = "none"
    can_view_code: bool = False
    can_create_files: bool = False
    can_edit_files: bool = False
    can_delete_files: bool = False
    can_delete_folders: bool = False
    can_change_settings: bool = False
    can_publish: bool = False
    can_play: bool = False
    allowed_paths: Optional[List[str]] = None
    denied_paths: List[str] = dc_field(default_factory=list)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _owner_access() -> GameAccess:
    return GameAccess(
        role="owner", can_view_code=True, can_create_files=True, can_edit_files=True,
        can_delete_files=True, can_delete_folders=True, can_change_settings=True,
        can_publish=True, can_play=True, allowed_paths=None, denied_paths=[],
    )


async def get_game_access(db, game: dict, user: dict) -> GameAccess:
    """Singurul loc care decide ce poate face un user cu un joc anume."""
    if game["owner_id"] == user["user_id"] or user.get("is_platform_admin"):
        return _owner_access()

    collab = await db.game_collaborators.find_one(
        {"game_id": game["game_id"], "user_id": user["user_id"]}, {"_id": 0}
    )
    if collab:
        return GameAccess(
            role="editor",
            can_view_code=True,
            can_create_files=bool(collab.get("can_create_files")),
            can_edit_files=bool(collab.get("can_edit_files")),
            can_delete_files=bool(collab.get("can_delete_files")),
            can_delete_folders=bool(collab.get("can_delete_folders")),
            can_change_settings=bool(collab.get("can_change_settings")),
            can_publish=bool(collab.get("can_publish")),
            can_play=True,
            allowed_paths=collab.get("allowed_paths") or None,
            denied_paths=collab.get("denied_paths") or [],
        )

    tester = await db.game_testers.find_one(
        {"game_id": game["game_id"], "user_id": user["user_id"]}, {"_id": 0}
    )
    if tester:
        # Testerul NU vede codul si NU poate edita nimic - doar acces de a juca/testa.
        return GameAccess(role="tester", can_play=True)

    # Niciun rol special: poate juca doar daca jocul e public (comportament neschimbat).
    return GameAccess(role="none", can_play=bool(game.get("is_public")))


def _norm_path(p: str) -> str:
    return p.strip("/")


def path_allowed(path: str, access: GameAccess) -> bool:
    """True daca un path (fisier sau folder) e in domeniul de acces al lui `access`."""
    if access.is_owner:
        return True
    p = _norm_path(path)

    def _match(prefix: str, target: str) -> bool:
        prefix = _norm_path(prefix)
        return target == prefix or target.startswith(prefix + "/")

    if any(_match(d, p) for d in access.denied_paths):
        return False
    if access.allowed_paths:
        return any(_match(a, p) for a in access.allowed_paths)
    return True


def filter_project_to_scope(files: list[dict], folders: list[str], access: GameAccess) -> tuple[list[dict], list[str]]:
    """Pentru un Editor cu acces restrans pe foldere: intoarce doar partea din proiect pe
    care are voie sa o VADA (GET files) - nu vede deloc cod din afara scope-ului lui."""
    if access.is_owner or (not access.allowed_paths and not access.denied_paths):
        return files, folders
    vis_files = [f for f in files if path_allowed(f["path"], access)]
    vis_folders = [f for f in folders if path_allowed(f, access)]
    return vis_files, vis_folders


# ---------- request bodies ----------

class GrantCollaboratorBody(BaseModel):
    target_user_id: str
    level: Level = "view"
    # Suprascrieri granulare peste preset-ul de `level` (vezi item 11).
    can_create_files: Optional[bool] = None
    can_edit_files: Optional[bool] = None
    can_delete_files: Optional[bool] = None
    can_delete_folders: Optional[bool] = None
    can_change_settings: Optional[bool] = None
    can_publish: Optional[bool] = None
    allowed_paths: Optional[List[str]] = Field(default=None, max_length=MAX_PATHS)
    denied_paths: Optional[List[str]] = Field(default=None, max_length=MAX_PATHS)


class UpdateCollaboratorBody(BaseModel):
    level: Optional[Level] = None
    can_create_files: Optional[bool] = None
    can_edit_files: Optional[bool] = None
    can_delete_files: Optional[bool] = None
    can_delete_folders: Optional[bool] = None
    can_change_settings: Optional[bool] = None
    can_publish: Optional[bool] = None
    allowed_paths: Optional[List[str]] = Field(default=None, max_length=MAX_PATHS)
    denied_paths: Optional[List[str]] = Field(default=None, max_length=MAX_PATHS)
    clear_paths: Optional[bool] = None  # true = sterge restrictiile de path (acces la tot proiectul)


class GrantTesterBody(BaseModel):
    target_user_id: str


def _clean_paths(paths: Optional[list]) -> list[str]:
    if not paths:
        return []
    out = []
    for p in paths:
        if not isinstance(p, str) or not p.strip() or len(p) > MAX_PATH_CHARS:
            raise HTTPException(status_code=400, detail=f"Invalid path: {p!r}")
        out.append(_norm_path(p))
    return out


def make_game_permissions_router(get_current_user, db, user_public) -> APIRouter:
    router = APIRouter(prefix="/games", tags=["collaboration"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.game_collaborators.create_index([("game_id", 1), ("user_id", 1)], unique=True)
            await db.game_collaborators.create_index("user_id")
            await db.game_testers.create_index([("game_id", 1), ("user_id", 1)], unique=True)
            await db.game_testers.create_index("user_id")
        except Exception:  # noqa: BLE001
            log.warning("could not create game_collaborators/game_testers indexes", exc_info=True)
        indexes_ready = True

    async def get_owned_game(game_id: str, current: dict) -> dict:
        g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
        if not g:
            raise HTTPException(status_code=404, detail="Game not found")
        if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
            raise HTTPException(status_code=403, detail="Only the Game Owner can manage access")
        return g

    def _collab_public(doc: dict, user_info: dict) -> dict:
        return {
            **user_info,
            "level": doc.get("level", "view"),
            "can_create_files": doc.get("can_create_files", False),
            "can_edit_files": doc.get("can_edit_files", False),
            "can_delete_files": doc.get("can_delete_files", False),
            "can_delete_folders": doc.get("can_delete_folders", False),
            "can_change_settings": doc.get("can_change_settings", False),
            "can_publish": doc.get("can_publish", False),
            "allowed_paths": doc.get("allowed_paths") or [],
            "denied_paths": doc.get("denied_paths") or [],
            "granted_at": doc.get("granted_at"),
        }

    @router.get("/{game_id}/my-access")
    async def my_access(game_id: str, current=Depends(get_current_user)):
        """Ce poate afisa frontend-ul (Code Editor, Delete, Publish...) - dar raspunsul e
        mereu re-verificat in backend la fiecare actiune reala, nu doar aici (item 15)."""
        g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
        if not g:
            raise HTTPException(status_code=404, detail="Game not found")
        access = await get_game_access(db, g, current)
        return {
            "role": access.role,
            "can_view_code": access.can_view_code,
            "can_create_files": access.can_create_files,
            "can_edit_files": access.can_edit_files,
            "can_delete_files": access.can_delete_files,
            "can_delete_folders": access.can_delete_folders,
            "can_change_settings": access.can_change_settings,
            "can_publish": access.can_publish,
            "can_play": access.can_play,
            "allowed_paths": access.allowed_paths,
            "denied_paths": access.denied_paths,
        }

    @router.post("/{game_id}/collaborators")
    async def grant_collaborator(game_id: str, body: GrantCollaboratorBody, current=Depends(get_current_user)):
        g = await get_owned_game(game_id, current)
        await ensure_indexes()
        if body.target_user_id == g["owner_id"]:
            raise HTTPException(status_code=400, detail="Owner already has full access")
        target = await db.users.find_one({"user_id": body.target_user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        preset = dict(LEVEL_PRESETS[body.level])
        for flag in ("can_create_files", "can_edit_files", "can_delete_files",
                     "can_delete_folders", "can_change_settings", "can_publish"):
            v = getattr(body, flag)
            if v is not None:
                preset[flag] = v

        doc = {
            "game_id": game_id, "user_id": body.target_user_id,
            "granted_by": current["user_id"], "granted_at": now_utc(),
            "level": body.level,
            "allowed_paths": _clean_paths(body.allowed_paths),
            "denied_paths": _clean_paths(body.denied_paths),
            **preset,
        }
        await db.game_collaborators.update_one(
            {"game_id": game_id, "user_id": body.target_user_id}, {"$set": doc}, upsert=True,
        )
        return {"collaborator": _collab_public(doc, user_public(target))}

    @router.get("/{game_id}/collaborators")
    async def list_collaborators(game_id: str, current=Depends(get_current_user)):
        await get_owned_game(game_id, current)
        rows = await db.game_collaborators.find({"game_id": game_id}, {"_id": 0}).to_list(500)
        if not rows:
            return {"collaborators": []}
        users = await db.users.find(
            {"user_id": {"$in": [r["user_id"] for r in rows]}}, {"_id": 0, "password_hash": 0}
        ).to_list(500)
        by_id = {u["user_id"]: user_public(u) for u in users}
        out = [_collab_public(r, by_id[r["user_id"]]) for r in rows if r["user_id"] in by_id]
        return {"collaborators": out}

    @router.patch("/{game_id}/collaborators/{user_id}")
    async def update_collaborator(game_id: str, user_id: str, body: UpdateCollaboratorBody, current=Depends(get_current_user)):
        await get_owned_game(game_id, current)
        existing = await db.game_collaborators.find_one({"game_id": game_id, "user_id": user_id}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Collaborator not found")
        updates: dict = {}
        if body.level:
            updates.update(LEVEL_PRESETS[body.level])
            updates["level"] = body.level
        for flag in ("can_create_files", "can_edit_files", "can_delete_files",
                     "can_delete_folders", "can_change_settings", "can_publish"):
            v = getattr(body, flag)
            if v is not None:
                updates[flag] = v
        if body.clear_paths:
            updates["allowed_paths"] = []
            updates["denied_paths"] = []
        else:
            if body.allowed_paths is not None:
                updates["allowed_paths"] = _clean_paths(body.allowed_paths)
            if body.denied_paths is not None:
                updates["denied_paths"] = _clean_paths(body.denied_paths)
        if updates:
            await db.game_collaborators.update_one({"game_id": game_id, "user_id": user_id}, {"$set": updates})
        doc = await db.game_collaborators.find_one({"game_id": game_id, "user_id": user_id}, {"_id": 0})
        target = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
        return {"collaborator": _collab_public(doc, user_public(target))}

    @router.delete("/{game_id}/collaborators/{user_id}")
    async def revoke_collaborator(game_id: str, user_id: str, current=Depends(get_current_user)):
        await get_owned_game(game_id, current)
        await db.game_collaborators.delete_one({"game_id": game_id, "user_id": user_id})
        return {"ok": True}

    @router.post("/{game_id}/testers")
    async def grant_tester(game_id: str, body: GrantTesterBody, current=Depends(get_current_user)):
        g = await get_owned_game(game_id, current)
        await ensure_indexes()
        if body.target_user_id == g["owner_id"]:
            raise HTTPException(status_code=400, detail="Owner already has full access")
        target = await db.users.find_one({"user_id": body.target_user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        await db.game_testers.update_one(
            {"game_id": game_id, "user_id": body.target_user_id},
            {"$setOnInsert": {"game_id": game_id, "user_id": body.target_user_id,
                               "granted_by": current["user_id"], "granted_at": now_utc()}},
            upsert=True,
        )
        return {"ok": True, "tester": user_public(target)}

    @router.get("/{game_id}/testers")
    async def list_testers(game_id: str, current=Depends(get_current_user)):
        await get_owned_game(game_id, current)
        rows = await db.game_testers.find({"game_id": game_id}, {"_id": 0}).to_list(500)
        if not rows:
            return {"testers": []}
        users = await db.users.find(
            {"user_id": {"$in": [r["user_id"] for r in rows]}}, {"_id": 0, "password_hash": 0}
        ).to_list(500)
        by_id = {u["user_id"]: user_public(u) for u in users}
        out = [{**by_id[r["user_id"]], "granted_at": r["granted_at"]} for r in rows if r["user_id"] in by_id]
        return {"testers": out}

    @router.delete("/{game_id}/testers/{user_id}")
    async def revoke_tester(game_id: str, user_id: str, current=Depends(get_current_user)):
        await get_owned_game(game_id, current)
        await db.game_testers.delete_one({"game_id": game_id, "user_id": user_id})
        return {"ok": True}

    return router