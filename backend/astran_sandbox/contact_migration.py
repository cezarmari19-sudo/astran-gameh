"""Curatare OPTIONALA a relatiilor Adult<->Minor create inainte de politica de contact.

NU este necesara pentru siguranta: contact_policy se aplica la CITIRE/SCRIERE pe server (DM open/send/history/lista,
liste de prieteni, acceptare), deci o prietenie veche Adult<->Minor nu poate fi folosita pentru DM chiar daca ramane
in baza de date. Scriptul doar scoate din DB randurile care nu mai pot fi folosite legitim:
  * `friendships` intre un adult si un minor
  * `friend_requests` PENDINTE intre un adult si un minor (oricare directie)

NU sterge NICIODATA `dm_conversations` / `dm_messages` (retentia istoricului ramane conform politicii platformei;
raportarea unui mesaj de catre un participant ramane posibila).

Implicit ruleaza in DRY-RUN (doar numara si afiseaza). Aplicarea cere --apply explicit:
    cd backend && MONGO_URL=... DB_NAME=... python -m astran_sandbox.contact_migration            # dry-run
    cd backend && MONGO_URL=... DB_NAME=... python -m astran_sandbox.contact_migration --apply
Idempotent: a doua rulare gaseste 0 randuri. Daca protectia e dezactivata explicit prin variabila de mediu,
scriptul refuza sa ruleze (ar sterge relatii pe care politica curenta le permite).
"""
from __future__ import annotations

import asyncio
import os
import sys

from astran_sandbox import contact_policy


async def _categories(db, ids: set) -> dict:
    rows = await db.users.find({"user_id": {"$in": sorted(ids)}}, {"_id": 0, "user_id": 1, "age_category": 1}).to_list(None)
    return {r["user_id"]: r.get("age_category") for r in rows}


def _violates(cats: dict, x: str, y: str) -> bool:
    # utilizator lipsa din DB => categorie None => minor (fail-closed), la fel ca peste tot in politica
    return not contact_policy.contact_allowed(cats.get(x), cats.get(y))


async def purge_cross_age_contacts(db, *, apply: bool = False, environ=None) -> dict:
    if not contact_policy.protection_enabled(environ):
        raise RuntimeError("Protectia Adult<->Minor este dezactivata explicit; nu se sterge nimic.")
    edges = await db.friendships.find({}, {"_id": 0}).to_list(None)
    reqs = await db.friend_requests.find({"status": "pending"}, {"_id": 0}).to_list(None)
    ids = {e["user_a"] for e in edges} | {e["user_b"] for e in edges} | {r["from_id"] for r in reqs} | {r["to_id"] for r in reqs}
    cats = await _categories(db, ids) if ids else {}
    bad_edges = [e for e in edges if _violates(cats, e["user_a"], e["user_b"])]
    bad_reqs = [r for r in reqs if _violates(cats, r["from_id"], r["to_id"])]
    removed_edges = removed_reqs = 0
    if apply:
        for e in bad_edges:
            removed_edges += (await db.friendships.delete_one({"user_a": e["user_a"], "user_b": e["user_b"]})).deleted_count
        for r in bad_reqs:
            removed_reqs += (await db.friend_requests.delete_one({"from_id": r["from_id"], "to_id": r["to_id"]})).deleted_count
    return {"applied": bool(apply), "friendships_found": len(bad_edges), "pending_requests_found": len(bad_reqs),
            "friendships_removed": removed_edges, "pending_requests_removed": removed_reqs}


async def _main(apply: bool) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient   # import local: modulul ramane importabil fara motor (teste)
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ.get("DB_NAME", "astran_game")]
    try:
        print(await purge_cross_age_contacts(db, apply=apply))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main("--apply" in sys.argv[1:])))
