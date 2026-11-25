"""Izolarea pe categorii de varsta in GRUPURI (membri + chat). Modul fara dependinte externe (doar stdlib), ca logica
sa poata fi testata fara FastAPI/Mongo si apelata din orice cale care adauga un membru.

Regula (aplicata EXCLUSIV pe server, vezi contact_policy.same_group_allowed - unica sursa de adevar):
  * un grup are o categorie PERSISTENTA (`groups.age_category`: "under_18" | "adult_18") fixata la creare din
    categoria proprietarului, citita din DB. Nu exista nicio ruta prin care un membru/admin/owner o poate schimba;
  * un utilizator poate fi membru DOAR daca categoria lui (din `users.age_category`, NICIODATA din cerere) este
    identica cu a grupului; categorie necunoscuta => refuz (nu se presupune adult sau minor);
  * un grup mostenit (fara categorie valida) sau marcat `age_review` e INGHETAT: nu primeste membri noi si nu
    livreaza chat pana la rezolvarea explicita (vezi group_age_migration.py);
  * orice cale de adaugare (join, reintrare dupa leave, invitatie/cod - cand vor exista) TREBUIE sa treaca prin
    `add_member`; verificarea se repeta la fiecare apel, inclusiv la reintrare.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import age_policy as AP
from . import contact_policy as CP

log = logging.getLogger("astran.group_age")


class GroupAccessError(Exception):
    """Refuz de acces/membru. `status` = cod HTTP; `code` stabil; `message` generic (nu dezvaluie varsta nimanui)."""

    def __init__(self, status: int, code: str, message: Optional[str] = None):
        self.status, self.code = status, code
        self.message = message or CP.MESSAGES.get(code, code)
        super().__init__(self.message)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


def _pid(value: Any) -> str:
    """Pseudonim scurt pentru loguri de securitate (minimizarea datelor: fara user_id/username in clar)."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:10]


def audit(event: str, *, user_id: Any = None, group_id: Any = None, code: Optional[str] = None) -> None:
    log.warning("age_scope event=%s code=%s user=%s group=%s", event, code or "-",
                _pid(user_id) if user_id else "-", _pid(group_id) if group_id else "-")


# ------------------------------------------------------------------------------------------------ categorii
async def user_category(db, user_id: str) -> Optional[str]:
    """Categoria CURENTA din DB (sursa autoritativa). None = utilizator lipsa sau categorie necunoscuta."""
    doc = await db.users.find_one({"user_id": user_id}, AP.ACCESS_PROJECTION)
    return CP.category_of(AP.effective_category(doc))


async def user_categories(db, user_ids: list) -> dict:
    """{user_id: categorie | None} pentru o lista de utilizatori (un singur query)."""
    ids = sorted({u for u in user_ids if isinstance(u, str)})
    if not ids:
        return {}
    rows = await db.users.find({"user_id": {"$in": ids}}, AP.ACCESS_PROJECTION).to_list(len(ids))
    out = {u: None for u in ids}
    for r in rows:
        out[r["user_id"]] = CP.category_of(AP.effective_category(r))
    return out


def group_category(group: Optional[dict]) -> Optional[str]:
    """Categoria persistenta a grupului, sau None daca grupul e mostenit/necategorizat."""
    return CP.category_of(group.get("age_category")) if isinstance(group, dict) else None


def group_frozen(group: Optional[dict]) -> bool:
    """Inghetat = fara categorie valida SAU marcat explicit pentru revizuire."""
    return group_category(group) is None or bool(group.get("age_review"))


def check_group_access(group: dict, user_cat: Optional[str], *, user_id: Any = None, event: str = "group_access") -> None:
    """Ridica GroupAccessError daca un utilizator cu `user_cat` nu are voie in `group`. Ordinea conteaza:
    grup inghetat -> varsta utilizatorului necunoscuta -> categorii diferite."""
    gid = group.get("group_id") if isinstance(group, dict) else None
    if group_frozen(group):
        audit(event, user_id=user_id, group_id=gid, code=CP.CODE_GROUP_REVIEW)
        raise GroupAccessError(423, CP.CODE_GROUP_REVIEW)
    code = CP.same_group_allowed(user_cat, group_category(group))
    if code is not None:
        audit(event, user_id=user_id, group_id=gid, code=code)
        raise GroupAccessError(403, code)


