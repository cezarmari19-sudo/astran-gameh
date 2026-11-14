"""
Astran Game Backend - Phase 1 MVP
Cross-platform 3D UGC gaming platform.
"""
from __future__ import annotations

import os
import re
import uuid
import json
import math
import base64
import asyncio
import hashlib
import logging
import secrets
from fractions import Fraction
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
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, Field, EmailStr
from dotenv import load_dotenv

from astran_sandbox.ratelimit import SlidingWindowLimiter  # folosit la nivel de modul mai jos (limitatoare)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "astran_game")
_DEFAULT_DEV_SECRET = "astran-dev-secret-change-in-prod"
JWT_SECRET = os.environ.get("JWT_SECRET", _DEFAULT_DEV_SECRET)
# SECURITY: un secret lipsa/implicit permite oricui sa falsifice token-uri pentru orice cont.
# In productie (ASTRAN_ENV=production sau Render) serverul REFUZA sa porneasca fara un secret real.
_IS_PRODUCTION = os.environ.get("ASTRAN_ENV", "").lower() == "production" or bool(os.environ.get("RENDER"))
if JWT_SECRET == _DEFAULT_DEV_SECRET or len(JWT_SECRET) < 24:
    if _IS_PRODUCTION:
        raise RuntimeError("JWT_SECRET missing/weak: set a random value of at least 24 characters")
    logging.getLogger("astran").warning("Using an insecure development JWT_SECRET - never do this in production")
JWT_ALG = "HS256"
# make_jwt(issued_at=marker) pune iat cu pana la 1s in viitor (dupa reset de credentiale); PyJWT respinge
# iat din viitor fara toleranta.
JWT_IAT_LEEWAY = 2
JWT_TTL_DAYS = 7

EMERGENT_AUTH_URL = "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app = FastAPI(title="Astran Game API")

# Plafon pentru corpul cererilor: nu exista altul (uvicorn nu pune limita), iar un POST/PUT de sute de MB
# ajungea in memoria procesului inainte de orice validare Pydantic. Cele mai mari corpuri legitime
# (proiect cu fisiere, model 3D, poze base64) sunt de ordinul MB. Limita se aplica pe Content-Length;
# corpurile "chunked" fara Content-Length nu sunt acoperite aici (le limiteaza reverse proxy-ul).
MAX_REQUEST_BYTES = int(os.environ.get("ASTRAN_MAX_REQUEST_BYTES", str(12 * 1024 * 1024)))


@app.middleware("http")
async def _limit_request_size(request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_REQUEST_BYTES:
        from starlette.responses import JSONResponse
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("astran")


class _RedactTokenFilter(logging.Filter):
    """WebSocket-ul trimite tokenul ca ?token=... (clientul React Native nu poate seta header-e la
    handshake). Access log-ul uvicorn scrie URL-ul complet, deci un token valid (7 zile) ajungea in
    loguri. Il mascam in orice inregistrare de log care contine `token=`."""
    _rx = re.compile(r"([?&]token=)[^&\s\"']+")

    def _clean(self, v):
        return self._rx.sub(r"\1[REDACTED]", v) if isinstance(v, str) else v

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._clean(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(self._clean(a) for a in record.args)
        except Exception:  # noqa: BLE001 - logging-ul nu trebuie sa pice niciodata din cauza filtrului
            pass
        return True


_redact = _RedactTokenFilter()
for _name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "websockets", "websockets.server", "astran"):
    _lg = logging.getLogger(_name)
    _lg.addFilter(_redact)
    for _h in _lg.handlers:
        _h.addFilter(_redact)

# Rate limiting (in memorie, per proces) - vezi astran_sandbox/ratelimit.py
_register_limiter = SlidingWindowLimiter(10, 3600)    # conturi noi / ora / IP
_login_ip_limiter = SlidingWindowLimiter(30, 600)     # incercari de login / 10 min / IP
_report_limiter = SlidingWindowLimiter(10, 3600)      # rapoarte / ora / utilizator
_friend_req_limiter = SlidingWindowLimiter(30, 3600)    # cereri de prietenie / ora / utilizator
_user_search_limiter = SlidingWindowLimiter(60, 60)     # cautari de utilizatori / minut / utilizator
MAX_PENDING_FRIEND_REQUESTS = 100
_BLOCK_ADULT_TO_MINOR_REQUESTS = os.environ.get("ASTRAN_BLOCK_ADULT_TO_MINOR_FRIEND_REQUESTS", "0") == "1"
_transfer_limiter = SlidingWindowLimiter(30, 3600)    # transferuri de Astrans / ora / utilizator


def _client_ip(request: Request) -> str:
    """IP-ul clientului. In spatele unui proxy (Render) request.client e IP-ul proxy-ului; atunci
    folosim X-Forwarded-For, dar DOAR intrarea adaugata de proxy-ul nostru de incredere (a N-a de la
    coada, N = ASTRAN_TRUSTED_PROXY_HOPS, implicit 1) - prima intrare o poate falsifica clientul."""
    if os.environ.get("ASTRAN_TRUST_PROXY") == "1" or os.environ.get("RENDER"):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            try:
                hops = max(1, int(os.environ.get("ASTRAN_TRUSTED_PROXY_HOPS", "1")))
            except ValueError:
                hops = 1
            if parts:
                return parts[-hops] if len(parts) >= hops else parts[0]
    return request.client.host if request.client else "unknown"


def _clean_image_or_400(value, **kw):
    try:
        return clean_image_ref(value, **kw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _clean_scene_or_400(scene):
    try:
        return clean_scene(scene)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Singurele hosturi externe acceptate pentru avatar: pozele de profil Google
_AVATAR_HTTPS_HOSTS = ("googleusercontent.com",)


def _too_many(wait: int) -> HTTPException:
    return HTTPException(status_code=429, detail="Prea multe cereri. Incearca din nou mai tarziu.",
                         headers={"Retry-After": str(wait)})

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

# CHAT IN JOC: administrarea regulilor de filtrare + mute (platform admin) - vezi
# astran_sandbox/chat_admin_routes.py. Chatul propriu-zis traieste in realtime.py (WebSocket).
try:
    from astran_sandbox.chat_admin_routes import make_chat_admin_router
except Exception as exc:  # noqa: BLE001
    make_chat_admin_router = None
    log.warning("Chat admin disabled: %s", exc)

# MESAGERIE PRIVATA intre prieteni (sistem separat de chatul din joc): /api/dm/..., WebSocket /ws/dm
# - vezi astran_sandbox/dm_core.py (logica), dm_store.py (MongoDB), dm_routes.py (rute).
try:
    from astran_sandbox.dm_routes import make_dm_routers
    from astran_sandbox.dm_store import ensure_indexes as dm_ensure_indexes
except Exception as exc:  # noqa: BLE001
    make_dm_routers = None
    dm_ensure_indexes = None
    log.warning("Private messaging disabled: %s", exc)

# GAME COLLABORATION / TESTERS: acces de Editor (granular, pe fisiere/foldere) si Tester
# pentru un joc - complet separat de Groups (vezi astran_sandbox/game_permissions.py).
try:
    from astran_sandbox.game_permissions import get_game_access, make_game_permissions_router, path_allowed
except Exception as exc:  # noqa: BLE001
    get_game_access = None
    make_game_permissions_router = None
    path_allowed = None
    log.warning("Game collaboration/testers disabled: %s", exc)

# SECURE DELETE/RECOVERY: parola de securitate a proiectului (Argon2id), rate limiting,
# audit log si recuperare (email + WebAuthn biometric) - vezi project_security.py. NU e
# optional ca celelalte module de mai sus: daca lipseste, stergerea proiectelor ramane
# dezactivata in loc sa cada inapoi pe fluxul vechi, nesecurizat.
import astran_sandbox.project_security as psec
# Mutari de Astrans sigure la cadere (jurnal + pasi idempotenti) - vezi astran_sandbox/money.py.
# NU e optional: fara el, platile/transferurile ar reveni la varianta cu pasi neatomici.
from astran_sandbox import money
from astran_sandbox import payment_reversal as preversal
from astran_sandbox import email_verification as emailver
from astran_sandbox.media_validation import clean_image_ref
from astran_sandbox.scene_validation import clean_scene


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


async def hash_pw_async(pw: str) -> str:
    # bcrypt/argon2 sunt CPU-bound (zeci/sute de ms): in handler async ar bloca TOT event loop-ul.
    return await asyncio.to_thread(hash_pw, pw)


async def verify_pw_async(pw: str, pw_hash: str) -> bool:
    return await asyncio.to_thread(verify_pw, pw, pw_hash)


PLAY_COUNT_COOLDOWN = timedelta(minutes=30)


def _play_counts(prev: Optional[dict], now: datetime) -> bool:
    """True daca acest play se numara in total_plays: prima data pentru (user, joc) sau dupa cooldown."""
    if not prev:
        return True
    last = prev.get("played_at")
    if not isinstance(last, datetime):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= PLAY_COUNT_COOLDOWN


WELCOME_BONUS = 100


async def _claim_welcome_bonus(user_id: str) -> bool:
    """Acorda bonusul de bun-venit O SINGURA DATA, dupa verificarea emailului. Idempotent la
    cadere de proces: randul de ledger are tx_id determinist (unic), iar `welcome_bonus_pending`
    se consuma atomic, deci nici retry-ul, nici doua verificari paralele nu dubleaza bonusul."""
    if not await db.users.find_one({"user_id": user_id, "welcome_bonus_pending": True}, {"_id": 1}):
        return False
    try:
        await db.astran_ledger.insert_one({
            "tx_id": f"txwb_{user_id}", "user_id": user_id, "counterparty_id": "system",
            "type": "welcome_bonus", "amount": WELCOME_BONUS, "fee": 0, "net": WELCOME_BONUS,
            "status": "completed", "timestamp": now_utc(), "reference": "email_verified",
        })
    except DuplicateKeyError:
        pass
    res = await db.users.update_one({"user_id": user_id, "welcome_bonus_pending": True},
                                    {"$set": {"welcome_bonus_pending": False},
                                     "$inc": {"astrans_balance": WELCOME_BONUS}})
    return res.matched_count == 1


def _jwt_predates_reset(payload: dict, user: Optional[dict]) -> bool:
    """True daca JWT-ul a fost emis ÎNAINTE de `sessions_valid_after` (resetare de credentiale)."""
    after = (user or {}).get("sessions_valid_after")
    if not isinstance(after, datetime):
        return False
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    iat = payload.get("iat")
    return not isinstance(iat, (int, float)) or iat < int(after.timestamp())


def _credentials_reset_marker() -> datetime:
    """Valoarea pentru `sessions_valid_after`: cu 1s IN VIITOR. `iat` are rezolutie de o secunda, deci
    orice JWT emis pana acum (inclusiv in aceeasi secunda) are iat < marker si cade; un JWT emis dupa
    resetare (iat >= marker, vezi make_jwt(issued_at=...)) trece."""
    return now_utc() + timedelta(seconds=1)


def make_jwt(user_id: str, issued_at: Optional[int] = None) -> str:
    payload = {
        "sub": user_id,
        "iat": issued_at if issued_at is not None else int(now_utc().timestamp()),
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
    has_password: bool = True   # False = cont Google (fara parola): fluxurile de securitate nu cer parola contului
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
    category: str = Field(default="adventure", max_length=40)
    # GENRE/SUBGENRE: "category" de mai sus e deja folosit ca Genre (lista fixa existenta in
    # UI: adventure/shooter/...); subgenre e liber, optional, NU inventa o valoare daca lipseste.
    subgenre: Optional[str] = Field(default=None, max_length=40)
    allow_join_via_friends: bool = True
    scene: Optional[dict] = None
    script: str = Field(default="", max_length=20000)
    max_players: int = Field(default=MAX_PLAYERS_DEFAULT, ge=MAX_PLAYERS_MIN, le=MAX_PLAYERS_MAX)
    # daca setat, jucatorii intra in joc cu acest model (din Studio) in loc de avatarul lor personal
    player_character_model_id: Optional[str] = Field(default=None, max_length=64)
    # de unde vine modelul: "shop_model" (Shop) sau "studio_model" (Studio). Optional: daca lipseste,
    # serverul il deduce cautand id-ul in ambele colectii.
    player_character_source: Optional[Literal["shop_model", "studio_model"]] = None
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
    category: Optional[str] = Field(default=None, max_length=40)
    subgenre: Optional[str] = Field(default=None, max_length=40)
    scene: Optional[dict] = None
    script: Optional[str] = Field(default=None, max_length=20000)
    max_players: Optional[int] = Field(default=None, ge=MAX_PLAYERS_MIN, le=MAX_PLAYERS_MAX)
    player_character_model_id: Optional[str] = Field(default=None, max_length=64)
    player_character_source: Optional[Literal["shop_model", "studio_model"]] = None
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
    player_character_source: Optional[str] = None
    # GROUPS
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    group_logo_url: Optional[str] = None


class FriendRequestBody(BaseModel):
    target_user_id: str


class TransferBody(BaseModel):
    recipient_user_id: str
    amount: int = Field(gt=0, le=10**12)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class BuyBody(BaseModel):
    package_id: str = Field(min_length=1, max_length=64)
    provider: Literal["stripe", "google_play", "apple", "mock"] = "mock"
    provider_token: Optional[str] = None


REPORT_REASON_MAX = 100
REPORT_DETAILS_MAX = 2000


class ReportBody(BaseModel):
    target_type: Literal["user", "game", "message"]
    target_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=REPORT_REASON_MAX)
    details: Optional[str] = Field(default=None, max_length=REPORT_DETAILS_MAX)


class JoinInstanceBody(BaseModel):
    instance_id: Optional[str] = None  # daca setat, incearca sa intre in acea instanta anume (ex: link de la un prieten)


class HeartbeatBody(BaseModel):
    instance_id: str


def _token_hash(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def _strip_secret(user: Optional[dict]) -> Optional[dict]:
    """Scoate hash-ul parolei din documentul userului, dar pastreaza informatia (booleana) ca
    exista o parola: conturile Google (fara parola) au nevoie de alt flux de re-autentificare."""
    if user is not None:
        user["has_password"] = bool(user.get("password_hash"))
        user.pop("password_hash", None)
    return user


async def resolve_user_from_token(token: str) -> Optional[dict]:
    """Logica reala de autentificare (sesiune sau JWT), extrasa din get_current_user ca sa
    poata fi refolosita si de WebSocket-ul din realtime.py - acolo nu exista header
    "Authorization" (clientul WebSocket din React Native nu poate seta header-e custom la
    handshake), deci token-ul vine ca query param si trebuie decodat manual, fara sa
    duplicam regulile de validare intr-un loc separat."""
    if await db.revoked_tokens.find_one({"token_hash": _token_hash(token)}, {"_id": 1}):
        return None
    sess = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if sess:
        exp = sess.get("expires_at")
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp and exp < now_utc():
            return None
        return _strip_secret(await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0}))

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG], leeway=JWT_IAT_LEEWAY)
        u = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
        if _jwt_predates_reset(payload, u):
            return None
        return _strip_secret(u)
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
    # Verifica la pornire daca jail-ul OS (bubblewrap) al sandbox-ului Luau chiar functioneaza si
    # scrie in log un motiv clar daca nu. NU schimba nicio politica: rularea ramane fail-closed.
    try:
        from astran_sandbox import runner as _sandbox_runner
        if _sandbox_runner.find_binary() is not None:
            await _sandbox_runner._check_bwrap()
    except Exception:  # noqa: BLE001
        logging.getLogger("astran.sandbox").warning("sandbox startup check failed", exc_info=True)
    if maybe_connect_redis is not None:
        await maybe_connect_redis()
    await db.users.create_index("user_id", unique=True)
    await db.users.create_index("email", unique=True, sparse=True)
    await db.users.create_index("username", unique=True)
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.revoked_tokens.create_index("token_hash", unique=True)
    await db.revoked_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.games.create_index("game_id", unique=True)
    await db.games.create_index([("owner_id", 1), ("created_at", -1)])
    await db.games.create_index([("age_category", 1), ("is_public", 1), ("total_plays", -1)])
    await db.games.create_index("group_id")  # GROUPS: lookup rapid al jocurilor unui Group
    await db.games.create_index([("owner_id", 1), ("deleted", 1)])  # Cosul de gunoi: lista rapida a jocurilor sterse ale unui Owner
    await psec.create_security_indexes(db)
    await db.friendships.create_index([("user_a", 1), ("user_b", 1)], unique=True)
    await db.friend_requests.create_index([("from_id", 1), ("to_id", 1)], unique=True)
    await db.blocks.create_index([("blocker_id", 1), ("blocked_id", 1)], unique=True)
    if dm_ensure_indexes is not None:
        try:
            await dm_ensure_indexes(db)
        except Exception:  # noqa: BLE001 - mesageria nu trebuie sa impiedice pornirea serverului
            log.warning("DM indexes could not be created", exc_info=True)
    await db.astran_ledger.create_index("tx_id", unique=True)
    await db.astran_ledger.create_index("idempotency_key", unique=True, sparse=True)
    # O singura creditare per plata REALA (purchase token Google / transaction_id Apple /
    # payment_intent Stripe) - vezi VerifyResult.external_id si buy_astrans mai jos.
    await db.astran_ledger.create_index("provider_ref", unique=True, sparse=True)
    await db.astran_ledger.create_index([("user_id", 1), ("timestamp", -1)])
    # Jurnalul mutarilor de Astrans + task-ul care duce la capat operatiile ramase la jumatate
    # (cadere de proces intre debit si credit/ledger) - vezi astran_sandbox/money.py.
    await money.ensure_indexes(db)
    app.state.money_recovery_task = asyncio.create_task(money.recovery_loop(db))
    await db.payment_reversed_refs.create_index("ref", unique=True)
    await db.email_verifications.create_index("user_id", unique=True)
    await db.password_resets.create_index("user_id", unique=True)
    await db.password_resets.create_index("expires_at", expireAfterSeconds=0)
    await db.email_verifications.create_index("expires_at", expireAfterSeconds=0)
    app.state.google_voided_task = asyncio.create_task(_google_voided_loop())
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

    # SECURITY: contul demo (platform owner/admin) NU mai are o parola cunoscuta in cod.
    # Se creeaza DOAR daca ASTRAN_SEED_DEMO_USER=1 si ASTRAN_DEMO_PASSWORD e setata. In rest,
    # daca exista deja din versiuni vechi, i se dezactiveaza autentificarea cu parola.
    demo_owner_id = "user_astran_demo"
    demo_pw = os.environ.get("ASTRAN_DEMO_PASSWORD")
    if os.environ.get("ASTRAN_SEED_DEMO_USER") == "1" and demo_pw:
        if not await db.users.find_one({"user_id": demo_owner_id}, {"_id": 0}):
            await db.users.insert_one({
                "user_id": demo_owner_id,
                "email": "demo@astran.game",
                "username": "AstranStudio",
                "display_name": "Astran Studio",
                "password_hash": await hash_pw_async(demo_pw),
                "avatar_url": None,
                "age_category": "adult_18",
                "astrans_balance": 10000,
                "language": "ro",
                "is_platform_owner": True,
                "is_platform_admin": True,
                "created_at": now_utc(),
            })
    else:
        # Demo-ul NU e activ: ii dezactivam parola si curatam jocurile ramase din versiuni vechi.
        await db.users.update_one({"user_id": demo_owner_id}, {"$set": {"password_hash": None}})
        # (inainte stergerea rula la FIECARE pornire, inclusiv cand contul demo era creat
        # intentionat cu ASTRAN_SEED_DEMO_USER=1 - jocurile lui dispareau la fiecare restart)
        await db.games.delete_many({"owner_id": demo_owner_id})
    # Ledger: campurile sparse-unique nu trebuie sa existe cu valoarea null (null SE indexeaza).
    await db.astran_ledger.update_many({"idempotency_key": {"$type": "null"}}, {"$unset": {"idempotency_key": ""}})
    await db.astran_ledger.update_many({"provider_ref": {"$type": "null"}}, {"$unset": {"provider_ref": ""}})
    # Varsta: conturile cu parola si-au declarat categoria la inregistrare -> o blocam (nu se mai
    # poate ridica la 18+ din API). Conturile Google fara marcaj mai pot face o singura alegere
    # (ecranul de onboarding), apoi se blocheaza si ele. Vezi update_me.
    await db.users.update_many(
        {"age_locked": {"$exists": False}, "password_hash": {"$nin": [None]}},
        {"$set": {"age_locked": True}},
    )
    log.info("Astran API ready. DB=%s", DB_NAME)


