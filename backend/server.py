"""
Astran Game Backend - Phase 1 MVP
Cross-platform 3D UGC gaming platform.
"""
from __future__ import annotations

import os
import uuid
import json
import base64
import logging
import secrets
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

import bcrypt
import jwt
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
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

# Sincronizarea live intre jucatori (WebSocket) - vezi realtime.py pentru protocol si limitari.
try:
    from realtime import make_realtime_router, maybe_connect_redis
except Exception as exc:  # noqa: BLE001
    make_realtime_router = None
    maybe_connect_redis = None
    log.warning("Realtime multiplayer disabled: %s", exc)

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

# Avatar Editor (corp) - optional, la fel ca celelalte module sandbox.
try:
    from astran_sandbox.avatar_routes import make_avatar_router
except Exception as exc:  # noqa: BLE001
    make_avatar_router = None
    log.warning("Avatar disabled: %s", exc)

# Setari persistente de player (Graphics Quality, Render Distance, etc) - optional, la fel.
try:
    from astran_sandbox.user_settings_routes import make_user_settings_router
except Exception as exc:  # noqa: BLE001
    make_user_settings_router = None
    log.warning("User settings disabled: %s", exc)

# GROUPS: Grupuri (Owner/Admin/Member, mute/ban, token privat, chat de grup) - optional,
# la fel ca celelalte module sandbox (daca lipseste, restul API-ului merge normal).
try:
    from astran_sandbox.group_routes import make_group_router
except Exception as exc:  # noqa: BLE001
    make_group_router = None
    log.warning("Groups disabled: %s", exc)

# GAME COLLABORATION / TESTERS: acces de Editor (granular, pe fisiere/foldere) si Tester
# pentru un joc - complet separat de Groups (vezi astran_sandbox/game_permissions.py).
try:
    from astran_sandbox.game_permissions import get_game_access, make_game_permissions_router
except Exception as exc:  # noqa: BLE001
    get_game_access = None
    make_game_permissions_router = None
    log.warning("Game collaboration/testers disabled: %s", exc)


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


# Max Players: input numeric liber (nu o lista fixa de optiuni), limitat doar la un interval rezonabil de server.
MAX_PLAYERS_MIN = 1
MAX_PLAYERS_MAX = 10000
MAX_PLAYERS_DEFAULT = 20


class GameCreateBody(BaseModel):
    title: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=2000)
    age_category: AgeCategory = "under_18"
    is_public: bool = True
    thumbnail_url: Optional[str] = None
    category: str = "adventure"
    # GENRE/SUBGENRE: "category" de mai sus e deja folosit ca Genre (lista fixa existenta in
    # UI: adventure/shooter/...); subgenre e liber, optional, NU inventa o valoare daca lipseste.
    subgenre: Optional[str] = Field(default=None, max_length=40)
    allow_join_via_friends: bool = True
    scene: Optional[dict] = None
    script: str = Field(default="", max_length=20000)
    max_players: int = Field(default=MAX_PLAYERS_DEFAULT, ge=MAX_PLAYERS_MIN, le=MAX_PLAYERS_MAX)
    # daca setat, jucatorii intra in joc cu acest model (din Studio) in loc de avatarul lor personal
    player_character_model_id: Optional[str] = Field(default=None, max_length=64)
    # GROUPS: token privat al unui Group (optional) - daca e valid, jocul se publica sub acel Group
    group_token: Optional[str] = Field(default=None, max_length=64)


class GameUpdateBody(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=64)
    description: Optional[str] = Field(default=None, max_length=2000)
    age_category: Optional[AgeCategory] = None
    is_public: Optional[bool] = None
    thumbnail_url: Optional[str] = None
    category: Optional[str] = None
    subgenre: Optional[str] = Field(default=None, max_length=40)
    scene: Optional[dict] = None
    script: Optional[str] = Field(default=None, max_length=20000)
    max_players: Optional[int] = Field(default=None, ge=MAX_PLAYERS_MIN, le=MAX_PLAYERS_MAX)
    player_character_model_id: Optional[str] = Field(default=None, max_length=64)
    clear_player_character: Optional[bool] = None  # true = revine la avatarul personal al jucatorului
    # GROUPS: schimba/elimina asocierea cu un Group (vezi create_game/update_game mai jos)
    group_token: Optional[str] = Field(default=None, max_length=64)
    clear_group: Optional[bool] = None


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
    subgenre: Optional[str] = None
    player_count: int = 0
    total_plays: int = 0
    likes: int = 0
    # Prima, respectiv ultima, PUBLICARE PUBLICA a jocului (nu data crearii randului in DB).
    # None daca jocul nu a fost niciodata publicat public (draft/private) - vezi _game_defaults.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    status: str = "active"
    max_players: int = MAX_PLAYERS_DEFAULT
    player_character_model_id: Optional[str] = None
    # GROUPS
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    group_logo_url: Optional[str] = None


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


class JoinInstanceBody(BaseModel):
    instance_id: Optional[str] = None  # daca setat, incearca sa intre in acea instanta anume (ex: link de la un prieten)


class HeartbeatBody(BaseModel):
    instance_id: str


async def resolve_user_from_token(token: str) -> Optional[dict]:
    """Logica reala de autentificare (sesiune sau JWT), extrasa din get_current_user ca sa
    poata fi refolosita si de WebSocket-ul din realtime.py - acolo nu exista header
    "Authorization" (clientul WebSocket din React Native nu poate seta header-e custom la
    handshake), deci token-ul vine ca query param si trebuie decodat manual, fara sa
    duplicam regulile de validare intr-un loc separat."""
    sess = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if sess:
        exp = sess.get("expires_at")
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp and exp < now_utc():
            return None
        return await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0, "password_hash": 0})

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    except jwt.PyJWTError:
        return None


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = await resolve_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


