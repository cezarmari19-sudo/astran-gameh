"""Clothes Shop: magazin separat, exclusiv pentru haine si accesorii de Avatar Editor.

Complet independent de Shop-ul de obiecte (shop_routes.py): colectie proprie
(`clothes_items`), rute proprii (/clothes/...), propriile cumparaturi
(`clothes_purchases`). Un copac facut in Studio si publicat in Shop-ul de obiecte
NU apare aici, si invers - cele doua magazine nu se ating.

Sursa modelului e aceeasi (un model salvat in Studio, din studio_routes.py):
utilizatorul construieste piesele o singura data, apoi alege in care magazin
il publica (obiecte de joc, sau haine de avatar).

Se ataseaza din server.py:
    api.include_router(make_clothes_router(get_current_user, db))

Colectia Mongo `clothes_items`:
    {item_id, owner_id, owner_username, name, description, price, is_public,
     slot (obligatoriu - una din SLOTS), thumbnail_url, downloads,
     model: {parts: [...]}, part_count, object: {...} (preview),
     source_model_id, created_at, updated_at}

Colectia Mongo `clothes_purchases`: {user_id, item_id} unic.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .studio_routes import shape_count, summary_object, validate_parts

log = logging.getLogger("astran.clothes")

PLATFORM_FEE_PERCENT = 5
MAX_NAME_CHARS = 48
MAX_DESC_CHARS = 500
MAX_PRICE = 100000
MAX_THUMB_CHARS = 300000

# Categoriile din Avatar Editor. Extensibil: adauga o linie aici SI in SLOT_DEFS
# din frontend/src/avatar/avatarTypes.ts (cheile trebuie sa fie identice).
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def split_price(price: int) -> tuple[int, int]:
    fee = (price * PLATFORM_FEE_PERCENT) // 100
    return fee, price - fee


def _clean_thumbnail(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) > MAX_THUMB_CHARS:
        raise HTTPException(status_code=400, detail="Thumbnail is too large")
    if not value.startswith("data:image/") or ";base64," not in value[:64]:
        raise HTTPException(status_code=400, detail="Thumbnail must be an image")
    return value


def _clean_slot(value: str) -> str:
    if value not in SLOTS:
        raise HTTPException(status_code=400, detail=f"Invalid slot '{value}'")
    return value


class PublishClothesBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=64)  # modelul salvat in Studio care se publica
    name: str = Field(min_length=2, max_length=MAX_NAME_CHARS)
    description: str = Field(default="", max_length=MAX_DESC_CHARS)
    price: int = Field(default=0, ge=0, le=MAX_PRICE)
    is_public: bool = True
    thumbnail_url: Optional[str] = Field(default=None, max_length=MAX_THUMB_CHARS + 100)
    slot: str = Field(min_length=1, max_length=32)  # obligatoriu - una din categoriile Avatar Editor


class UpdateClothesBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=MAX_NAME_CHARS)
    description: Optional[str] = Field(default=None, max_length=MAX_DESC_CHARS)
    price: Optional[int] = Field(default=None, ge=0, le=MAX_PRICE)
    is_public: Optional[bool] = None
    thumbnail_url: Optional[str] = Field(default=None, max_length=MAX_THUMB_CHARS + 100)  # "" = sterge poza
    model_id: Optional[str] = Field(default=None, max_length=64)  # re-sincronizeaza continutul din Studio
    slot: Optional[str] = Field(default=None, max_length=32)


def _preview(doc: dict) -> dict:
    preview = dict(doc.get("object") or {})
    if doc.get("part_count"):
        preview["part_count"] = doc["part_count"]
    return preview


def _public_item(doc: dict, owned: bool) -> dict:
    return {
        "item_id": doc["item_id"],
        "owner_id": doc["owner_id"],
        "owner_username": doc["owner_username"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "price": doc["price"],
        "is_public": doc["is_public"],
        "slot": doc["slot"],
        "downloads": doc.get("downloads", 0),
        "created_at": doc["created_at"],
        "thumbnail_url": doc.get("thumbnail_url"),
        "owned": owned,
        "preview": _preview(doc),
    }


def _owner_content(doc: dict) -> dict:
    out: dict = {}
    if "object" in doc:
        out["object"] = doc["object"]
    model = doc.get("model")
    if isinstance(model, dict) and isinstance(model.get("parts"), list):
        out["parts"] = model["parts"]
    return out


def make_clothes_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/clothes", tags=["clothes"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.clothes_items.create_index("item_id", unique=True)
            await db.clothes_items.create_index([("is_public", 1), ("slot", 1), ("downloads", -1)])
            await db.clothes_items.create_index([("owner_id", 1), ("created_at", -1)])
            await db.clothes_items.create_index([("name", "text"), ("description", "text")])
            await db.clothes_purchases.create_index([("user_id", 1), ("item_id", 1)], unique=True)
        except Exception:  # noqa: BLE001
            log.warning("could not create clothes indexes", exc_info=True)
        indexes_ready = True

    async def get_item(item_id: str) -> dict:
        it = await db.clothes_items.find_one({"item_id": item_id}, {"_id": 0})
        if not it:
            raise HTTPException(status_code=404, detail="Item not found")
        return it

    async def owns(user_id: str, item: dict) -> bool:
        if item["owner_id"] == user_id:
            return True
        if item["price"] == 0 and item["is_public"]:
            return True
        return await db.clothes_purchases.find_one({"user_id": user_id, "item_id": item["item_id"]}, {"_id": 0}) is not None

    def visible(item: dict, current: dict) -> bool:
        if item["is_public"]:
            return True
        return item["owner_id"] == current["user_id"] or bool(current.get("is_platform_admin"))

    async def snapshot_from_studio(model_id: str, current: dict) -> dict:
        doc = await db.studio_models.find_one({"model_id": model_id, "owner_id": current["user_id"]}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Model not found in your Studio")
        try:
            parts = validate_parts(doc.get("parts"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        count = shape_count(parts)
        if count == 0:
            raise HTTPException(status_code=400, detail="The model is empty - add objects in Studio first")
        return {
            "model": {"parts": parts},
            "part_count": count,
            "object": summary_object(parts),
            "source_model_id": model_id,
        }

    @router.get("/slots")
    async def list_slots():
        return {"slots": [{"key": k, "label": v} for k, v in SLOTS.items()]}

    @router.get("/items")
    async def list_items(q: Optional[str] = None, mine: bool = False, slot: Optional[str] = None, current=Depends(get_current_user)):
        await ensure_indexes()
        query: dict = {}
        if mine:
            query["owner_id"] = current["user_id"]
        else:
            query["is_public"] = True
        if slot:
            query["slot"] = _clean_slot(slot)
        if q:
            query["$text"] = {"$search": q}
        cursor = db.clothes_items.find(query, {"_id": 0}).sort(
            [("score", {"$meta": "textScore"})] if q else [("downloads", -1), ("created_at", -1)]
        ).limit(50)
        docs = await cursor.to_list(50)
        out = []
        for d in docs:
            is_owned = await owns(current["user_id"], d)
            out.append(_public_item(d, is_owned))
        return {"items": out}

    @router.post("/items")
    async def publish_item(body: PublishClothesBody, current=Depends(get_current_user)):
        await ensure_indexes()
        thumb = _clean_thumbnail(body.thumbnail_url)
        slot = _clean_slot(body.slot)
        extra = await snapshot_from_studio(body.model_id, current)
        if thumb:
            extra["thumbnail_url"] = thumb
        doc = {
            "item_id": new_id("cloth_"),
            "owner_id": current["user_id"],
            "owner_username": current["username"],
            "name": body.name,
            "description": body.description,
            "price": body.price,
            "is_public": body.is_public,
            "slot": slot,
            "downloads": 0,
            "created_at": now_utc(),
            "updated_at": now_utc(),
            **extra,
        }
        await db.clothes_items.insert_one(doc)
        return {"item": _public_item(doc, True)}

    @router.get("/items/{item_id}")
    async def get_item_detail(item_id: str, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if not visible(it, current):
            raise HTTPException(status_code=403, detail="This item is private")
        is_owned = await owns(current["user_id"], it)
        result = _public_item(it, is_owned)
        if is_owned:
            result.update(_owner_content(it))
        return {"item": result}

    @router.patch("/items/{item_id}")
    async def update_item(item_id: str, body: UpdateClothesBody, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if it["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
            raise HTTPException(status_code=403, detail="Not the owner")

        updates: dict = {}
        if body.name is not None:
            updates["name"] = body.name
        if body.description is not None:
            updates["description"] = body.description
        if body.price is not None:
            updates["price"] = body.price
        if body.is_public is not None:
            updates["is_public"] = body.is_public
        if body.thumbnail_url is not None:
            updates["thumbnail_url"] = _clean_thumbnail(body.thumbnail_url)
        if body.slot is not None:
            updates["slot"] = _clean_slot(body.slot)
        if body.model_id is not None:
            updates.update(await snapshot_from_studio(body.model_id, current))

        if updates:
            updates["updated_at"] = now_utc()
            await db.clothes_items.update_one({"item_id": item_id}, {"$set": updates})
        it2 = await get_item(item_id)
        return {"item": _public_item(it2, True) | _owner_content(it2)}

    @router.delete("/items/{item_id}")
    async def delete_item(item_id: str, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if it["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
            raise HTTPException(status_code=403, detail="Not the owner")
        await db.clothes_items.delete_one({"item_id": item_id})
        return {"ok": True}

    @router.post("/items/{item_id}/buy")
    async def buy_item(item_id: str, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if not visible(it, current):
            raise HTTPException(status_code=403, detail="This item is private")
        if it["owner_id"] == current["user_id"]:
            raise HTTPException(status_code=400, detail="You already own this item")

        already = await db.clothes_purchases.find_one({"user_id": current["user_id"], "item_id": item_id}, {"_id": 0})
        if already:
            return {"ok": True, "already_owned": True}

        price = it["price"]
        if price > 0:
            fee, author_share = split_price(price)
            debit = await db.users.update_one(
                {"user_id": current["user_id"], "astrans_balance": {"$gte": price}},
                {"$inc": {"astrans_balance": -price}},
            )
            if debit.modified_count == 0:
                raise HTTPException(status_code=402, detail="Insufficient Astrans balance")

            await db.users.update_one({"user_id": it["owner_id"]}, {"$inc": {"astrans_balance": author_share}})

            tx_buyer = {
                "tx_id": new_id("tx_"), "user_id": current["user_id"], "counterparty_id": it["owner_id"],
                "type": "clothes_purchase", "amount": price, "fee": fee, "net": price,
                "status": "completed", "timestamp": now_utc(),
                "reference": f"clothes:{it['name']}",
            }
            tx_seller = {
                "tx_id": new_id("tx_"), "user_id": it["owner_id"], "counterparty_id": current["user_id"],
                "type": "clothes_sale", "amount": author_share, "fee": fee, "net": author_share,
                "status": "completed", "timestamp": now_utc(),
                "reference": f"clothes:{it['name']}",
            }
            await db.astran_ledger.insert_one(tx_buyer)
            await db.astran_ledger.insert_one(tx_seller)

        try:
            await db.clothes_purchases.insert_one({"user_id": current["user_id"], "item_id": item_id, "purchased_at": now_utc()})
        except Exception:
            pass
        await db.clothes_items.update_one({"item_id": item_id}, {"$inc": {"downloads": 1}})

        return {"ok": True, "already_owned": False}

    @router.get("/mine/purchases")
    async def my_purchases(current=Depends(get_current_user)):
        await ensure_indexes()
        cursor = db.clothes_purchases.find({"user_id": current["user_id"]}, {"_id": 0}).sort("purchased_at", -1).limit(200)
        purchases = await cursor.to_list(200)
        item_ids = [p["item_id"] for p in purchases]
        if not item_ids:
            return {"items": []}
        docs = await db.clothes_items.find({"item_id": {"$in": item_ids}}, {"_id": 0}).to_list(200)
        idx = {d["item_id"]: d for d in docs}
        return {"items": [_public_item(idx[i], True) for i in item_ids if i in idx]}

    return router