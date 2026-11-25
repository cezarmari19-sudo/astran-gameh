"""Migrare SIGURA a grupurilor create inainte de politica Adult<->Minor + procedura de separare.

CE FACE `audit_groups`:
  * citeste fiecare grup + membrii ACTIVI (+ proprietarul) si categoria lor din `users.age_category` (sursa autoritativa);
  * grup cu TOTI membrii intr-o singura categorie cunoscuta (si, daca are deja `age_category`, aceeasi) =>
    primeste/pastreaza `age_category` si `age_review` dispare (grup compatibil);
  * orice alt caz (membri din categorii diferite, categorie necunoscuta/lipsa la cineva, categoria grupului diferita de
    a membrilor) => grupul e INGHETAT: `age_review=True` + motiv. Un grup inghetat nu primeste membri noi (423) si nu
    livreaza chat (vezi group_age.check_group_access) pana la rezolvare.
  * NU sterge si NU scoate niciun utilizator, NU sterge mesaje, NU modifica `users`. Scrie DOAR in `groups`.
  * NU inventeaza categorii: un cont fara categorie valida ramane "necunoscut".
  * Implicit DRY-RUN; `--apply` scrie. Idempotent (a doua rulare nu schimba nimic).

CE FACE `split_group` (separarea unui grup mixt, tot dry-run implicit, pe un singur grup, actiune de operator):
  * grupul ORIGINAL ramane cu categoria PROPRIETARULUI; membrii din cealalta categorie sunt mutati intr-un grup NOU
    (categoria lor), al carui proprietar e primul membru eligibil (cel mai vechi admin, apoi cel mai vechi membru care
    nu detine deja un grup - indexul unic `owner_id`). Randurile vechi de membru NU se sterg: devin `status="left"`
    cu `split_to` (istoric pastrat); mesajele raman neschimbate in grupul original (vizibile doar aceleiasi categorii).
  * daca nu exista un proprietar eligibil pentru cealalta categorie, NU se face nimic (raport `no_eligible_owner`).
  * membrii cu categorie necunoscuta nu sunt mutati (nu inventam categorii): grupul ramane inghetat pana li se stabileste.

    cd backend && MONGO_URL=... DB_NAME=... python -m astran_sandbox.group_age_migration                 # dry-run audit
    cd backend && MONGO_URL=... DB_NAME=... python -m astran_sandbox.group_age_migration --apply         # aplica
    cd backend && ... python -m astran_sandbox.group_age_migration --split GROUP_ID [--apply]             # separare
"""
from __future__ import annotations

import asyncio
import os
import secrets
import string
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import contact_policy as CP
from . import group_age as GA

REASON_MIXED = "mixed_categories"
REASON_UNKNOWN_MEMBER = "unknown_member_category"
REASON_UNKNOWN_OWNER = "unknown_owner_category"
REASON_CATEGORY_MISMATCH = "group_category_mismatch"

_TOKEN_CHARS = string.ascii_letters + string.digits


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify(group: dict, owner_cat: Optional[str], member_cats: list) -> tuple[Optional[str], Optional[str]]:
    """(categoria de setat pe grup | None, motiv de inghetare | None). Functie pura (testabila).
    `member_cats` = categoriile membrilor ACTIVI (None = necunoscuta)."""
    if owner_cat is None:
        return None, REASON_UNKNOWN_OWNER
    if any(c is None for c in member_cats):
        return None, REASON_UNKNOWN_MEMBER
    if any(c != owner_cat for c in member_cats):
        return None, REASON_MIXED
    existing = GA.group_category(group)
    if existing is not None and existing != owner_cat:
        return None, REASON_CATEGORY_MISMATCH
    return owner_cat, None


async def audit_groups(db, *, apply: bool = False) -> dict:
    groups = await db.groups.find({}, {"_id": 0}).to_list(None)
    frozen: dict = {}
    categorized = unchanged = 0
    for g in groups:
        rows = await db.group_members.find({"group_id": g["group_id"], "status": "active"}, {"_id": 0, "user_id": 1}).to_list(None)
        ids = sorted({r["user_id"] for r in rows} | {g["owner_id"]})
        cats = await GA.user_categories(db, ids)
        member_cats = [cats.get(u) for u in ids]
        category, reason = classify(g, cats.get(g["owner_id"]), member_cats)
        if reason is None:
            changed = GA.group_category(g) != category or g.get("age_review")
            if changed:
                categorized += 1
                if apply:
                    await db.groups.update_one({"group_id": g["group_id"]}, {"$set": {"age_category": category, "age_review": False},
                                                                          "$unset": {"age_review_reason": "", "age_review_at": ""}})
            else:
                unchanged += 1
        else:
            already = bool(g.get("age_review")) and g.get("age_review_reason") == reason
            frozen[g["group_id"]] = reason
            if apply and not already:
                await db.groups.update_one({"group_id": g["group_id"]},
                                           {"$set": {"age_review": True, "age_review_reason": reason, "age_review_at": _now()}})
    return {"applied": bool(apply), "groups_total": len(groups), "groups_categorized_or_confirmed": categorized,
            "groups_unchanged": unchanged, "groups_frozen": len(frozen), "frozen": frozen}


