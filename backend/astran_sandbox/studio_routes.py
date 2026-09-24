"""Studio 3D: modelele facute de utilizator din mai multe piese (ciorne private).

Se ataseaza din shop_routes.make_shop_router (deci server.py ramane neschimbat):
    /api/studio/models ...

Colectia Mongo `studio_models`:
    {model_id, owner_id, name, parts, part_count, created_at, updated_at}

`parts` e o lista plata. Fiecare piesa are `parent` = id-ul grupului parinte.
Exista mereu o singura piesa radacina {id: "root", type: "group", parent: None};
numele ei este numele modelului.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("astran.studio")

ROOT_ID = "root"
MAX_PARTS = 300
MAX_NAME_CHARS = 48
MAX_MODELS_PER_USER = 100
SHAPES = ("cube", "sphere", "cylinder", "cone", "plane", "torus")
MATERIALS = ("matte", "glossy", "metal", "glass", "neon")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PART_ID = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def _num(value: Any, lo: float, hi: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return round(max(lo, min(hi, v)), 4)


def default_root(name: str = "Model") -> dict:
    return {
        "id": ROOT_ID, "name": name, "type": "group", "parent": None,
        "x": 0.0, "y": 0.0, "z": 0.0, "rx": 0.0, "ry": 0.0, "rz": 0.0,
        "sx": 1.0, "sy": 1.0, "sz": 1.0,
        "color": "#cccccc", "material": "matte", "opacity": 1.0, "visible": True,
    }


def validate_parts(raw: Any) -> list[dict]:
    """Verifica lista de piese si intoarce o copie curata. Ridica ValueError."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("The model has no parts")
    if len(raw) > MAX_PARTS:
        raise ValueError(f"Too many parts (max {MAX_PARTS})")

    clean: list[dict] = []
    ids: set[str] = set()
    for p in raw:
        if not isinstance(p, dict):
            raise ValueError("Invalid part")
        pid = p.get("id")
        if not isinstance(pid, str) or not _PART_ID.match(pid):
            raise ValueError("Invalid part id")
        if pid in ids:
            raise ValueError("Duplicate part id")
        ids.add(pid)

        ptype = p.get("type")
        if ptype != "group" and ptype not in SHAPES:
            raise ValueError(f"Unknown part type '{str(ptype)[:20]}'")

        name = p.get("name")
        name = name.strip()[:MAX_NAME_CHARS] if isinstance(name, str) and name.strip() else "Part"

        parent = p.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise ValueError("Invalid parent")

        color = p.get("color")
        if not (isinstance(color, str) and _HEX_COLOR.match(color)):
            color = "#cccccc"
        material = p.get("material") if p.get("material") in MATERIALS else "matte"

        clean.append({
            "id": pid, "name": name, "type": ptype, "parent": parent,
            "x": _num(p.get("x"), -1000, 1000, 0.0),
            "y": _num(p.get("y"), -1000, 1000, 0.0),
            "z": _num(p.get("z"), -1000, 1000, 0.0),
            "rx": _num(p.get("rx"), -3600, 3600, 0.0),
            "ry": _num(p.get("ry"), -3600, 3600, 0.0),
            "rz": _num(p.get("rz"), -3600, 3600, 0.0),
            "sx": _num(p.get("sx"), 0.001, 1000, 1.0),
            "sy": _num(p.get("sy"), 0.001, 1000, 1.0),
            "sz": _num(p.get("sz"), 0.001, 1000, 1.0),
            "color": color.lower(),
            "material": material,
            "opacity": _num(p.get("opacity"), 0.0, 1.0, 1.0),
            "visible": bool(p.get("visible", True)),
        })

    roots = [c for c in clean if c["parent"] is None]
    if len(roots) != 1 or roots[0]["id"] != ROOT_ID or roots[0]["type"] != "group":
        raise ValueError("The model must have exactly one root group")

    by_id = {c["id"]: c for c in clean}
    for c in clean:
        if c["parent"] is not None:
            parent = by_id.get(c["parent"])
            if parent is None or parent["type"] != "group":
                raise ValueError("Invalid parent")
        # fara cicluri: urcam pana la radacina
        seen: set[str] = set()
        cur = c
        while cur["parent"] is not None:
            if cur["id"] in seen:
                raise ValueError("Invalid hierarchy")
            seen.add(cur["id"])
            cur = by_id[cur["parent"]]
    return clean