# ------------------------------------------------------------------------------------------------ membri
async def add_member(db, group_id: str, user_id: str, *, role: str = "member") -> str:
    """UNICA cale de adaugare a unui membru (join, reintrare, invitatie, cod, import...). Intoarce "joined" sau
    "rejoined". Ridica GroupAccessError.

    Atomicitate: categoria grupului e imuabila, deci doi utilizatori din categorii diferite care intra simultan sunt
    decisi fiecare independent fata de aceeasi categorie a grupului - doar cel compatibil trece. Fereastra ramasa
    (grupul e inghetat de o migrare / categoria utilizatorului se schimba intre citire si scriere) se inchide cu o
    RE-VERIFICARE dupa scriere si anulare (compensare) daca nu mai e valida."""
    group = await db.groups.find_one({"group_id": group_id}, {"_id": 0})
    if not group:
        raise GroupAccessError(404, "group_not_found", "Group not found")
    cat = await user_category(db, user_id)
    check_group_access(group, cat, user_id=user_id, event="join_denied")

    existing = await db.group_members.find_one({"group_id": group_id, "user_id": user_id}, {"_id": 0})
    outcome = "joined"
    if existing:
        if existing.get("status") == "banned":
            raise GroupAccessError(403, "banned", "You are banned from this Group")
        if existing.get("status") != "left":
            raise GroupAccessError(409, "already_member", "Already a member")
        await db.group_members.update_one({"group_id": group_id, "user_id": user_id},
                                          {"$set": {"status": "active", "role": role}})
        outcome = "rejoined"
    else:
        try:
            await db.group_members.insert_one({
                "group_id": group_id, "user_id": user_id, "role": role, "status": "active", "muted": False,
                "joined_at": datetime.now(timezone.utc),
            })
        except Exception as e:  # noqa: BLE001 - DuplicateKeyError (pymongo) fara a importa pymongo aici
            if type(e).__name__ == "DuplicateKeyError":
                raise GroupAccessError(409, "already_member", "Already a member")
            raise

    # re-verificare dupa scriere (grup inghetat / categorie schimbata in fereastra dintre citire si scriere)
    group2 = await db.groups.find_one({"group_id": group_id}, {"_id": 0})
    cat2 = await user_category(db, user_id)
    try:
        if not group2:
            raise GroupAccessError(404, "group_not_found", "Group not found")
        check_group_access(group2, cat2, user_id=user_id, event="join_reverify_denied")
    except GroupAccessError:
        if outcome == "joined":
            await db.group_members.delete_one({"group_id": group_id, "user_id": user_id})
        else:
            await db.group_members.update_one({"group_id": group_id, "user_id": user_id}, {"$set": {"status": "left"}})
        raise
    return outcome


# ------------------------------------------------------------------------------------------------ chat de grup
async def sender_category_or_deny(db, group: dict, user_id: str) -> str:
    """Categoria expeditorului (din DB) daca are voie sa participe la chatul grupului; altfel GroupAccessError."""
    cat = await user_category(db, user_id)
    check_group_access(group, cat, user_id=user_id, event="chat_send_denied")
    return cat  # type: ignore[return-value]  # check_group_access garanteaza ca nu e None


async def visible_messages(db, group: dict, viewer_id: str, msgs: list) -> list:
    """Filtreaza mesajele unui grup pentru un spectator: doar mesajele autorilor din ACEEASI categorie cunoscuta
    (plus propriile mesaje). Defensiv si in grupuri mixte mostenite: categoria autorului vine din
    `sender_age_category` (scris la trimitere, din DB), iar pentru mesaje vechi din categoria CURENTA a autorului
    din DB; necunoscuta => ascuns. Spectator fara categorie valida => nimic din ce au scris altii."""
    check_group_access(group, await user_category(db, viewer_id), user_id=viewer_id, event="chat_read_denied")
    viewer_cat = await user_category(db, viewer_id)
    unresolved = [m["user_id"] for m in msgs if CP.category_of(m.get("sender_age_category")) is None
                  and m.get("user_id") != viewer_id]
    current = await user_categories(db, unresolved) if unresolved else {}
    out = []
    for m in msgs:
        if m.get("user_id") == viewer_id:
            out.append(m)
            continue
        author = CP.category_of(m.get("sender_age_category")) or current.get(m.get("user_id"))
        if CP.chat_allowed(author, viewer_cat):
            out.append(m)
    return out