@app.on_event("shutdown")
async def shutdown():
    task = getattr(app.state, "money_recovery_task", None)
    if task is not None:
        task.cancel()
    gtask = getattr(app.state, "google_voided_task", None)
    if gtask is not None:
        gtask.cancel()
    client.close()


def _user_public(user: dict) -> dict:
    out = _user_public_base(user)
    # has_password apare DOAR cand stim sigur (documentul propriu al userului, cu hash sau cu marcajul
    # din _strip_secret): pentru alti utilizatori nu il expunem si nu raportam gresit "false".
    if "has_password" in user:
        out["has_password"] = bool(user["has_password"])
    elif "password_hash" in user:
        out["has_password"] = bool(user["password_hash"])
    out["email_verified"] = bool(user.get("email_verified"))
    return out


def _user_public_base(user: dict) -> dict:
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
async def register(body: RegisterBody, request: Request):
    wait = _register_limiter.hit(_client_ip(request))
    if wait is not None:
        raise _too_many(wait)
    email = body.email.strip().lower()
    ci = lambda v: {"$regex": f"^{re.escape(v)}$", "$options": "i"}  # noqa: E731
    existing = await db.users.find_one({"$or": [{"email": ci(email)}, {"username": ci(body.username)}]}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail="Email or username already exists")
    user_id = new_id("user_")
    doc = {
        "user_id": user_id,
        "email": email,
        "username": body.username,
        "display_name": body.display_name or body.username,
        "password_hash": await hash_pw_async(body.password),
        "avatar_url": None,
        "age_category": body.age_category,
        # Categoria de varsta declarata la inregistrare e FINALA pentru API (nu se poate ridica la
        # 18+ ulterior). NOTA: e o declaratie, nu o verificare - vezi update_me.
        "age_locked": True,
        # Emailul nu e verificat la inregistrare. Cu SMTP configurat, bonusul de bun-venit se acorda
        # DOAR dupa verificare (altfel: conturi-farm cu adrese inventate = Astrans gratis). Fara
        # SMTP (dev) verificarea nu se poate face, deci ramane comportamentul vechi.
        "email_verified": False,
        "astrans_balance": 0 if psec.email_is_configured() else WELCOME_BONUS,
        "welcome_bonus_pending": bool(psec.email_is_configured()),
        "language": body.language,
        "is_platform_owner": False,
        "is_platform_admin": False,
        "created_at": now_utc(),
    }
    try:
        await db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email or username already exists")
    if not psec.email_is_configured():
        await db.astran_ledger.insert_one({
            "tx_id": new_id("tx_"),
            "user_id": user_id,
            "counterparty_id": "system",
            "type": "welcome_bonus",
            "amount": WELCOME_BONUS,
            "fee": 0,
            "net": WELCOME_BONUS,
            "status": "completed",
            "timestamp": now_utc(),
            "reference": "signup",
        })
    token = make_jwt(user_id)
    return {"token": token, "session_token": token, "user": _user_public(doc)}


@api.post("/auth/login")
async def login(body: LoginBody, request: Request):
    ip = _client_ip(request)
    wait = _login_ip_limiter.hit(ip)
    if wait is not None:
        raise _too_many(wait)
    # Dupa 5 incercari ESUATE pentru aceeasi pereche (IP, email) -> blocare 15 min (in MongoDB).
    # Perechea (nu doar emailul) evita ca un atacator sa poata incuia contul altcuiva.
    scope = f"{ip}|{body.email.strip().lower()}"[:200]
    await psec.ensure_rate_limit_or_raise(db, user_id="login", scope_id=scope, purpose="login")
    # conturile noi au email-ul in litere mici; cele vechi pot avea litere mari - incercam ambele
    user = await db.users.find_one({"email": body.email})
    if not user and body.email != body.email.lower():
        user = await db.users.find_one({"email": body.email.lower()})
    if not user or not user.get("password_hash") or not await verify_pw_async(body.password, user["password_hash"]):
        await psec.record_failed_attempt(db, user_id="login", scope_id=scope, purpose="login")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    await psec.reset_attempts(db, user_id="login", scope_id=scope, purpose="login")
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
    try:
        picture = clean_image_ref(data.get("picture"), https_hosts=_AVATAR_HTTPS_HOSTS)
    except ValueError:
        picture = None   # poza nevalida de la broker nu se stocheaza (contul se creeaza oricum)
    session_token = data.get("session_token") or secrets.token_urlsafe(32)

    # case-insensitive, ca in /auth/register (emailul se salveaza cu litere mici acolo): fara asta,
    # "User@Gmail.com" de la Google nu gasea contul "user@gmail.com" si crea un cont duplicat.
    existing = await db.users.find_one({"email": {"$regex": f"^{re.escape(email.strip())}$", "$options": "i"}})
    password_login_reset = False
    if existing:
        user_id = existing["user_id"]
        patch = {"display_name": existing.get("display_name") or name}
        # poza Google se pune doar daca userul nu are una si nu a sters-o explicit (update_me)
        if not existing.get("avatar_url") and not existing.get("avatar_cleared"):
            patch["avatar_url"] = picture
        if not existing.get("email_verified"):
            # PRE-HIJACKING: emailul acestui cont nu a fost niciodata verificat, deci cine l-a
            # inregistrat cu parola a putut fi ORICINE (nu neaparat proprietarul adresei). Google tocmai
            # a dovedit ca utilizatorul curent detine adresa => contul ii apartine lui; orice parola
            # setata inainte de verificare este stearsa, iar sesiunile / JWT-urile vechi sunt invalidate.
            patch["email_verified"] = True
            patch["email_verified_at"] = now_utc()
            if existing.get("password_hash"):
                patch["password_hash"] = None
                patch["sessions_valid_after"] = _credentials_reset_marker()
                password_login_reset = True
        await db.users.update_one({"user_id": user_id}, {"$set": patch})
        if password_login_reset:
            await db.user_sessions.delete_many({"user_id": user_id})
        await _claim_welcome_bonus(user_id)
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
            "email_verified": True,   # emailul vine de la Google (proprietatea adresei e dovedita)
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
    resp_body = {"session_token": session_token, "token": session_token, "user": _user_public(user)}
    if password_login_reset:
        # clientul poate informa utilizatorul: parola veche nu mai e valida (autentificare doar Google)
        resp_body["password_login_reset"] = True
    return resp_body


class EmailCodeBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)


_email_send_limiter = SlidingWindowLimiter(5, 3600)   # coduri de verificare trimise / ora / utilizator


@api.post("/auth/email/send-code")
async def email_send_code(current=Depends(get_current_user)):
    """Trimite pe emailul contului un cod de verificare (15 min, 5 incercari, retrimitere la 60s)."""
    if current.get("email_verified"):
        return {"already_verified": True}
    if not psec.email_is_configured():
        raise HTTPException(status_code=503, detail="Email service is not configured")
    wait = _email_send_limiter.hit(current["user_id"])
    if wait is not None:
        raise _too_many(wait)
    try:
        code = await emailver.issue(db, current["user_id"], current["email"])
    except emailver.VerificationError as e:
        raise HTTPException(status_code=e.status, detail=e.detail,
                            headers={"Retry-After": str(e.retry_after)} if e.retry_after else None)
    sent = await asyncio.to_thread(
        psec.send_plain_email, current["email"], "Verifica emailul - Astran Game",
        f"Codul tau de verificare este: {code}\n\nExpira in {emailver.CODE_TTL_MINUTES} minute si poate fi "
        "folosit o singura data. Daca nu ai creat tu un cont Astran Game, ignora acest email.")
    if not sent:
        raise HTTPException(status_code=502, detail="Could not send the verification email")
    return {"sent": True, "expires_in_minutes": emailver.CODE_TTL_MINUTES}


@api.post("/auth/email/verify")
async def email_verify(body: EmailCodeBody, current=Depends(get_current_user)):
    if current.get("email_verified"):
        return {"user": _user_public(current)}
    try:
        await emailver.verify(db, current["user_id"], current["email"], body.code)
    except emailver.VerificationError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    await db.users.update_one({"user_id": current["user_id"]},
                              {"$set": {"email_verified": True, "email_verified_at": now_utc()}})
    await _claim_welcome_bonus(current["user_id"])
    user = await db.users.find_one({"user_id": current["user_id"]}, {"_id": 0})
    return {"user": _user_public(_strip_secret(user))}


# ---------------- parola contului: recuperare (email) si schimbare ----------------

class ForgotPasswordBody(BaseModel):
    email: EmailStr


class ResetPasswordBody(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=6, max_length=128)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


_PW_RESET_COLLECTION = "password_resets"
_forgot_ip_limiter = SlidingWindowLimiter(5, 3600)        # cereri "am uitat parola" / ora / IP
_pw_change_limiter = SlidingWindowLimiter(10, 3600)       # incercari schimbare parola / ora / utilizator


async def _send_reset_email(email: str, code: str) -> None:
    await asyncio.to_thread(
        psec.send_plain_email, email, "Resetare parola - Astran Game",
        f"Codul tau pentru resetarea parolei este: {code}\n\nExpira in {emailver.CODE_TTL_MINUTES} minute si "
        "poate fi folosit o singura data. Daca nu ai cerut tu resetarea, ignora acest email: parola ta ramane "
        "neschimbata.")


@api.post("/auth/password/forgot")
async def password_forgot(body: ForgotPasswordBody, request: Request):
    """Trimite un cod de resetare DOAR pe un email verificat. Raspunsul e IDENTIC indiferent daca contul
    exista (fara enumerare de conturi); trimiterea ruleaza in fundal ca sa nu se vada nici din timp."""
    wait = _forgot_ip_limiter.hit(_client_ip(request))
    if wait is not None:
        raise _too_many(wait)
    if not psec.email_is_configured():
        raise HTTPException(status_code=503, detail="Email service is not configured")
    email = body.email.strip().lower()
    user = await db.users.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
                                   {"_id": 0, "user_id": 1, "email": 1, "email_verified": 1})
    if user and user.get("email_verified"):
        try:
            code = await emailver.issue(db, user["user_id"], user["email"], collection=_PW_RESET_COLLECTION)
        except emailver.VerificationError:
            code = None   # cooldown: raspundem la fel, fara sa trimitem inca un cod
        if code:
            asyncio.create_task(_send_reset_email(user["email"], code))
    return {"ok": True, "message": "If the account exists and its email is verified, a code was sent."}


@api.post("/auth/password/reset")
async def password_reset(body: ResetPasswordBody, request: Request):
    wait = _forgot_ip_limiter.hit("reset:" + _client_ip(request))
    if wait is not None:
        raise _too_many(wait)
    email = body.email.strip().lower()
    user = await db.users.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}},
                                   {"_id": 0, "user_id": 1, "email": 1, "email_verified": 1})
    generic = HTTPException(status_code=400, detail="Invalid or expired code")
    if not user or not user.get("email_verified"):
        raise generic
    try:
        await emailver.verify(db, user["user_id"], user["email"], body.code, collection=_PW_RESET_COLLECTION)
    except emailver.VerificationError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)
    await db.users.update_one({"user_id": user["user_id"]}, {"$set": {
        "password_hash": await hash_pw_async(body.new_password),
        "sessions_valid_after": _credentials_reset_marker(),
    }})
    await db.user_sessions.delete_many({"user_id": user["user_id"]})   # toate sesiunile vechi cad
    return {"ok": True}


@api.post("/auth/password/change")
async def password_change(body: ChangePasswordBody, request: Request, current=Depends(get_current_user)):
    wait = _pw_change_limiter.hit(current["user_id"])
    if wait is not None:
        raise _too_many(wait)
    doc = await db.users.find_one({"user_id": current["user_id"]}, {"_id": 0, "password_hash": 1})
    if not doc or not doc.get("password_hash"):
        raise HTTPException(status_code=409, detail="This account has no password; use password reset by email")
    if not await verify_pw_async(body.current_password, doc["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    marker = _credentials_reset_marker()
    await db.users.update_one({"user_id": current["user_id"]}, {"$set": {
        "password_hash": await hash_pw_async(body.new_password), "sessions_valid_after": marker}})
    await db.user_sessions.delete_many({"user_id": current["user_id"]})
    # token NOU, emis exact la marker: cel folosit pentru aceasta cerere (si orice altul vechi) nu mai e valid
    token = make_jwt(current["user_id"], issued_at=int(marker.timestamp()))
    return {"ok": True, "token": token, "session_token": token}


@api.get("/auth/me")
async def me(current=Depends(get_current_user)):
    return {"user": _user_public(current)}


@api.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        await db.user_sessions.delete_one({"session_token": token})
        # JWT-urile nu au stare pe server: le revocam explicit (pana la expirarea lor naturala).
        # Doar token-uri CU semnatura valida: altfel oricine ar putea umple colectia cu
        # siruri arbitrare. Un al doilea logout cu acelasi token nu mai da 500 (index unic).
        try:
            jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG], leeway=JWT_IAT_LEEWAY)
        except jwt.PyJWTError:
            return {"ok": True}
        try:
            await db.revoked_tokens.insert_one({
                "token_hash": _token_hash(token),
                "expires_at": now_utc() + timedelta(days=JWT_TTL_DAYS + 1),
            })
        except DuplicateKeyError:
            pass
    return {"ok": True}


@api.patch("/users/me")
async def update_me(body: UpdateProfileBody, current=Depends(get_current_user)):
    provided = body.dict(exclude_unset=True)
    updates = {k: v for k, v in provided.items() if v is not None}
    # avatar_url: null / "" trimis explicit = STERGE poza (inainte None era ignorat, deci poza
    # nu se putea elimina niciodata). La celelalte campuri None inseamna "nu schimba".
    if "avatar_url" in provided and not provided["avatar_url"]:
        updates["avatar_url"] = None
        updates["avatar_cleared"] = True
    if "age_category" in updates:
        new_age = updates.pop("age_category")
        cur_age = current.get("age_category", "under_18")
        # SECURITY: un minor nu isi poate declara singur 18+ dupa ce categoria a fost setata:
        # asta ar ocoli filtrul de chat si restrictiile de continut. Coborarea (18+ -> sub 18) e
        # mereu permisa si definitiva. O singura alegere ramane posibila pentru conturile Google
        # inca nemarcate (ecranul de onboarding). NOTA: fara verificare reala a varstei (ID /
        # parinte) orice categorie ramane o declaratie - vezi raportul.
        if new_age != cur_age:
            if new_age == "adult_18" and current.get("age_locked"):
                raise HTTPException(
                    status_code=403,
                    detail="Categoria de varsta nu mai poate fi ridicata la 18+. Contacteaza suportul.",
                )
            updates["age_category"] = new_age
        if not current.get("age_locked"):
            updates["age_locked"] = True
    if "display_name" in updates:
        updates["display_name"] = updates["display_name"].strip()
        if not (1 <= len(updates["display_name"]) <= 40):
            raise HTTPException(status_code=400, detail="display_name must be 1-40 characters")
    if "language" in updates and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", updates["language"]):
        raise HTTPException(status_code=400, detail="Invalid language code")
    if updates.get("avatar_url"):
        updates["avatar_url"] = _clean_image_or_400(updates["avatar_url"], https_hosts=_AVATAR_HTTPS_HOSTS)
    if updates:
        updates["updated_at"] = now_utc()
        await db.users.update_one({"user_id": current["user_id"]}, {"$set": updates})
    user = _strip_secret(await db.users.find_one({"user_id": current["user_id"]}, {"_id": 0}))
    return {"user": _user_public(user)}


@api.get("/users/search")
async def search_users(q: str = "", current=Depends(get_current_user)):
    q = (q or "").strip()[:32]
    if len(q) < 2:
        return {"users": []}
    wait = _user_search_limiter.hit(current["user_id"])   # 60 / minut: opreste enumerarea in masa a conturilor
    if wait is not None:
        raise _too_many(wait)
    # SECURITY: q e escapat (altfel e regex controlat de atacator = ReDoS) iar raspunsul contine
    # DOAR profilul public (fara email / sold / limba / rol - vezi _user_public_minimal).
    rx = {"$regex": re.escape(q), "$options": "i"}
    cursor = db.users.find(
        {"$or": [{"username": rx}, {"display_name": rx}]},
        {"_id": 0, "password_hash": 0},
    ).limit(20)
    users = [_user_public_minimal(u) for u in await cursor.to_list(20) if u["user_id"] != current["user_id"]]
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
    doc.setdefault("player_character_source", None)
    if doc.get("player_character_model_id") and not doc.get("player_character_source"):
        # jocuri salvate inainte de campul `source`: id-urile Shop incep cu "shop_", cele din Studio cu "model_"
        doc["player_character_source"] = "shop_model" if str(doc["player_character_model_id"]).startswith("shop_") else "studio_model"
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


INSTANCE_ACTIVE_SECONDS = 60  # = expireAfterSeconds al indexului TTL de pe instance_players.last_seen


def _active_cutoff() -> datetime:
    return now_utc() - timedelta(seconds=INSTANCE_ACTIVE_SECONDS)


