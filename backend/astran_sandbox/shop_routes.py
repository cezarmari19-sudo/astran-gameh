"""Magazinul Astran: Modele (obiecte 3D facute in Studio, fara cod) si Scripturi (Luau).

Se ataseaza din server.py:
    api.include_router(make_shop_router(get_current_user, db))
(acelasi apel atasaza si rutele Studio: /studio/models)

Colectii Mongo folosite:
- shop_items: {item_id, kind ("model"|"script"), owner_id, owner_username, name,
    description, price, is_public, created_at, updated_at, downloads, thumbnail_url,
    (model) model: {parts: [...]}, part_count, source_model_id,
            object: {type, color, scale}  -- aproximare pentru Assets.load si listele vechi
    (script) files: [{name, source}]}
- shop_purchases: {user_id, item_id} unic — cine a cumparat ce (Astrans nu se cer de doua ori)

Un model se publica doar dintr-un model salvat in Studio (model_id).
Comisionul platformei e 5%: la un pret de 100 Astrans, autorul primeste 95.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .studio_routes import make_studio_router, shape_count, summary_object, validate_parts

log = logging.getLogger("astran.shop")

PLATFORM_FEE_PERCENT = 5
MAX_NAME_CHARS = 48
MAX_DESC_CHARS = 500
MAX_PRICE = 100000
MAX_THUMB_CHARS = 300000


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def split_price(price: int) -> tuple[int, int]:
    """Intoarce (comision_platforma, venit_autor) pentru un pret dat."""
    fee = (price * PLATFORM_FEE_PERCENT) // 100
    return fee, price - fee


def _clean_thumbnail(value: Optional[str]) -> Optional[str]:
    """Imaginea de coperta: doar poze trimise ca data URL (base64), cu limita de marime."""
    if not value:
        return None
    if len(value) > MAX_THUMB_CHARS:
        raise HTTPException(status_code=400, detail="Thumbnail is too large")
    if not value.startswith("data:image/") or ";base64," not in value[:64]:
        raise HTTPException(status_code=400, detail="Thumbnail must be an image")
    return value


class ScriptFileBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    source: str = Field(default="", max_length=20000)


class PublishModelBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=64)  # modelul salvat in Studio care se publica
    name: str = Field(min_length=2, max_length=MAX_NAME_CHARS)
    description: str = Field(default="", max_length=MAX_DESC_CHARS)
    price: int = Field(default=0, ge=0, le=MAX_PRICE)
    is_public: bool = True
    thumbnail_url: Optional[str] = Field(default=None, max_length=MAX_THUMB_CHARS + 100)


class PublishScriptBody(BaseModel):
    name: str = Field(min_length=2, max_length=MAX_NAME_CHARS)
    description: str = Field(default="", max_length=MAX_DESC_CHARS)
    price: int = Field(default=0, ge=0, le=MAX_PRICE)
    is_public: bool = True
    files: List[ScriptFileBody] = Field(min_length=1, max_length=32)


class UpdateItemBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=MAX_NAME_CHARS)
    description: Optional[str] = Field(default=None, max_length=MAX_DESC_CHARS)
    price: Optional[int] = Field(default=None, ge=0, le=MAX_PRICE)
    is_public: Optional[bool] = None
    thumbnail_url: Optional[str] = Field(default=None, max_length=MAX_THUMB_CHARS + 100)  # "" = sterge poza
    model_id: Optional[str] = Field(default=None, max_length=64)  # re-sincronizeaza continutul din Studio
    files: Optional[List[ScriptFileBody]] = Field(default=None, min_length=1, max_length=32)


def _model_preview(doc: dict) -> dict:
    preview = dict(doc.get("object") or {})
    if doc.get("part_count"):
        preview["part_count"] = doc["part_count"]
    return preview


def _public_item(doc: dict, owned: bool) -> dict:
    """Ce vede oricine in lista/cautare. Continutul (files/parts) se da doar la detaliu, daca e gratis/detinut."""
    return {
        "item_id": doc["item_id"],
        "kind": doc["kind"],
        "owner_id": doc["owner_id"],
        "owner_username": doc["owner_username"],
        "name": doc["name"],
        "description": doc.get("description", ""),
        "price": doc["price"],
        "is_public": doc["is_public"],
        "downloads": doc.get("downloads", 0),
        "created_at": doc["created_at"],
        "thumbnail_url": doc.get("thumbnail_url"),
        "owned": owned,
        "preview": (_model_preview(doc) if doc["kind"] == "model" else {"file_count": len(doc.get("files", []))}),
    }


def _owner_content(doc: dict) -> dict:
    """Continutul real al itemului, pentru cei care il detin."""
    out: dict = {}
    if "object" in doc:
        out["object"] = doc["object"]
    if "files" in doc:
        out["files"] = doc["files"]
    model = doc.get("model")
    if isinstance(model, dict) and isinstance(model.get("parts"), list):
        out["parts"] = model["parts"]
    return out


def make_shop_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/shop", tags=["shop"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.shop_items.create_index("item_id", unique=True)
            await db.shop_items.create_index([("kind", 1), ("is_public", 1), ("downloads", -1)])
            await db.shop_items.create_index([("owner_id", 1), ("created_at", -1)])
            await db.shop_items.create_index([("name", "text"), ("description", "text")])
            await db.shop_purchases.create_index([("user_id", 1), ("item_id", 1)], unique=True)
        except Exception:  # noqa: BLE001
            log.warning("could not create shop indexes", exc_info=True)
        indexes_ready = True

    async def get_item(item_id: str) -> dict:
        it = await db.shop_items.find_one({"item_id": item_id}, {"_id": 0})
        if not it:
            raise HTTPException(status_code=404, detail="Item not found")
        return it

    async def owns(user_id: str, item: dict) -> bool:
        """Detine efectiv continutul: autorul, oricine daca e gratis SI public, sau cine l-a cumparat."""
        if item["owner_id"] == user_id:
            return True
        if item["price"] == 0 and item["is_public"]:
            return True
        return await db.shop_purchases.find_one({"user_id": user_id, "item_id": item["item_id"]}, {"_id": 0}) is not None

    def visible(item: dict, current: dict) -> bool:
        """Poate vedea ca itemul exista (lista/cautare/detaliu), indiferent daca ii detine continutul."""
        if item["is_public"]:
            return True
        return item["owner_id"] == current["user_id"] or bool(current.get("is_platform_admin"))

    async def snapshot_from_studio(model_id: str, current: dict) -> dict:
        """Ia modelul salvat in Studio (doar al utilizatorului) si intoarce campurile de pus in Magazin."""
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

    async def list_or_search(kind: str, q: Optional[str], mine: bool, current: dict) -> list[dict]:
        await ensure_indexes()
        query: dict = {"kind": kind}
        if mine:
            query["owner_id"] = current["user_id"]
        else:
            query["is_public"] = True
        if q:
            query["$text"] = {"$search": q}
        cursor = db.shop_items.find(query, {"_id": 0}).sort(
            [("score", {"$meta": "textScore"})] if q else [("downloads", -1), ("created_at", -1)]
        ).limit(50)
        docs = await cursor.to_list(50)
        out = []
        for d in docs:
            is_owned = await owns(current["user_id"], d)
            out.append(_public_item(d, is_owned))
        return out

    async def publish(kind: str, owner_id: str, owner_username: str, name: str, description: str, price: int, is_public: bool, extra: dict) -> dict:
        await ensure_indexes()
        doc = {
            "item_id": new_id("shop_"),
            "kind": kind,
            "owner_id": owner_id,
            "owner_username": owner_username,
            "name": name,
            "description": description,
            "price": price,
            "is_public": is_public,
            "downloads": 0,
            "created_at": now_utc(),
            "updated_at": now_utc(),
            **extra,
        }
        await db.shop_items.insert_one(doc)
        return doc

    # ---------- Modele ----------

    @router.get("/models")
    async def list_models(q: Optional[str] = None, mine: bool = False, current=Depends(get_current_user)):
        return {"items": await list_or_search("model", q, mine, current)}

    @router.post("/models")
    async def publish_model(body: PublishModelBody, current=Depends(get_current_user)):
        thumb = _clean_thumbnail(body.thumbnail_url)
        extra = await snapshot_from_studio(body.model_id, current)
        if thumb:
            extra["thumbnail_url"] = thumb
        doc = await publish(
            "model", current["user_id"], current["username"], body.name, body.description,
            body.price, body.is_public, extra,
        )
        return {"item": _public_item(doc, True)}

    # ---------- Scripturi ----------

    @router.get("/scripts")
    async def list_scripts(q: Optional[str] = None, mine: bool = False, current=Depends(get_current_user)):
        return {"items": await list_or_search("script", q, mine, current)}

    @router.post("/scripts")
    async def publish_script(body: PublishScriptBody, current=Depends(get_current_user)):
        names = [f.name for f in body.files]
        if len(set(names)) != len(names):
            raise HTTPException(status_code=400, detail="Duplicate script names")
        if "main" not in names:
            raise HTTPException(status_code=400, detail="Missing the 'main' script (it runs first)")
        doc = await publish(
            "script", current["user_id"], current["username"], body.name, body.description,
            body.price, body.is_public, {"files": [f.dict() for f in body.files]},
        )
        return {"item": _public_item(doc, True)}

    # ---------- Comun: detaliu, editare, stergere, cumparare ----------

    @router.get("/items/{item_id}")
    async def get_item_detail(item_id: str, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if not visible(it, current):
            raise HTTPException(status_code=403, detail="This item is private")
        is_owned = await owns(current["user_id"], it)
        result = _public_item(it, is_owned)
        if is_owned:
            # continutul real (piesele sau fisierele) se da doar celor care detin itemul
            result.update(_owner_content(it))
        return {"item": result}

    @router.patch("/items/{item_id}")
    async def update_item(item_id: str, body: UpdateItemBody, current=Depends(get_current_user)):
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
        if body.model_id is not None:
            if it["kind"] != "model":
                raise HTTPException(status_code=400, detail="This item is not a model")
            updates.update(await snapshot_from_studio(body.model_id, current))
        if body.files is not None:
            if it["kind"] != "script":
                raise HTTPException(status_code=400, detail="This item is not a script")
            names = [f.name for f in body.files]
            if len(set(names)) != len(names):
                raise HTTPException(status_code=400, detail="Duplicate script names")
            if "main" not in names:
                raise HTTPException(status_code=400, detail="Missing the 'main' script (it runs first)")
            updates["files"] = [f.dict() for f in body.files]

        if updates:
            updates["updated_at"] = now_utc()
            await db.shop_items.update_one({"item_id": item_id}, {"$set": updates})
        it2 = await get_item(item_id)
        return {"item": _public_item(it2, True) | _owner_content(it2)}

    @router.delete("/items/{item_id}")
    async def delete_item(item_id: str, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if it["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
            raise HTTPException(status_code=403, detail="Not the owner")
        await db.shop_items.delete_one({"item_id": item_id})
        return {"ok": True}

    @router.post("/items/{item_id}/buy")
    async def buy_item(item_id: str, current=Depends(get_current_user)):
        it = await get_item(item_id)
        if not visible(it, current):
            raise HTTPException(status_code=403, detail="This item is private")
        if it["owner_id"] == current["user_id"]:
            raise HTTPException(status_code=400, detail="You already own this item")

        already = await db.shop_purchases.find_one({"user_id": current["user_id"], "item_id": item_id}, {"_id": 0})
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
                "type": "shop_purchase", "amount": price, "fee": fee, "net": price,
                "status": "completed", "timestamp": now_utc(),
                "reference": f"shop:{it['kind']}:{it['name']}",
            }
            tx_seller = {
                "tx_id": new_id("tx_"), "user_id": it["owner_id"], "counterparty_id": current["user_id"],
                "type": "shop_sale", "amount": author_share, "fee": fee, "net": author_share,
                "status": "completed", "timestamp": now_utc(),
                "reference": f"shop:{it['kind']}:{it['name']}",
            }
            await db.astran_ledger.insert_one(tx_buyer)
            await db.astran_ledger.insert_one(tx_seller)

        try:
            await db.shop_purchases.insert_one({"user_id": current["user_id"], "item_id": item_id, "purchased_at": now_utc()})
        except Exception:
            pass  # cumparat deja intre timp (dublu-click) — nu e o eroare pentru cumparator
        await db.shop_items.update_one({"item_id": item_id}, {"$inc": {"downloads": 1}})

        return {"ok": True, "already_owned": False}

    @router.get("/mine/purchases")
    async def my_purchases(current=Depends(get_current_user)):
        await ensure_indexes()
        cursor = db.shop_purchases.find({"user_id": current["user_id"]}, {"_id": 0}).sort("purchased_at", -1).limit(200)
        purchases = await cursor.to_list(200)
        item_ids = [p["item_id"] for p in purchases]
        if not item_ids:
            return {"items": []}
        docs = await db.shop_items.find({"item_id": {"$in": item_ids}}, {"_id": 0}).to_list(200)
        idx = {d["item_id"]: d for d in docs}
        return {"items": [_public_item(idx[i], True) for i in item_ids if i in idx]}

    # Un singur router de intors catre server.py: Magazinul + Studio 3D
    outer = APIRouter()
    outer.include_router(router)
    outer.include_router(make_studio_router(get_current_user, db))
    return outer