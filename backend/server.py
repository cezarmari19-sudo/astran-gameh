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
from pydantic import BaseModel, Field, EmailStr, StrictBool
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
from astran_sandbox import trash_purge
from astran_sandbox import privacy            # GDPR: stergere cont + export date (vezi PRIVACY_GDPR.md)
from astran_sandbox.privacy_routes import make_privacy_router
from astran_sandbox import privacy_export            # GDPR: export ZIP asincron (Art. 15/20)
from astran_sandbox.account_export_routes import make_account_export_router
from astran_sandbox import consent as consent_svc      # GDPR: consimtamant demonstrabil (registru adauga-doar)
from astran_sandbox import legal_docs                  # versiuni Terms of Service / Privacy Policy (hash verificat)
from astran_sandbox.consent_routes import make_consent_router, make_legal_router
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
    # GDPR: acceptarea e VALIDATA de server (nu doar checkbox in UI). Fara ambele `true` contul nu se creeaza.
    # Versiunea si data acceptarii le decide serverul; `presented_versions` e doar comparat cu versiunea curenta.
    accept_terms: StrictBool = False
    accept_privacy: StrictBool = False
    optional_consents: dict[str, bool] = Field(default_factory=dict)   # ex. {"marketing_communications": false}
    presented_versions: Optional[dict[str, str]] = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class SessionBody(BaseModel):
    session_id: str
    # Folosite DOAR cand sesiunea Google creeaza un cont NOU (vezi google_session); ignorate pentru conturi existente.
    accept_terms: StrictBool = False
    accept_privacy: StrictBool = False
    optional_consents: dict[str, bool] = Field(default_factory=dict)
    presented_versions: Optional[dict[str, str]] = None
    consent_language: Optional[str] = None


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
        su = await db.users.find_one({"user_id": sess["user_id"]}, {"_id": 0})
        if su is not None and su.get("deletion_state"):
            return None   # GDPR: cont in curs de stergere - nicio sesiune/token nu mai este valid
        return _strip_secret(su)

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG], leeway=JWT_IAT_LEEWAY)
        u = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0})
        if u is not None and u.get("deletion_state"):
            return None   # GDPR: cont in curs de stergere
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
    # Cosul de gunoi: stergere definitiva automata dupa TRASH_RETENTION_DAYS (30) - vezi astran_sandbox/trash_purge.py.
    await trash_purge.ensure_indexes(db)
    app.state.trash_purge_task = asyncio.create_task(trash_purge.purge_loop(db))
    # GDPR: indexuri + worker care reia stergerile de conturi ramase la jumatate si elimina datele pseudonimizate expirate.
    await privacy.ensure_indexes(db)
    legal_docs.get_registry()   # fail-closed: manifest + amprente SHA-256 verificate la pornire
    await consent_svc.ensure_indexes(db)
    app.state.privacy_task = asyncio.create_task(privacy.deletion_worker(db))
    await privacy_export.ensure_indexes(db)
    app.state.privacy_export_task = asyncio.create_task(privacy_export.export_worker(db))
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
        await trash_purge.purge_owner_games_data(db, demo_owner_id)  # fara date orfane
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
    ptask = getattr(app.state, "trash_purge_task", None)
    if ptask is not None:
        ptask.cancel()
    gdpr_task = getattr(app.state, "privacy_task", None)
    if gdpr_task is not None:
        gdpr_task.cancel()
    gdpr_export_task = getattr(app.state, "privacy_export_task", None)
    if gdpr_export_task is not None:
        gdpr_export_task.cancel()
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
    # GDPR: acceptarea obligatorie se valideaza INAINTE de orice scriere; fara ea nu se creeaza contul.
    try:
        consent_checked = consent_svc.validate_registration_consent(
            body.accept_terms, body.accept_privacy, body.optional_consents, body.presented_versions,
            age_category=body.age_category, lang=body.language)
    except consent_svc.ConsentError as ce:
        raise HTTPException(status_code=ce.status, detail=ce.detail())
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
    # GDPR: dovada acceptarii se scrie persistent, imediat dupa cont. Daca scrierea esueaza, contul tocmai creat
    # (inca fara bonus, sesiune sau alte date) se anuleaza: nu poate exista un cont fara dovada acceptarii.
    try:
        await consent_svc.record_registration_consents(
            db, user_id, consent_checked, age_category=body.age_category, lang=body.language, source="register")
    except Exception:
        log.exception("consent proof could not be stored; rolling back registration")
        await db.users.delete_one({"user_id": user_id})
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")
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
    if user.get("deletion_state"):
        raise HTTPException(status_code=403, detail="Account deletion is in progress")
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
    if existing and existing.get("deletion_state"):
        raise HTTPException(status_code=403, detail="Account deletion is in progress")
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
        # GDPR: un cont NOU nu se creeaza fara acceptarea validata pe server (aceeasi regula ca la /auth/register).
        # Nu s-a scris nimic pana aici; clientul cere acceptarea si reia autentificarea Google.
        try:
            google_consent = consent_svc.validate_registration_consent(
                body.accept_terms, body.accept_privacy, body.optional_consents, body.presented_versions,
                age_category="under_18", lang=body.consent_language or "en")
        except consent_svc.ConsentError as ce:
            raise HTTPException(status_code=ce.status, detail=ce.detail())
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
        try:
            await consent_svc.record_registration_consents(
                db, user_id, google_consent, age_category="under_18", lang=body.consent_language or "en", source="google_signup")
        except Exception:
            log.exception("consent proof could not be stored; rolling back google signup")
            await db.users.delete_one({"user_id": user_id})
            raise HTTPException(status_code=500, detail="Registration failed. Please try again.")
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
