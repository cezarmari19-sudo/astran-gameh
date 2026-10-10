"""Groups: sistem de grupuri (Owner/Admin/Member), similar conceptual cu Roblox Groups.

Se ataseaza din server.py, ca toate celelalte module optionale:
    api.include_router(make_group_router(get_current_user, db, _user_public))

Colectiile Mongo:
    groups
        {group_id, owner_id, name, description, logo_url, token, chat_mode,
         created_at, updated_at}
        - owner_id are index UNIC: fiecare cont poate detine MAXIM UN Group.
        - token (~35 caractere alfanumerice, generat random) e privat - doar owner-ul
          grupului il poate vedea/copia (vezi /groups/{id}/token). Jocurile se asociaza
          cu Group-ul DOAR prin acest token, niciodata prin group_id (public).

    group_members
        {group_id, user_id, role, status, muted, joined_at}
        - role: "owner" | "admin" | "member"
        - status: "active" | "banned" - persistent; un user banned nu poate reintra
          (vezi join_group), indiferent cate ori incearca.
        - "owner" primeste automat un rand aici cu role="owner" la crearea grupului.

    group_chat_messages
        {message_id, group_id, user_id, username, text, created_at}
        - Mesajele Group Chat-ului; cine POATE trimite e decis de chat_mode + rolul din
          group_members + muted (vezi can_speak). Simplu REST (poll), ca restul API-ului
          (nu exista websockets nicaieri in platforma).

Game <-> Group:
    Games tine un camp `group_id` (nullable), setat in server.py DOAR dupa ce tokenul
    privat a fost validat impotriva colectiei `groups` de aici - vezi server.py
    create_game/update_game. Acest modul nu stie nimic despre jocuri (fara dependinta
    circulara), doar expune colectia `groups` pe care server.py o interogheaza direct.
"""
from __future__ import annotations

import logging
import secrets
import string
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("astran.groups")

GROUP_CREATE_COST = 200
GROUP_NAME_MIN = 2
GROUP_NAME_MAX = 50
GROUP_DESC_MAX = 500
TOKEN_LEN = 35
TOKEN_CHARS = string.ascii_letters + string.digits
MAX_CHAT_TEXT = 500
CHAT_PAGE_SIZE = 100

ChatMode = Literal["owner_only", "owner_admins", "everyone"]
Role = Literal["owner", "admin", "member"]
MemberStatus = Literal["active", "banned"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


async def _generate_token(db) -> str:
    """~35 caractere, litere+cifre, greu de ghicit, unic. Nu e niciodata ID-ul grupului."""
    for _ in range(25):
        token = "".join(secrets.choice(TOKEN_CHARS) for _ in range(TOKEN_LEN))
        if not await db.groups.find_one({"token": token}, {"_id": 0, "token": 1}):
            return token
    raise HTTPException(status_code=500, detail="Could not generate a unique group token")


# ---------- request bodies ----------

class CreateGroupBody(BaseModel):
    name: str = Field(min_length=GROUP_NAME_MIN, max_length=GROUP_NAME_MAX)
    description: str = Field(default="", max_length=GROUP_DESC_MAX)
    logo_url: Optional[str] = None


class UpdateGroupBody(BaseModel):
    name: Optional[str] = Field(default=None, min_length=GROUP_NAME_MIN, max_length=GROUP_NAME_MAX)
    description: Optional[str] = Field(default=None, max_length=GROUP_DESC_MAX)
    logo_url: Optional[str] = None
    chat_mode: Optional[ChatMode] = None


class ChatMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHAT_TEXT)


# ---------- response shaping ----------

def _group_public(g: dict, member_count: int) -> dict:
    """Niciodata nu include 'token' aici - vezi /groups/{id}/token (doar owner)."""
    return {
        "group_id": g["group_id"],
        "owner_id": g["owner_id"],
        "name": g["name"],
        "description": g.get("description", ""),
        "logo_url": g.get("logo_url"),
        "chat_mode": g.get("chat_mode", "everyone"),
        "member_count": member_count,
        "created_at": g["created_at"],
        "updated_at": g.get("updated_at", g["created_at"]),
    }


