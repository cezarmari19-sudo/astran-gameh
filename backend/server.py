"""
Astran Game Backend - Phase 1 MVP
Cross-platform 3D UGC gaming platform.
"""
from __future__ import annotations

import os
import uuid
import logging
import secrets
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

import bcrypt
import jwt
import httpx
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Header, Request
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "astran_game")
JWT_SECRET = os.environ.get("JWT_SECRET", "astran-dev-secret-change-in-prod")
JWT_ALG = "HS256"
JWT_TTL_DAYS = 7

EMERGENT_AUTH_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app = FastAPI(title="Astran Game API")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("astran")

# Sandbox-ul Luau este optional: daca lipseste sau da eroare la import, restul API-ului merge normal.
try:
    from astran_sandbox.routes import make_sandbox_router
except Exception as exc:  # noqa: BLE001
    make_sandbox_router = None
    log.warning("Luau sandbox disabled: %s", exc)

# Magazinul de obiecte de joc (Modele + Scripturi) e la fel de optional.
try:
    from astran_sandbox.shop_routes import make_shop_router
except Exception as exc:  # noqa: BLE001
    make_shop_router = None
    log.warning("Shop disabled: %s", exc)

# Magazinul separat de haine/accesorii pentru Avatar Editor.
try:
    from astran_sandbox.clothes_routes import make_clothes_router
except Exception as exc:  # noqa: BLE001
    make_clothes_router = None
    log.warning("Clothes shop disabled: %s", exc)

# Avatar Editor (corp + sloturi echipate) - optional, la fel ca celelalte module sandbox.
try:
    from astran_sandbox.avatar_routes import make_avatar_router
except Exception as exc:  # noqa: BLE001
    make_avatar_router = None
    log.warning("Avatar disabled: %s", exc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_pw(pw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), pw_hash.encode())
    except Exception:
        return False