@app.on_event("startup")
async def startup():
    if maybe_connect_redis is not None:
        await maybe_connect_redis()
    await db.users.create_index("user_id", unique=True)
    await db.users.create_index("email", unique=True, sparse=True)
    await db.users.create_index("username", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.games.create_index("game_id", unique=True)
    await db.games.create_index([("owner_id", 1), ("created_at", -1)])
    await db.games.create_index([("age_category", 1), ("is_public", 1), ("total_plays", -1)])
    await db.games.create_index("group_id")  # GROUPS: lookup rapid al jocurilor unui Group
    await db.friendships.create_index([("user_a", 1), ("user_b", 1)], unique=True)
    await db.friend_requests.create_index([("from_id", 1), ("to_id", 1)], unique=True)
    await db.blocks.create_index([("blocker_id", 1), ("blocked_id", 1)], unique=True)
    await db.astran_ledger.create_index("tx_id", unique=True)
    await db.astran_ledger.create_index("idempotency_key", unique=True, sparse=True)
    # O singura creditare per plata REALA (purchase token Google / transaction_id Apple /
    # payment_intent Stripe) - vezi VerifyResult.external_id si buy_astrans mai jos.
    await db.astran_ledger.create_index("provider_ref", unique=True, sparse=True)
    await db.astran_ledger.create_index([("user_id", 1), ("timestamp", -1)])
    await db.reports.create_index("report_id", unique=True)
    await db.recently_played.create_index([("user_id", 1), ("played_at", -1)])
    # Server instances: mai multe "camere" pentru acelasi joc, fiecare cu propriul plafon de jucatori.
    await db.game_instances.create_index("instance_id", unique=True)
    await db.game_instances.create_index([("game_id", 1), ("status", 1), ("player_count", 1)])
    await db.instance_players.create_index([("instance_id", 1), ("user_id", 1)], unique=True)
    await db.instance_players.create_index("last_seen", expireAfterSeconds=60)  # jucator inactiv >60s = considerat plecat

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


def _user_public_minimal(user: dict) -> dict:
    """Profilul public al ALTCUIVA (ex: creatorul unui joc) - fara email/balanta/limba,
    care sunt private. Folosit de GET /users/{user_id} mai jos."""
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "avatar_url": user.get("avatar_url"),
        "is_platform_owner": user.get("is_platform_owner", False),
        "created_at": user.get("created_at", now_utc()),
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
    # BUG REPARAT: verificarea trebuie facuta INAINTE de a calcula 'name' - "name = ... or
    # email.split('@')[0]" arunca o exceptie necontrolata (AttributeError: 'NoneType' object
    # has no attribute 'split') daca Google nu trimite deloc email, pentru ca atunci email e
    # None, iar None.split(...) crapa. Userul vedea o eroare generica 500 in loc de mesajul
    # clar de mai jos. Mutand verificarea aici, email lipsa se trateaza curat, cu 401.
    if not email:
        raise HTTPException(status_code=401, detail="No email in session")
    name = data.get("name") or email.split("@")[0]
    picture = data.get("picture")
    session_token = data.get("session_token") or secrets.token_urlsafe(32)

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


@api.get("/users/{user_id}")
async def get_user_public(user_id: str, current=Depends(get_current_user)):
    """Profil public minimal - folosit de pagina de joc pentru a face creatorul clickabil,
    si de orice alt loc care trebuie sa arate 'cine e acest user' fara date private."""
    u = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": _user_public_minimal(u)}


def _game_defaults(doc: dict) -> dict:
    """Completeaza campurile noi (max_players, player_character, group, subgenre) pentru
    jocurile salvate inainte de ele, si expune created_at/updated_at cu semantica ceruta:
    PRIMA, respectiv ULTIMA, publicare PUBLICA a jocului - nu data la care a fost creat
    randul in baza de date (acel moment intern ramane in 'record_created_at', folosit
    pentru sortarea interna - vezi my_games/discover, neschimbate)."""
    doc.setdefault("max_players", MAX_PLAYERS_DEFAULT)
    doc.setdefault("player_character_model_id", None)
    doc.setdefault("group_id", None)
    doc.setdefault("subgenre", None)
    doc["record_created_at"] = doc.get("created_at")
    doc["created_at"] = doc.get("published_created_at")
    doc["updated_at"] = doc.get("published_updated_at")
    return doc


async def _attach_group_info(games: list[dict]) -> list[dict]:
    """GROUPS: adauga group_name/group_logo_url (niciodata tokenul) pe fiecare joc care
    are group_id - un singur query batch, nu N+1, indiferent cate jocuri sunt in lista."""
    ids = {g["group_id"] for g in games if g.get("group_id")}
    if not ids:
        for g in games:
            g.setdefault("group_name", None)
            g.setdefault("group_logo_url", None)
        return games
    groups = await db.groups.find(
        {"group_id": {"$in": list(ids)}}, {"_id": 0, "group_id": 1, "name": 1, "logo_url": 1}
    ).to_list(len(ids))
    by_id = {gr["group_id"]: gr for gr in groups}
    for g in games:
        gi = by_id.get(g.get("group_id"))
        g["group_name"] = gi["name"] if gi else None
        g["group_logo_url"] = gi.get("logo_url") if gi else None
    return games


async def _attach_live_player_counts(games: list[dict]) -> list[dict]:
    """'Playing' REAL: suma player_count din instantele DESCHISE ale fiecarui joc.

    Campul static games.player_count nu e actualizat nicaieri de cand jocurile pot avea
    mai multe server instances (vezi play_game/reap_empty_instances) - afisarea lui directa
    ar fi o statistica falsa (ramane la valoarea de la creare). Aici calculam valoarea reala,
    intr-un singur query de agregare pentru tot batch-ul (nu N+1)."""
    ids = [g["game_id"] for g in games]
    if not ids:
        return games
    pipeline = [
        {"$match": {"game_id": {"$in": ids}, "status": "open"}},
        {"$group": {"_id": "$game_id", "total": {"$sum": "$player_count"}}},
    ]
    rows = await db.game_instances.aggregate(pipeline).to_list(len(ids))
    live = {r["_id"]: r["total"] for r in rows}
    for g in games:
        g["player_count"] = live.get(g["game_id"], 0)
    return games


def _default_game_scripts() -> list[dict]:
    """Setul de fisiere Luau cu care incepe ORICE joc nou - fizica, anti-cheat, randare si
    reguli de lume, plus un README care explica fiecare fisier. Developer-ul le poate edita
    sau sterge liber din Code Editor, exact ca pe orice alt fisier din proiect; astea sunt
    doar punctul de plecare, nu fisiere speciale/protejate.

    IMPORTANT, de citit inainte sa te bazezi pe ele: Astran NU are inca un runtime Luau
    viu (nici pe server, nici pe telefon) care sa execute aceste fisiere FRAME CU FRAME
    in timpul jocului - vezi notele "CURRENT LIMITATION" din fiecare fisier. O parte E
    conectata la Play Mode insa: main.lua citeste GRAVITY/JUMP_SPEED/MOVE_SPEED din
    server/physics.lua si le pune pe workspace.Gravity/.JumpSpeed/.MoveSpeed (vezi
    prelude.luau), iar miscarea reala a jucatorului din Play Mode (frontend/app/play/[id].tsx
    si frontend/app/studio/play/[id].tsx) citeste chiar acele valori - NU mai foloseste
    defaults hardcodate in TypeScript daca scriptul le suprascrie. Restul (functiile
    Physics.step()/tryJump(), anticheat.lua, world.lua, rendering.lua) raman doar punctul
    de plecare, pregatirea pentru ziua in care runtime-ul Luau viu exista pe server/client."""
    main_lua = """-- main.lua (runs on: SERVER)
--
-- Entry point: this is the first (and only) file the sandbox runs when you press Run in
-- the Code Editor. It doesn't do anything on its own - it just loads the other starter
-- files with require(...) and prints a few of their values, so pressing Run gives you
-- immediate, visible proof that your edits to those files are actually being picked up.
--
-- require() paths mirror the project's folder structure, without the .lua extension -
-- "server/physics.lua" becomes require("server/physics"). Feel free to rewrite this file
-- entirely once you have real game logic to run; it isn't special or protected.

local Physics = require("server/physics")
local AntiCheat = require("server/anticheat")
local World = require("server/world")

-- Aceste 3 linii sunt ce conecteaza physics.lua la Play Mode real: valorile tale de
-- mai jos (gravitate, saritura, viteza de mers) chiar schimba cum se joaca jocul, nu
-- doar ce apare printat in consola de Run. Sterge-le doar daca nu mai vrei ca Physics
-- din acest fisier sa controleze miscarea personajului.
workspace.Gravity = math.abs(Physics.GRAVITY)
workspace.JumpSpeed = Physics.JUMP_SPEED
workspace.MoveSpeed = Physics.MOVE_SPEED

print("physics.lua loaded - GRAVITY=" .. tostring(Physics.GRAVITY) .. ", JUMP_SPEED=" .. tostring(Physics.JUMP_SPEED) .. ", MOVE_SPEED=" .. tostring(Physics.MOVE_SPEED))
print("anticheat.lua loaded - MAX_MOVE_PER_TICK=" .. tostring(AntiCheat.MAX_MOVE_PER_TICK) .. ", MAX_VERTICAL_SPEED=" .. tostring(AntiCheat.MAX_VERTICAL_SPEED))
print("world.lua loaded - GRAVITY=" .. tostring(World.GRAVITY) .. ", AIR_DENSITY=" .. tostring(World.AIR_DENSITY))
print("Edit any value above, press Save, then Play - you'll see it change in the actual game.")
"""

    physics_lua = """-- physics.lua (runs on: SERVER)
--
-- Defines the core physics constants and movement/jump rules for this game's characters.
-- GRAVITY, JUMP_SPEED and MOVE_SPEED below DO affect Play Mode: main.lua assigns them to
-- workspace.Gravity / workspace.JumpSpeed / workspace.MoveSpeed, which the actual player
-- movement in Play Mode now reads (instead of its old hardcoded defaults). Change a value,
-- Save, then Play - you'll feel the difference.
--
-- STILL A LIMITATION: Astran does not yet have a live, per-frame Luau server loop, so the
-- Physics.step()/Physics.tryJump() functions below are not called by Play Mode each tick -
-- only the 3 constants above (via workspace.*) are wired in today. Rewrite step()/tryJump()
-- freely for when a live runtime calls them, but don't expect them to run yet.

local Physics = {}

-- World gravity, in studs/second^2 (negative = pulls down). Matches Astran's current default.
Physics.GRAVITY = -18

-- Initial upward velocity applied when a character jumps, in studs/second.
-- With GRAVITY = -18, this produces a jump height of about 1.56 studs (v^2 / (2 * |g|)).
Physics.JUMP_SPEED = 7.5

-- Horizontal movement speed while walking, in studs/second.
Physics.MOVE_SPEED = 3.2

-- Character capsule radius, used for horizontal collision against solid objects, in studs.
Physics.PLAYER_RADIUS = 0.35

-- Default half-height of a character's collision capsule, in studs (used when a game has
-- no custom avatar body or character model attached).
Physics.PLAYER_HALF_HEIGHT_DEFAULT = 0.9

-- Called once per simulation step (per server tick) for a single player.
-- state: { x, y, z, velocityY, grounded, moveX, moveZ, groundY } - current known state
-- dt: time, in seconds, since the last step
-- Returns the updated { x, y, z, velocityY, grounded } for this player.
function Physics.step(state, dt)
	local velocityY = state.velocityY

	-- Horizontal movement (straight pass-through here; replace with your own logic, e.g.
	-- acceleration or friction, if you want something other than instant-speed walking).
	local x = state.x + state.moveX * Physics.MOVE_SPEED * dt
	local z = state.z + state.moveZ * Physics.MOVE_SPEED * dt

	-- Vertical integration (semi-implicit Euler, same approach as Astran's built-in engine).
	velocityY = velocityY + Physics.GRAVITY * dt
	local y = state.y + velocityY * dt

	local groundY = state.groundY or 0
	local grounded = false
	if y <= groundY then
		y = groundY
		velocityY = 0
		grounded = true
	end

	return { x = x, y = y, z = z, velocityY = velocityY, grounded = grounded }
end

-- Called when the player presses the Jump button (or jump key). Only takes effect if the
-- player is currently grounded - this is what prevents double-jumping / flying.
function Physics.tryJump(state)
	if not state.grounded then
		return state.velocityY
	end
	return Physics.JUMP_SPEED
end

return Physics
"""

    anticheat_lua = """-- anticheat.lua (runs on: SERVER)
--
-- Server-authoritative validation of what a client claims about itself (position, speed,
-- actions). The core anti-cheat principle: NEVER trust a value a client sends outright -
-- always check it against what is physically possible, and correct or reject it otherwise.
--
-- CURRENT LIMITATION: Astran's live multiplayer sync (see backend/realtime.py) does NOT
-- yet call into this file - it currently relays whatever position a client sends,
-- unvalidated, to other players. This file defines what full server-authoritative
-- validation SHOULD look like; it will be wired into the live sync path once Astran's
-- server-side Luau runtime exists. Until then, treat this as the anti-cheat your game
-- WILL enforce once that exists - not as protection you already have today.

local AntiCheat = {}

-- Maximum distance (in studs) a player is allowed to move in a single server tick, given
-- Physics.MOVE_SPEED and a generous safety margin for lag/jitter. Anything beyond this is
-- treated as impossible movement (speedhack / teleport) and rejected.
AntiCheat.MAX_MOVE_PER_TICK = 0.6

-- Maximum vertical speed a player is allowed to report, in studs/second - a bit above
-- Physics.JUMP_SPEED, to allow for a legitimate jump plus falling speed, but well below
-- what a "fly hack" would produce.
AntiCheat.MAX_VERTICAL_SPEED = 12

-- Validates a claimed new position against the player's last known, server-trusted position.
-- lastState: { x, y, z, t } - last position this server considered valid, and when
-- claimedState: { x, y, z, t } - what the client is now claiming
-- Returns: isValid (boolean), correctedState (the state the server should actually use -
-- either the claimed one, if valid, or the last trusted one, if not)
function AntiCheat.validateMove(lastState, claimedState)
	local dt = math.max(0.001, claimedState.t - lastState.t)

	local dx = claimedState.x - lastState.x
	local dz = claimedState.z - lastState.z
	local horizontalDist = math.sqrt(dx * dx + dz * dz)

	local maxAllowed = AntiCheat.MAX_MOVE_PER_TICK * (dt / (1 / 20)) -- scaled for a ~20 ticks/second server
	if horizontalDist > maxAllowed then
		return false, lastState
	end

	local verticalSpeed = math.abs(claimedState.y - lastState.y) / dt
	if verticalSpeed > AntiCheat.MAX_VERTICAL_SPEED then
		return false, lastState
	end

	return true, claimedState
end

-- Example hook for validating a combat hit (PvP). Replace with real hit detection (e.g.
-- raycasting or hitbox checks performed by the SERVER, never trusting a client's claim of
-- "I hit player X").
function AntiCheat.validateHit(attackerState, targetState, weaponRange)
	local dx = attackerState.x - targetState.x
	local dz = attackerState.z - targetState.z
	local dist = math.sqrt(dx * dx + dz * dz)
	return dist <= weaponRange
end

return AntiCheat
"""

    rendering_lua = """-- rendering.lua (runs on: CLIENT)
--
-- Visual/rendering settings for this game: graphics quality presets and render distance
-- options shown to players in the in-game Settings menu. Purely cosmetic - safe to run on
-- the client, since nothing here affects gameplay outcomes (unlike physics.lua/anticheat.lua,
-- which must stay server-side).
--
-- CURRENT LIMITATION: Play Mode currently uses its own built-in graphics/render distance
-- settings (10 fixed presets each), not this file. Once Astran's client-side Luau runtime
-- exists, this file will be what actually drives those settings for your game.

local Rendering = {}

-- Ten lighting presets, from flattest/cheapest (1) to richest/most expensive (10): ambient,
-- directional (sun), hemisphere, and fill light intensities.
Rendering.GRAPHICS_LEVELS = {
	{ ambient = 1.00, directional = 0.00, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.85, directional = 0.45, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.70, directional = 0.80, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.60, directional = 1.00, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.55, directional = 1.10, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.45, directional = 1.10, hemisphere = 0.25, fill = 0.00 },
	{ ambient = 0.40, directional = 1.15, hemisphere = 0.30, fill = 0.25 },
	{ ambient = 0.35, directional = 1.20, hemisphere = 0.35, fill = 0.35 },
	{ ambient = 0.30, directional = 1.25, hemisphere = 0.40, fill = 0.45 },
	{ ambient = 0.25, directional = 1.30, hemisphere = 0.45, fill = 0.55 },
}

-- Ten render distance options, in studs, from nearest/cheapest (1) to farthest (10).
Rendering.RENDER_DISTANCES = { 20, 30, 45, 60, 80, 100, 125, 150, 200, 250 }

-- Returns the lighting preset for a given level (1-10), clamped to a valid range.
function Rendering.getGraphicsLevel(level)
	local clamped = math.max(1, math.min(10, level))
	return Rendering.GRAPHICS_LEVELS[clamped]
end

-- Returns the render distance, in studs, for a given level (1-10), clamped to a valid range.
function Rendering.getRenderDistance(level)
	local clamped = math.max(1, math.min(10, level))
	return Rendering.RENDER_DISTANCES[clamped]
end

return Rendering
"""

    world_lua = """-- world.lua (runs on: SERVER)
--
-- Global rules for THIS game's world only - they never affect, and are never affected by,
-- any other game on Astran. Each game has its own fully independent copy of this file.
--
-- CURRENT LIMITATION: see physics.lua - this file is not executed by Play Mode yet.
-- Astran's built-in engine currently applies a single, fixed gravity value to every game.

local World = {}

-- Global gravity for this game's world, in studs/second^2. Change this to make your game's
-- world feel different (e.g. a low-gravity moon level, or a heavier, grounded feel) -
-- this ONLY affects this game, never any other game on the platform.
World.GRAVITY = -18

-- Air density / drag applied to falling or thrown objects in this game's world.
-- 0 = no air resistance.
World.AIR_DENSITY = 0

return World
"""

    readme_md = """# Game Scripts

This project comes with a starter set of Luau scripts that define how your game behaves.
You can edit, replace, or delete any of them - they are regular files in your project, not
special or protected in any way.

| File | Runs on | What it defines |
|---|---|---|
| `server/physics.lua` | Server | Gravity, jump strength, movement speed, and character collision size. |
| `server/anticheat.lua` | Server | Server-side validation of player movement and combat, so clients can't cheat by sending fake positions or claiming hits that didn't happen. |
| `server/world.lua` | Server | Global rules for this game's world only (gravity, air density) - never shared with or affected by other games. |
| `client/rendering.lua` | Client | Graphics quality presets and render distance options shown in the in-game Settings menu. Purely visual - has no effect on gameplay. |

## Server vs. Client

Each file is tagged **Server** or **Client** in the Code Editor (see the toggle next to the
file name when it's open). Server files run with full authority and can't be seen or
tampered with by players - use them for anything that affects fairness (physics, anti-cheat,
scoring). Client files run on each player's own device - use them only for things that are
purely visual or don't need to be trusted (graphics settings, UI, cosmetic effects).

## Current limitation (please read)

Astran does not yet have a live runtime that executes these Luau files every frame during
gameplay. One part IS wired in today though: `main.lua` reads GRAVITY/JUMP_SPEED/MOVE_SPEED
from `server/physics.lua` and assigns them to `workspace.Gravity` / `workspace.JumpSpeed` /
`workspace.MoveSpeed` - Play Mode's actual player movement uses those values. Everything else
(the rest of `physics.lua`'s functions, `anticheat.lua`, `world.lua`, `rendering.lua`) is still
just a starting point - it will take effect automatically, without any changes needed on your
part, once Astran's full Luau runtime ships for both server and client.

## Trying it out with Run

Press Run (top of the Code Editor) to actually execute these scripts right now, in a real
Luau sandbox - this works today, independently of the Play Mode limitation above. main.lua
is the entry point (required by the sandbox); it loads physics.lua, anticheat.lua and
world.lua with require(...) and prints their current values. Change a value in physics.lua,
press Run again, and you'll see your change reflected in the console immediately - that's
how you can confirm an edit actually took effect, even before Play Mode can run these files.
"""

    return [
        {"path": "main.lua", "source": main_lua, "target": "server"},
        {"path": "server/physics.lua", "source": physics_lua, "target": "server"},
        {"path": "server/anticheat.lua", "source": anticheat_lua, "target": "server"},
        {"path": "server/world.lua", "source": world_lua, "target": "server"},
        {"path": "client/rendering.lua", "source": rendering_lua, "target": "client"},
        {"path": "README.md", "source": readme_md, "target": "server"},  # ignorat la executie (nu e .lua) - vezi is_luau_path
        ]


@api.post("/games")
async def create_game(body: GameCreateBody, current=Depends(get_current_user)):
    gid = new_id("game_")

    # GROUPS: asociere DOAR prin tokenul privat, validat aici - niciodata prin group_id public.
    group_id = None
    if body.group_token:
        grp = await db.groups.find_one({"token": body.group_token}, {"_id": 0, "group_id": 1})
        if not grp:
            raise HTTPException(status_code=400, detail="Invalid Group token")
        group_id = grp["group_id"]

    # Created/Updated afisate = prima/ultima publicare PUBLICA, nu momentul crearii randului.
    published_ts = now_utc() if body.is_public else None

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
        "subgenre": body.subgenre,
        "group_id": group_id,
        "player_count": 0,
        "total_plays": 0,
        "likes": 0,
        "created_at": now_utc(),  # moment intern de creare a randului (sortare interna)
        "published_created_at": published_ts,
        "published_updated_at": published_ts,
        "status": "active",
        "allow_join_via_friends": body.allow_join_via_friends,
        "scene": body.scene or {"objects": [], "sky": "#0F1012", "ground": "#1A1D21"},
        "script": body.script,
        "max_players": body.max_players,
        "player_character_model_id": body.player_character_model_id,
    }
    await db.games.insert_one(doc)
    # Scaffold: fiecare joc nou porneste cu setul implicit de fisiere Luau (fizica, anti-cheat,
    # randare, reguli de lume) - vezi _default_game_scripts(). Developer-ul le poate edita/
    # sterge liber din Code Editor dupa aceea; nu sunt protejate in niciun fel, doar punctul
    # de plecare. Nu suprascriem niciodata un proiect existent (upsert ar fi gresit aici -
    # jocul tocmai a fost creat, deci game_scripts pentru el nu poate exista deja).
    await db.game_scripts.insert_one({
        "game_id": gid,
        "files": _default_game_scripts(),
        "folders": [],
        "updated_at": now_utc(),
    })
    out_list = await _attach_live_player_counts(
        await _attach_group_info([_game_defaults({k: v for k, v in doc.items() if k != "_id"})])
    )
    return {"game": out_list[0]}


