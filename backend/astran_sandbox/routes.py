"""Endpoint-uri FastAPI pentru sandbox-ul Luau si pentru Code Editor-ul jocului (proiect
real de fisiere: foldere/subfoldere/fisiere, adancime nelimitata).

Se ataseaza din server.py:
    api.include_router(make_sandbox_router(get_current_user, db))
(dependintele sunt primite ca argumente, ca sa nu existe import circular)

============================================================================
MODELUL DE PROIECT (foldere + fisiere, nu o lista plata)
============================================================================
Proiectul unui joc se tine in colectia `game_scripts`:
    { game_id, files: [{path, source}], folders: [path, ...], updated_at }

- `files[].path` e calea COMPLETA, cu extensie, asa cum o vede utilizatorul in File
  Explorer: "scripts/player/movement.lua", "README.md", "assets/notes.txt". Folderele
  nu sunt entitati separate cu ID propriu - sunt DERIVATE din path-urile fisierelor
  (exact ca intr-un repo Git), plus lista explicita `folders` de mai jos.
- `folders` tine path-urile de foldere care exista dar NU au (inca) niciun fisier in
  ele - altfel un folder gol ar disparea din proiect (nu poate fi reprezentat doar din
  path-urile fisierelor). Un folder care are deja un fisier in el nu trebuie listat
  aici separat (arborele complet = folders U dirname(f) pentru fiecare fisier U toti
  stramosii lor), dar nu e o eroare daca apare si acolo si aici.
- Adancimea e nelimitata ca principiu; MAX_PATH_DEPTH de mai jos e doar un plafon de
  SIGURANTA (anti-abuz), nu o limita de design - vezi cerinta "fara limita artificiala".

============================================================================
CE SE EXECUTA CU ADEVARAT: DOAR .lua / .luau
============================================================================
Sandbox-ul (runner.py -> binarul C++ luau-sandbox, vezi host.cpp) intelege NUMAI Luau,
si numele lui de modul (folosite de require() si de fisierul "main" care ruleaza primul)
NU accepta punct - e un contract fixat in binarul compilat, pe care nu il pot schimba
fara o recompilare pe server (build.sh). De-aia:
    - Un fisier de proiect cu path "scripts/player/movement.lua" devine, DOAR pentru
      sandbox, modulul Luau "scripts/player/movement" (extensia se scoate) - vezi
      luau_bundle() mai jos. E exact conventia require() din Lua, care oricum nu
      foloseste extensii.
    - Fisierul de la radacina "main.lua" e "main" pentru sandbox - deci e in
      continuare fisierul care ruleaza primul (ENTRY_NAME din runner.py ramane "main").
    - Orice alt fisier (.md, .py, .ts, .json, orice) e organizare REALA, salvata si
      editabila cu highlighting corespunzator, dar NU ajunge la sandbox - nu exista
      (inca) un motor care sa execute Python/JS/Kotlin/C++/C# aici, si nu pretindem
      ca ar exista. Arhitectura permite adaugarea altor "motoare" per extensie mai
      tarziu, fara sa schimbe modelul de fisiere de mai sus.

Jocurile vechi (game_scripts cu {name, source} fara extensie, sau game["script"] ca
string simplu) se migreaza automat LA CITIRE in noul model (vezi _migrate_legacy_files),
nimic manual necesar; la urmatorul Save se rescriu deja in formatul nou.

============================================================================
PERMISIUNI (Game Collaboration / Tester) - vezi game_permissions.py
============================================================================
Accesul NU mai e doar "owner sau nimic": un Editor poate avea acces total sau restrans
pe anumite foldere/fisiere (allowed_paths/denied_paths), cu permisiuni granulare
(create/edit/delete fisiere, delete foldere). Un Tester NU ajunge niciodata aici - vede
doar Play Mode (server.py), niciodata Code Editor-ul.

Pentru un Editor cu acces restrans, Save NU inlocuieste tot proiectul (ca la owner) -
se schimba DOAR domeniul lui, restul ramane neatins, chiar daca el nu l-a trimis in
payload (altfel ar putea sterge fisiere din afara domeniului lui doar omitandu-le).
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, List, Optional

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

try:
    from .game_permissions import get_game_access, path_allowed, filter_project_to_scope
except Exception:  # noqa: BLE001 - modulul de permisiuni e optional, ca restul modulelor sandbox
    get_game_access = None
    path_allowed = None
    filter_project_to_scope = None

log = logging.getLogger("astran.sandbox")

RUNS_PER_MINUTE = 30

# ---------- limitele proiectului (Code Editor / File Explorer) ----------
# Astea sunt limitele intregului proiect (toate tipurile de fisiere la un loc), mult
# mai generoase decat MAX_FILES/MAX_TOTAL_CHARS din runner.py, care raman neschimbate
# si se aplica DOAR subsetului de fisiere .lua/.luau trimis catre sandbox (vezi
# luau_bundle() + validate_files() mai jos, apelat explicit la fiecare Save).
MAX_PROJECT_FILES = 300
MAX_PROJECT_FOLDERS = 300
MAX_PATH_CHARS = 240
MAX_PATH_SEGMENT_CHARS = 64
MAX_PATH_DEPTH = 32  # plafon de siguranta anti-abuz, NU o limita practica de design
MAX_FILE_CHARS = MAX_SOURCE_CHARS  # un fisier de proiect poate fi la fel de mare ca un script Luau
MAX_PROJECT_TOTAL_CHARS = 400000  # tot proiectul la un loc (independent de limita stricta doar-Luau)

_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,%d}$" % MAX_PATH_SEGMENT_CHARS)

# Extensiile care chiar ajung in sandbox-ul Luau (vezi luau_bundle()). Orice alta
# extensie e continut de proiect pur organizational/editabil, nu executabil.
LUAU_EXTENSIONS = (".lua", ".luau")


@dataclass
class _FallbackAccess:
    """Folosit DOAR daca game_permissions.py nu s-a putut incarca deloc - pastreaza
    exact comportamentul dinainte de Faza 2 (doar owner-ul are acces de editare)."""
    role: str = "none"
    can_view_code: bool = False
    can_create_files: bool = False
    can_edit_files: bool = False
    can_delete_files: bool = False
    can_delete_folders: bool = False
    can_change_settings: bool = False
    can_publish: bool = False
    can_play: bool = False
    allowed_paths: Optional[list] = None
    denied_paths: list = dc_field(default_factory=list)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


# ---------- validarea unui path (fisier sau folder) ----------

def _validate_path(path: Any, kind: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError(f"Invalid {kind} path")
    if len(path) > MAX_PATH_CHARS:
        raise ValueError(f"Path too long: {path[:60]}...")
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise ValueError(f"Invalid path: {path}")
    segments = path.split("/")
    if len(segments) > MAX_PATH_DEPTH:
        raise ValueError(f"Path too deep (max {MAX_PATH_DEPTH} levels): {path}")
    for seg in segments:
        if seg in (".", ".."):
            raise ValueError(f"Invalid path segment in: {path}")
        if not _PATH_SEGMENT_RE.match(seg):
            raise ValueError(f"Invalid characters in path segment: {seg!r}")
    return path


def path_depth(path: str) -> int:
    return path.count("/") + 1


def parent_folders(path: str) -> list[str]:
    """Toti stramosii unui path (fara path-ul insusi): "a/b/c.lua" -> ["a", "a/b"]."""
    parts = path.split("/")[:-1]
    out = []
    for i in range(1, len(parts) + 1):
        out.append("/".join(parts[:i]))
    return out


def validate_project(files: list[dict], folders: list[Any]) -> tuple[list[dict], list[str]]:
    """Valideaza intreg arborele de proiect. Ridica ValueError cu un mesaj clar la
    prima problema gasita (path invalid, duplicat, fisier/folder in conflict, etc)."""
    if not isinstance(files, list):
        raise ValueError("files must be a list")
    if not isinstance(folders, list):
        raise ValueError("folders must be a list")
    if len(files) > MAX_PROJECT_FILES:
        raise ValueError(f"Too many files (max {MAX_PROJECT_FILES})")
    if len(folders) > MAX_PROJECT_FOLDERS:
        raise ValueError(f"Too many folders (max {MAX_PROJECT_FOLDERS})")

    clean_files: list[dict] = []
    seen_paths: set[str] = set()
    total = 0
    for f in files:
        if not isinstance(f, dict):
            raise ValueError("Invalid file entry")
        path = _validate_path(f.get("path"), "file")
        source = f.get("source", "")
        if not isinstance(source, str):
            raise ValueError(f"Invalid source for file: {path}")
        if len(source) > MAX_FILE_CHARS:
            raise ValueError(f"File '{path}' is too long (max {MAX_FILE_CHARS} characters)")
        if path in seen_paths:
            raise ValueError(f"Duplicate path: {path}")
        seen_paths.add(path)
        total += len(source)
        clean_files.append({"path": path, "source": source})
    if total > MAX_PROJECT_TOTAL_CHARS:
        raise ValueError(f"Project is too large together (max {MAX_PROJECT_TOTAL_CHARS} characters)")

    clean_folders: list[str] = []
    seen_folders: set[str] = set()
    for raw in folders:
        path = _validate_path(raw, "folder")
        if path in seen_folders:
            continue
        seen_folders.add(path)
        clean_folders.append(path)

    # un path nu poate fi in acelasi timp fisier SI folder explicit
    conflict = seen_folders & seen_paths
    if conflict:
        raise ValueError(f"'{sorted(conflict)[0]}' is both a file and a folder")

    # un fisier nu poate "contine" alt fisier (a.lua nu poate fi parinte pentru a.lua/b.lua) -
    # se poate intampla doar daca cineva trimite manual un path malformat catre API.
    all_paths = sorted(seen_paths)
    for i, p in enumerate(all_paths):
        prefix = p + "/"
        for other in all_paths[i + 1:]:
            if other.startswith(prefix):
                raise ValueError(f"'{p}' cannot contain other files")
            if not other.startswith(p[:1]):
                break  # sortat alfabetic: nimic de dupa mai poate incepe cu acelasi prefix

    return clean_files, clean_folders


# ---------- fisiere de proiect <-> module Luau pentru sandbox ----------

def is_luau_path(path: str) -> bool:
    return path.endswith(LUAU_EXTENSIONS)


def luau_name_for(path: str) -> str:
    """Path de proiect -> nume de modul Luau (fara extensie) - vezi nota din docstring-ul
    fisierului despre de ce host.cpp/runner.py nu pot accepta puncte in nume."""
    for ext in LUAU_EXTENSIONS:
        if path.endswith(ext):
            return path[: -len(ext)]
    return path


def luau_bundle(files: list[dict]) -> list[dict]:
    """Din tot proiectul, extrage DOAR fisierele .lua/.luau ca listă {name, source} -
    exact formatul pe care runner.py/validate_files/run_files il asteapta neschimbat.
    Restul fisierelor (orice alta extensie) sunt organizare pura, nu ajung aici."""
    return [{"name": luau_name_for(f["path"]), "source": f["source"]} for f in files if is_luau_path(f["path"])]


def _migrate_legacy_files(raw: list[dict]) -> list[dict]:
    """Jocuri salvate inainte de File Explorer-ul real: {name, source}, nume fara
    extensie si fara foldere. Devin {path, source} cu extensia .lua adaugata, la
    radacina proiectului - comportament identic cu inainte (toate rulau ca Luau)."""
    out = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        if "path" in f and isinstance(f.get("path"), str):
            out.append({"path": f["path"], "source": f.get("source", "") if isinstance(f.get("source"), str) else ""})
            continue
        name = f.get("name")
        if not isinstance(name, str):
            continue
        last_segment = name.split("/")[-1]
        path = name if "." in last_segment else f"{name}.lua"
        out.append({"path": path, "source": f.get("source", "") if isinstance(f.get("source"), str) else ""})
    return out


# ---------- corpuri de request ----------

class ProjectFileBody(BaseModel):
    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    source: str = Field(default="", max_length=MAX_FILE_CHARS)


class SaveProjectBody(BaseModel):
    files: List[ProjectFileBody] = Field(default_factory=list)
    folders: List[str] = Field(default_factory=list, max_length=MAX_PROJECT_FOLDERS)


MAX_GAME_ASSETS = 100


class RunBody(BaseModel):
    # files: intregul proiect curent din editor (nu doar .lua) - filtram noi partea
    # executabila; source: compatibilitate veche (un singur script = "main").
    files: Optional[List[ProjectFileBody]] = None
    source: Optional[str] = Field(default=None, max_length=MAX_SOURCE_CHARS)
    asset_ids: Optional[List[str]] = None  # id-uri de modele din Shop, pentru testare cu Assets.load in editor


class SaveAssetsBody(BaseModel):
    asset_ids: List[str] = Field(default_factory=list, max_length=MAX_GAME_ASSETS)


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

    async def get_access(g: dict, current: dict):
        """Punct unic de acces, folosit de toate endpoint-urile de mai jos. Daca modulul
        de permisiuni n-a putut fi incarcat, se pastreaza exact comportamentul dinainte
        de Faza 2 (doar owner-ul editeaza; jocul ramane jucabil daca e public)."""
        if get_game_access is not None:
            return await get_game_access(db, g, current)
        if g["owner_id"] == current["user_id"] or current.get("is_platform_admin"):
            return _FallbackAccess(
                role="owner", can_view_code=True, can_create_files=True, can_edit_files=True,
                can_delete_files=True, can_delete_folders=True, can_change_settings=True,
                can_publish=True, can_play=True,
            )
        return _FallbackAccess(role="none", can_play=bool(g.get("is_public")))

    async def load_project(game_id: str, g: dict) -> tuple[list[dict], list[str]]:
        """Intoarce (files, folders) in formatul nou {path, source} / [path...],
        migrand automat orice format vechi gasit in baza de date (vezi docstring)."""
        doc = await db.game_scripts.find_one({"game_id": game_id}, {"_id": 0})
        if doc is not None and isinstance(doc.get("files"), list):
            files = _migrate_legacy_files(doc["files"])
            folders_raw = doc.get("folders")
            folders = [f for f in folders_raw if isinstance(f, str)] if isinstance(folders_raw, list) else []
            return files, folders
        legacy = g.get("script") or ""
        if isinstance(legacy, str) and legacy.strip():
            return [{"path": "main.lua", "source": legacy}], []
        return [], []

    @router.post("/run")
    async def run_source(body: RunBody, current=Depends(get_current_user)):
        """Testeaza scripturile din editor (proprietarul vede output-ul si erorile).

        Primeste intregul proiect curent (body.files) si ruleaza doar subsetul .lua/.luau
        - restul fisierelor (README, alte limbaje) sunt ignorate, nu produc erori.
        Nu foloseste scena unui joc anume (editorul nu ruleaza inca in contextul
        unui joc salvat), deci workspace nu contine obiecte din Studio aici.
        """
        check_rate_limit(current["user_id"])
        try:
            if body.files is not None:
                project_files = [{"path": f.path, "source": f.source} for f in body.files]
                files = luau_bundle(project_files)
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
        """Proiectul (sau partea din el la care are acces) pentru un joc: fisiere (cu
        path si continut) si folderele goale explicite. Owner-ul vede tot; un Editor
        restrans vede DOAR domeniul lui (allowed_paths/denied_paths); un Tester nu
        ajunge aici deloc (can_view_code=False)."""
        g = await get_game(game_id)
        access = await get_access(g, current)
        if not access.can_view_code:
            raise HTTPException(status_code=403, detail="No access to this project's code")
        files, folders = await load_project(game_id, g)
        if filter_project_to_scope is not None:
            files, folders = filter_project_to_scope(files, folders, access)
        return {"files": files, "folders": folders}

    @router.put("/games/{game_id}/files")
    async def save_files(game_id: str, body: SaveProjectBody, current=Depends(get_current_user)):
        """Salveaza proiectul.

        Owner (sau platform admin): comportament NESCHIMBAT - inlocuieste toata
        structura (fisiere + foldere), ca inainte de Faza 2.

        Editor cu acces (eventual restrans pe foldere): se schimba DOAR domeniul lui.
        Restul proiectului ramane EXACT neschimbat, chiar daca el nu l-a trimis deloc
        in payload - altfel un Editor fara DELETE pe /server/ ar putea sterge /server/
        doar omitandu-l din ce trimite. Fiecare schimbare (fisier nou/modificat/sters,
        folder sters) e verificata impotriva permisiunilor lui GRANULARE - orice
        incalcare respinge TOT request-ul (atomic), niciodata o salvare partiala.

        Doua validari distincte, cu scopuri diferite, raman neschimbate:
        1. validate_project: structura arborelui in sine (path-uri valide, fara duplicate,
           fara conflicte fisier/folder, in limitele generale ale proiectului).
        2. validate_files (runner.py, NESCHIMBATA): doar subsetul .lua/.luau, cu EXACT
           regulile pe care sandbox-ul le impune.
        """
        g = await get_game(game_id)
        access = await get_access(g, current)
        if not access.can_view_code:
            raise HTTPException(status_code=403, detail="No access to this project's code")

        submitted_files = [f.dict() for f in body.files]
        submitted_folders = list(body.folders)

        if access.is_owner:
            try:
                files, folders = validate_project(submitted_files, submitted_folders)
                validate_files(luau_bundle(files))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        else:
            if path_allowed is None:
                raise HTTPException(status_code=403, detail="No permission to edit this project")

            old_files, old_folders = await load_project(game_id, g)

            for f in submitted_files:
                if not path_allowed(f["path"], access):
                    raise HTTPException(status_code=403, detail=f"No access to path: {f['path']}")
            for fld in submitted_folders:
                if not path_allowed(fld, access):
                    raise HTTPException(status_code=403, detail=f"No access to path: {fld}")

            old_in_scope = {f["path"]: f["source"] for f in old_files if path_allowed(f["path"], access)}
            new_in_scope = {f["path"]: f["source"] for f in submitted_files}
            old_folders_in_scope = {fo for fo in old_folders if path_allowed(fo, access)}
            new_folders_in_scope = set(submitted_folders)

            added = [p for p in new_in_scope if p not in old_in_scope]
            removed = [p for p in old_in_scope if p not in new_in_scope]
            changed = [p for p in new_in_scope if p in old_in_scope and new_in_scope[p] != old_in_scope[p]]
            added_folders = [fo for fo in new_folders_in_scope if fo not in old_folders_in_scope]
            removed_folders = [fo for fo in old_folders_in_scope if fo not in new_folders_in_scope]

            if added and not access.can_create_files:
                raise HTTPException(status_code=403, detail="No permission to create files")
            if changed and not access.can_edit_files:
                raise HTTPException(status_code=403, detail="No permission to edit files")
            if removed and not access.can_delete_files:
                raise HTTPException(status_code=403, detail="No permission to delete files")
            if added_folders and not access.can_create_files:
                raise HTTPException(status_code=403, detail="No permission to create folders")
            if removed_folders and not access.can_delete_folders:
                raise HTTPException(status_code=403, detail="No permission to delete folders")

            # Merge: tot ce e in afara domeniului editorului ramane exact ca inainte.
            merged_files = [f for f in old_files if not path_allowed(f["path"], access)] + submitted_files
            merged_folders = list({*[fo for fo in old_folders if not path_allowed(fo, access)], *submitted_folders})

            try:
                files, folders = validate_project(merged_files, merged_folders)
                validate_files(luau_bundle(files))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        await ensure_index()
        await db.game_scripts.update_one(
            {"game_id": game_id},
            {"$set": {"game_id": game_id, "files": files, "folders": folders, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        out_files, out_folders = (files, folders)
        if not access.is_owner and filter_project_to_scope is not None:
            out_files, out_folders = filter_project_to_scope(files, folders, access)
        return {"ok": True, "files": out_files, "folders": out_folders}

    @router.get("/games/{game_id}/assets")
    async def get_game_assets(game_id: str, current=Depends(get_current_user)):
        """Id-urile modelelor din Shop atasate unui joc (owner sau Editor cu acces)."""
        g = await get_game(game_id)
        access = await get_access(g, current)
        if not access.can_view_code:
            raise HTTPException(status_code=403, detail="No access to this project")
        await ensure_index()
        doc = await db.game_assets.find_one({"game_id": game_id}, {"_id": 0})
        asset_ids = doc["asset_ids"] if doc and isinstance(doc.get("asset_ids"), list) else []
        resolved = await resolve_assets(g["owner_id"], asset_ids)
        # pastram si id-urile care nu s-au putut rezolva (ex: itemul a fost sters), ca sa le poata scoate din lista
        resolved_ids = {a["id"] for a in resolved}
        missing = [aid for aid in asset_ids if aid not in resolved_ids]
        return {"asset_ids": asset_ids, "assets": resolved, "missing_ids": missing}

    @router.put("/games/{game_id}/assets")
    async def save_game_assets(game_id: str, body: SaveAssetsBody, current=Depends(get_current_user)):
        """Salveaza lista de modele din Shop atasate unui joc (inlocuieste lista veche) -
        necesita can_edit_files SAU can_change_settings (owner le are mereu pe amandoua)."""
        g = await get_game(game_id)
        access = await get_access(g, current)
        if not (access.can_edit_files or access.can_change_settings):
            raise HTTPException(status_code=403, detail="No permission to change attached assets")
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
        """Ruleaza partea Luau a proiectului unui joc si intoarce operatiile (create/set/
        destroy/world/material) pe care le va reda ecranul de Play. Obiectele din scena
        Studio a jocului sunt puse la dispozitia scriptului ca piese deja existente in
        workspace (vezi runner.scene_file), ca proprietarul sa poata scrie, de exemplu,
        workspace.Cube1.Material = m1.

        Poate fi apelat de oricine are voie sa INTRE in joc (owner, Editor, Tester, sau
        oricine daca jocul e public) — comportamentul jocului trebuie sa fie acelasi
        pentru toti jucatorii, ca in Play Mode obisnuit. Verificarea reala de acces la
        JOIN se face in server.py (/games/{id}/play); aici e un al doilea strat de
        siguranta (item 15 - nu te bazezi doar pe un singur punct de control).
        """
        check_rate_limit(current["user_id"])
        g = await get_game(game_id)
        access = await get_access(g, current)
        if not access.can_play:
            raise HTTPException(status_code=403, detail="No access to play this game")
        try:
            project_files, _folders = await load_project(game_id, g)
            files = luau_bundle(project_files)
            if not files:
                return {"ok": True, "output": [], "errors": [], "ops": [], "duration": 0.0, "truncated": False}

            asset_doc = await db.game_assets.find_one({"game_id": game_id}, {"_id": 0})
            asset_ids = asset_doc["asset_ids"] if asset_doc and isinstance(asset_doc.get("asset_ids"), list) else []
            # Assets.load ruleaza in numele proprietarului jocului (nu al jucatorului curent):
            # proprietarul e cel care a atasat modelele jocului, deci accesul se verifica pe el.
            assets = await resolve_assets(g["owner_id"], asset_ids)

            scene = g.get("scene")
            return await run_cached_files(files, assets=assets, scene=scene)
        except Exception as exc:  # noqa: BLE001
            raise sandbox_error(exc)

    return router