async def _owns_group(db, user_id: str) -> bool:
    return await db.groups.find_one({"owner_id": user_id}, {"_id": 0, "group_id": 1}) is not None


async def split_group(db, group_id: str, *, apply: bool = False) -> dict:
    g = await db.groups.find_one({"group_id": group_id}, {"_id": 0})
    if not g:
        return {"status": "group_not_found"}
    rows = await db.group_members.find({"group_id": group_id, "status": "active"}, {"_id": 0}).to_list(None)
    ids = sorted({r["user_id"] for r in rows} | {g["owner_id"]})
    cats = await GA.user_categories(db, ids)
    owner_cat = cats.get(g["owner_id"])
    if owner_cat is None:
        return {"status": "owner_category_unknown"}
    movers = [r for r in rows if cats.get(r["user_id"]) is not None and cats[r["user_id"]] != owner_cat]
    unknown = [r["user_id"] for r in rows if cats.get(r["user_id"]) is None]
    if not movers:
        return {"status": "nothing_to_split", "unknown_members": len(unknown)}
    other_cat = cats[movers[0]["user_id"]]
    movers.sort(key=lambda r: (r.get("role") != "admin", r.get("joined_at") or _now()))
    new_owner = None
    for r in movers:
        if not await _owns_group(db, r["user_id"]):
            new_owner = r["user_id"]
            break
    if new_owner is None:
        return {"status": "no_eligible_owner", "members_to_move": len(movers)}
    plan = {"status": "planned", "members_to_move": len(movers), "unknown_members_left_in_place": len(unknown),
            "original_keeps_category": owner_cat, "new_group_category": other_cat, "applied": bool(apply)}
    if not apply:
        return plan

    new_gid = "group_" + uuid.uuid4().hex[:16]
    now = _now()
    token = "".join(secrets.choice(_TOKEN_CHARS) for _ in range(35))
    await db.groups.insert_one({
        "group_id": new_gid, "owner_id": new_owner, "name": f"{g['name']} (2)"[:50], "description": g.get("description", ""),
        "logo_url": g.get("logo_url"), "token": token, "chat_mode": g.get("chat_mode", "everyone"),
        "age_category": other_cat, "split_from": group_id, "created_at": now, "updated_at": now,
    })
    for r in movers:
        uid = r["user_id"]
        await db.group_members.insert_one({
            "group_id": new_gid, "user_id": uid, "role": "owner" if uid == new_owner else r.get("role", "member"),
            "status": "active", "muted": bool(r.get("muted")), "joined_at": r.get("joined_at") or now,
        })
        # randul vechi NU se sterge (istoric): doar iese din uz
        await db.group_members.update_one({"group_id": group_id, "user_id": uid},
                                          {"$set": {"status": "left", "role": "member", "split_to": new_gid}})
    remaining = [r["user_id"] for r in rows if r["user_id"] not in {m["user_id"] for m in movers}]
    if not unknown:
        await db.groups.update_one({"group_id": group_id}, {"$set": {"age_category": owner_cat, "age_review": False},
                                                            "$unset": {"age_review_reason": "", "age_review_at": ""}})
    else:
        await db.groups.update_one({"group_id": group_id}, {"$set": {"age_category": owner_cat, "age_review": True,
                                                                     "age_review_reason": REASON_UNKNOWN_MEMBER, "age_review_at": now}})
    plan.update({"new_group_id": new_gid, "remaining_members": len(remaining)})
    return plan


async def _main(argv: list) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient   # import local: modulul ramane importabil fara motor (teste)
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "astran_game")]
    try:
        apply = "--apply" in argv
        if "--split" in argv:
            print(await split_group(db, argv[argv.index("--split") + 1], apply=apply))
        else:
            print(await audit_groups(db, apply=apply))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
