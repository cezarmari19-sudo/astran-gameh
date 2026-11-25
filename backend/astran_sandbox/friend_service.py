"""Logica prieteniilor (cerere / acceptare / liste), extrasa din server.py ca sa fie testabila fara FastAPI.

server.py pastreaza DOAR: autentificarea, rate-limiter-ul si maparea FriendError -> HTTPException.
Toate deciziile de siguranta sunt aici si folosesc contact_policy (categoria de varsta se citeste din DB,
pentru AMBII utilizatori, la fiecare operatie - nu se foloseste nimic din body / din dict-ul `me` primit).

Regula Adult<->Minor se aplica pe TOATE caile care creeaza o prietenie:
  * send_request            - cerere noua                                  (blocat inainte de orice insert)
  * send_request (reverse)  - auto-acceptarea a doua cereri simultane      (acelasi control, inainte de ea)
  * accept_request          - acceptarea unei cereri pendinte, inclusiv cele create inainte de aceasta politica
Listele (/friends, /friends/requests) ascund relatiile care incalca regula (prietenii vechi / cereri vechi),
fara a sterge date; vezi si contact_migration.py pentru curatarea optionala.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from astran_sandbox import age_policy as _AP
from astran_sandbox import contact_policy


class FriendError(Exception):
    def __init__(self, status: int, detail: Any, code: Optional[str] = None) -> None:
        super().__init__(str(detail))
        self.status, self.detail, self.code = status, detail, code


def contact_denied() -> FriendError:
    """Eroare GENERICA (nu dezvaluie care parte e minora)."""
    return FriendError(403, {"code": contact_policy.DENIED_CODE, "message": contact_policy.DENIED_MESSAGE},
                       contact_policy.DENIED_CODE)


# Id-urile de utilizator sunt generate de server (user_<hex>), dar contul demo / conturile vechi pot avea alt format:
# acceptam doar caractere sigure si o lungime limitata, ca un id arbitrar sa nu ajunga niciodata in interogari.
_USER_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
MAX_BLOCKED_LIST = 500


def clean_user_id(value: Any) -> str:
    """Validare stricta a id-ului tinta (string, 1-64 caractere sigure). Altfel 400 'Invalid user id'."""
    if not isinstance(value, str) or not _USER_ID_RE.fullmatch(value):
        raise FriendError(400, "Invalid user id")
    return value


def pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _is_dup(exc: BaseException) -> bool:
    return type(exc).__name__ == "DuplicateKeyError"   # pymongo sau FakeDB din teste, fara import de pymongo aici


async def _load_user(db, uid: str) -> Optional[dict]:
    return await db.users.find_one({"user_id": uid}, {"_id": 0, "password_hash": 0})


async def send_request(db, me_id: str, target_id: str, now, *, max_pending: int = 100) -> dict:
    if target_id == me_id:
        raise FriendError(400, "Cannot friend yourself")
    # Ambele conturi se recitesc din DB: categoria de varsta NU vine din request si nu din `me` primit.
    me = await _load_user(db, me_id)
    target = await _load_user(db, target_id)
    if me is None:
        raise FriendError(401, "Invalid token")
    if not target or target.get("deletion_state"):
        raise FriendError(404, "User not found")
    # POLITICA DE SIGURANTA - inainte de orice scriere / alta verificare care ar putea crea o relatie.
    if not contact_policy.users_contact_allowed(_AP.derive_category_doc(me), _AP.derive_category_doc(target)):
        raise contact_denied()
    if await db.friend_requests.count_documents({"from_id": me_id, "status": "pending"}) >= max_pending:
        raise FriendError(429, "Too many pending friend requests")
    a, b = pair(me_id, target_id)
    if await db.friendships.find_one({"user_a": a, "user_b": b}):
        raise FriendError(409, "Already friends")
    if await db.blocks.find_one({"$or": [
        {"blocker_id": me_id, "blocked_id": target_id},
        {"blocker_id": target_id, "blocked_id": me_id},
    ]}):
        raise FriendError(403, "Blocked")
    # Cererea inversa pendinta: cele doua cereri simultane devin prietenie (politica a fost verificata mai sus).
    reverse = await db.friend_requests.find_one({"from_id": target_id, "to_id": me_id, "status": "pending"})
    if reverse:
        await db.friendships.update_one(
            {"user_a": a, "user_b": b},
            {"$setOnInsert": {"user_a": a, "user_b": b, "since": now()}},
            upsert=True,
        )
        await db.friend_requests.delete_one({"from_id": target_id, "to_id": me_id})
        return {"ok": True, "auto_accepted": True}
    try:
        await db.friend_requests.insert_one({
            "from_id": me_id, "to_id": target_id, "status": "pending", "created_at": now(),
        })
    except Exception as e:  # noqa: BLE001
        if _is_dup(e):
            raise FriendError(409, "Request already exists")
        raise
    return {"ok": True}


async def accept_request(db, me_id: str, from_id: str, now) -> dict:
    req = await db.friend_requests.find_one(
        {"from_id": from_id, "to_id": me_id, "status": "pending"}, {"_id": 0})
    if not req:
        raise FriendError(404, "No pending request")
    me = await _load_user(db, me_id)
    other = await _load_user(db, from_id)
    if me is None:
        raise FriendError(401, "Invalid token")
    if other is None or other.get("deletion_state"):
        await db.friend_requests.delete_one({"from_id": from_id, "to_id": me_id})
        raise FriendError(404, "No pending request")
    # O cerere pendinta veche (creata inainte de politica / inainte ca cineva sa-si schimbe categoria)
    # NU se poate accepta: se sterge, ca sa nu mai apara, si se refuza.
    if not contact_policy.users_contact_allowed(_AP.derive_category_doc(me), _AP.derive_category_doc(other)):
        await db.friend_requests.delete_one({"from_id": from_id, "to_id": me_id})
        raise contact_denied()
    a, b = pair(me_id, from_id)
    await db.friendships.update_one({"user_a": a, "user_b": b},
                                    {"$setOnInsert": {"user_a": a, "user_b": b, "since": now()}}, upsert=True)
    await db.friend_requests.delete_one({"from_id": from_id, "to_id": me_id})
    return {"ok": True}


async def list_friends(db, me: dict, public: Callable[[dict], dict]) -> dict:
    uid = me["user_id"]
    edges = await db.friendships.find({"$or": [{"user_a": uid}, {"user_b": uid}]}, {"_id": 0}).to_list(500)
    ids = [e["user_b"] if e["user_a"] == uid else e["user_a"] for e in edges]
    users = await db.users.find({"user_id": {"$in": ids}}, {"_id": 0, "password_hash": 0}).to_list(500)
    me_doc = await _load_user(db, uid)
    # prieteniile Adult<->Minor (ex. create inainte de politica) nu se listeaza: nu se poate initia DM din ele
    return {"friends": [public(u) for u in users if contact_policy.users_contact_allowed(_AP.derive_category_doc(me_doc), _AP.derive_category_doc(u))]}


async def list_requests(db, me: dict, public: Callable[[dict], dict]) -> dict:
    uid = me["user_id"]
    incoming = await db.friend_requests.find({"to_id": uid, "status": "pending"}, {"_id": 0}).to_list(200)
    outgoing = await db.friend_requests.find({"from_id": uid, "status": "pending"}, {"_id": 0}).to_list(200)
    in_users = await db.users.find({"user_id": {"$in": [r["from_id"] for r in incoming]}},
                                   {"_id": 0, "password_hash": 0}).to_list(200)
    out_users = await db.users.find({"user_id": {"$in": [r["to_id"] for r in outgoing]}},
                                    {"_id": 0, "password_hash": 0}).to_list(200)
    me_doc = await _load_user(db, uid)
    return {
        "incoming": [public(u) for u in in_users if contact_policy.users_contact_allowed(_AP.derive_category_doc(me_doc), _AP.derive_category_doc(u))],
        "outgoing": [public(u) for u in out_users if contact_policy.users_contact_allowed(_AP.derive_category_doc(me_doc), _AP.derive_category_doc(u))],
    }


# ----------------------------------------------------------------------------------------------- blocari
async def block_user(db, me_id: str, target_id: str, now) -> dict:
    """Blocheaza `target_id`. Comportament neschimbat fata de vechiul handler din server.py: sterge prietenia si
    cererile in ambele sensuri, apoi scrie (idempotent) randul din db.blocks."""
    if target_id == me_id:
        raise FriendError(400, "Cannot block yourself")
    a, b = pair(me_id, target_id)
    await db.friendships.delete_one({"user_a": a, "user_b": b})
    await db.friend_requests.delete_many({"$or": [
        {"from_id": me_id, "to_id": target_id},
        {"from_id": target_id, "to_id": me_id},
    ]})
    await db.blocks.update_one(
        {"blocker_id": me_id, "blocked_id": target_id},
        {"$setOnInsert": {"blocker_id": me_id, "blocked_id": target_id, "since": now()}},
        upsert=True,
    )
    return {"ok": True}


async def unblock_user(db, me_id: str, target_id: str) -> dict:
    """Ridica DOAR blocarea facuta de `me_id`.

    * Filtrul contine mereu blocker_id = me_id: nu se poate atinge blocarea facuta de altcineva (inclusiv blocarea
      inversa target -> me, care ramane in vigoare si nu e dezvaluita).
    * NU scrie nimic in friendships / friend_requests / conversatii: deblocarea doar scoate randul din db.blocks.
      Prietenia nu revine, nu se trimite nicio cerere, iar politica Adult<->Minor si verificarile DM raman cele
      aplicate la fiecare operatie (friend_service.send_request/accept_request, dm_core).
    * Utilizator neblocat de tine -> 404 (cod `not_blocked`)."""
    if target_id == me_id:
        raise FriendError(400, "Cannot unblock yourself")
    res = await db.blocks.delete_one({"blocker_id": me_id, "blocked_id": target_id})
    if not getattr(res, "deleted_count", 0):
        raise FriendError(404, "User is not blocked", "not_blocked")
    return {"ok": True}


def blocked_public(user: dict) -> dict:
    """DOAR ce are nevoie UI-ul: id, username, nume afisat, avatar. Fara email / sold / varsta / alte campuri."""
    return {
        "user_id": user["user_id"],
        "username": user.get("username"),
        "display_name": user.get("display_name") or user.get("username"),
        "avatar_url": user.get("avatar_url"),
    }


async def list_blocked(db, me_id: str) -> dict:
    """Utilizatorii blocati de contul curent (nu si cei care l-au blocat pe el). Conturile sterse nu se afiseaza."""
    rows = await db.blocks.find({"blocker_id": me_id}, {"_id": 0, "blocked_id": 1, "since": 1}).to_list(MAX_BLOCKED_LIST)
    ids = [r["blocked_id"] for r in rows if r.get("blocked_id")]
    if not ids:
        return {"blocked": []}
    users = await db.users.find({"user_id": {"$in": ids}},
                                {"_id": 0, "user_id": 1, "username": 1, "display_name": 1, "avatar_url": 1,
                                 "deletion_state": 1}).to_list(MAX_BLOCKED_LIST)
    by_id = {u["user_id"]: u for u in users if not u.get("deletion_state")}
    # cele mai recente blocari primele
    rows.sort(key=lambda r: str(r.get("since") or ""), reverse=True)
    return {"blocked": [blocked_public(by_id[r["blocked_id"]]) for r in rows if r.get("blocked_id") in by_id]}
