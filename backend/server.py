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

# SECURE DELETE/RECOVERY: parola de securitate a proiectului (Argon2id), rate limiting,
# audit log si recuperare (email + WebAuthn biometric) - vezi project_security.py. NU e
# optional ca celelalte module de mai sus: daca lipseste, stergerea proiectelor ramane
# dezactivata in loc sa cada inapoi pe fluxul vechi, nesecurizat.
import astran_sandbox.project_security as psec


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
    # SECURITY: parola de securitate a proiectului - OBLIGATORIE la creare (vezi project_security.py).
    # Nu se stocheaza niciodata in clar: doar hash-ul Argon2id ajunge in baza de date (create_game mai jos).
    project_password: str = Field(min_length=1, max_length=200)
    project_password_confirm: str = Field(min_length=1, max_length=200)


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
    await db.games.create_index([("owner_id", 1), ("deleted", 1)])  # Cosul de gunoi: lista rapida a jocurilor sterse ale unui Owner
    await psec.create_security_indexes(db)
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
    doc.setdefault("deleted", False)
    doc.setdefault("deleted_at", None)
    doc["has_project_password"] = bool(doc.get("project_password_hash"))
    # SECURITY: hash-ul parolei de proiect nu paraseste niciodata serverul - ultima linie
    # de aparare, indiferent de proiectia folosita la query (vezi si queries de mai jos,
    # care deja il exclud explicit).
    doc.pop("project_password_hash", None)
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

    # SECURITY: parola de securitate a proiectului e obligatorie la creare - validata (lungime
    # 6-30, confirmare identica) si hash-uita cu Argon2id (salt unic, generat automat de
    # argon2-cffi). Niciodata stocata sau logata in clar.
    psec.validate_project_password_shape(body.project_password, body.project_password_confirm)
    project_password_hash = psec.hash_project_password(body.project_password)

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
        # SECURITY: vezi project_security.py - niciodata expus prin API (filtrat in _game_defaults).
        "project_password_hash": project_password_hash,
        "project_password_set_at": now_utc(),
        "deleted": False,
        "deleted_at": None,
        "deleted_by": None,
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
    if not g or g.get("deleted"):
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


class DeleteGameBody(BaseModel):
    project_password: str = Field(min_length=1, max_length=200)
    confirm: bool = False


class SetupProjectPasswordBody(BaseModel):
    account_password: str
    project_password: str = Field(min_length=1, max_length=200)
    project_password_confirm: str = Field(min_length=1, max_length=200)


class RecoveryStartBody(BaseModel):
    account_password: str


class RecoveryVerifyEmailBody(BaseModel):
    token_id: str
    code: str = Field(min_length=1, max_length=12)


class RecoveryConfirmBody(BaseModel):
    token_id: str


class PasswordResetConfirmBody(BaseModel):
    token_id: str
    new_project_password: str = Field(min_length=1, max_length=200)
    new_project_password_confirm: str = Field(min_length=1, max_length=200)


class WebAuthnVerifyBody(BaseModel):
    challenge_id: str
    credential: str  # JSON string produsa de navigator.credentials pe client


@api.delete("/games/{game_id}")
async def delete_game(game_id: str, body: DeleteGameBody, request: Request, current=Depends(get_current_user)):
    """SECURITY: stergerea unui proiect NU mai e imediata/ireversibila din acest endpoint -
    e un soft-delete in Cosul de gunoi, posibil DOAR pentru Owner, DOAR cu parola de
    securitate a proiectului, cu rate limiting si audit log (vezi project_security.py).

    Regula "owner-only" e verificata AICI, pe server, din campul owner_id salvat in baza de
    date la creare - rolul trimis de client (daca exista undeva in payload) nu e niciodata
    folosit ca dovada de autorizare."""
    ip = request.client.host if request.client else None
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or g.get("deleted"):
        raise HTTPException(status_code=404, detail="Game not found")

    if g["owner_id"] != current["user_id"] and not current.get("is_platform_admin"):
        await psec.log_security_event(
            db, action="project_delete", result="denied", user_id=current["user_id"],
            game_id=game_id, detail="not_owner", ip=ip,
        )
        # Mesaj deliberat generic - nu confirmam/infirmam existenta sau detalii ale jocului
        # unui non-owner, indiferent daca acesta cunoaste sau nu parola proiectului.
        raise HTTPException(status_code=403, detail="Doar Owner-ul poate sterge acest proiect")

    if not body.confirm:
        raise HTTPException(status_code=400, detail="Confirmarea actiunii este obligatorie")

    await psec.ensure_rate_limit_or_raise(db, user_id=current["user_id"], scope_id=game_id, purpose="project_delete")

    if not g.get("project_password_hash"):
        # Proiect vechi, creat inainte de acest sistem - nu inventam o parola si nu blocam
        # definitiv, dar nici nu permitem stergerea pana nu e configurata una.
        raise HTTPException(
            status_code=409,
            detail={"code": "PASSWORD_SETUP_REQUIRED", "message": "Configureaza mai intai o parola de securitate pentru acest proiect"},
        )

    if not psec.verify_project_password(body.project_password, g["project_password_hash"]):
        await psec.record_failed_attempt(db, user_id=current["user_id"], scope_id=game_id, purpose="project_delete")
        await psec.log_security_event(
            db, action="project_delete", result="failed", user_id=current["user_id"],
            game_id=game_id, detail="bad_project_password", ip=ip,
        )
        raise HTTPException(status_code=401, detail="Parola de securitate a proiectului este incorecta")

    await psec.reset_attempts(db, user_id=current["user_id"], scope_id=game_id, purpose="project_delete")
