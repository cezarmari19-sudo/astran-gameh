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

from typing import Any, Callable, Optional

from astran_sandbox import contact_policy


class FriendError(Exception):
    def __init__(self, status: int, detail: Any, code: Optional[str] = None) -> None:
        super().__init__(str(detail))
        self.status, self.detail, self.code = status, detail, code


def contact_denied() -> FriendError:
    """Eroare GENERICA (nu dezvaluie care parte e minora)."""
    return FriendError(403, {"code": contact_policy.DENIED_CODE, "message": contact_policy.DENIED_MESSAGE},
                       contact_policy.DENIED_CODE)


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
    if not contact_policy.users_contact_allowed(me, target):
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
    if not contact_policy.users_contact_allowed(me, other):
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
    return {"friends": [public(u) for u in users if contact_policy.users_contact_allowed(me_doc, u)]}


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
        "incoming": [public(u) for u in in_users if contact_policy.users_contact_allowed(me_doc, u)],
        "outgoing": [public(u) for u in out_users if contact_policy.users_contact_allowed(me_doc, u)],
    }