def make_jwt(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "iat": int(now_utc().timestamp()),
        "exp": int((now_utc() + timedelta(days=JWT_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


AgeCategory = Literal["under_18", "adult_18"]


class UserPublic(BaseModel):
    user_id: str
    username: str
    display_name: str
    email: Optional[EmailStr] = None
    avatar_url: Optional[str] = None
    age_category: AgeCategory = "under_18"
    astrans_balance: int = 0
    language: str = "ro"
    is_platform_owner: bool = False
    is_platform_admin: bool = False
    created_at: datetime
    online: bool = False


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    username: str = Field(min_length=3, max_length=24)
    display_name: Optional[str] = None
    age_category: AgeCategory = "under_18"
    language: str = "ro"


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class SessionBody(BaseModel):
    session_id: str


class UpdateProfileBody(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    age_category: Optional[AgeCategory] = None
    language: Optional[str] = None


class GameCreateBody(BaseModel):
    title: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=2000)
    age_category: AgeCategory = "under_18"
    is_public: bool = True
    thumbnail_url: Optional[str] = None
    category: str = "adventure"
    allow_join_via_friends: bool = True
    scene: Optional[dict] = None
    script: str = Field(default="", max_length=20000)


class GameUpdateBody(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=64)
    description: Optional[str] = Field(default=None, max_length=2000)
    age_category: Optional[AgeCategory] = None
    is_public: Optional[bool] = None
    thumbnail_url: Optional[str] = None
    category: Optional[str] = None
    scene: Optional[dict] = None
    script: Optional[str] = Field(default=None, max_length=20000)


class GamePublic(BaseModel):
    game_id: str
    owner_id: str
    owner_username: str
    title: str
    description: str
    age_category: AgeCategory
    is_public: bool
    thumbnail_url: Optional[str] = None
    category: str
    player_count: int = 0
    total_plays: int = 0
    likes: int = 0
    created_at: datetime
    updated_at: datetime
    status: str = "active"


class FriendRequestBody(BaseModel):
    target_user_id: str


class TransferBody(BaseModel):
    recipient_user_id: str
    amount: int = Field(gt=0)
    idempotency_key: Optional[str] = None


class BuyBody(BaseModel):
    package_id: str
    provider: Literal["stripe", "google_play", "apple", "mock"] = "mock"
    provider_token: Optional[str] = None


class ReportBody(BaseModel):
    target_type: Literal["user", "game", "message"]
    target_id: str
    reason: str
    details: Optional[str] = None


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    sess = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if sess:
        exp = sess.get("expires_at")
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp and exp < now_utc():
            raise HTTPException(status_code=401, detail="Session expired")
        user = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        user = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.on_event("startup")
async def startup():
    await db.users.create_index("user_id", unique=True)
    await db.users.create_index("email", unique=True, sparse=True)
    await db.users.create_index("username", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.games.create_index("game_id", unique=True)
    await db.games.create_index([("owner_id", 1), ("created_at", -1)])
    await db.games.create_index([("age_category", 1), ("is_public", 1), ("total_plays", -1)])
    await db.friendships.create_index([("user_a", 1), ("user_b", 1)], unique=True)
    await db.friend_requests.create_index([("from_id", 1), ("to_id", 1)], unique=True)
    await db.blocks.create_index([("blocker_id", 1), ("blocked_id", 1)], unique=True)
    await db.astran_ledger.create_index("tx_id", unique=True)
    await db.astran_ledger.create_index("idempotency_key", unique=True, sparse=True)
    await db.astran_ledger.create_index([("user_id", 1), ("timestamp", -1)])
    await db.reports.create_index("report_id", unique=True)
    await db.recently_played.create_index([("user_id", 1), ("played_at", -1)])

    existing = await db.platform_config.find_one({"key": "transfer_fees"}, {"_id": 0})
    if not existing:
        await db.platform_config.insert_one({
            "key": "transfer_fees",
            "tiers": [
                {"max_amount": 100, "fee_percent": 5},
                {"max_amount": 1000, "fee_percent": 7},
                {"max_amount": None, "fee_percent": 10},
            ],
            "updated_at": now_utc(),
        })
    pkg = await db.platform_config.find_one({"key": "astran_packages"}, {"_id": 0})
    if not pkg:
        await db.platform_config.insert_one({
            "key": "astran_packages",
            "currency": "RON",
            "packages": [
                {"package_id": "starter_60", "astrans": 60, "price": 2.99, "label": "Starter"},
                {"package_id": "pro_120", "astrans": 120, "price": 4.99, "label": "Pro"},
                {"package_id": "plus_300", "astrans": 300, "price": 11.99, "label": "Plus"},
                {"package_id": "elite_800", "astrans": 800, "price": 29.99, "label": "Elite"},
            ],
            "updated_at": now_utc(),
        })

    demo_owner_id = "user_astran_demo"
    if not await db.users.find_one({"user_id": demo_owner_id}, {"_id": 0}):
        await db.users.insert_one({
            "user_id": demo_owner_id,
            "email": "demo@astran.game",
            "username": "AstranStudio",
            "display_name": "Astran Studio",
            "password_hash": hash_pw("Astran#Demo2026"),
            "avatar_url": None,
            "age_category": "adult_18",
            "astrans_balance": 10000,
            "language": "ro",
            "is_platform_owner": True,
            "is_platform_admin": True,
            "created_at": now_utc(),
        })
    await db.games.delete_many({"owner_id": demo_owner_id})
    log.info("Astran API ready. DB=%s", DB_NAME)


@app.on_event("shutdown")
async def shutdown():
    client.close()


def _user_public(user: dict) -> dict:
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "email": user.get("email"),
        "avatar_url": user.get("avatar_url"),
        "age_category": user.get("age_category", "under_18"),
        "astrans_balance": user.get("astrans_balance", 0),
        "language": user.get("language", "ro"),
        "is_platform_owner": user.get("is_platform_owner", False),
        "is_platform_admin": user.get("is_platform_admin", False),
        "created_at": user.get("created_at", now_utc()),
        "online": True,
    }


@api.get("/")
async def root():
    return {"service": "astran-game", "version": "0.1.0", "status": "ok"}


@api.post("/auth/register")
async def register(body: RegisterBody):
    existing = await db.users.find_one({"$or": [{"email": body.email}, {"username": body.username}]}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail="Email or username already exists")
    user_id = new_id("user_")
    doc = {
        "user_id": user_id,
        "email": body.email,
        "username": body.username,
        "display_name": body.display_name or body.username,
        "password_hash": hash_pw(body.password),
        "avatar_url": None,
        "age_category": body.age_category,
        "astrans_balance": 100,
        "language": body.language,
        "is_platform_owner": False,
        "is_platform_admin": False,
        "created_at": now_utc(),
    }
    await db.users.insert_one(doc)
    await db.astran_ledger.insert_one({
        "tx_id": new_id("tx_"),
        "user_id": user_id,
        "counterparty_id": "system",
        "type": "welcome_bonus",
        "amount": 100,
        "fee": 0,
        "net": 100,
        "status": "completed",
        "timestamp": now_utc(),
        "reference": "signup",
    })
    token = make_jwt(user_id)
    return {"token": token, "session_token": token, "user": _user_public(doc)}


@api.post("/auth/login")
async def login(body: LoginBody):
    user = await db.users.find_one({"email": body.email})
    if not user or not user.get("password_hash") or not verify_pw(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = make_jwt(user["user_id"])
    return {"token": token, "session_token": token, "user": _user_public(user)}


@api.post("/auth/session")
async def google_session(body: SessionBody):
    async with httpx.AsyncClient(timeout=10.0) as h:
        try:
            resp = await h.get(EMERGENT_AUTH_URL, headers={"X-Session-ID": body.session_id})
        except Exception as e:
            log.error("Emergent auth error: %s", e)
            raise HTTPException(status_code=401, detail="Auth service unavailable")
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or used session_id")
    data = resp.json()
    email = data.get("email")
    name = data.get("name") or email.split("@")[0]
    picture = data.get("picture")
    session_token = data.get("session_token") or secrets.token_urlsafe(32)

    if not email:
        raise HTTPException(status_code=401, detail="No email in session")

    existing = await db.users.find_one({"email": email})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one({"user_id": user_id}, {"$set": {"avatar_url": picture, "display_name": existing.get("display_name") or name}})
        user = await db.users.find_one({"user_id": user_id})
    else:
        user_id = new_id("user_")
        base_username = "".join(c for c in name if c.isalnum())[:20] or f"player{user_id[-6:]}"
        username = base_username
        i = 0
        while await db.users.find_one({"username": username}):
            i += 1
            username = f"{base_username}{i}"
        user = {
            "user_id": user_id,
            "email": email,
            "username": username,
            "display_name": name,
            "password_hash": None,
            "avatar_url": picture,
            "age_category": "under_18",
            "astrans_balance": 100,
            "language": "ro",
            "is_platform_owner": False,
            "is_platform_admin": False,
            "created_at": now_utc(),
        }
        await db.users.insert_one(user)
        await db.astran_ledger.insert_one({
            "tx_id": new_id("tx_"), "user_id": user_id, "counterparty_id": "system",
            "type": "welcome_bonus", "amount": 100, "fee": 0, "net": 100,
            "status": "completed", "timestamp": now_utc(), "reference": "signup_google",
        })

    await db.user_sessions.insert_one({
        "session_token": session_token,
        "user_id": user_id,
        "created_at": now_utc(),
        "expires_at": now_utc() + timedelta(days=7),
    })
    return {"session_token": session_token, "token": session_token, "user": _user_public(user)}


@api.get("/auth/me")
async def me(current=Depends(get_current_user)):
    return {"user": _user_public(current)}


@api.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        await db.user_sessions.delete_one({"session_token": token})
    return {"ok": True}


@api.patch("/users/me")
async def update_me(body: UpdateProfileBody, current=Depends(get_current_user)):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if updates:
        updates["updated_at"] = now_utc()
        await db.users.update_one({"user_id": current["user_id"]}, {"$set": updates})
    user = await db.users.find_one({"user_id": current["user_id"]}, {"_id": 0, "password_hash": 0})
    return {"user": _user_public(user)}


@api.get("/users/search")
async def search_users(q: str = "", current=Depends(get_current_user)):
    if not q or len(q) < 2:
        return {"users": []}
    cursor = db.users.find(
        {"$or": [{"username": {"$regex": q, "$options": "i"}}, {"display_name": {"$regex": q, "$options": "i"}}]},
        {"_id": 0, "password_hash": 0},
    ).limit(20)
    users = [_user_public(u) for u in await cursor.to_list(20) if u["user_id"] != current["user_id"]]
    return {"users": users}


@api.post("/games")
async def create_game(body: GameCreateBody, current=Depends(get_current_user)):
    gid = new_id("game_")
    doc = {
        "game_id": gid,
        "owner_id": current["user_id"],
        "owner_username": current["username"],
        "title": body.title,
        "description": body.description,
        "age_category": body.age_category,
        "is_public": body.is_public,
        "thumbnail_url": body.thumbnail_url,
        "category": body.category,
        "player_count": 0,
        "total_plays": 0,
        "likes": 0,
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "status": "active",
        "allow_join_via_friends": body.allow_join_via_friends,
        "scene": body.scene or {"objects": [], "sky": "#0F1012", "ground": "#1A1D21"},
        "script": body.script,
    }
    await db.games.insert_one(doc)
    return {"game": {k: v for k, v in doc.items() if k != "_id"}}


@api.patch("/games/{game_id}")
async def update_game(game_id: str, body: GameUpdateBody, current=Depends(get_current_user)):
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
        raise HTTPException(status_code=403, detail="Not the owner")
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if updates:
        updates["updated_at"] = now_utc()
        await db.games.update_one({"game_id": game_id}, {"$set": updates})
    g2 = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    return {"game": g2}


@api.delete("/games/{game_id}")
async def delete_game(game_id: str, current=Depends(get_current_user)):
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
        raise HTTPException(status_code=403, detail="Not the owner")
    await db.games.delete_one({"game_id": game_id})
    return {"ok": True}


@api.get("/games/discover")
async def discover(section: str = "for_you", current=Depends(get_current_user)):
    age = current.get("age_category", "under_18")
    q: dict = {"is_public": True, "status": "active"}
    if age == "under_18":
        q["age_category"] = "under_18"
    sort_map = {
        "for_you": [("total_plays", -1), ("likes", -1)],
        "trending": [("player_count", -1), ("total_plays", -1)],
        "new": [("created_at", -1)],
        "popular": [("likes", -1), ("total_plays", -1)],
    }
    sort = sort_map.get(section, sort_map["for_you"])
    cursor = db.games.find(q, {"_id": 0, "script": 0}).sort(sort).limit(30)
    return {"section": section, "games": await cursor.to_list(30)}


@api.get("/games/mine")
async def my_games(current=Depends(get_current_user)):
    cursor = db.games.find({"owner_id": current["user_id"]}, {"_id": 0, "script": 0}).sort("created_at", -1)
    return {"games": await cursor.to_list(100)}


@api.get("/games/recently-played")
async def recently_played(current=Depends(get_current_user)):
    cursor = db.recently_played.find({"user_id": current["user_id"]}, {"_id": 0}).sort("played_at", -1).limit(50)
    entries = await cursor.to_list(50)
    game_ids = [e["game_id"] for e in entries]
    if not game_ids:
        return {"games": []}
    games = await db.games.find({"game_id": {"$in": game_ids}}, {"_id": 0, "script": 0}).to_list(50)
    idx = {g["game_id"]: g for g in games}
    return {"games": [idx[gid] for gid in game_ids if gid in idx]}


@api.get("/games/{game_id}")
async def get_game(game_id: str, current=Depends(get_current_user)):
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["age_category"] == "adult_18" and current.get("age_category") == "under_18":
        raise HTTPException(status_code=403, detail="Age-restricted content")
    if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
        g.pop("script", None)
    return {"game": g}


@api.post("/games/{game_id}/play")
async def play_game(game_id: str, current=Depends(get_current_user)):
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["age_category"] == "adult_18" and current.get("age_category") == "under_18":
        raise HTTPException(status_code=403, detail="Age-restricted content")
    await db.games.update_one({"game_id": game_id}, {"$inc": {"total_plays": 1, "player_count": 1}})
    await db.recently_played.update_one(
        {"user_id": current["user_id"], "game_id": game_id},
        {"$set": {"played_at": now_utc()}},
        upsert=True,
    )
    return {"ok": True, "session": {"instance_id": new_id("inst_"), "game_id": game_id}}


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


@api.get("/friends")
async def list_friends(current=Depends(get_current_user)):
    uid = current["user_id"]
    cursor = db.friendships.find({"$or": [{"user_a": uid}, {"user_b": uid}]}, {"_id": 0})
    edges = await cursor.to_list(500)
    friend_ids = [e["user_b"] if e["user_a"] == uid else e["user_a"] for e in edges]
    users = await db.users.find({"user_id": {"$in": friend_ids}}, {"_id": 0, "password_hash": 0}).to_list(500)
    return {"friends": [_user_public(u) for u in users]}


@api.get("/friends/requests")
async def list_requests(current=Depends(get_current_user)):
    incoming = await db.friend_requests.find({"to_id": current["user_id"], "status": "pending"}, {"_id": 0}).to_list(200)
    outgoing = await db.friend_requests.find({"from_id": current["user_id"], "status": "pending"}, {"_id": 0}).to_list(200)
    in_users = await db.users.find({"user_id": {"$in": [r["from_id"] for r in incoming]}}, {"_id": 0, "password_hash": 0}).to_list(200)
    out_users = await db.users.find({"user_id": {"$in": [r["to_id"] for r in outgoing]}}, {"_id": 0, "password_hash": 0}).to_list(200)
    return {
        "incoming": [_user_public(u) for u in in_users],
        "outgoing": [_user_public(u) for u in out_users],
    }


@api.post("/friends/request")
async def send_request(body: FriendRequestBody, current=Depends(get_current_user)):
    if body.target_user_id == current["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot friend yourself")
    target = await db.users.find_one({"user_id": body.target_user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    a, b = _pair(current["user_id"], body.target_user_id)
    if await db.friendships.find_one({"user_a": a, "user_b": b}):
        raise HTTPException(status_code=409, detail="Already friends")
    if await db.blocks.find_one({"$or": [
        {"blocker_id": current["user_id"], "blocked_id": body.target_user_id},
        {"blocker_id": body.target_user_id, "blocked_id": current["user_id"]},
    ]}):
        raise HTTPException(status_code=403, detail="Blocked")
    try:
        await db.friend_requests.insert_one({
            "from_id": current["user_id"], "to_id": body.target_user_id,
            "status": "pending", "created_at": now_utc(),
        })
    except Exception:
        raise HTTPException(status_code=409, detail="Request already exists")
    return {"ok": True}


@api.post("/friends/accept")
async def accept_request(body: FriendRequestBody, current=Depends(get_current_user)):
    req = await db.friend_requests.find_one({
        "from_id": body.target_user_id, "to_id": current["user_id"], "status": "pending",
    }, {"_id": 0})
    if not req:
        raise HTTPException(status_code=404, detail="No pending request")
    a, b = _pair(current["user_id"], body.target_user_id)
    await db.friendships.update_one({"user_a": a, "user_b": b}, {"$setOnInsert": {"user_a": a, "user_b": b, "since": now_utc()}}, upsert=True)
    await db.friend_requests.update_one({"from_id": body.target_user_id, "to_id": current["user_id"]}, {"$set": {"status": "accepted"}})
    return {"ok": True}


@api.post("/friends/reject")
async def reject_request(body: FriendRequestBody, current=Depends(get_current_user)):
    await db.friend_requests.delete_one({"from_id": body.target_user_id, "to_id": current["user_id"]})
    return {"ok": True}


@api.post("/friends/remove")
async def remove_friend(body: FriendRequestBody, current=Depends(get_current_user)):
    a, b = _pair(current["user_id"], body.target_user_id)
    await db.friendships.delete_one({"user_a": a, "user_b": b})
    return {"ok": True}


@api.post("/friends/block")
async def block_user(body: FriendRequestBody, current=Depends(get_current_user)):
    if body.target_user_id == current["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    a, b = _pair(current["user_id"], body.target_user_id)
    await db.friendships.delete_one({"user_a": a, "user_b": b})
    await db.friend_requests.delete_many({"$or": [
        {"from_id": current["user_id"], "to_id": body.target_user_id},
        {"from_id": body.target_user_id, "to_id": current["user_id"]},
    ]})
    await db.blocks.update_one(
        {"blocker_id": current["user_id"], "blocked_id": body.target_user_id},
        {"$setOnInsert": {"blocker_id": current["user_id"], "blocked_id": body.target_user_id, "since": now_utc()}},
        upsert=True,
    )
    return {"ok": True}


@api.get("/wallet/balance")
async def wallet_balance(current=Depends(get_current_user)):
    return {"balance": current.get("astrans_balance", 0), "currency": "ASTRANS"}


@api.get("/wallet/packages")
async def wallet_packages():
    cfg = await db.platform_config.find_one({"key": "astran_packages"}, {"_id": 0})
    return cfg or {"packages": [], "currency": "RON"}


@api.get("/wallet/fees")
async def wallet_fees():
    cfg = await db.platform_config.find_one({"key": "transfer_fees"}, {"_id": 0})
    return cfg or {"tiers": []}


def _calc_fee(amount: int, tiers: list[dict]) -> tuple[int, int]:
    for tier in tiers:
        maxa = tier.get("max_amount")
        if maxa is None or amount <= maxa:
            pct = tier["fee_percent"]
            fee = int(amount * pct / 100)
            return fee, amount - fee
    return 0, amount


@api.post("/wallet/transfer/preview")
async def transfer_preview(body: TransferBody, current=Depends(get_current_user)):
    cfg = await db.platform_config.find_one({"key": "transfer_fees"}, {"_id": 0})
    fee, net = _calc_fee(body.amount, cfg["tiers"])
    return {"gross": body.amount, "fee": fee, "net": net}


@api.post("/wallet/transfer")
async def transfer_astrans(body: TransferBody, current=Depends(get_current_user)):
    if body.recipient_user_id == current["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot transfer to self")
    recipient = await db.users.find_one({"user_id": body.recipient_user_id})
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    if body.idempotency_key:
        existing = await db.astran_ledger.find_one({"idempotency_key": body.idempotency_key}, {"_id": 0})
        if existing:
            return {"ok": True, "transaction": existing, "idempotent": True}

    cfg = await db.platform_config.find_one({"key": "transfer_fees"}, {"_id": 0})
    fee, net = _calc_fee(body.amount, cfg["tiers"])

    debit = await db.users.update_one(
        {"user_id": current["user_id"], "astrans_balance": {"$gte": body.amount}},
        {"$inc": {"astrans_balance": -body.amount}},
    )
    if debit.modified_count == 0:
        raise HTTPException(status_code=402, detail="Insufficient Astrans balance")

    await db.users.update_one({"user_id": recipient["user_id"]}, {"$inc": {"astrans_balance": net}})

    tx = {
        "tx_id": new_id("tx_"),
        "user_id": current["user_id"],
        "counterparty_id": recipient["user_id"],
        "type": "transfer_out",
        "amount": body.amount,
        "fee": fee,
        "net": net,
        "status": "completed",
        "timestamp": now_utc(),
        "idempotency_key": body.idempotency_key,
        "reference": f"transfer_to:{recipient['username']}",
    }
    await db.astran_ledger.insert_one(tx)
    await db.astran_ledger.insert_one({
        "tx_id": new_id("tx_"),
        "user_id": recipient["user_id"],
        "counterparty_id": current["user_id"],
        "type": "transfer_in",
        "amount": net,
        "fee": 0,
        "net": net,
        "status": "completed",
        "timestamp": now_utc(),
        "reference": f"transfer_from:{current['username']}",
    })
    return {"ok": True, "transaction": {k: v for k, v in tx.items() if k != "_id"}}


@api.get("/wallet/transactions")
async def wallet_tx(current=Depends(get_current_user)):
    cursor = db.astran_ledger.find({"user_id": current["user_id"]}, {"_id": 0}).sort("timestamp", -1).limit(100)
    return {"transactions": await cursor.to_list(100)}


class PaymentProvider:
    name: str = "base"

    async def verify(self, token: str, package: dict, user: dict) -> bool:
        raise NotImplementedError


class MockProvider(PaymentProvider):
    name = "mock"

    async def verify(self, token: str, package: dict, user: dict) -> bool:
        return True


class StripeProvider(PaymentProvider):
    name = "stripe"

    async def verify(self, token: str, package: dict, user: dict) -> bool:
        api_key = os.environ.get("STRIPE_SECRET_KEY")
        if not api_key or not token:
            return False
        async with httpx.AsyncClient(timeout=10.0) as h:
            r = await h.get(f"https://api.stripe.com/v1/payment_intents/{token}", auth=(api_key, ""))
        if r.status_code != 200:
            return False
        data = r.json()
        return data.get("status") == "succeeded" and int(data.get("amount", 0)) >= int(package["price"] * 100)


class GooglePlayProvider(PaymentProvider):
    name = "google_play"

    async def verify(self, token: str, package: dict, user: dict) -> bool:
        return bool(token and token.startswith("gp_"))


class AppleProvider(PaymentProvider):
    name = "apple"

    async def verify(self, token: str, package: dict, user: dict) -> bool:
        return bool(token and token.startswith("ap_"))


PROVIDERS: dict[str, PaymentProvider] = {
    "mock": MockProvider(),
    "stripe": StripeProvider(),
    "google_play": GooglePlayProvider(),
    "apple": AppleProvider(),
}


@api.post("/wallet/buy")
async def buy_astrans(body: BuyBody, current=Depends(get_current_user)):
    cfg = await db.platform_config.find_one({"key": "astran_packages"}, {"_id": 0})
    package = next((p for p in cfg["packages"] if p["package_id"] == body.package_id), None)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    provider = PROVIDERS.get(body.provider)
    if not provider:
        raise HTTPException(status_code=400, detail="Unknown payment provider")
    ok = await provider.verify(body.provider_token or "", package, current)
    if not ok:
        raise HTTPException(status_code=402, detail="Payment verification failed")

    await db.users.update_one({"user_id": current["user_id"]}, {"$inc": {"astrans_balance": package["astrans"]}})
    tx = {
        "tx_id": new_id("tx_"),
        "user_id": current["user_id"],
        "counterparty_id": "system",
        "type": "purchase",
        "amount": package["astrans"],
        "fee": 0,
        "net": package["astrans"],
        "status": "completed",
        "timestamp": now_utc(),
        "reference": f"buy:{package['package_id']}:{provider.name}",
        "provider": provider.name,
        "provider_token": body.provider_token,
        "price": package["price"],
        "currency": cfg.get("currency", "RON"),
    }
    await db.astran_ledger.insert_one(tx)
    return {"ok": True, "credited": package["astrans"], "transaction": {k: v for k, v in tx.items() if k != "_id"}}


@api.post("/reports")
async def create_report(body: ReportBody, current=Depends(get_current_user)):
    rid = new_id("rep_")
    doc = {
        "report_id": rid,
        "reporter_id": current["user_id"],
        "target_type": body.target_type,
        "target_id": body.target_id,
        "reason": body.reason,
        "details": body.details,
        "status": "open",
        "created_at": now_utc(),
    }
    await db.reports.insert_one(doc)
    return {"report_id": rid}


@api.get("/config/languages")
async def languages():
    return {
        "default": "ro",
        "languages": [
            {"code": "ro", "label": "Română", "native": "Română"},
            {"code": "en", "label": "English", "native": "English"},
            {"code": "es", "label": "Spanish", "native": "Español"},
            {"code": "ru", "label": "Russian", "native": "Русский"},
            {"code": "de", "label": "German", "native": "Deutsch"},
            {"code": "fr", "label": "French", "native": "Français"},
            {"code": "it", "label": "Italian", "native": "Italiano"},
            {"code": "pt", "label": "Portuguese", "native": "Português"},
            {"code": "ar", "label": "Arabic", "native": "العربية"},
            {"code": "zh", "label": "Chinese", "native": "中文"},
            {"code": "ja", "label": "Japanese", "native": "日本語"},
            {"code": "hi", "label": "Hindi", "native": "हिन्दी"},
        ],
    }


# Sandbox Luau: /api/sandbox/run si /api/sandbox/games/{game_id}/run
if make_sandbox_router is not None:
    api.include_router(make_sandbox_router(get_current_user, db))

# Shop de obiecte de joc (Modele + Scripturi): /api/shop/models, /api/shop/scripts, /api/shop/items/{id}...
if make_shop_router is not None:
    api.include_router(make_shop_router(get_current_user, db))

# Clothes Shop (haine/accesorii pentru Avatar Editor): /api/clothes/items, /api/clothes/slots...
if make_clothes_router is not None:
    api.include_router(make_clothes_router(get_current_user, db))

# Avatar Editor: /api/avatar/me, /api/avatar/inventory, /api/avatar/slots, /api/avatar/user/{id}
if make_avatar_router is not None:
    api.include_router(make_avatar_router(get_current_user, db))

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)