@api.patch("/games/{game_id}")
async def update_game(game_id: str, body: GameUpdateBody, current=Depends(get_current_user)):
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")

    is_owner_actor = g["owner_id"] == current["user_id"] or bool(current.get("is_platform_admin"))

    updates = {
        k: v for k, v in body.dict(exclude={"clear_player_character", "group_token", "clear_group"}).items()
        if v is not None
    }
    character_touched = bool(body.clear_player_character) or ("player_character_model_id" in updates)
    if body.clear_player_character:
        updates["player_character_model_id"] = None

    group_touched = bool(body.clear_group) or (body.group_token is not None)
    publish_touched = "is_public" in updates
    settings_touched = group_touched or character_touched or bool(
        {"title", "description", "category", "subgenre", "age_category", "thumbnail_url", "max_players"} & updates.keys()
    )
    build_touched = bool({"scene", "script"} & updates.keys())

    # SECURITY (item 15): verificare in BACKEND, camp cu camp - un Editor nu primeste
    # niciodata mai mult decat i s-a acordat explicit (vezi game_permissions.py).
    if not is_owner_actor:
        if get_game_access is None:
            raise HTTPException(status_code=403, detail="Not the owner")
        access = await get_game_access(db, g, current)
        if access.role != "editor":
            raise HTTPException(status_code=403, detail="Not the owner")
        if settings_touched and not access.can_change_settings:
            raise HTTPException(status_code=403, detail="No permission to change game settings")
        if build_touched and not access.can_edit_files:
            raise HTTPException(status_code=403, detail="No permission to edit the game")
        if publish_touched and not access.can_publish:
            raise HTTPException(status_code=403, detail="No permission to publish this game")

    # GROUPS: asociere/dezasociere DOAR prin tokenul privat, validat aici.
    if body.clear_group:
        updates["group_id"] = None
    elif body.group_token is not None:
        if body.group_token == "":
            updates["group_id"] = None
        else:
            grp = await db.groups.find_one({"token": body.group_token}, {"_id": 0, "group_id": 1})
            if not grp:
                raise HTTPException(status_code=400, detail="Invalid Group token")
            updates["group_id"] = grp["group_id"]

    # Created/Updated (afisate) NU sunt editabile manual - nu exista camp pentru ele in body.
    # "Updated" se schimba doar cand jocul (re)devine public prin acest request; "Created"
    # se seteaza o singura data, la prima publicare publica, si ramane neschimbat dupa.
    resulting_public = updates.get("is_public", g.get("is_public", False))
    if updates and resulting_public:
        now = now_utc()
        updates["published_updated_at"] = now
        if not g.get("published_created_at"):
            updates["published_created_at"] = now

    if updates:
        await db.games.update_one({"game_id": game_id}, {"$set": updates})
    g2 = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    out_list = await _attach_live_player_counts(await _attach_group_info([_game_defaults(g2)]))
    return {"game": out_list[0]}


