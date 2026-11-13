"""Scriere conditionata (optimistic concurrency) pentru proiectul unui joc.

PROBLEMA: PUT /games/{id}/files inlocuia tot proiectul fara nicio verificare: doi colaboratori sau doua
dispozitive ale aceluiasi owner isi suprascriau munca reciproc (ultimul care salveaza castiga).
SOLUTIA: fiecare salvare creste `rev`; clientul trimite `base_rev` (revizia de la care a pornit) iar
serverul aplica scrierea doar daca revizia curenta e aceeasi. Altfel ProjectConflict (-> 409).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class ProjectConflict(Exception):
    """Proiectul a fost modificat de altcineva / de alt dispozitiv de la incarcarea clientului."""


async def save_project_doc(db, game_id: str, files: list, folders: list, base_rev: Optional[int]) -> int:
    flt: dict = {"game_id": game_id}
    if base_rev is not None:
        flt["rev"] = {"$in": [0, None]} if base_rev == 0 else base_rev
    try:
        saved = await db.game_scripts.find_one_and_update(
            flt,
            {"$set": {"game_id": game_id, "files": files, "folders": folders,
                      "updated_at": datetime.now(timezone.utc)},
             "$inc": {"rev": 1}},
            upsert=True, return_document=ReturnDocument.AFTER, projection={"_id": 0, "rev": 1},
        )
    except DuplicateKeyError:
        # filtrul cu `rev` nu a potrivit documentul existent => upsert-ul a lovit indexul unic game_id
        raise ProjectConflict()
    return int((saved or {}).get("rev") or 0)