async def _attach_live_player_counts(games: list[dict]) -> list[dict]:
    """'Playing' REAL: cati jucatori au dat semn de viata in ultimele 60s in instantele DESCHISE.

    Se numara direct din instance_players (filtrat pe last_seen), NU din game_instances.player_count:
    acela e un contor incrementat/decrementat la mana si NU scade cand un jucator expira prin TTL
    (indexul TTL sterge randul, dar nimeni nu decrementa contorul) -> 'playing' crestea la infinit.
    Doua query-uri pentru tot batch-ul (nu N+1)."""
    ids = [g["game_id"] for g in games]
    if not ids:
        return games
    insts = await db.game_instances.find(
        {"game_id": {"$in": ids}, "status": "open"}, {"_id": 0, "instance_id": 1, "game_id": 1}
    ).to_list(5000)
    live: dict[str, int] = {}
    if insts:
        inst_game = {i["instance_id"]: i["game_id"] for i in insts}
        rows = await db.instance_players.aggregate([
            {"$match": {"instance_id": {"$in": list(inst_game)}, "last_seen": {"$gte": _active_cutoff()}}},
            {"$group": {"_id": "$instance_id", "n": {"$sum": 1}}},
        ]).to_list(len(inst_game))
        for r in rows:
            gid = inst_game.get(r["_id"])
            if gid:
                live[gid] = live.get(gid, 0) + r["n"]
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
def _with_main_script(files: list[dict], source: str) -> list[dict]:
    """Pune `source` ca main.lua (punctul de intrare al sandbox-ului), pastrand celelalte fisiere."""
    out: list[dict] = []
    replaced = False
    for f in files:
        if f.get("path") == "main.lua":
            out.append({**f, "source": source, "target": f.get("target") or "server"})
            replaced = True
        else:
            out.append(f)
    if not replaced:
        out.append({"path": "main.lua", "source": source, "target": "server"})
    return out


def _validate_scripts_or_400(files: list[dict], folders: Optional[list] = None) -> list[dict]:
    """Aceleasi validari ca la PUT /sandbox/games/{id}/files (structura + regulile sandbox-ului).
    Daca modulul sandbox nu s-a putut incarca, nu putem valida aici si pastram fisierele ca atare."""
    try:
        from astran_sandbox.routes import validate_project, luau_bundle
        from astran_sandbox.runner import validate_files
    except Exception:  # noqa: BLE001
        return files
    try:
        clean, _ = validate_project(files, folders or [])
        validate_files(luau_bundle(clean))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid script: {exc}")
    return clean


async def _prepare_main_script(game_id: str, g: dict, source: str) -> tuple[list[dict], list]:
    """Proiectul curent al jocului cu main.lua inlocuit de `source` (validat). Nu scrie nimic."""
    doc = await db.game_scripts.find_one({"game_id": game_id}, {"_id": 0})
    files: list[dict] = []
    folders: list = []
    if doc is not None and isinstance(doc.get("files"), list):
        raw = doc["files"]
        try:
            from astran_sandbox.routes import _migrate_legacy_files
            files = _migrate_legacy_files(raw)
        except Exception:  # noqa: BLE001
            files = [dict(f) for f in raw if isinstance(f, dict)]
        if isinstance(doc.get("folders"), list):
            folders = [f for f in doc["folders"] if isinstance(f, str)]
    return _validate_scripts_or_400(_with_main_script(files, source), folders), folders


async def _resolve_player_character(model_id: str, source: Optional[str], owner_id: str, actor: dict) -> tuple[str, str]:
    """Valideaza modelul ales ca personaj si intoarce (model_id, source).

    Modelul trebuie sa existe (Shop = kind "model", sau Studio) si sa apartina proprietarului jocului
    sau celui care il seteaza: altfel cineva ar putea 'atasa' modelul PLATIT al altcuiva si il
    vedea gratis prin endpointul de personaj."""
    allowed = {owner_id, actor["user_id"]}
    if source in (None, "shop_model"):
        it = await db.shop_items.find_one({"item_id": model_id, "kind": "model"}, {"_id": 0, "owner_id": 1})
        if it:
            if it["owner_id"] not in allowed and not actor.get("is_platform_admin"):
                raise HTTPException(status_code=403, detail="You can only use your own model as the player character")
            return model_id, "shop_model"
    if source in (None, "studio_model"):
        m = await db.studio_models.find_one({"model_id": model_id}, {"_id": 0, "owner_id": 1})
        if m:
            if m["owner_id"] not in allowed and not actor.get("is_platform_admin"):
                raise HTTPException(status_code=403, detail="You can only use your own model as the player character")
            return model_id, "studio_model"
    raise HTTPException(status_code=400, detail="Player character model not found")


@api.post("/games")
async def create_game(body: GameCreateBody, current=Depends(get_current_user)):
    gid = new_id("game_")

    # SECURITY: parola de securitate a proiectului e obligatorie la creare - validata (lungime
    # 6-30, confirmare identica) si hash-uita cu Argon2id (salt unic, generat automat de
    # argon2-cffi). Niciodata stocata sau logata in clar.
    psec.validate_project_password_shape(body.project_password, body.project_password_confirm)
    project_password_hash = await asyncio.to_thread(psec.hash_project_password, body.project_password)

    # Scriptul din body (GameCreateBody.script) devine main.lua al proiectului. Inainte era salvat
    # doar in games.script, pe care NIMIC nu il mai citeste cat exista game_scripts (creat mereu cu
    # fisierele implicite) -> scriptul trimis la creare era ignorat. Validam INAINTE de orice scriere.
    initial_files = _default_game_scripts()
    if body.script and body.script.strip():
        initial_files = _validate_scripts_or_400(_with_main_script(initial_files, body.script))

    pc_id: Optional[str] = None
    pc_source: Optional[str] = None
    if body.player_character_model_id:
        pc_id, pc_source = await _resolve_player_character(
            body.player_character_model_id, body.player_character_source, current["user_id"], current)

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
        "thumbnail_url": _clean_image_or_400(body.thumbnail_url, any_https=True),
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
        "scene": _clean_scene_or_400(body.scene) if body.scene else {"objects": [], "sky": "#0F1012", "ground": "#1A1D21"},
        "script": body.script,
        "max_players": body.max_players,
        "player_character_model_id": pc_id,
        "player_character_source": pc_source,
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
        "files": initial_files,
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
        k: v for k, v in body.dict(
            exclude={"clear_player_character", "group_token", "clear_group", "player_character_source"}
        ).items()
        if v is not None
    }
    if "thumbnail_url" in updates:
        updates["thumbnail_url"] = _clean_image_or_400(updates["thumbnail_url"], any_https=True)
    if "scene" in updates:
        updates["scene"] = _clean_scene_or_400(updates["scene"])
    # "" = acelasi lucru cu clear_player_character
    clear_character = bool(body.clear_player_character) or updates.get("player_character_model_id") == ""
    character_touched = clear_character or ("player_character_model_id" in updates)
    if clear_character:
        updates["player_character_model_id"] = None
        updates["player_character_source"] = None

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
        if updates.get("script", "").strip() and (path_allowed is None or not path_allowed("main.lua", access)):
            raise HTTPException(status_code=403, detail="No access to path: main.lua")
        if publish_touched and not access.can_publish:
            raise HTTPException(status_code=403, detail="No permission to publish this game")

    if updates.get("player_character_model_id"):
        if updates["player_character_model_id"] == g.get("player_character_model_id"):
            # acelasi model ca pana acum (editorul retrimite campul la fiecare Save): nu revalidam, ca un
            # model sters intre timp sa nu blocheze salvarea jocului; pastram sursa deja stocata
            updates.pop("player_character_model_id")
            character_touched = False
        else:
            updates["player_character_model_id"], updates["player_character_source"] = await _resolve_player_character(
                updates["player_character_model_id"], body.player_character_source, g["owner_id"], current)

    # Scriptul trimis la PATCH merge in proiect (main.lua), nu doar in games.script (vezi create_game).
    # Un script gol NU sterge main.lua (ar fi usor sa pierzi codul din greseala).
    new_project: Optional[tuple[list[dict], list]] = None
    if updates.get("script", "").strip():
        new_project = await _prepare_main_script(game_id, g, updates["script"])

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
    if new_project is not None:
        files, folders = new_project
        await db.game_scripts.update_one(
            {"game_id": game_id},
            {"$set": {"game_id": game_id, "files": files, "folders": folders, "updated_at": now_utc()}},
            upsert=True,
        )
    g2 = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    out_list = await _attach_live_player_counts(await _attach_group_info([_game_defaults(g2)]))
    return {"game": out_list[0]}


class DeleteGameBody(BaseModel):
    project_password: str = Field(min_length=1, max_length=200)
    confirm: bool = False


class SetupProjectPasswordBody(BaseModel):
    account_password: Optional[str] = Field(default=None, max_length=200)  # None la conturile Google (fara parola)
    project_password: str = Field(min_length=1, max_length=200)
    project_password_confirm: str = Field(min_length=1, max_length=200)


class RecoveryStartBody(BaseModel):
    account_password: Optional[str] = Field(default=None, max_length=200)  # None la conturile Google (fara parola)


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


async def _reauth_account_password(user: Optional[dict], account_password: Optional[str], *,
                                   game_id: str, purpose: str, action: str, ip: Optional[str]) -> None:
    """Re-autentificare cu parola CONTULUI pentru fluxurile de recuperare/resetare.

    Conturile Google nu au parola de cont (password_hash None) - inainte ele primeau mereu 401 si
    nu puteau recupera proiecte sterse. Pentru ele nu exista parola de verificat; protectia vine
    din pasii care urmeaza OBLIGATORIU: cod trimis pe emailul contului (+ biometrie daca e inregistrata)."""
    if not user:
        raise HTTPException(status_code=401, detail="Parola contului este incorecta")
    if not user.get("password_hash"):
        return
    if not account_password or not await verify_pw_async(account_password, user["password_hash"]):
        await psec.record_failed_attempt(db, user_id=user["user_id"], scope_id=game_id, purpose=purpose)
        await psec.log_security_event(db, action=action, result="denied", user_id=user["user_id"], game_id=game_id, detail="bad_account_password", ip=ip)
        raise HTTPException(status_code=401, detail="Parola contului este incorecta")


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

    if not await asyncio.to_thread(psec.verify_project_password, body.project_password, g["project_password_hash"]):
        await psec.record_failed_attempt(db, user_id=current["user_id"], scope_id=game_id, purpose="project_delete")
        await psec.log_security_event(
            db, action="project_delete", result="failed", user_id=current["user_id"],
            game_id=game_id, detail="bad_project_password", ip=ip,
        )
        raise HTTPException(status_code=401, detail="Parola de securitate a proiectului este incorecta")

    await psec.reset_attempts(db, user_id=current["user_id"], scope_id=game_id, purpose="project_delete")
    now = now_utc()
    await db.games.update_one(
        {"game_id": game_id},
        {"$set": {
            "deleted": True, "deleted_at": now, "deleted_by": current["user_id"],
            "status": "trashed", "is_public": False,
        }},
    )
    await psec.log_security_event(
        db, action="project_delete", result="success", user_id=current["user_id"], game_id=game_id, ip=ip,
    )
    # Datele (fisiere, scene, setari, colaboratori) NU sunt sterse - raman intacte in
    # game_scripts / game_collaborators / game_testers pentru recuperare (vezi item 2).
    return {"ok": True, "trashed": True}
@api.get("/games/trash")
async def list_trash(current=Depends(get_current_user)):
    """Cosul de gunoi securizat - vizibil DOAR Owner-ului (query filtrat pe owner_id al
    utilizatorului autentificat, niciodata pe un id primit din client)."""
    cursor = db.games.find(
        {"owner_id": current["user_id"], "deleted": True},
        {"_id": 0, "script": 0, "scene": 0, "project_password_hash": 0},
    ).sort("deleted_at", -1)
    games = await cursor.to_list(200)
    out = []
    for g in games:
        out.append({
            "game_id": g["game_id"],
            "title": g.get("title"),
            "deleted_at": g.get("deleted_at"),
            "thumbnail_url": g.get("thumbnail_url"),
            "recovery_state": "pending",
        })
    return {"games": out}