@api.delete("/games/{game_id}")
async def delete_game(game_id: str, current=Depends(get_current_user)):
    # Stergerea jocului ramane EXCLUSIV a Owner-ului (sau platform admin) - niciun nivel
    # de Editor, oricat de "full", nu primeste aceasta actiune (ireversibila, distructiva).
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
        raise HTTPException(status_code=403, detail="Not the owner")
    await db.games.delete_one({"game_id": game_id})
    await db.game_instances.delete_many({"game_id": game_id})
    await db.game_collaborators.delete_many({"game_id": game_id})
    await db.game_testers.delete_many({"game_id": game_id})
    return {"ok": True}


@api.get("/games/discover")
async def discover(section: str = "for_you", current=Depends(get_current_user)):
    age = current.get("age_category", "under_18")
    q: dict = {"is_public": True, "status": "active"}
    if age == "under_18":
        q["age_category"] = "under_18"
    sort_map = {
        "for_you": [("total_plays", -1), ("likes", -1)],
        # NOTA: "trending" sorta dupa games.player_count, care nu mai e actualizat (vezi
        # _attach_live_player_counts) - ar fi fost mereu 0 pentru toate jocurile, deci sortarea
        # nu facea nimic. total_plays e proxy-ul real disponibil pentru "activitate recenta".
        "trending": [("total_plays", -1), ("likes", -1)],
        "new": [("created_at", -1)],
        "popular": [("likes", -1), ("total_plays", -1)],
    }
    sort = sort_map.get(section, sort_map["for_you"])
    cursor = db.games.find(q, {"_id": 0, "script": 0}).sort(sort).limit(30)
    games = await _attach_live_player_counts(
        await _attach_group_info([_game_defaults(g) for g in await cursor.to_list(30)])
    )
    return {"section": section, "games": games}


