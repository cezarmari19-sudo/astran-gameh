"""Avatar Editor: corpul si sloturile echipate ale utilizatorului (persistent, ca in Roblox).

Se ataseaza din server.py:
    api.include_router(make_avatar_router(get_current_user, db))

Colectia Mongo `user_avatars`:
    {user_id, body: {height, width, proportions, head_size, skin_color, body_shape},
     equipped: {slot: item_id | None, ...}, updated_at}

Un item se poate echipa intr-un slot doar daca:
- e de tip "model" in shop_items (deci vine din Studio), SI
- are un camp "slot" valid (setat la publicare in shop_routes.py), SI
- utilizatorul il detine (proprietar, gratis+public, sau cumparat - vezi shop_routes.owns)

Sloturile sunt extensibile: SLOTS de mai jos e singura lista care trebuie extinsa
cand se adauga o categorie noua de item-uri.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("astran.avatar")

# Categoriile din Avatar Editor. Cheia = "slot" stocat pe shop_items; eticheta = ce vede userul.
# Pentru a adauga o categorie noua: adauga o linie aici (backend) si in SLOT_DEFS din avatarTypes.ts (frontend).
SLOTS: dict[str, str] = {
    "hair": "Păr",
    "shirt": "Tricou",
    "pants": "Pantaloni",
    "shoes": "Încălțăminte",
    "hat": "Pălărie",
    "accessory": "Accesoriu",
    "face": "Față",
    "back": "Accesoriu spate",
    "effect": "Efect",
}

BODY_SHAPES = ("standard", "slim", "broad")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(v: float, lo: float, hi: float, default: float) -> float:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return default
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return round(max(lo, min(hi, v)), 4)


def default_body() -> dict:
    return {
        "height": 1.0,
        "width": 1.0,
        "proportions": 1.0,  # raport trunchi/picioare, 0.7..1.3
        "head_size": 1.0,
        "skin_color": "#E8B48C",
        "body_shape": "standard",
    }


def clean_body(raw: Optional[dict]) -> dict:
    raw = raw or {}
    d = default_body()
    color = raw.get("skin_color")
    import re
    hexre = re.compile(r"^#[0-9a-fA-F]{6}$")
    return {
        "height": _clamp(raw.get("height"), 0.7, 1.4, d["height"]),
        "width": _clamp(raw.get("width"), 0.7, 1.4, d["width"]),
        "proportions": _clamp(raw.get("proportions"), 0.7, 1.3, d["proportions"]),
        "head_size": _clamp(raw.get("head_size"), 0.7, 1.4, d["head_size"]),
        "skin_color": color.lower() if isinstance(color, str) and hexre.match(color) else d["skin_color"],
        "body_shape": raw.get("body_shape") if raw.get("body_shape") in BODY_SHAPES else d["body_shape"],
    }


def default_avatar(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "body": default_body(),
        "equipped": {slot: None for slot in SLOTS},
        "updated_at": now_utc(),
    }


class SaveAvatarBody(BaseModel):
    body: dict = Field(default_factory=dict)
    equipped: dict[str, Optional[str]] = Field(default_factory=dict)


def make_avatar_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/avatar", tags=["avatar"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.user_avatars.create_index("user_id", unique=True)
        except Exception:  # noqa: BLE001
            log.warning("could not create avatar indexes", exc_info=True)
        indexes_ready = True

    async def owns_item(user_id: str, item: dict) -> bool:
        """Aceeasi logica ca in shop_routes.owns, dar avem nevoie de ea si aici."""
        if item["owner_id"] == user_id:
            return True
        if item["price"] == 0 and item["is_public"]:
            return True
        return await db.shop_purchases.find_one({"user_id": user_id, "item_id": item["item_id"]}, {"_id": 0}) is not None

    async def owned_model_items(user_id: str) -> list[dict]:
        """Toate item-urile de tip 'model' cu slot valid pe care userul le poate echipa: proprii + cumparate."""
        purchases = await db.shop_purchases.find({"user_id": user_id}, {"_id": 0, "item_id": 1}).to_list(1000)
        purchased_ids = [p["item_id"] for p in purchases]
        cursor = db.shop_items.find(
            {
                "kind": "model",
                "slot": {"$in": list(SLOTS.keys())},
                "$or": [
                    {"owner_id": user_id},
                    {"item_id": {"$in": purchased_ids}},
                    {"price": 0, "is_public": True},
                ],
            },
            {"_id": 0},
        )
        return await cursor.to_list(1000)

    def _inventory_item(doc: dict) -> dict:
        preview = dict(doc.get("object") or {})
        if doc.get("part_count"):
            preview["part_count"] = doc["part_count"]
        return {
            "item_id": doc["item_id"],
            "name": doc["name"],
            "slot": doc.get("slot"),
            "thumbnail_url": doc.get("thumbnail_url"),
            "preview": preview,
            "owner_username": doc["owner_username"],
        }

    @router.get("/slots")
    async def list_slots():
        return {"slots": [{"key": k, "label": v} for k, v in SLOTS.items()]}

    @router.get("/inventory")
    async def get_inventory(current=Depends(get_current_user)):
        docs = await owned_model_items(current["user_id"])
        return {"items": [_inventory_item(d) for d in docs]}

    @router.get("/me")
    async def get_my_avatar(current=Depends(get_current_user)):
        await ensure_indexes()
        doc = await db.user_avatars.find_one({"user_id": current["user_id"]}, {"_id": 0})
        if not doc:
            doc = default_avatar(current["user_id"])
            await db.user_avatars.insert_one(doc)
        return {"avatar": doc}

    @router.get("/user/{user_id}")
    async def get_user_avatar(user_id: str, current=Depends(get_current_user)):
        """Avatarul echipat al oricarui utilizator - de folosit in jocuri si profiluri."""
        doc = await db.user_avatars.find_one({"user_id": user_id}, {"_id": 0})
        if not doc:
            doc = default_avatar(user_id)
        # Alaturam si continutul (piesele) fiecarui item echipat, ca clientul sa poata reda direct avatarul.
        equipped = doc.get("equipped") or {}
        item_ids = [v for v in equipped.values() if v]
        parts_by_item: dict[str, dict] = {}
        if item_ids:
            docs = await db.shop_items.find({"item_id": {"$in": item_ids}}, {"_id": 0}).to_list(len(item_ids))
            for d in docs:
                model = d.get("model") or {}
                if isinstance(model.get("parts"), list):
                    parts_by_item[d["item_id"]] = {"item_id": d["item_id"], "name": d["name"], "parts": model["parts"]}
        return {"avatar": doc, "equipped_content": parts_by_item}

    @router.put("/me")
    async def save_my_avatar(body: SaveAvatarBody, current=Depends(get_current_user)):
        await ensure_indexes()
        clean = clean_body(body.body)

        # validam echiparea: doar sloturi cunoscute, doar item-uri detinute si compatibile cu slotul
        owned = await owned_model_items(current["user_id"])
        owned_by_id = {d["item_id"]: d for d in owned}

        equipped_in: dict = body.equipped or {}
        equipped_out: dict[str, Optional[str]] = {slot: None for slot in SLOTS}
        for slot, item_id in equipped_in.items():
            if slot not in SLOTS:
                continue
            if not item_id:
                equipped_out[slot] = None
                continue
            item = owned_by_id.get(item_id)
            if not item:
                raise HTTPException(status_code=403, detail=f"You don't own item {item_id}")
            if item.get("slot") != slot:
                raise HTTPException(status_code=400, detail=f"Item {item_id} is not a '{slot}' item")
            equipped_out[slot] = item_id

        updates = {"body": clean, "equipped": equipped_out, "updated_at": now_utc()}
        await db.user_avatars.update_one(
            {"user_id": current["user_id"]},
            {"$set": updates, "$setOnInsert": {"user_id": current["user_id"]}},
            upsert=True,
        )
        doc = await db.user_avatars.find_one({"user_id": current["user_id"]}, {"_id": 0})
        return {"avatar": doc}

    return router