def make_group_router(get_current_user, db, user_public) -> APIRouter:
    router = APIRouter(prefix="/groups", tags=["groups"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.groups.create_index("group_id", unique=True)
            # Un singur Group per cont - impus la nivel de baza de date, nu doar in cod.
            await db.groups.create_index("owner_id", unique=True)
            await db.groups.create_index("token", unique=True)
            await db.group_members.create_index([("group_id", 1), ("user_id", 1)], unique=True)
            await db.group_members.create_index("user_id")
            await db.group_chat_messages.create_index([("group_id", 1), ("created_at", -1)])
        except Exception:  # noqa: BLE001
            log.warning("could not create group indexes", exc_info=True)
        indexes_ready = True

    async def get_group_or_404(group_id: str) -> dict:
        g = await db.groups.find_one({"group_id": group_id}, {"_id": 0})
        if not g:
            raise HTTPException(status_code=404, detail="Group not found")
        return g

    async def get_membership(group_id: str, user_id: str) -> Optional[dict]:
        return await db.group_members.find_one({"group_id": group_id, "user_id": user_id}, {"_id": 0})

    async def require_membership(group_id: str, user_id: str) -> dict:
        m = await get_membership(group_id, user_id)
        if not m or m["status"] != "active":
            raise HTTPException(status_code=403, detail="Not a member of this Group")
        return m

    async def require_owner(group: dict, user_id: str) -> None:
        if group["owner_id"] != user_id:
            raise HTTPException(status_code=403, detail="Only the Group Owner can do this")

    async def member_count(group_id: str) -> int:
        return await db.group_members.count_documents({"group_id": group_id, "status": "active"})

    # ---------- create / read / update group ----------

    @router.post("")
    async def create_group(body: CreateGroupBody, current=Depends(get_current_user)):
        await ensure_indexes()
        if await db.groups.find_one({"owner_id": current["user_id"]}, {"_id": 0, "group_id": 1}):
            raise HTTPException(status_code=400, detail="You already own a Group (max one per account)")

        # Taxa se incaseaza ATOMIC (verifica soldul in aceeasi operatie), inainte de create.
        debit = await db.users.update_one(
            {"user_id": current["user_id"], "astrans_balance": {"$gte": GROUP_CREATE_COST}},
            {"$inc": {"astrans_balance": -GROUP_CREATE_COST}},
        )
        if debit.modified_count == 0:
            raise HTTPException(status_code=402, detail=f"Insufficient Astrans balance (need {GROUP_CREATE_COST})")

        async def refund() -> None:
            await db.users.update_one({"user_id": current["user_id"]}, {"$inc": {"astrans_balance": GROUP_CREATE_COST}})

        token = await _generate_token(db)
        gid = new_id("group_")
        doc = {
            "group_id": gid,
            "owner_id": current["user_id"],
            "name": body.name,
            "description": body.description,
            "logo_url": body.logo_url,
            "token": token,
            "chat_mode": "everyone",
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        try:
            await db.groups.insert_one(doc)
        except Exception:  # noqa: BLE001 - race condition pe unique index owner_id/token
            await refund()
            raise HTTPException(status_code=400, detail="You already own a Group (max one per account)")

        try:
            await db.group_members.insert_one({
                "group_id": gid, "user_id": current["user_id"], "role": "owner",
                "status": "active", "muted": False, "joined_at": now_utc(),
            })
        except Exception:  # noqa: BLE001
            await db.groups.delete_one({"group_id": gid})
            await refund()
            raise HTTPException(status_code=500, detail="Could not create Group, try again")

        await db.astran_ledger.insert_one({
            "tx_id": new_id("tx_"), "user_id": current["user_id"], "counterparty_id": "system",
            "type": "group_create", "amount": GROUP_CREATE_COST, "fee": 0, "net": -GROUP_CREATE_COST,
            "status": "completed", "timestamp": now_utc(), "reference": f"group:{body.name}",
        })
        return {"group": _group_public(doc, 1)}

    @router.get("/mine")
    async def my_groups(current=Depends(get_current_user)):
        """Grupul detinut (daca exista) + toate apartenentele curente (owned + joined)."""
        await ensure_indexes()
        owned = await db.groups.find_one({"owner_id": current["user_id"]}, {"_id": 0})
        memberships = await db.group_members.find(
            {"user_id": current["user_id"], "status": "active"}, {"_id": 0},
        ).to_list(200)
        group_ids = [m["group_id"] for m in memberships]
        groups_by_id = {}
        if group_ids:
            docs = await db.groups.find({"group_id": {"$in": group_ids}}, {"_id": 0}).to_list(200)
            groups_by_id = {d["group_id"]: d for d in docs}
        membership_out = [
            {
                "group_id": m["group_id"],
                "name": groups_by_id[m["group_id"]]["name"],
                "logo_url": groups_by_id[m["group_id"]].get("logo_url"),
                "role": m["role"],
            }
            for m in memberships if m["group_id"] in groups_by_id
        ]
        owned_out = None
        if owned:
            owned_out = _group_public(owned, await member_count(owned["group_id"]))
        return {"owned_group": owned_out, "memberships": membership_out}

    @router.get("/{group_id}")
    async def get_group(group_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        out = _group_public(g, await member_count(group_id))
        mine = await get_membership(group_id, current["user_id"])
        out["my_membership"] = mine
        return {"group": out}

    @router.patch("/{group_id}")
    async def update_group(group_id: str, body: UpdateGroupBody, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        updates = {k: v for k, v in body.dict().items() if v is not None}
        if updates:
            updates["updated_at"] = now_utc()
            await db.groups.update_one({"group_id": group_id}, {"$set": updates})
        g2 = await get_group_or_404(group_id)
        return {"group": _group_public(g2, await member_count(group_id))}

    @router.get("/{group_id}/token")
    async def get_token(group_id: str, current=Depends(get_current_user)):
        """Doar OWNER-ul vede/copiaza tokenul privat (asociere joc <-> Group)."""
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        return {"token": g["token"]}

    @router.post("/{group_id}/token/regenerate")
    async def regenerate_token(group_id: str, current=Depends(get_current_user)):
        """Daca tokenul s-a scurs undeva, owner-ul poate genera unul nou (jocurile deja
        asociate raman asociate - asocierea foloseste group_id intern, nu tokenul)."""
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        token = await _generate_token(db)
        await db.groups.update_one({"group_id": group_id}, {"$set": {"token": token, "updated_at": now_utc()}})
        return {"token": token}

    # ---------- members ----------

    @router.get("/{group_id}/members")
    async def list_members(group_id: str, current=Depends(get_current_user)):
        await get_group_or_404(group_id)
        rows = await db.group_members.find(
            {"group_id": group_id, "status": "active"}, {"_id": 0},
        ).sort("joined_at", 1).to_list(1000)
        user_ids = [r["user_id"] for r in rows]
        users = await db.users.find({"user_id": {"$in": user_ids}}, {"_id": 0, "password_hash": 0}).to_list(1000)
        users_by_id = {u["user_id"]: user_public(u) for u in users}
        out = []
        for r in rows:
            u = users_by_id.get(r["user_id"])
            if not u:
                continue
            out.append({**u, "role": r["role"], "muted": r.get("muted", False), "joined_at": r["joined_at"]})
        return {"members": out}

    @router.post("/{group_id}/join")
    async def join_group(group_id: str, current=Depends(get_current_user)):
        await get_group_or_404(group_id)
        existing = await get_membership(group_id, current["user_id"])
        if existing:
            if existing["status"] == "banned":
                raise HTTPException(status_code=403, detail="You are banned from this Group")
            raise HTTPException(status_code=409, detail="Already a member")
        await db.group_members.insert_one({
            "group_id": group_id, "user_id": current["user_id"], "role": "member",
            "status": "active", "muted": False, "joined_at": now_utc(),
        })
        return {"ok": True}

    @router.post("/{group_id}/leave")
    async def leave_group(group_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        if g["owner_id"] == current["user_id"]:
            raise HTTPException(status_code=400, detail="The Owner cannot leave their own Group")
        # Un membru BANAT nu are voie sa-si stearga singur randul de membership prin /leave -
        # asta ar sterge si interdictia (ban_member doar marcheaza status="banned", nu sterge
        # randul), lasandu-l liber sa intre din nou cu /join imediat dupa. Doar un membru activ
        # poate pleca de bunavoie; un ban se ridica exclusiv de un owner/admin (unban_member).
        existing = await get_membership(group_id, current["user_id"])
        if existing and existing["status"] == "banned":
            raise HTTPException(status_code=403, detail="You are banned from this Group")
        await db.group_members.delete_one({"group_id": group_id, "user_id": current["user_id"]})
        return {"ok": True}

    @router.post("/{group_id}/members/{user_id}/promote")
    async def promote_member(group_id: str, user_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        m = await get_membership(group_id, user_id)
        if not m or m["status"] != "active":
            raise HTTPException(status_code=404, detail="Member not found")
        if m["role"] == "owner":
            raise HTTPException(status_code=400, detail="Owner role cannot change")
        await db.group_members.update_one({"group_id": group_id, "user_id": user_id}, {"$set": {"role": "admin"}})
        return {"ok": True}

    @router.post("/{group_id}/members/{user_id}/demote")
    async def demote_member(group_id: str, user_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        m = await get_membership(group_id, user_id)
        if not m or m["status"] != "active" or m["role"] != "admin":
            raise HTTPException(status_code=400, detail="User is not an Admin of this Group")
        await db.group_members.update_one({"group_id": group_id, "user_id": user_id}, {"$set": {"role": "member"}})
        return {"ok": True}

    @router.post("/{group_id}/members/{user_id}/mute")
    async def mute_member(group_id: str, user_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        actor = await require_membership(group_id, current["user_id"])
        if actor["role"] not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Only Owner or Admins can mute")
        target = await get_membership(group_id, user_id)
        if not target or target["status"] != "active":
            raise HTTPException(status_code=404, detail="Member not found")
        if target["role"] == "owner":
            raise HTTPException(status_code=403, detail="Cannot mute the Owner")
        # Adminii nu pot modifica permisiunile Owner-ului; un Admin NU poate muta alt Admin -
        # doar Owner-ul poate muta Admini (Adminii gestioneaza doar Membri).
        if actor["role"] == "admin" and target["role"] == "admin":
            raise HTTPException(status_code=403, detail="Admins cannot mute other Admins")
        await db.group_members.update_one({"group_id": group_id, "user_id": user_id}, {"$set": {"muted": True}})
        return {"ok": True}

    @router.post("/{group_id}/members/{user_id}/unmute")
    async def unmute_member(group_id: str, user_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        actor = await require_membership(group_id, current["user_id"])
        if actor["role"] not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Only Owner or Admins can unmute")
        target = await get_membership(group_id, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Member not found")
        if actor["role"] == "admin" and target["role"] in ("admin", "owner"):
            raise HTTPException(status_code=403, detail="Admins cannot unmute Admins or the Owner")
        await db.group_members.update_one({"group_id": group_id, "user_id": user_id}, {"$set": {"muted": False}})
        return {"ok": True}

    @router.post("/{group_id}/members/{user_id}/ban")
    async def ban_member(group_id: str, user_id: str, current=Depends(get_current_user)):
        """Doar OWNER-ul poate bloca permanent un membru (Adminii nu pot)."""
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        if user_id == current["user_id"]:
            raise HTTPException(status_code=400, detail="Owner cannot ban themselves")
        target = await get_membership(group_id, user_id)
        if not target or target["status"] != "active":
            raise HTTPException(status_code=404, detail="Member not found")
        await db.group_members.update_one(
            {"group_id": group_id, "user_id": user_id},
            {"$set": {"status": "banned", "role": "member", "muted": False, "banned_at": now_utc()}},
        )
        return {"ok": True}

    @router.post("/{group_id}/members/{user_id}/unban")
    async def unban_member(group_id: str, user_id: str, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        await require_owner(g, current["user_id"])
        target = await get_membership(group_id, user_id)
        if not target or target["status"] != "banned":
            raise HTTPException(status_code=404, detail="No banned record for this user")
        await db.group_members.delete_one({"group_id": group_id, "user_id": user_id})
        return {"ok": True}

    # ---------- group chat (persistent, permisiuni verificate mereu in backend) ----------

    async def can_speak(group: dict, membership: Optional[dict]) -> bool:
        if not membership or membership["status"] != "active":
            return False
        if membership.get("muted"):
            return False
        mode = group.get("chat_mode", "everyone")
        if mode == "everyone":
            return True
        if mode == "owner_admins":
            return membership["role"] in ("owner", "admin")
        return membership["role"] == "owner"  # owner_only

    @router.get("/{group_id}/chat/messages")
    async def list_chat(group_id: str, current=Depends(get_current_user)):
        await get_group_or_404(group_id)
        await require_membership(group_id, current["user_id"])
        cursor = db.group_chat_messages.find({"group_id": group_id}, {"_id": 0}).sort("created_at", -1).limit(CHAT_PAGE_SIZE)
        msgs = await cursor.to_list(CHAT_PAGE_SIZE)
        msgs.reverse()
        return {"messages": msgs}

    @router.post("/{group_id}/chat/messages")
    async def send_chat(group_id: str, body: ChatMessageBody, current=Depends(get_current_user)):
        g = await get_group_or_404(group_id)
        m = await get_membership(group_id, current["user_id"])
        if not await can_speak(g, m):
            raise HTTPException(status_code=403, detail="You are not allowed to speak in this Group right now")
        doc = {
            "message_id": new_id("gmsg_"), "group_id": group_id, "user_id": current["user_id"],
            "username": current["username"], "text": body.text, "created_at": now_utc(),
        }
        await db.group_chat_messages.insert_one(doc)
        return {"message": {k: v for k, v in doc.items() if k != "_id"}}

    return router