@api.get("/games/mine")
async def my_games(current=Depends(get_current_user)):
    cursor = db.games.find({"owner_id": current["user_id"]}, {"_id": 0, "script": 0}).sort("created_at", -1)
    games = await _attach_live_player_counts(
        await _attach_group_info([_game_defaults(g) for g in await cursor.to_list(100)])
    )
    return {"games": games}


@api.get("/games/recently-played")
async def recently_played(current=Depends(get_current_user)):
    cursor = db.recently_played.find({"user_id": current["user_id"]}, {"_id": 0}).sort("played_at", -1).limit(50)
    entries = await cursor.to_list(50)
    game_ids = [e["game_id"] for e in entries]
    if not game_ids:
        return {"games": []}
    games = await db.games.find({"game_id": {"$in": game_ids}}, {"_id": 0, "script": 0}).to_list(50)
    idx = {g["game_id"]: g for g in games}
    ordered = [idx[gid] for gid in game_ids if gid in idx]
    ordered = await _attach_live_player_counts(await _attach_group_info([_game_defaults(g) for g in ordered]))
    return {"games": ordered}


async def _can_view_private_game(g: dict, current: dict) -> bool:
    """SECURITY: un joc PRIVAT (draft, inca nepublicat) e vizibil DOAR pentru owner,
    platform admin, sau cineva cu acces explicit (Editor/Tester) - niciodata doar pentru
    ca cineva "stie" game_id-ul. Foloseste exact aceeasi regula ca play_game (vezi
    SECURITY items 12/13/15), aplicata acum si la citirea jocului/instantelor lui, nu doar
    la a-l juca efectiv."""
    if g["owner_id"] == current["user_id"] or current.get("is_platform_admin"):
        return True
    if get_game_access is None:
        return False
    access = await get_game_access(db, g, current)
    return bool(access.can_play)


