"""Clothes Shop: magazin separat, exclusiv pentru haine si accesorii de Avatar Editor.

Complet independent de Shop-ul de obiecte (shop_routes.py): colectie proprie
(`clothes_items`), rute proprii (/clothes/...), propriile cumparaturi
(`clothes_purchases`). Un copac facut in Studio si publicat in Shop-ul de obiecte
NU apare aici, si invers.

Doua feluri de item, dupa `render_kind`:
- "geometry": hat, hair, accessory, back, face, effect - obiecte 3D construite in Studio,
   continut in `model: {parts: [...]}` (acelasi format ca in shop_routes.py).
- "texture": shirt, pants - o singura imagine UV (585x559, sistem clasic Roblox) aplicata
   pe geometria corpului, continut in `texture_url` (data URL PNG, generat de editorul
   de textura din aplicatie).

Un item e creat ca DRAFT (is_public=False, fara continut) prin POST /clothes/items,
apoi capata continut prin PATCH .../geometry sau PATCH .../texture (dupa render_kind),
si devine vizibil in Shop abia dupa POST .../publish. Editarea unui item existent
trece prin aceleasi rute de continut, fara sa creeze un item nou.

Se ataseaza din server.py:
    api.include_router(make_clothes_router(get_current_user, db))

Colectia Mongo `clothes_items`:
    {item_id, owner_id, owner_username, name, description, price, is_public,
     slot (una din SLOTS), render_kind ("geometry"|"texture"), thumbnail_url, downloads,
     -- geometry --
     model: {parts: [...]}, part_count, object: {...} (preview), source_model_id,
     -- texture --
     texture_url: str,
     created_at, updated_at}

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
MAX_TEXTURE_CHARS = 2_500_000  # ~1.8MB binar dupa decodare base64: suficient pentru 585x559 PNG

TEMPLATE_W = 585
TEMPLATE_H = 559

# Categoriile din Avatar Editor. Extensibil: adauga o linie aici SI in avatar_routes.SLOTS
# SI in SLOT_DEFS din avatarTypes.ts (frontend) - toate trei identice ca chei.
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

# Care sloturi folosesc textura UV (shirt/pants) vs geometrie din Studio (tot restul).
TEXTURE_SLOTS = {"shirt", "pants"}


def render_kind_for_slot(slot: str) -> str:
    return "texture" if slot in TEXTURE_SLOTS else "geometry"


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


def _clean_texture(value: str) -> str:
    if not value:
        raise HTTPException(status_code=400, detail="Texture is empty")
    if len(value) > MAX_TEXTURE_CHARS:
        raise HTTPException(status_code=400, detail="Texture is too large")
    if not value.startswith("data:image/") or ";base64," not in value[:64]:
        raise HTTPException(status_code=400, detail="Texture must be an image")
    return value


def _clean_slot(value: str) -> str:
    if value not in SLOTS:
        raise HTTPException(status_code=400, detail=f"Invalid slot '{value}'")
    return value


class CreateClothesBody(BaseModel):
    """Creeaza un draft gol intr-o categorie. Continutul se adauga separat, dupa render_kind."""
    name: str = Field(min_length=2, max_length=MAX_NAME_CHARS)
    slot: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=MAX_DESC_CHARS)


class UpdateClothesMetaBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=MAX_NAME_CHARS)
    description: Optional[str] = Field(default=None, max_length=MAX_DESC_CHARS)
    price: Optional[int] = Field(default=None, ge=0, le=MAX_PRICE)
    is_public: Optional[bool] = None
    thumbnail_url: Optional[str] = Field(default=None, max_length=MAX_THUMB_CHARS + 100)


class SetGeometryBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=64)  # modelul salvat in Studio


class SetTextureBody(BaseModel):
    texture_url: str = Field(max_length=MAX_TEXTURE_CHARS + 100)  # PNG data URL, 585x559, din TextureEditor


class PublishBody(BaseModel):
    price: int = Field(default=0, ge=0, le=MAX_PRICE)
    is_public: bool = True


def _preview(doc: dict) -> dict:
    if doc.get("render_kind") == "texture":
        return {"render_kind": "texture", "has_texture": bool(doc.get("texture_url"))}
    preview = dict(doc.get("object") or {})
    if doc.get("part_count"):
        preview["part_count"] = doc["part_count"]
    preview["render_kind"] = "geometry"
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
        "render_kind": doc["render_kind"],
        "downloads": doc.get("downloads", 0),
        "created_at": doc["created_at"],
        "thumbnail_url": doc.get("thumbnail_url"),
        "owned": owned,
        "has_content": bool(doc.get("model") or doc.get("texture_url")),
        "preview": _preview(doc),
    }


def _owner_content(doc: dict) -> dict:
    out: dict = {}
    if doc.get("render_kind") == "texture":
        if doc.get("texture_url"):
            out["texture_url"] = doc["texture_url"]
    else:
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

    async def get_own_item(item_id: str, current: dict) -> dict:
        it = await get_item(item_id)
        if it["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
            raise HTTPException(status_code=403, detail="Not the owner")
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

    # ---------- categorii ----------

    @router.get("/slots")
    async def list_slots():
        return {"slots": [{"key": k, "label": v, "render_kind": render_kind_for_slot(k)} for k, v in SLOTS.items()]}

    # ---------- listare / cautare ----------

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

    # ---------- creare (draft) ----------

    @router.post("/items")
    async def create_item(body: CreateClothesBody, current=Depends(get_current_user)):
        await ensure_indexes()
        slot = _clean_slot(body.slot)
        doc = {
            "item_id": new_id("cloth_"),
            "owner_id": current["user_id"],
            "owner_username": current["username"],
            "name": body.name,
            "description": body.description,
            "price": 0,
            "is_public": False,  # draft: nu apare in Shop pana la /publish
            "slot": slot,
            "render_kind": render_kind_for_slot(slot),
            "downloads": 0,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        await db.clothes_items.insert_one(doc)
        return {"item": _public_item(doc, True)}

    # ---------- continut ----------

    @router.patch("/items/{item_id}/geometry")
    async def set_geometry(item_id: str, body: SetGeometryBody, current=Depends(get_current_user)):
        it = await get_own_item(item_id, current)
        if it["render_kind"] != "geometry":
            raise HTTPException(status_code=400, detail=f"'{it['slot']}' items use a texture, not geometry")
        extra = await snapshot_from_studio(body.model_id, current)
        extra["updated_at"] = now_utc()
        await db.clothes_items.update_one({"item_id": item_id}, {"$set": extra})
        it2 = await get_item(item_id)
        return {"item": _public_item(it2, True) | _owner_content(it2)}

    @router.patch("/items/{item_id}/texture")
    async def set_texture(item_id: str, body: SetTextureBody, current=Depends(get_current_user)):
        it = await get_own_item(item_id, current)
        if it["render_kind"] != "texture":
            raise HTTPException(status_code=400, detail=f"'{it['slot']}' items use geometry from Studio, not a texture")
        texture = _clean_texture(body.texture_url)
        await db.clothes_items.update_one(
            {"item_id": item_id},
            {"$set": {"texture_url": texture, "updated_at": now_utc()}},
        )
        it2 = await get_item(item_id)
        return {"item": _public_item(it2, True) | _owner_content(it2)}

    # ---------- meta (nume/descriere/pret/vizibilitate/thumbnail) ----------

    @router.patch("/items/{item_id}")
    async def update_meta(item_id: str, body: UpdateClothesMetaBody, current=Depends(get_current_user)):
        await get_own_item(item_id, current)
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
        if updates:
            updates["updated_at"] = now_utc()
            await db.clothes_items.update_one({"item_id": item_id}, {"$set": updates})
        it2 = await get_item(item_id)
        return {"item": _public_item(it2, True) | _owner_content(it2)}

    # ---------- publicare ----------

    @router.post("/items/{item_id}/publish")
    async def publish_item(item_id: str, body: PublishBody, current=Depends(get_current_user)):
        it = await get_own_item(item_id, current)
        has_content = bool(it.get("model") or it.get("texture_url"))
        if not has_content:
            kind_hint = "a model from Studio" if it["render_kind"] == "geometry" else "a texture"
            raise HTTPException(status_code=400, detail=f"Add {kind_hint} before publishing")
        await db.clothes_items.update_one(
            {"item_id": item_id},
            {"$set": {"price": body.price, "is_public": body.is_public, "updated_at": now_utc()}},
        )
        it2 = await get_item(item_id)
        return {"item": _public_item(it2, True)}

    # ---------- detaliu / stergere / cumparare ----------

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

    @router.delete("/items/{item_id}")
    async def delete_item(item_id: str, current=Depends(get_current_user)):
        await get_own_item(item_id, current)
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

    @router.get("/mine/drafts")
    async def my_drafts(current=Depends(get_current_user)):
        """Itemele proprii, publicate sau nu - pentru ecranul 'itemele mele' din Create."""
        await ensure_indexes()
        cursor = db.clothes_items.find({"owner_id": current["user_id"]}, {"_id": 0}).sort("updated_at", -1).limit(200)
        docs = await cursor.to_list(200)
        return {"items": [_public_item(d, True) for d in docs]}

    return router