def shape_count(parts: list[dict]) -> int:
    """Cate piese reale (fara grupuri) are modelul."""
    return sum(1 for p in parts if p["type"] != "group")


def summary_object(parts: list[dict]) -> dict:
    """Aproximare pentru codul vechi (Assets.load): cea mai mare piesa, ca un singur obiect."""
    best: Optional[dict] = None
    best_vol = -1.0
    for p in parts:
        if p["type"] == "group":
            continue
        vol = abs(p["sx"] * p["sy"] * p["sz"])
        if vol > best_vol:
            best, best_vol = p, vol
    if best is None:
        return {"type": "cube", "color": "#cccccc", "scale": 1.0}
    shape = best["type"] if best["type"] in ("cube", "sphere", "cylinder", "cone") else "cube"
    return {"type": shape, "color": best["color"].lower(), "scale": 1.0}


class CreateModelBody(BaseModel):
    parts: Optional[List[dict]] = Field(default=None, max_length=MAX_PARTS)


class SaveModelBody(BaseModel):
    parts: List[dict] = Field(min_length=1, max_length=MAX_PARTS)


def _summary(doc: dict) -> dict:
    return {
        "model_id": doc["model_id"],
        "name": doc.get("name", "Model"),
        "part_count": doc.get("part_count", 0),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def make_studio_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/studio", tags=["studio"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.studio_models.create_index("model_id", unique=True)
            await db.studio_models.create_index([("owner_id", 1), ("updated_at", -1)])
        except Exception:  # noqa: BLE001
            log.warning("could not create studio indexes", exc_info=True)
        indexes_ready = True

    async def get_own(model_id: str, current: dict) -> dict:
        doc = await db.studio_models.find_one({"model_id": model_id, "owner_id": current["user_id"]}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Model not found")
        return doc

    @router.get("/models")
    async def list_models(current=Depends(get_current_user)):
        await ensure_indexes()
        cursor = db.studio_models.find({"owner_id": current["user_id"]}, {"_id": 0, "parts": 0}).sort("updated_at", -1).limit(200)
        return {"models": [_summary(d) for d in await cursor.to_list(200)]}

    @router.post("/models")
    async def create_model(body: CreateModelBody, current=Depends(get_current_user)):
        await ensure_indexes()
        if await db.studio_models.count_documents({"owner_id": current["user_id"]}) >= MAX_MODELS_PER_USER:
            raise HTTPException(status_code=400, detail=f"Too many models (max {MAX_MODELS_PER_USER})")
        try:
            parts = validate_parts(body.parts if body.parts is not None else [default_root()])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        root = next(p for p in parts if p["id"] == ROOT_ID)
        doc = {
            "model_id": new_id("model_"),
            "owner_id": current["user_id"],
            "name": root["name"],
            "parts": parts,
            "part_count": shape_count(parts),
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        await db.studio_models.insert_one(doc)
        return {"model": {k: v for k, v in doc.items() if k != "_id"}}

    @router.get("/models/{model_id}")
    async def get_model(model_id: str, current=Depends(get_current_user)):
        return {"model": await get_own(model_id, current)}

    @router.put("/models/{model_id}")
    async def save_model(model_id: str, body: SaveModelBody, current=Depends(get_current_user)):
        await get_own(model_id, current)
        try:
            parts = validate_parts(body.parts)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        root = next(p for p in parts if p["id"] == ROOT_ID)
        updates = {"name": root["name"], "parts": parts, "part_count": shape_count(parts), "updated_at": now_utc()}
        await db.studio_models.update_one({"model_id": model_id}, {"$set": updates})
        return {"model": await get_own(model_id, current)}

    @router.delete("/models/{model_id}")
    async def delete_model(model_id: str, current=Depends(get_current_user)):
        await get_own(model_id, current)
        await db.studio_models.delete_one({"model_id": model_id})
        return {"ok": True}

    return router