@api.get("/games/{game_id}")
async def get_game(game_id: str, current=Depends(get_current_user)):
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["age_category"] == "adult_18" and current.get("age_category") == "under_18":
        raise HTTPException(status_code=403, detail="Age-restricted content")

    # SECURITY: vezi _can_view_private_game - un joc privat/nepublicat nu mai e vizibil
    # doar pentru ca cineva are/ghiceste game_id-ul (acelasi bug era si la list_instances).
    if not g.get("is_public") and not await _can_view_private_game(g, current):
        raise HTTPException(status_code=403, detail="This game is private")

    if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
        g.pop("script", None)
    out_list = await _attach_live_player_counts(await _attach_group_info([_game_defaults(g)]))
    return {"game": out_list[0]}


# ---------- Server instances ----------
# Cand un server ajunge la max_players, urmatorul jucator intra intr-o instanta noua, nu in
# aceeasi sesiune. O instanta "moare" (status=closed) cand ultimul jucator pleaca de 60s+
# (vezi indexul TTL pe instance_players.last_seen si reap_empty_instances de mai jos).

async def reap_empty_instances(game_id: str) -> None:
    """Inchide instantele fara niciun jucator activ (curatare oportunista, apelata la join)."""
    open_instances = await db.game_instances.find({"game_id": game_id, "status": "open"}, {"_id": 0}).to_list(200)
    for inst in open_instances:
        active = await db.instance_players.count_documents({"instance_id": inst["instance_id"]})
        if active == 0:
            # Niciun jucator activ (toti au plecat sau au expirat din TTL-ul de 60s) - inchidem
            # instanta de tot, altfel ramanea "open" cu player_count=0 pentru totdeauna si se
            # acumula la infinit in game_instances (vezi comentariul de mai sus despre status=closed).
            await db.game_instances.update_one(
                {"instance_id": inst["instance_id"]},
                {"$set": {"status": "closed", "player_count": 0, "closed_at": now_utc()}},
            )
        elif active != inst["player_count"]:
            await db.game_instances.update_one({"instance_id": inst["instance_id"]}, {"$set": {"player_count": active}})


@api.post("/games/{game_id}/play")
async def play_game(game_id: str, body: JoinInstanceBody = JoinInstanceBody(), current=Depends(get_current_user)):
    """Gaseste sau creeaza o instanta de server cu loc liber si inregistreaza jucatorul in ea."""
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["age_category"] == "adult_18" and current.get("age_category") == "under_18":
        raise HTTPException(status_code=403, detail="Age-restricted content")

    # SECURITY (items 12/13/15): un joc PRIVAT (draft, inca nepublicat) se poate juca
    # doar de owner, de un Editor, sau de un Tester caruia i s-a dat acces explicit -
    # niciodata doar pentru ca cineva "stie" game_id-ul. Jocurile publice raman neschimbate.
    if not g.get("is_public") and not await _can_view_private_game(g, current):
        raise HTTPException(status_code=403, detail="This game is private")

    g = _game_defaults(g)
    max_players = g["max_players"]

    await reap_empty_instances(game_id)

    instance = None
    if body.instance_id:
        instance = await db.game_instances.find_one({"instance_id": body.instance_id, "game_id": game_id, "status": "open"}, {"_id": 0})
        if instance and instance["player_count"] >= max_players:
            instance = None  # instanta ceruta e plina - cautam/cream alta mai jos

    if not instance:
        instance = await db.game_instances.find_one_and_update(
            {"game_id": game_id, "status": "open", "player_count": {"$lt": max_players}},
            {"$inc": {"player_count": 1}},
            sort=[("player_count", -1)],  # umplem instantele existente inainte sa deschidem una noua
            return_document=True,
            projection={"_id": 0},
        )

    if not instance:
        instance = {
            "instance_id": new_id("inst_"),
            "game_id": game_id,
            "status": "open",
            "player_count": 1,
            "created_at": now_utc(),
        }
        await db.game_instances.insert_one(instance)
    else:
        # find_one_and_update deja a incrementat player_count in DB; il oglindim si aici pentru raspuns
        instance["player_count"] = instance.get("player_count", 0)

    await db.instance_players.update_one(
        {"instance_id": instance["instance_id"], "user_id": current["user_id"]},
        {"$set": {"instance_id": instance["instance_id"], "user_id": current["user_id"], "last_seen": now_utc()}},
        upsert=True,
    )

    await db.games.update_one({"game_id": game_id}, {"$inc": {"total_plays": 1}})
    await db.recently_played.update_one(
        {"user_id": current["user_id"], "game_id": game_id},
        {"$set": {"played_at": now_utc()}},
        upsert=True,
    )

    return {
        "ok": True,
        "session": {"instance_id": instance["instance_id"], "game_id": game_id},
        "max_players": max_players,
        "player_character_model_id": g.get("player_character_model_id"),
    }


