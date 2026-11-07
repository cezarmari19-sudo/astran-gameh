"""Stocarea mesageriei private in MongoDB (Motor), pentru dm_core.DmService.

Colectii:
  dm_conversations  {conversation_id, user_a, user_b (sortati, unic per pereche), created_at,
                     last_message_id, last_message_at (ms), unread_a, unread_b}
  dm_messages       {message_id, conversation_id, sender_id, ts (ms), sender_adult, raw|None, safe}

O pereche de utilizatori are EXACT o conversatie (index unic pe (user_a, user_b), creare prin
upsert) - apasarea repetata pe acelasi prieten nu creeaza conversatii noi. O conversatie fara
niciun mesaj nu apare in lista (last_message_id = None).

Nu se salveaza nimic in afara de ce cere functionarea chatului: ids, text (brut doar pentru
expeditori 18+, plus varianta filtrata), timp, contor de necitite.
"""
from __future__ import annotations

from typing import Any, Optional

try:  # pymongo e dependenta existenta a proiectului (requirements.txt)
    from pymongo.errors import DuplicateKeyError
except Exception:  # noqa: BLE001
    class DuplicateKeyError(Exception):  # type: ignore[no-redef]
        pass


async def ensure_indexes(db: Any) -> None:
    await db.dm_conversations.create_index("conversation_id", unique=True)
    await db.dm_conversations.create_index([("user_a", 1), ("user_b", 1)], unique=True)
    await db.dm_conversations.create_index([("user_a", 1), ("last_message_at", -1)])
    await db.dm_conversations.create_index([("user_b", 1), ("last_message_at", -1)])
    await db.dm_messages.create_index("message_id", unique=True)
    await db.dm_messages.create_index([("conversation_id", 1), ("ts", -1), ("message_id", -1)])


class MongoDmStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def get_or_create(self, a: str, b: str, new_conversation_id: str, now: Any) -> dict:
        key = {"user_a": a, "user_b": b}
        doc = {
            "conversation_id": new_conversation_id, "user_a": a, "user_b": b, "created_at": now,
            "last_message_id": None, "last_message_at": None, "unread_a": 0, "unread_b": 0,
        }
        try:
            await self.db.dm_conversations.update_one(key, {"$setOnInsert": doc}, upsert=True)
        except DuplicateKeyError:
            pass  # cursa intre doua cereri simultane: conversatia exista deja, o citim mai jos
        return await self.db.dm_conversations.find_one(key, {"_id": 0})

    async def get_conversation(self, cid: str) -> Optional[dict]:
        return await self.db.dm_conversations.find_one({"conversation_id": cid}, {"_id": 0})

    async def list_conversations(self, uid: str, limit: int) -> list[dict]:
        cur = self.db.dm_conversations.find(
            {"$or": [{"user_a": uid}, {"user_b": uid}], "last_message_id": {"$ne": None}}, {"_id": 0}
        ).sort("last_message_at", -1).limit(limit)
        return await cur.to_list(limit)

    async def unread_conversations(self, uid: str) -> list[dict]:
        q = {"$or": [{"user_a": uid, "unread_a": {"$gt": 0}}, {"user_b": uid, "unread_b": {"$gt": 0}}]}
        return await self.db.dm_conversations.find(q, {"_id": 0}).limit(500).to_list(500)

    async def insert_message(self, msg: dict) -> None:
        await self.db.dm_messages.insert_one(dict(msg))

    async def bump_conversation(self, cid: str, message_id: str, ts: int, recipient_slot: str) -> None:
        # contorul de necitite creste atomic ($inc), indiferent de ordinea mesajelor concurente
        await self.db.dm_conversations.update_one({"conversation_id": cid}, {"$inc": {"unread_" + recipient_slot: 1}})
        # "ultimul mesaj" nu poate merge inapoi in timp daca doua trimiteri se intretaie
        await self.db.dm_conversations.update_one(
            {"conversation_id": cid, "$or": [{"last_message_at": None}, {"last_message_at": {"$lte": ts}}]},
            {"$set": {"last_message_id": message_id, "last_message_at": ts}},
        )

    async def mark_read(self, cid: str, slot: str) -> None:
        await self.db.dm_conversations.update_one({"conversation_id": cid}, {"$set": {"unread_" + slot: 0}})

    async def page_messages(self, cid: str, before: Optional[tuple], limit: int) -> list[dict]:
        q: dict = {"conversation_id": cid}
        if before is not None:
            ts, mid = before
            q["$or"] = [{"ts": {"$lt": ts}}, {"ts": ts, "message_id": {"$lt": mid}}]
        cur = self.db.dm_messages.find(q, {"_id": 0}).sort([("ts", -1), ("message_id", -1)]).limit(limit)
        return await cur.to_list(limit)

    async def get_messages_by_ids(self, ids: list) -> dict:
        if not ids:
            return {}
        rows = await self.db.dm_messages.find({"message_id": {"$in": ids}}, {"_id": 0}).to_list(len(ids))
        return {r["message_id"]: r for r in rows}

    async def get_message(self, mid: str) -> Optional[dict]:
        return await self.db.dm_messages.find_one({"message_id": mid}, {"_id": 0})

    async def insert_report(self, doc: dict) -> None:
        await self.db.reports.insert_one(doc)