@api.post("/games/{game_id}/security/password/setup")
async def setup_project_password(game_id: str, body: SetupProjectPasswordBody, request: Request, current=Depends(get_current_user)):
    """Flux pentru proiecte create INAINTE de acest sistem - seteaza parola de securitate
    lipsa, la urmatoarea actiune sensibila. Cere reautentificare cu parola CONTULUI (nu a
    proiectului, care inca nu exista) si functioneaza o singura data per proiect: odata
    setata, schimbarea ei trece doar prin fluxul de reset (vezi mai jos)."""
    ip = request.client.host if request.client else None
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or g.get("deleted"):
        raise HTTPException(status_code=404, detail="Game not found")
    if g["owner_id"] != current["user_id"]:
        raise HTTPException(status_code=403, detail="Doar Owner-ul poate configura parola proiectului")
    if g.get("project_password_hash"):
        raise HTTPException(status_code=409, detail="Proiectul are deja o parola de securitate - foloseste resetarea")

    await psec.ensure_rate_limit_or_raise(db, user_id=current["user_id"], scope_id=game_id, purpose="project_password_setup")
    user = await db.users.find_one({"user_id": current["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="Parola contului este incorecta")
    if user.get("password_hash"):
        if not body.account_password or not await verify_pw_async(body.account_password, user["password_hash"]):
            await psec.record_failed_attempt(db, user_id=current["user_id"], scope_id=game_id, purpose="project_password_setup")
            await psec.log_security_event(db, action="project_password_setup", result="denied", user_id=current["user_id"], game_id=game_id, detail="bad_account_password", ip=ip)
            raise HTTPException(status_code=401, detail="Parola contului este incorecta")
    else:
        # Cont Google: nu exista parola de cont. Cerem o autentificare RECENTA (sesiune creata in ultimele
        # 15 min) - un token furat/vechi nu poate seta parola proiectului si apoi sterge proiectul.
        recent = await db.user_sessions.find_one(
            {"user_id": current["user_id"], "created_at": {"$gte": now_utc() - timedelta(minutes=15)}}, {"_id": 1})
        if not recent:
            await psec.log_security_event(db, action="project_password_setup", result="denied", user_id=current["user_id"], game_id=game_id, detail="stale_session", ip=ip)
            raise HTTPException(status_code=401, detail="Autentifica-te din nou cu Google si reincearca (este necesara o sesiune recenta).")

    psec.validate_project_password_shape(body.project_password, body.project_password_confirm)
    pw_hash = await asyncio.to_thread(psec.hash_project_password, body.project_password)
    await db.games.update_one({"game_id": game_id}, {"$set": {"project_password_hash": pw_hash, "project_password_set_at": now_utc()}})
    await psec.log_security_event(db, action="project_password_setup", result="success", user_id=current["user_id"], game_id=game_id, ip=ip)
    return {"ok": True}
@api.post("/games/{game_id}/security/password/reset/start")
async def reset_password_start(game_id: str, body: RecoveryStartBody, request: Request, current=Depends(get_current_user)):
    ip = request.client.host if request.client else None
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or g.get("deleted"):
        raise HTTPException(status_code=404, detail="Game not found")
    if g["owner_id"] != current["user_id"]:
        raise HTTPException(status_code=403, detail="Doar Owner-ul poate reseta parola proiectului")
    await psec.ensure_rate_limit_or_raise(db, user_id=current["user_id"], scope_id=game_id, purpose="password_reset")

    user = await db.users.find_one({"user_id": current["user_id"]})
    await _reauth_account_password(user, body.account_password, game_id=game_id, purpose="password_reset", action="password_reset", ip=ip)
    if not user.get("email") or not user.get("email_verified"):
        # codul de recuperare merge pe email: daca adresa nu e dovedita (POST /auth/email/verify), cine a
        # inregistrat contul cu un email strain ar primi codurile de recuperare ale proiectelor
        raise HTTPException(status_code=409, detail="Contul nu are un email verificat - recuperarea nu este posibila")

    tok = await psec.create_recovery_token(db, user_id=current["user_id"], game_id=game_id, purpose="project_password_reset")
    email_sent = await asyncio.to_thread(psec.send_recovery_code_email, user["email"], tok.code, purpose="project_password_reset", game_title=g.get("title"))
    if not email_sent:
        # Nu lasam tokenul "created" in picioare daca nimeni nu a primit codul - altfel
        # ramane un cod valid, nefolosit, pe care nimeni nu-l poate introduce.
        await psec.consume_token(db, token_id=tok.token_id)
        await psec.log_security_event(db, action="password_reset", result="failed", user_id=current["user_id"], game_id=game_id, detail="email_delivery_failed", ip=ip)
        raise HTTPException(status_code=503, detail="Nu am putut trimite emailul cu codul de verificare. Incearca din nou in cateva minute sau contacteaza suportul daca problema persista.")
    has_biometric = await psec.user_has_webauthn(db, user_id=current["user_id"])
    await psec.log_security_event(db, action="password_reset", result="started", user_id=current["user_id"], game_id=game_id, ip=ip)
    return {"token_id": tok.token_id, "email_sent_to": user["email"][:2] + "***", "biometric_available": has_biometric}


@api.post("/games/{game_id}/security/password/reset/verify-email")
async def reset_password_verify_email(game_id: str, body: RecoveryVerifyEmailBody, current=Depends(get_current_user)):
    await psec.verify_recovery_code(db, token_id=body.token_id, user_id=current["user_id"], game_id=game_id, purpose="project_password_reset", code=body.code)
    has_biometric = await psec.user_has_webauthn(db, user_id=current["user_id"])
    return {"ok": True, "biometric_required": has_biometric}


@api.post("/games/{game_id}/security/password/reset/confirm")
async def reset_password_confirm(game_id: str, body: PasswordResetConfirmBody, request: Request, current=Depends(get_current_user)):
    ip = request.client.host if request.client else None
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g:
        raise HTTPException(status_code=404, detail="Game not found")
    if g["owner_id"] != current["user_id"]:
        raise HTTPException(status_code=403, detail="Not the owner")
    tok = await psec.get_valid_token(db, token_id=body.token_id, user_id=current["user_id"], game_id=game_id, purpose="project_password_reset")
    if tok["stage"] not in ("email_verified", "biometric_verified"):
        raise HTTPException(status_code=400, detail="Verificarea prin email nu a fost finalizata")
    has_biometric = await psec.user_has_webauthn(db, user_id=current["user_id"])
    if has_biometric and tok["stage"] != "biometric_verified":
        raise HTTPException(status_code=400, detail="Verificarea biometrica este obligatorie pentru acest cont")

    psec.validate_project_password_shape(body.new_project_password, body.new_project_password_confirm)
    pw_hash = await asyncio.to_thread(psec.hash_project_password, body.new_project_password)
    await db.games.update_one({"game_id": game_id}, {"$set": {"project_password_hash": pw_hash, "project_password_set_at": now_utc()}})
    await psec.consume_token(db, token_id=body.token_id)
    await psec.invalidate_other_tokens(db, user_id=current["user_id"], game_id=game_id, purpose="project_password_reset", keep_token_id=body.token_id)
    await psec.reset_attempts(db, user_id=current["user_id"], scope_id=game_id, purpose="password_reset")
    await psec.log_security_event(db, action="password_reset", result="success", user_id=current["user_id"], game_id=game_id, ip=ip)
    return {"ok": True}


@api.post("/games/{game_id}/security/recover/start")
async def recover_start(game_id: str, body: RecoveryStartBody, request: Request, current=Depends(get_current_user)):
    ip = request.client.host if request.client else None
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or not g.get("deleted"):
        raise HTTPException(status_code=404, detail="No deleted project with this id")
    if g["owner_id"] != current["user_id"]:
        # Colaboratorii (Editor/Tester), chiar cu link direct catre proiect, primesc exact
        # acelasi refuz - ownership-ul e verificat pe server, nu pe ce poate vedea clientul.
        await psec.log_security_event(db, action="project_recover", result="denied", user_id=current["user_id"], game_id=game_id, detail="not_owner", ip=ip)
        raise HTTPException(status_code=403, detail="Doar Owner-ul poate recupera acest proiect")

    await psec.ensure_rate_limit_or_raise(db, user_id=current["user_id"], scope_id=game_id, purpose="project_recover")
    user = await db.users.find_one({"user_id": current["user_id"]})
    await _reauth_account_password(user, body.account_password, game_id=game_id, purpose="project_recover", action="project_recover", ip=ip)
    if not user.get("email") or not user.get("email_verified"):
        # codul de recuperare merge pe email: daca adresa nu e dovedita (POST /auth/email/verify), cine a
        # inregistrat contul cu un email strain ar primi codurile de recuperare ale proiectelor
        raise HTTPException(status_code=409, detail="Contul nu are un email verificat - recuperarea nu este posibila")

    tok = await psec.create_recovery_token(db, user_id=current["user_id"], game_id=game_id, purpose="project_recover")
    email_sent = await asyncio.to_thread(psec.send_recovery_code_email, user["email"], tok.code, purpose="project_recover", game_title=g.get("title"))
    if not email_sent:
        await psec.consume_token(db, token_id=tok.token_id)
        await psec.log_security_event(db, action="project_recover", result="failed", user_id=current["user_id"], game_id=game_id, detail="email_delivery_failed", ip=ip)
        raise HTTPException(status_code=503, detail="Nu am putut trimite emailul cu codul de verificare. Incearca din nou in cateva minute sau contacteaza suportul daca problema persista.")
    has_biometric = await psec.user_has_webauthn(db, user_id=current["user_id"])
    await psec.log_security_event(db, action="project_recover", result="started", user_id=current["user_id"], game_id=game_id, ip=ip)
    return {"token_id": tok.token_id, "email_sent_to": user["email"][:2] + "***", "biometric_available": has_biometric}


@api.post("/games/{game_id}/security/recover/verify-email")
async def recover_verify_email(game_id: str, body: RecoveryVerifyEmailBody, current=Depends(get_current_user)):
    await psec.verify_recovery_code(db, token_id=body.token_id, user_id=current["user_id"], game_id=game_id, purpose="project_recover", code=body.code)
    has_biometric = await psec.user_has_webauthn(db, user_id=current["user_id"])
    return {"ok": True, "biometric_required": has_biometric}


@api.post("/games/{game_id}/security/recover/confirm")
async def recover_confirm(game_id: str, body: RecoveryConfirmBody, request: Request, current=Depends(get_current_user)):
    ip = request.client.host if request.client else None
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or not g.get("deleted"):
        raise HTTPException(status_code=404, detail="No deleted project with this id")
    if g["owner_id"] != current["user_id"]:
        raise HTTPException(status_code=403, detail="Doar Owner-ul poate recupera acest proiect")

    tok = await psec.get_valid_token(db, token_id=body.token_id, user_id=current["user_id"], game_id=game_id, purpose="project_recover")
    if tok["stage"] not in ("email_verified", "biometric_verified"):
        raise HTTPException(status_code=400, detail="Verificarea prin email nu a fost finalizata")
    has_biometric = await psec.user_has_webauthn(db, user_id=current["user_id"])
    if has_biometric and tok["stage"] != "biometric_verified":
        raise HTTPException(status_code=400, detail="Verificarea biometrica este obligatorie pentru acest cont")

    await db.games.update_one(
        {"game_id": game_id},
        {"$set": {"deleted": False, "deleted_at": None, "deleted_by": None, "status": "active"}},
    )
    await psec.consume_token(db, token_id=body.token_id)
    await psec.invalidate_other_tokens(db, user_id=current["user_id"], game_id=game_id, purpose="project_recover", keep_token_id=body.token_id)
    await psec.reset_attempts(db, user_id=current["user_id"], scope_id=game_id, purpose="project_recover")
    await psec.log_security_event(db, action="project_recover", result="success", user_id=current["user_id"], game_id=game_id, ip=ip)
    # Colaboratorii nu au fost niciodata stersi la soft-delete (game_collaborators/game_testers
    # raman neatinse) - permisiunile lor anterioare se aplica automat din nou.
    g2 = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    out_list = await _attach_live_player_counts(await _attach_group_info([_game_defaults(g2)]))
    return {"ok": True, "game": out_list[0]}


@api.post("/auth/webauthn/register/options")
async def webauthn_register_options(current=Depends(get_current_user)):
    challenge_id, options_json = await psec.webauthn_registration_options(db, user_id=current["user_id"], username=current["username"])
    return {"challenge_id": challenge_id, "options": options_json}


@api.post("/auth/webauthn/register/verify")
async def webauthn_register_verify(body: WebAuthnVerifyBody, current=Depends(get_current_user)):
    await psec.webauthn_verify_registration(db, user_id=current["user_id"], challenge_id=body.challenge_id, credential_json=body.credential)
    await psec.log_security_event(db, action="webauthn_register", result="success", user_id=current["user_id"])
    return {"ok": True}


@api.post("/auth/webauthn/authenticate/options")
async def webauthn_auth_options(current=Depends(get_current_user)):
    challenge_id, options_json = await psec.webauthn_authentication_options(db, user_id=current["user_id"])
    return {"challenge_id": challenge_id, "options": options_json}


@api.post("/games/{game_id}/security/webauthn/verify")
async def webauthn_verify_for_recovery(game_id: str, body: WebAuthnVerifyBody, current=Depends(get_current_user), token_id: str = ""):
    """Verifica asertiunea WebAuthn si marcheaza token-ul de recuperare/reset drept
    'biometric_verified' - apelat dupa recover/verify-email sau reset/verify-email, doar
    daca raspunsul acelora a intors biometric_required=true."""
    if not token_id:
        raise HTTPException(status_code=400, detail="token_id lipseste")
    tok = await db.recovery_tokens.find_one({"token_id": token_id, "user_id": current["user_id"]}, {"_id": 0})
    if not tok or tok.get("used") or tok.get("stage") not in ("email_verified",):
        raise HTTPException(status_code=400, detail="Verifica mai intai emailul")
    # Tokenul trebuie sa apartina EXACT jocului din URL si sa nu fie expirat (inainte nu se
    # verifica nici una, spre deosebire de get_valid_token folosit de restul fluxului).
    if tok.get("game_id") != game_id:
        raise HTTPException(status_code=400, detail="Token de recuperare invalid")
    _exp = tok.get("expires_at")
    if isinstance(_exp, datetime) and _exp.tzinfo is None:
        _exp = _exp.replace(tzinfo=timezone.utc)
    if not _exp or _exp < now_utc():
        raise HTTPException(status_code=400, detail="Codul de verificare a expirat")
    await psec.webauthn_verify_authentication(db, user_id=current["user_id"], challenge_id=body.challenge_id, credential_json=body.credential)
    await psec.mark_token_stage(db, token_id=token_id, stage="biometric_verified")
    await psec.log_security_event(db, action="webauthn_authenticate", result="success", user_id=current["user_id"], game_id=tok.get("game_id"))
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
    # SECURITY: proiectele sterse (soft-delete) nu mai apar in lista normala - traiesc doar
    # in Cosul de gunoi (GET /games/trash), vizibil si recuperabil doar prin fluxul securizat.
    cursor = db.games.find(
        {"owner_id": current["user_id"], "deleted": {"$ne": True}}, {"_id": 0, "script": 0}
    ).sort("created_at", -1)
    games = await _attach_live_player_counts(        await _attach_group_info([_game_defaults(g) for g in await cursor.to_list(100)])
    )
    return {"games": games}


@api.get("/games/recently-played")
async def recently_played(current=Depends(get_current_user)):
    cursor = db.recently_played.find({"user_id": current["user_id"]}, {"_id": 0}).sort("played_at", -1).limit(50)
    entries = await cursor.to_list(50)
    game_ids = [e["game_id"] for e in entries]
    if not game_ids:
        return {"games": []}
    games = await db.games.find(
        {"game_id": {"$in": game_ids}, "deleted": {"$ne": True}}, {"_id": 0, "script": 0}
    ).to_list(50)
    idx = {g["game_id"]: g for g in games}
    ordered = []
    for gid in game_ids:
        gg = idx.get(gid)
        if gg is None:
            continue
        if gg.get("age_category") == "adult_18" and current.get("age_category") == "under_18":
            continue
        if not gg.get("is_public") and not await _can_view_private_game(gg, current):
            continue
        ordered.append(gg)
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
    # SECURITY: un proiect sters (soft-delete) nu mai e accesibil prin URL-uri/endpointuri
    # obisnuite - raspunde exact ca un 404 normal, nu dezvaluie ca ar exista dar e in cos.
    if not g or g.get("deleted"):
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


@api.get("/games/{game_id}/player-character")
async def get_player_character(game_id: str, current=Depends(get_current_user)):
    """Piesele 3D ale personajului custom al jocului, pentru ORICARE jucator care poate juca jocul.

    Inainte, clientul citea modelul din /shop/items/{id} (da continutul doar celor care DETIN itemul
    - deci nimeni in afara de autor, la modele platite/private) sau din /studio/models/{id} (doar
    owner). Aici autorizarea e cea a JOCULUI (acelasi regim ca get_game/play_game); iar modelul e
    garantat al proprietarului jocului (vezi _resolve_player_character), deci nu se scurge continut
    al altcuiva."""
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or g.get("deleted"):
        raise HTTPException(status_code=404, detail="Game not found")
    if g["age_category"] == "adult_18" and current.get("age_category") == "under_18":
        raise HTTPException(status_code=403, detail="Age-restricted content")
    if not g.get("is_public") and not await _can_view_private_game(g, current):
        raise HTTPException(status_code=403, detail="This game is private")
    g = _game_defaults(g)
    mid, src = g.get("player_character_model_id"), g.get("player_character_source")
    if not mid:
        raise HTTPException(status_code=404, detail="This game has no custom player character")
    parts = None
    if src == "shop_model":
        it = await db.shop_items.find_one({"item_id": mid, "kind": "model"}, {"_id": 0, "model": 1})
        parts = ((it or {}).get("model") or {}).get("parts")
    else:
        m = await db.studio_models.find_one({"model_id": mid}, {"_id": 0, "parts": 1})
        parts = (m or {}).get("parts")
    if not isinstance(parts, list) or not parts:
        raise HTTPException(status_code=404, detail="Player character model not found")
    return {"model_id": mid, "source": src, "parts": parts}


# ---------- Server instances ----------
# Cand un server ajunge la max_players, urmatorul jucator intra intr-o instanta noua, nu in
# aceeasi sesiune. O instanta "moare" (status=closed) cand ultimul jucator pleaca de 60s+
# (vezi indexul TTL pe instance_players.last_seen si reap_empty_instances de mai jos).
async def reap_empty_instances(game_id: str) -> None:
    """Inchide instantele fara niciun jucator activ (curatare oportunista, apelata la join)."""
    open_instances = await db.game_instances.find({"game_id": game_id, "status": "open"}, {"_id": 0}).to_list(200)
    if open_instances:
        # Nu ne bazam pe monitorul TTL din MongoDB (ruleaza la ~60s si poate intarzia): stergem noi
        # jucatorii inactivi, ca player_count sa reflecte realitatea la fiecare join/list.
        await db.instance_players.delete_many({
            "instance_id": {"$in": [i["instance_id"] for i in open_instances]},
            "last_seen": {"$lt": _active_cutoff()},
        })
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
    if not g or g.get("deleted"):
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
    # Jucatorul e deja intr-o instanta deschisa a acestui joc: il re-folosim, nu incrementam
    # din nou player_count (un refresh / dublu-apel umfla contorul si umplea serverele "fantoma").
    mine = await db.instance_players.find({"user_id": current["user_id"]}, {"_id": 0, "instance_id": 1}).to_list(20)
    my_ids = [m["instance_id"] for m in mine]
    current_inst = None
    if my_ids:
        current_inst = await db.game_instances.find_one(
            {"instance_id": {"$in": my_ids}, "game_id": game_id, "status": "open"}, {"_id": 0})
    if current_inst and (not body.instance_id or body.instance_id == current_inst["instance_id"]):
        await db.instance_players.update_one(
            {"instance_id": current_inst["instance_id"], "user_id": current["user_id"]},
            {"$set": {"last_seen": now_utc()}},
        )
        # (re-apelarea /play pentru aceeasi instanta = refresh, nu o noua sesiune de joc:
        # NU incrementam total_plays - inainte fiecare refresh il umfla)
        return {
            "ok": True,
            "session": {"instance_id": current_inst["instance_id"], "game_id": game_id},
            "max_players": max_players,
            "player_character_model_id": g.get("player_character_model_id"),
            "player_character_source": g.get("player_character_source"),
        }
    if current_inst:
        # trece intr-o alta instanta: o parasim curat pe cea veche
        left = await db.instance_players.delete_one({"instance_id": current_inst["instance_id"], "user_id": current["user_id"]})
        if left.deleted_count:
            await db.game_instances.update_one(
                {"instance_id": current_inst["instance_id"], "player_count": {"$gt": 0}}, {"$inc": {"player_count": -1}})
    already = None
    if body.instance_id:
        already = await db.instance_players.find_one({"instance_id": body.instance_id, "user_id": current["user_id"]})
        if already:
            instance = await db.game_instances.find_one({"instance_id": body.instance_id, "game_id": game_id, "status": "open"}, {"_id": 0})
        else:
            # atomic: loc liber verificat SI ocupat in aceeasi operatie (inainte nu se incrementa deloc)
            instance = await db.game_instances.find_one_and_update(
                {"instance_id": body.instance_id, "game_id": game_id, "status": "open", "player_count": {"$lt": max_players}},
                {"$inc": {"player_count": 1}}, return_document=True, projection={"_id": 0})

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

    joined = await db.instance_players.update_one(
        {"instance_id": instance["instance_id"], "user_id": current["user_id"]},
        {"$set": {"instance_id": instance["instance_id"], "user_id": current["user_id"], "last_seen": now_utc()}},
        upsert=True,
    )
    if joined.upserted_id is None and not already:
        # Randul exista deja (cerere concurenta / dublu-tap): am incrementat player_count in plus.
        await db.game_instances.update_one(
            {"instance_id": instance["instance_id"], "player_count": {"$gt": 0}}, {"$inc": {"player_count": -1}})
    prev_play = await db.recently_played.find_one_and_update(
        {"user_id": current["user_id"], "game_id": game_id},
        {"$set": {"played_at": now_utc()}},
        upsert=True, return_document=ReturnDocument.BEFORE,
    )
    # total_plays alimenteaza Discover / For You / Trending. Inainte, fiecare iesire + reintrare
    # (leave -> play) il incrementa, deci un singur cont il umfla nelimitat. Acum un utilizator
    # conteaza O DATA pe fereastra PLAY_COUNT_COOLDOWN pentru acelasi joc.
    if joined.upserted_id is not None and _play_counts(prev_play, now_utc()):
        await db.games.update_one({"game_id": game_id}, {"$inc": {"total_plays": 1}})

    return {
        "ok": True,
        "session": {"instance_id": instance["instance_id"], "game_id": game_id},
        "max_players": max_players,
        "player_character_model_id": g.get("player_character_model_id"),
        "player_character_source": g.get("player_character_source"),
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
    res = await db.instance_players.delete_one({"instance_id": body.instance_id, "user_id": current["user_id"]})
    # Decrementam DOAR daca jucatorul chiar era in instanta (altfel orice user ar putea goli
    # contorul altei instante) si doar pe o instanta a jocului din URL.
    if res.deleted_count:
        await db.game_instances.update_one(
            {"instance_id": body.instance_id, "game_id": game_id, "player_count": {"$gt": 0}},
            {"$inc": {"player_count": -1}},
        )
    return {"ok": True}


@api.get("/games/{game_id}/instances")
async def list_instances(game_id: str, current=Depends(get_current_user)):
    """Lista serverelor deschise pentru un joc (util pentru debugging/afisare optionala in UI)."""
    g = await db.games.find_one({"game_id": game_id}, {"_id": 0})
    if not g or g.get("deleted"):
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


def _friend_public(user: dict) -> dict:
    """Profilul public al unui prieten / membru de grup (fara email, sold sau limba).

    BUG REPARAT: functia era apelata (list_friends, list_requests si, la nivel de modul,
    make_group_router(..., _friend_public)) dar nu era definita nicaieri -> NameError la
    import, deci serverul nu pornea deloc cand modulul de grupuri se incarca.
    "online" e fals: backend-ul nu tine evidenta prezentei, iar valoarea fixa True din
    _user_public ar fi aratat pe toata lumea ca fiind online."""
    return {**_user_public_minimal(user), "online": False}


@api.get("/friends")
async def list_friends(current=Depends(get_current_user)):
    uid = current["user_id"]
    cursor = db.friendships.find({"$or": [{"user_a": uid}, {"user_b": uid}]}, {"_id": 0})
    edges = await cursor.to_list(500)
    friend_ids = [e["user_b"] if e["user_a"] == uid else e["user_a"] for e in edges]
    users = await db.users.find({"user_id": {"$in": friend_ids}}, {"_id": 0, "password_hash": 0}).to_list(500)
    return {"friends": [_friend_public(u) for u in users]}


@api.get("/friends/requests")
async def list_requests(current=Depends(get_current_user)):
    incoming = await db.friend_requests.find({"to_id": current["user_id"], "status": "pending"}, {"_id": 0}).to_list(200)
    outgoing = await db.friend_requests.find({"from_id": current["user_id"], "status": "pending"}, {"_id": 0}).to_list(200)
    in_users = await db.users.find({"user_id": {"$in": [r["from_id"] for r in incoming]}}, {"_id": 0, "password_hash": 0}).to_list(200)
    out_users = await db.users.find({"user_id": {"$in": [r["to_id"] for r in outgoing]}}, {"_id": 0, "password_hash": 0}).to_list(200)
    return {
        "incoming": [_friend_public(u) for u in in_users],
        "outgoing": [_friend_public(u) for u in out_users],
    }


@api.post("/friends/request")
async def send_request(body: FriendRequestBody, current=Depends(get_current_user)):
    if body.target_user_id == current["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot friend yourself")
    # Fara limite, un cont putea trimite cereri in masa (spam / abordare de minori gasiti prin /users/search).
    wait = _friend_req_limiter.hit(current["user_id"])
    if wait is not None:
        raise _too_many(wait)
    if await db.friend_requests.count_documents({"from_id": current["user_id"], "status": "pending"}) >= MAX_PENDING_FRIEND_REQUESTS:
        raise HTTPException(status_code=429, detail="Too many pending friend requests")
    target = await db.users.find_one({"user_id": body.target_user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    # Politica optionala (decizie de produs, implicit OFF): un adult nu poate TRIMITE cereri catre un cont
    # sub 18 ani. Prietenia ramane posibila daca minorul trimite el primul cererea.
    if _BLOCK_ADULT_TO_MINOR_REQUESTS and current.get("age_category") == "adult_18" \
            and target.get("age_category") == "under_18":
        raise HTTPException(status_code=403, detail="Adults cannot send friend requests to under-18 accounts")
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
    """(taxa, net). Taxa se rotunjeste ÎN SUS: `int(amount*pct/100)` trunchia, deci sub 20 Astrans
    (la 5%) taxa era 0 si transferurile mici erau gratuite. Aritmetica e exacta (Fraction), fara
    erori de virgula mobila."""
    for tier in tiers:
        maxa = tier.get("max_amount")
        if maxa is None or amount <= maxa:
            pct = Fraction(str(tier["fee_percent"]))
            fee = math.ceil(Fraction(amount) * pct / 100) if pct > 0 else 0
            fee = min(fee, amount)
            return fee, amount - fee
    return 0, amount


MIN_NET_TRANSFER = 1  # destinatarul trebuie sa primeasca cel putin 1 Astran dupa taxa


@api.post("/wallet/transfer/preview")
async def transfer_preview(body: TransferBody, current=Depends(get_current_user)):
    cfg = await db.platform_config.find_one({"key": "transfer_fees"}, {"_id": 0})
    fee, net = _calc_fee(body.amount, (cfg or {}).get("tiers", []))
    return {"gross": body.amount, "fee": fee, "net": net, "valid": net >= MIN_NET_TRANSFER}


@api.post("/wallet/transfer")
async def transfer_astrans(body: TransferBody, current=Depends(get_current_user)):
    # 30 transferuri / ora / utilizator: un transfer legitim e rar; un script care muta bonusuri
    # sau "spala" Astrans prin multe conturi e oprit (limita e per proces, ca celelalte).
    wait = _transfer_limiter.hit(current["user_id"])
    if wait is not None:
        raise _too_many(wait)
    if body.recipient_user_id == current["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot transfer to self")
    recipient = await db.users.find_one({"user_id": body.recipient_user_id})
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    # Cheia se STOCHEAZA prefixata cu user_id: indexul unic e global, deci fara prefix un user
    # putea "rezerva" cheia altuia si ii bloca transferurile cu 409.
    idem = f"{current['user_id']}:{body.idempotency_key}" if body.idempotency_key else None
    if idem:
        existing = await db.astran_ledger.find_one(
            {"idempotency_key": idem, "user_id": current["user_id"]}, {"_id": 0})
        if existing:
            return {"ok": True, "transaction": existing, "idempotent": True}

    cfg = await db.platform_config.find_one({"key": "transfer_fees"}, {"_id": 0})
    fee, net = _calc_fee(body.amount, (cfg or {}).get("tiers", []))
    if net < MIN_NET_TRANSFER:
        raise HTTPException(status_code=400, detail="Amount too small: the transfer fee would consume it")

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
        "reference": f"transfer_to:{recipient['username']}",
    }
    # indexul sparse+unique INDEXEAZA valorile null: cheia se pune doar daca exista
    if idem:
        tx["idempotency_key"] = idem
    tx_in = {
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
    }

    # Debit + credit + ledger NU mai sunt trei pasi independenti: intentia se scrie INTAI in jurnal,
    # apoi fiecare pas e idempotent (vezi astran_sandbox/money.py). Daca procesul pica dupa debit,
    # task-ul de recuperare duce transferul la capat - banii nu mai dispar.
    base = ("tr_" + hashlib.sha256(idem.encode()).hexdigest()[:30]) if idem else None
    op_id = None
    for attempt in range(5):
        cand = f"{base}:{attempt}" if base else money.new_op_id("tr_")
        try:
            await money.begin(
                db, op_id=cand, kind="transfer", payer_id=current["user_id"], amount=body.amount,
                credits=[{"user_id": recipient["user_id"], "amount": net}],
                inserts=[{"collection": "astran_ledger", "doc": tx}, {"collection": "astran_ledger", "doc": tx_in}],
            )
            op_id = cand
            break
        except DuplicateKeyError:
            # aceeasi cheie de idempotenta a mai fost folosita
            prev = await money.get_op(db, cand)
            if not prev or prev["payer_id"] != current["user_id"] or prev["amount"] != body.amount \
                    or [c["user_id"] for c in prev["credits"]] != [recipient["user_id"]]:
                raise HTTPException(status_code=409, detail="Idempotency key already used for a different transfer")
            if prev["state"] in ("failed", "refunded"):
                continue  # incercarea precedenta a esuat (ex. sold insuficient): reluam cu un op nou
            if prev["state"] == "pending":
                if await money.run(db, cand) == "insufficient":
                    raise HTTPException(status_code=402, detail="Insufficient Astrans balance")
            existing = await db.astran_ledger.find_one(
                {"idempotency_key": idem, "user_id": current["user_id"]}, {"_id": 0})
            if existing:
                return {"ok": True, "transaction": existing, "idempotent": True}
            raise HTTPException(status_code=409, detail="Transfer already in progress, try again")
    if op_id is None:
        raise HTTPException(status_code=409, detail="Transfer already in progress, try again")

    if await money.run(db, op_id) == "insufficient":
        raise HTTPException(status_code=402, detail="Insufficient Astrans balance")
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

    def __init__(self, ok: bool, external_id: Optional[str] = None, detail: Optional[str] = None,
                 transient: bool = False):
        self.ok = ok
        self.external_id = external_id
        self.detail = detail
        # transient=True: nu am putut VERIFICA plata (furnizor indisponibil / neconfigurat), nu
        # "plata e invalida". buy_astrans raspunde 503 (reincercabil), nu 402 (respins definitiv);
        # clientul inchide tranzactia la magazin DOAR la respingeri definitive.
        self.transient = transient


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
            return VerifyResult(False, detail="Missing Stripe payment intent", transient=not api_key)
        # token vine de la client si ajunge in path-ul URL-ului: doar forma unui PaymentIntent real
        if not re.fullmatch(r"pi_[A-Za-z0-9_]{6,100}", token):
            return VerifyResult(False, detail="Invalid Stripe payment intent")
        async with httpx.AsyncClient(timeout=10.0) as h:
            try:
                # expand latest_charge: PaymentIntent.status ramane "succeeded" si dupa un refund /
                # dispute, deci rambursarea se vede DOAR pe charge (refunded / disputed).
                r = await h.get(f"https://api.stripe.com/v1/payment_intents/{token}", auth=(api_key, ""),
                                params={"expand[]": "latest_charge"})
            except Exception as e:  # noqa: BLE001
                log.error("Stripe verify error: %s", e)
                return VerifyResult(False, detail="Stripe verification failed", transient=True)
        if r.status_code >= 500:
            return VerifyResult(False, detail="Stripe unavailable", transient=True)
        if r.status_code != 200:
            return VerifyResult(False, detail="Stripe payment intent not found")
        data = r.json()
        # SECURITY: un PaymentIntent real e platit de UN cont anume. Fara aceasta verificare, orice
        # user putea trimite payment_intent-ul altcuiva (primul care il trimitea il "consuma").
        # PaymentIntent-urile se creeaza DOAR prin POST /wallet/stripe/intent, care scrie in metadata
        # contul care plateste si pachetul.
        meta = data.get("metadata") or {}
        if meta.get("astran_user_id") != user.get("user_id") or meta.get("package_id") != package.get("package_id"):
            return VerifyResult(False, detail="This payment does not belong to this account/package")
        charge = data.get("latest_charge")
        if isinstance(charge, dict) and (charge.get("refunded") or charge.get("disputed")):
            return VerifyResult(False, detail="This payment was refunded or disputed")
        if await db.payment_reversed_refs.find_one({"ref": token}, {"_id": 1}):
            return VerifyResult(False, detail="This payment was refunded or disputed")
        expected_cur = str(package.get("currency", "")).lower()
        cur_ok = (not expected_cur) or str(data.get("currency", "")).lower() == expected_cur
        ok = (data.get("status") == "succeeded" and cur_ok
              and int(data.get("amount", 0)) >= round(package["price"] * 100))
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
            return VerifyResult(False, detail="Google Play verification not configured", transient=True)
        access_token = await _google_access_token()
        if not access_token:
            return VerifyResult(False, detail="Google Play verification unavailable", transient=True)

        product_id = package["package_id"]  # SKU-ul din Play Console = package_id din astran_packages
        from urllib.parse import quote
        url = (
            f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
            f"{quote(pkg_name, safe='')}/purchases/products/{quote(product_id, safe='')}/tokens/{quote(token, safe='')}"
        )
        async with httpx.AsyncClient(timeout=10.0) as h:
            try:
                r = await h.get(url, headers={"Authorization": f"Bearer {access_token}"})
            except Exception as e:  # noqa: BLE001
                log.error("Google Play verify error: %s", e)
                return VerifyResult(False, detail="Google Play verification failed", transient=True)
        if r.status_code >= 500:
            return VerifyResult(False, detail="Google Play unavailable", transient=True)
        if r.status_code != 200:
            return VerifyResult(False, detail=f"Google Play rejected purchase (status {r.status_code})")
        data = r.json()
        # purchaseState: 0 = achitata, 1 = anulata, 2 = in asteptare (ex: plata in numerar)
        if data.get("purchaseState") != 0:
            return VerifyResult(False, detail="Purchase not completed")
        if await db.payment_reversed_refs.find_one({"ref": token}, {"_id": 1}):
            return VerifyResult(False, detail="This purchase was refunded")
        return VerifyResult(True, external_id=token)

    async def acknowledge(self, token: str, product_id: str) -> bool:
        """Confirma achizitia la Google DIN SERVER dupa creditare. Fara confirmare, Google ramburseaza
        automat dupa 3 zile; confirmarea facuta doar de client (finishTransaction) poate fi sarita de
        un client modificat, care ar pastra Astransii si ar primi banii inapoi. Best-effort: o
        achizitie deja confirmata/consumata de client intoarce 400, ceea ce e in regula."""
        pkg_name = os.environ.get("GOOGLE_PLAY_PACKAGE_NAME")
        access_token = await _google_access_token()
        if not pkg_name or not access_token:
            return False
        from urllib.parse import quote
        url = (f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
               f"{quote(pkg_name, safe='')}/purchases/products/{quote(product_id, safe='')}/tokens/"
               f"{quote(token, safe='')}:acknowledge")
        try:
            async with httpx.AsyncClient(timeout=10.0) as h:
                r = await h.post(url, headers={"Authorization": f"Bearer {access_token}"}, json={})
        except Exception as e:  # noqa: BLE001
            log.warning("Google acknowledge failed: %s", e)
            return False
        return r.status_code in (200, 204)


# ---------- Apple: verificare REALA prin App Store receipt validation ----------
def _apple_sandbox_allowed(user: dict) -> bool:
    """True doar daca user_id-ul e in lista explicita ASTRAN_APPLE_SANDBOX_USER_IDS (separata prin virgula)."""
    raw = os.environ.get("ASTRAN_APPLE_SANDBOX_USER_IDS", "")
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    return bool(user and user.get("user_id") in allowed)


class AppleProvider(PaymentProvider):
    name = "apple"

    async def verify(self, token: str, package: dict, user: dict) -> VerifyResult:
        if not token:
            return VerifyResult(False, detail="Missing receipt")
        shared_secret = os.environ.get("APPLE_SHARED_SECRET")
        if not shared_secret:
            log.error("APPLE_SHARED_SECRET is not configured")
            return VerifyResult(False, detail="Apple verification not configured", transient=True)

        body = {"receipt-data": token, "password": shared_secret, "exclude-old-transactions": True}
        data = await self._call(body, "https://buy.itunes.apple.com/verifyReceipt")
        if data is None:
            return VerifyResult(False, detail="Apple verification failed", transient=True)
        # 21007: chitanta de test (sandbox) trimisa la serverul de productie. SECURITY: o chitanta
        # de sandbox se obtine GRATIS (TestFlight / tester sandbox), deci creditarea ei cu Astrans
        # reali = Astrans gratis pentru oricine. Reincercarea pe sandbox si creditarea sunt permise
        # DOAR pentru conturile din ASTRAN_APPLE_SANDBOX_USER_IDS (ex: contul de test folosit de
        # App Review); pentru oricine altcineva chitanta de sandbox e respinsa.
        sandbox_ok = _apple_sandbox_allowed(user)
        if data.get("status") == 21007:
            if not sandbox_ok:
                return VerifyResult(False, detail="Sandbox receipts are not accepted for this account")
            data = await self._call(body, "https://sandbox.itunes.apple.com/verifyReceipt")
            if data is None:
                return VerifyResult(False, detail="Apple verification failed", transient=True)

        if data.get("status") != 0:
            return VerifyResult(False, detail=f"Apple rejected receipt (status {data.get('status')})")
        # defensiv: chiar si pe raspunsul serverului de productie, un `environment` = Sandbox
        # inseamna aceeasi chitanta gratuita - aceeasi regula.
        if str(data.get("environment", "")).lower() == "sandbox" and not sandbox_ok:
            return VerifyResult(False, detail="Sandbox receipts are not accepted for this account")

        # fail-closed: fara APPLE_BUNDLE_ID se foloseste bundle id-ul aplicatiei (app.json)
        bundle_id = os.environ.get("APPLE_BUNDLE_ID") or "com.emergent.astranforge.wnjr5i"
        receipt = data.get("receipt", {})
        if receipt.get("bundle_id") != bundle_id:
            return VerifyResult(False, detail="Receipt belongs to a different app")

        product_id = package["package_id"]  # product id-ul din App Store Connect = package_id
        in_app = data.get("latest_receipt_info") or receipt.get("in_app") or []
        matches = [it for it in in_app if it.get("product_id") == product_id and it.get("transaction_id")]
        if not matches:
            return VerifyResult(False, detail="Receipt does not contain this product")
        # O chitanta poate contine mai multe achizitii ale aceluiasi produs: o alegem pe prima
        # NECREDITATA inca (altfel a doua achizitie platita nu s-ar crediti niciodata).
        match = matches[0]
        for it in matches:
            if not await db.astran_ledger.find_one({"provider_ref": it["transaction_id"]}, {"_id": 1}):
                match = it
                break
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

    result = await provider.verify(body.provider_token or "", {**package, "currency": cfg.get("currency", "RON")}, current)
    if not result.ok:
        # 503 = n-am putut verifica acum (reincercabil); 402 = plata e respinsa DEFINITIV
        raise HTTPException(status_code=503 if result.transient else 402, detail=result.detail or "Payment verification failed")

    if result.external_id:
        existing = await db.astran_ledger.find_one({"provider_ref": result.external_id}, {"_id": 0})
        if existing:
            return {"ok": True, "credited": 0, "transaction": existing, "idempotent": True}

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
        "price": package["price"],
        "currency": cfg.get("currency", "RON"),
    }
    if result.external_id:
        tx["provider_ref"] = result.external_id
    # Creditul + randul de ledger merg prin jurnal (money.py): plata REALA a fost deja luata de
    # furnizor, deci o cadere intre cele doua scrieri NU are voie sa o piarda. op_id e derivat din
    # identificatorul platii, deci indexul unic al jurnalului ramane arbitrul "o plata = o creditare".
    op_id = ("buy_" + hashlib.sha256(result.external_id.encode()).hexdigest()[:32]) if result.external_id \
        else money.new_op_id("buy_")
    try:
        await money.begin(
            db, op_id=op_id, kind="purchase", payer_id=current["user_id"], amount=0,
            credits=[{"user_id": current["user_id"], "amount": package["astrans"]}],
            inserts=[{"collection": "astran_ledger", "doc": tx}],
        )
    except DuplicateKeyError:
        prev = await money.get_op(db, op_id)
        if prev and prev["payer_id"] != current["user_id"]:
            raise HTTPException(status_code=409, detail="This payment was already used")
        if prev and prev["state"] == "pending":
            await money.run(db, op_id)  # o incercare anterioara a ramas la jumatate: o ducem la capat
        existing = await db.astran_ledger.find_one({"provider_ref": result.external_id}, {"_id": 0})
        return {"ok": True, "credited": 0, "transaction": existing, "idempotent": True}
    except Exception:  # noqa: BLE001
        log.exception("could not journal verified payment")
        raise HTTPException(status_code=503, detail="Could not record the purchase, try again - you will not be charged twice")
    await money.run(db, op_id)
    if provider.name == "google_play" and result.external_id:
        await provider.acknowledge(result.external_id, package["package_id"])
    return {"ok": True, "credited": package["astrans"], "transaction": {k: v for k, v in tx.items() if k != "_id"}}


class StripeIntentBody(BaseModel):
    package_id: str = Field(min_length=1, max_length=64)


_stripe_intent_limiter = SlidingWindowLimiter(20, 3600)


@api.post("/wallet/stripe/intent")
async def stripe_create_intent(body: StripeIntentBody, current=Depends(get_current_user)):
    """Creeaza PaymentIntent-ul Stripe PE SERVER, legat de contul care plateste (metadata).
    Clientul confirma plata cu client_secret, apoi trimite payment_intent_id la /wallet/buy
    (provider="stripe"); StripeProvider.verify refuza intent-urile altui cont."""
    wait = _stripe_intent_limiter.hit(current["user_id"])
    if wait is not None:
        raise _too_many(wait)
    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured")
    cfg = await db.platform_config.find_one({"key": "astran_packages"}, {"_id": 0})
    package = next((p for p in (cfg or {}).get("packages", []) if p["package_id"] == body.package_id), None)
    if not package:
        raise HTTPException(status_code=404, detail="Package not found")
    currency = str((cfg or {}).get("currency", "RON")).lower()
    async with httpx.AsyncClient(timeout=10.0) as h:
        try:
            r = await h.post(
                "https://api.stripe.com/v1/payment_intents", auth=(api_key, ""),
                data={
                    "amount": round(package["price"] * 100), "currency": currency,
                    "automatic_payment_methods[enabled]": "true",
                    "metadata[astran_user_id]": current["user_id"],
                    "metadata[package_id]": package["package_id"],
                },
            )
        except Exception as e:  # noqa: BLE001
            log.error("Stripe create intent error: %s", e)
            raise HTTPException(status_code=503, detail="Stripe unavailable")
    if r.status_code != 200:
        log.error("Stripe create intent rejected: %s", r.text[:300])
        raise HTTPException(status_code=502, detail="Could not create the payment")
    data = r.json()
    return {"payment_intent_id": data["id"], "client_secret": data.get("client_secret"),
            "amount": data.get("amount"), "currency": data.get("currency")}


@api.post("/wallet/stripe/webhook")
async def stripe_webhook(request: Request):
    """Webhook Stripe (refund total / dispute): retrage Astransii cumparati cu plata respectiva.
    Autentificat DOAR prin semnatura Stripe (STRIPE_WEBHOOK_SECRET), nu prin token de utilizator."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhook is not configured")
    payload = await request.body()
    if len(payload) > 1_000_000:
        raise HTTPException(status_code=413, detail="Payload too large")
    if not preversal.verify_stripe_signature(payload, request.headers.get("stripe-signature", ""), secret):
        raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    ref = preversal.stripe_reversal_ref(event)
    if ref:
        pi, reason = ref
        # tombstone INAINTE: daca plata nu a fost inca creditata (userul nu a apelat inca /wallet/buy),
        # verify() o va refuza ulterior.
        try:
            await db.payment_reversed_refs.insert_one({"ref": pi, "reason": reason, "at": now_utc()})
        except DuplicateKeyError:
            pass
        outcome = await preversal.reverse_purchase(db, pi, reason)
        log.warning("stripe %s for %s -> %s", reason, pi, outcome)
    elif isinstance(event, dict) and event.get("type") == "charge.refunded":
        # refund PARTIAL: nu se retrage automat; il lasam intr-o coada pentru verificare manuala
        log.warning("stripe partial refund needs manual review: %s", str((event.get("data") or {}).get("object", {}).get("id")))
        await db.payment_alerts.insert_one({"kind": "partial_refund", "event_id": event.get("id"), "at": now_utc()})
    return {"received": True}


_GOOGLE_VOIDED_INTERVAL = 900  # secunde


async def _reconcile_google_voided() -> int:
    """Citeste achizitiile ANULATE/RAMBURSATE de Google si retrage Astransii corespunzatori."""
    pkg_name = os.environ.get("GOOGLE_PLAY_PACKAGE_NAME")
    if not pkg_name or not os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"):
        return 0
    access_token = await _google_access_token()
    if not access_token:
        return 0
    from urllib.parse import quote
    cp = await db.platform_config.find_one({"key": "google_voided_checkpoint"}, {"_id": 0})
    now_ms = int(now_utc().timestamp() * 1000)
    # Google pastreaza ~30 zile; la prima rulare luam ultimele 7 zile, apoi cu 1h suprapunere
    start_ms = max(now_ms - 7 * 86400_000, int(cp["at_ms"]) - 3600_000) if cp else now_ms - 7 * 86400_000
    base = (f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
            f"{quote(pkg_name, safe='')}/purchases/voidedpurchases")
    params: dict = {"startTime": str(start_ms), "type": "0", "maxResults": "100"}
    reversed_n = 0
    async with httpx.AsyncClient(timeout=15.0) as h:
        for _ in range(10):  # plafon de pagini
            r = await h.get(base, params=params, headers={"Authorization": f"Bearer {access_token}"})
            if r.status_code != 200:
                log.warning("Google voidedpurchases status %s", r.status_code)
                return reversed_n  # NU avansam checkpoint-ul: reincercam la urmatoarea rulare
            data = r.json()
            for ref in preversal.google_voided_refs(data):
                try:
                    await db.payment_reversed_refs.insert_one({"ref": ref, "reason": "google_voided", "at": now_utc()})
                except DuplicateKeyError:
                    pass
                if await preversal.reverse_purchase(db, ref, "google_voided") == "reversed":
                    reversed_n += 1
            nxt = ((data.get("tokenPagination") or {}).get("nextPageToken"))
            if not nxt:
                break
            params["token"] = nxt
    await db.platform_config.update_one({"key": "google_voided_checkpoint"}, {"$set": {"key": "google_voided_checkpoint", "at_ms": now_ms}}, upsert=True)
    return reversed_n
async def _google_voided_loop() -> None:
    while True:
        try:
            await asyncio.sleep(_GOOGLE_VOIDED_INTERVAL)
            await _reconcile_google_voided()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("google voided reconcile failed")


async def _report_target_exists(body: ReportBody, current: dict) -> bool:
    tid = body.target_id
    if body.target_type == "user":
        return tid != current["user_id"] and await db.users.find_one({"user_id": tid}, {"_id": 1}) is not None
    if body.target_type == "game":
        return await db.games.find_one({"game_id": tid, "deleted": {"$ne": True}}, {"_id": 1}) is not None
    # "message": mesaje persistente (DM sau chat de grup). Raportezi doar mesaje la care ai acces:
    # altfel endpointul ar confirma existenta mesajelor private ale altora.
    dm = await db.dm_messages.find_one({"message_id": tid}, {"_id": 0, "conversation_id": 1})
    if dm:
        conv = await db.dm_conversations.find_one({"conversation_id": dm["conversation_id"]}, {"_id": 0, "user_a": 1, "user_b": 1})
        return bool(conv) and current["user_id"] in (conv.get("user_a"), conv.get("user_b"))
    gm = await db.group_chat_messages.find_one({"message_id": tid}, {"_id": 0, "group_id": 1})
    if gm:
        mem = await db.group_members.find_one({"group_id": gm["group_id"], "user_id": current["user_id"]}, {"_id": 0, "status": 1})
        return bool(mem) and mem.get("status") == "active"
    return False


@api.post("/reports")
async def create_report(body: ReportBody, current=Depends(get_current_user)):
    wait = _report_limiter.hit(current["user_id"])
    if wait is not None:
        raise _too_many(wait)
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")
    if not await _report_target_exists(body, current):
        raise HTTPException(status_code=404, detail="Report target not found")
    rid = new_id("rep_")
    doc = {
        "report_id": rid,
        "reporter_id": current["user_id"],
        "target_type": body.target_type,
        "target_id": body.target_id,
        "reason": reason,
        "details": (body.details or "").strip() or None,
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
    api.include_router(make_group_router(get_current_user, db, _friend_public))

# CHAT IN JOC (admin): /api/admin/chat/rules (GET/PUT/POST/DELETE), /api/admin/chat/mute|unmute
if make_chat_admin_router is not None:
    api.include_router(make_chat_admin_router(get_current_user, db))

# MESAGERIE PRIVATA: /api/dm/open, /api/dm/conversations, /api/dm/unread,
# /api/dm/conversations/{id}/messages|read, /api/dm/messages/{id}/report  +  WebSocket /ws/dm
if make_dm_routers is not None:
    _dm_api_router, _dm_ws_router = make_dm_routers(get_current_user, resolve_user_from_token, db, _friend_public)
    api.include_router(_dm_api_router)
    app.include_router(_dm_ws_router)  # pe `app`, fara prefixul /api (ca WebSocket-ul din realtime.py)

# GAME COLLABORATION / TESTERS: /api/games/{id}/collaborators, /api/games/{id}/testers,
# /api/games/{id}/my-access
if make_game_permissions_router is not None:
    # SECURITY: ownerul vede doar profilul public al colaboratorilor (fara email/sold)
    api.include_router(make_game_permissions_router(get_current_user, db, _user_public_minimal))

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