@api.post("/games/{game_id}/instance/heartbeat")
async def instance_heartbeat(game_id: str, body: HeartbeatBody, current=Depends(get_current_user)):
    """Trimis periodic de client cat timp e in Play Mode - tine jucatorul 'activ' in instanta."""
    res = await db.instance_players.update_one(
        {"instance_id": body.instance_id, "user_id": current["user_id"]},
        {"$set": {"last_seen": now_utc()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Not in this instance")
    return {"ok": True}


@api.post("/games/{game_id}/instance/leave")
async def instance_leave(game_id: str, body: HeartbeatBody, current=Depends(get_current_user)):
    """Apelat la Leave, ca instanta sa se elibereze imediat (nu doar dupa expirarea TTL)."""
    await db.instance_players.delete_one({"instance_id": body.instance_id, "user_id": current["user_id"]})
    await db.game_instances.update_one(
        {"instance_id": body.instance_id, "player_count": {"$gt": 0}},
        {"$inc": {"player_count": -1}},
    )
    return {"ok": True}


@api.get("/games/{game_id}/instances")
async def list_instances(game_id: str, current=Depends(get_current_user)):
    """Lista serverelor deschise pentru un joc (util pentru debugging/afisare optionala in UI)."""
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")

    # SECURITY: vezi _can_view_private_game - aceeasi regula ca la get_game/play_game, ca
    # sa nu se poata afla cate instante/locuri libere are un joc privat doar stiindu-i id-ul.
    if not g.get("is_public") and not await _can_view_private_game(g, current):
        raise HTTPException(status_code=403, detail="This game is private")

    await reap_empty_instances(game_id)
    cursor = db.game_instances.find({"game_id": game_id, "status": "open"}, {"_id": 0}).sort("created_at", 1)
    return {"instances": await cursor.to_list(200)}


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
    # Daca celalalt ne-a trimis deja o cerere (pending, in sens invers), cele doua cereri
    # simultane devin direct prietenie, in loc sa ramana doua cereri pendinte in paralel.
    reverse = await db.friend_requests.find_one({
        "from_id": body.target_user_id, "to_id": current["user_id"], "status": "pending",
    })
    if reverse:
        await db.friendships.update_one(
            {"user_a": a, "user_b": b},
            {"$setOnInsert": {"user_a": a, "user_b": b, "since": now_utc()}},
            upsert=True,
        )
        await db.friend_requests.delete_one({"from_id": body.target_user_id, "to_id": current["user_id"]})
        return {"ok": True, "auto_accepted": True}
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
    # Stergem cererea (nu doar o marcam "accepted"): un rand ramas in friend_requests ar
    # bloca permanent o re-trimitere viitoare, din cauza indexului unic pe (from_id, to_id) -
    # de exemplu daca cei doi se deprietenesc mai tarziu si unul vrea sa trimita din nou o cerere.
    await db.friend_requests.delete_one({"from_id": body.target_user_id, "to_id": current["user_id"]})
    return {"ok": True}


@api.post("/friends/reject")
async def reject_request(body: FriendRequestBody, current=Depends(get_current_user)):
    await db.friend_requests.delete_one({"from_id": body.target_user_id, "to_id": current["user_id"]})
    return {"ok": True}


@api.post("/friends/remove")
async def remove_friend(body: FriendRequestBody, current=Depends(get_current_user)):
    a, b = _pair(current["user_id"], body.target_user_id)
    await db.friendships.delete_one({"user_a": a, "user_b": b})
    # Curatam si eventuale cereri ramase (de ex. dintr-o acceptare veche, salvata inainte de
    # acest fix) - altfel indexul unic de pe friend_requests ar bloca permanent o viitoare
    # re-trimitere de cerere intre acesti doi useri.
    await db.friend_requests.delete_many({"$or": [
        {"from_id": current["user_id"], "to_id": body.target_user_id},
        {"from_id": body.target_user_id, "to_id": current["user_id"]},
    ]})
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
    fee, net = _calc_fee(body.amount, (cfg or {}).get("tiers", []))
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
    fee, net = _calc_fee(body.amount, (cfg or {}).get("tiers", []))

    debit = await db.users.update_one(
        {"user_id": current["user_id"], "astrans_balance": {"$gte": body.amount}},
        {"$inc": {"astrans_balance": -body.amount}},
    )
    if debit.modified_count == 0:
        raise HTTPException(status_code=402, detail="Insufficient Astrans balance")

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
    try:
        await db.astran_ledger.insert_one(tx)
    except Exception:
        await db.users.update_one({"user_id": current["user_id"]}, {"$inc": {"astrans_balance": body.amount}})
        if body.idempotency_key:
            existing = await db.astran_ledger.find_one({"idempotency_key": body.idempotency_key}, {"_id": 0})
            if existing:
                return {"ok": True, "transaction": existing, "idempotent": True}
        raise HTTPException(status_code=409, detail="Transfer already in progress, try again")

    await db.users.update_one({"user_id": recipient["user_id"]}, {"$inc": {"astrans_balance": net}})
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


class VerifyResult:
    """Rezultatul verificarii unei plati: ok=True DOAR daca plata e reala si confirmata
    de furnizor (Google/Apple/Stripe), nu doar "token-ul are forma corecta". external_id
    e identificatorul UNIC al acelei plati (purchase token la Google, transaction_id la
    Apple, payment_intent la Stripe) - folosit in buy_astrans mai jos ca sa nu creditam
    Astrans de doua ori pentru aceeasi plata (retry de retea, token retrimis etc)."""

    def __init__(self, ok: bool, external_id: Optional[str] = None, detail: Optional[str] = None):
        self.ok = ok
        self.external_id = external_id
        self.detail = detail


class PaymentProvider:
    name: str = "base"

    async def verify(self, token: str, package: dict, user: dict) -> VerifyResult:
        raise NotImplementedError


class MockProvider(PaymentProvider):
    name = "mock"

    async def verify(self, token: str, package: dict, user: dict) -> VerifyResult:
        return VerifyResult(True, external_id=f"mock:{new_id()}")


class StripeProvider(PaymentProvider):
    name = "stripe"

    async def verify(self, token: str, package: dict, user: dict) -> VerifyResult:
        api_key = os.environ.get("STRIPE_SECRET_KEY")
        if not api_key or not token:
            return VerifyResult(False, detail="Missing Stripe payment intent")
        async with httpx.AsyncClient(timeout=10.0) as h:
            try:
                r = await h.get(f"https://api.stripe.com/v1/payment_intents/{token}", auth=(api_key, ""))
            except Exception as e:  # noqa: BLE001
                log.error("Stripe verify error: %s", e)
                return VerifyResult(False, detail="Stripe verification failed")
        if r.status_code != 200:
            return VerifyResult(False, detail="Stripe payment intent not found")
        data = r.json()
        ok = data.get("status") == "succeeded" and int(data.get("amount", 0)) >= int(package["price"] * 100)
        return VerifyResult(ok, external_id=token if ok else None, detail=None if ok else "Payment not completed")


# ---------- Google Play: verificare REALA prin Android Publisher API ----------
# Fluxul standard Google OAuth2 "Service Account" (JWT Bearer): semnam noi insine un JWT
# cu cheia privata din Service Account-ul descarcat din Google Cloud Console, il schimbam
# la Google pe un access_token, si cu el intrebam Android Publisher API daca tokenul de
# achizitie primit de la client e intr-adevar o plata REALA si confirmata. Nu folosim
# biblioteca google-auth (dependinta noua) - semnarea RS256 se face cu 'cryptography',
# deja in requirements.txt.
_google_token_cache: dict = {"token": None, "expires_at": 0.0}


def _rsa_sign_rs256(private_key_pem: str, data: bytes) -> bytes:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return key.sign(data, padding.PKCS1v15(), hashes.SHA256())


async def _google_access_token() -> Optional[str]:
    """Token OAuth2 pentru Android Publisher API. Cache in memorie ~55 minute (tokenul
    Google e valabil 60 minute) - nu cerem un token nou la fiecare achizitie verificata."""
    now = now_utc().timestamp()
    if _google_token_cache["token"] and _google_token_cache["expires_at"] > now:
        return _google_token_cache["token"]

    raw = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")
    if not raw:
        log.error("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON is not configured")
        return None
    try:
        sa = json.loads(raw)
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        claims = {
            "iss": sa["client_email"],
            "scope": "https://www.googleapis.com/auth/androidpublisher",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": int(now),
            "exp": int(now) + 3600,
        }
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
        signing_input = header + b"." + payload
        signature = base64.urlsafe_b64encode(_rsa_sign_rs256(sa["private_key"], signing_input)).rstrip(b"=")
        assertion = (signing_input + b"." + signature).decode()
    except Exception as e:  # noqa: BLE001
        log.error("Invalid GOOGLE_PLAY_SERVICE_ACCOUNT_JSON: %s", e)
        return None

    async with httpx.AsyncClient(timeout=10.0) as h:
        try:
            r = await h.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            })
        except Exception as e:  # noqa: BLE001
            log.error("Google OAuth token error: %s", e)
            return None
    if r.status_code != 200:
        log.error("Google OAuth token rejected: %s", r.text[:300])
        return None
    data = r.json()
    token = data.get("access_token")
    _google_token_cache["token"] = token
    _google_token_cache["expires_at"] = now + max(60, int(data.get("expires_in", 3300)) - 120)
    return token


class GooglePlayProvider(PaymentProvider):
    name = "google_play"

    async def verify(self, token: str, package: dict, user: dict) -> VerifyResult:
        if not token:
            return VerifyResult(False, detail="Missing purchase token")
        pkg_name = os.environ.get("GOOGLE_PLAY_PACKAGE_NAME")
        if not pkg_name:
            log.error("GOOGLE_PLAY_PACKAGE_NAME is not configured")
            return VerifyResult(False, detail="Google Play verification not configured")
        access_token = await _google_access_token()
        if not access_token:
            return VerifyResult(False, detail="Google Play verification unavailable")

        product_id = package["package_id"]  # SKU-ul din Play Console = package_id din astran_packages
        url = (
            f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
            f"{pkg_name}/purchases/products/{product_id}/tokens/{token}"
        )
        async with httpx.AsyncClient(timeout=10.0) as h:
            try:
                r = await h.get(url, headers={"Authorization": f"Bearer {access_token}"})
            except Exception as e:  # noqa: BLE001
                log.error("Google Play verify error: %s", e)
                return VerifyResult(False, detail="Google Play verification failed")
        if r.status_code != 200:
            return VerifyResult(False, detail=f"Google Play rejected purchase (status {r.status_code})")
        data = r.json()
        # purchaseState: 0 = achitata, 1 = anulata, 2 = in asteptare (ex: plata in numerar)
        if data.get("purchaseState") != 0:
            return VerifyResult(False, detail="Purchase not completed")
        return VerifyResult(True, external_id=token)


# ---------- Apple: verificare REALA prin App Store receipt validation ----------
class AppleProvider(PaymentProvider):
    name = "apple"

    async def verify(self, token: str, package: dict, user: dict) -> VerifyResult:
        if not token:
            return VerifyResult(False, detail="Missing receipt")
        shared_secret = os.environ.get("APPLE_SHARED_SECRET")
        if not shared_secret:
            log.error("APPLE_SHARED_SECRET is not configured")
            return VerifyResult(False, detail="Apple verification not configured")

        body = {"receipt-data": token, "password": shared_secret, "exclude-old-transactions": True}
        data = await self._call(body, "https://buy.itunes.apple.com/verifyReceipt")
        if data is None:
            return VerifyResult(False, detail="Apple verification failed")
        # 21007: chitanta de test (sandbox) trimisa din greseala la serverul de productie -
        # reincercam automat pe serverul de sandbox, exact cum recomanda documentatia Apple.
        if data.get("status") == 21007:
            data = await self._call(body, "https://sandbox.itunes.apple.com/verifyReceipt")
            if data is None:
                return VerifyResult(False, detail="Apple verification failed")

        if data.get("status") != 0:
            return VerifyResult(False, detail=f"Apple rejected receipt (status {data.get('status')})")

        bundle_id = os.environ.get("APPLE_BUNDLE_ID")
        receipt = data.get("receipt", {})
        if bundle_id and receipt.get("bundle_id") != bundle_id:
            return VerifyResult(False, detail="Receipt belongs to a different app")

        product_id = package["package_id"]  # product id-ul din App Store Connect = package_id
        in_app = data.get("latest_receipt_info") or receipt.get("in_app") or []
        match = next((it for it in in_app if it.get("product_id") == product_id), None)
        if not match:
            return VerifyResult(False, detail="Receipt does not contain this product")
        return VerifyResult(True, external_id=match.get("transaction_id"))

    async def _call(self, body: dict, url: str) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=10.0) as h:
            try:
                r = await h.post(url, json=body)
            except Exception as e:  # noqa: BLE001
                log.error("Apple verify error: %s", e)
                return None
        if r.status_code != 200:
            return None
        return r.json()


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
    # Mock-ul crediteaza Astrans fara nicio plata reala - util pentru dezvoltare, dar
    # PERICULOS daca ar ramane deschis in productie (oricine cu un cont ar putea apela
    # acest API direct, nu doar din aplicatie, si s-ar credita la nesfarsit gratis).
    # Activ DOAR daca serverul are explicit ALLOW_MOCK_PAYMENTS=1 (niciodata pe Render productie).
    if provider.name == "mock" and os.environ.get("ALLOW_MOCK_PAYMENTS") != "1":
        raise HTTPException(status_code=403, detail="Mock payments are disabled")

    result = await provider.verify(body.provider_token or "", package, current)
    if not result.ok:
        raise HTTPException(status_code=402, detail=result.detail or "Payment verification failed")

    if result.external_id:
        existing = await db.astran_ledger.find_one({"provider_ref": result.external_id}, {"_id": 0})
        if existing:
            return {"ok": True, "credited": 0, "transaction": existing, "idempotent": True}

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
        "provider_ref": result.external_id,
        "price": package["price"],
        "currency": cfg.get("currency", "RON"),
    }
    try:
        await db.astran_ledger.insert_one(tx)
    except Exception:  # noqa: BLE001
        await db.users.update_one({"user_id": current["user_id"]}, {"$inc": {"astrans_balance": -package["astrans"]}})
        existing = await db.astran_ledger.find_one({"provider_ref": result.external_id}, {"_id": 0})
        return {"ok": True, "credited": 0, "transaction": existing, "idempotent": True}
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

# Multiplayer live (WebSocket): /ws/games/{game_id}/instances/{instance_id} - vezi realtime.py.
# Montat direct pe `app`, NU pe `api` (fara prefixul /api) - WebSocket-urile sunt oricum o
# ruta separata de REST, nu are rost sa poarte acelasi prefix ca restul API-ului HTTP.
if make_realtime_router is not None:
    app.include_router(make_realtime_router(resolve_user_from_token, db))

# Shop de obiecte de joc (Modele + Scripturi): /api/shop/models, /api/shop/scripts, /api/shop/items/{id}...
if make_shop_router is not None:
    api.include_router(make_shop_router(get_current_user, db))

# Clothes Shop (haine/accesorii pentru Avatar Editor): /api/clothes/items, /api/clothes/slots...
if make_clothes_router is not None:
    api.include_router(make_clothes_router(get_current_user, db))

# Avatar Editor: /api/avatar/me, /api/avatar/slots
if make_avatar_router is not None:
    api.include_router(make_avatar_router(get_current_user, db))

# Setari de player: /api/settings/me (GET/PUT) - vezi astran_sandbox/user_settings_routes.py
if make_user_settings_router is not None:
    api.include_router(make_user_settings_router(get_current_user, db))

# GROUPS: /api/groups (create, page, membri, roluri, mute/ban, token privat, chat de grup)
if make_group_router is not None:
    api.include_router(make_group_router(get_current_user, db, _user_public))

# GAME COLLABORATION / TESTERS: /api/games/{id}/collaborators, /api/games/{id}/testers,
# /api/games/{id}/my-access
if make_game_permissions_router is not None:
    api.include_router(make_game_permissions_router(get_current_user, db, _user_public))

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)