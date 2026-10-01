"""Preferintele PLAYERULUI (nu ale jocului): Graphics Quality, Render Distance, si orice
alta setare viitoare de acest fel (Volume, Music, Sound Effects, Camera Sensitivity,
Controls...). Persistente per utilizator, incarcate automat la intrarea in ORICE joc -
nu se amesteca niciodata cu setarile de gameplay ale jocului (acelea raman in game.scene /
game.max_players etc, configurate de creator, vezi routes.py din Studio).

Se ataseaza din server.py, exact ca avatar_routes.py:
    api.include_router(make_user_settings_router(get_current_user, db))

Colectia Mongo `user_settings`:
    {user_id, settings: {graphics_quality, render_level, graphics_level, volume,
     music_volume, sfx_volume, camera_sensitivity}, updated_at}

Arhitectura e deschisa: SETTINGS_SCHEMA de mai jos e singurul loc care trebuie extins
cand se adauga o setare noua de player - clean_settings() si default_settings() se
adapteaza automat dupa el, fara sa fie nevoie de alte schimbari in router.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

log = logging.getLogger("astran.user_settings")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------- schema setarilor de player ----------
# Fiecare intrare: (cheie, default, validator(valoare_bruta) -> valoare_curata_sau_None).
# Un validator care intoarce None inseamna "valoare invalida, foloseste default-ul".
# Pentru a adauga o setare noua de player (ex. "music_volume"), adaugi o linie aici -
# restul (save/load/defaults) functioneaza automat, fara alte modificari.

def _int_range(lo: int, hi: int):
    def validate(v: Any) -> int | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        iv = int(v)
        if iv < lo or iv > hi:
            return None
        return iv
    return validate


def _float_range(lo: float, hi: float):
    def validate(v: Any) -> float | None:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        fv = float(v)
        if fv != fv or fv in (float("inf"), float("-inf")) or fv < lo or fv > hi:
            return None
        return round(fv, 4)
    return validate


def _enum(options: tuple[str, ...]):
    def validate(v: Any) -> str | None:
        return v if isinstance(v, str) and v in options else None
    return validate


# (cheie, default, validator) - vezi QUALITY_LABELS/RENDER_DISTANCES/GRAPHICS_LEVELS in
# frontend/app/play/[id].tsx pentru ce inseamna fiecare interval.
SETTINGS_SCHEMA: list[tuple[str, Any, Any]] = [
    ("graphics_quality", "medium", _enum(("low", "medium", "high"))),  # "Viziune" 1/2/3
    ("render_level", 4, _int_range(1, 10)),       # distanta de randare, 1..10 (vezi RENDER_DISTANCES)
    ("graphics_level", 5, _int_range(1, 10)),     # calitatea iluminarii, 1..10 (vezi GRAPHICS_LEVELS)
    ("master_volume", 1.0, _float_range(0.0, 1.0)),
    ("music_volume", 1.0, _float_range(0.0, 1.0)),
    ("sfx_volume", 1.0, _float_range(0.0, 1.0)),
    ("camera_sensitivity", 1.0, _float_range(0.2, 3.0)),
]


def default_settings() -> dict:
    return {key: default for key, default, _ in SETTINGS_SCHEMA}


def clean_settings(raw: Any) -> dict:
    """Valideaza setarile primite de la client, cheie cu cheie - o cheie lipsa sau
    invalida cade pe default, nu respinge tot request-ul. Chei necunoscute se ignora
    (compatibilitate inainte - un client vechi care nu stie de o setare noua nu trebuie
    sa-i strice pe cele existente)."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, default, validate in SETTINGS_SCHEMA:
        cleaned = validate(raw.get(key)) if key in raw else None
        out[key] = cleaned if cleaned is not None else default
    return out


def default_doc(user_id: str) -> dict:
    return {"user_id": user_id, "settings": default_settings(), "updated_at": now_utc()}


class SaveSettingsBody(BaseModel):
    # partial: userul schimba o singura setare o data (save automat la fiecare toggle),
    # nu intregul obiect - vezi PATCH semantics mai jos.
    settings: dict[str, Any] = Field(default_factory=dict)


def make_user_settings_router(get_current_user, db) -> APIRouter:
    router = APIRouter(prefix="/settings", tags=["settings"])
    indexes_ready = False

    async def ensure_indexes() -> None:
        nonlocal indexes_ready
        if indexes_ready:
            return
        try:
            await db.user_settings.create_index("user_id", unique=True)
        except Exception:  # noqa: BLE001
            log.warning("could not create user_settings index", exc_info=True)
        indexes_ready = True

    @router.get("/me")
    async def get_my_settings(current=Depends(get_current_user)):
        """Setarile playerului curent. Daca nu exista inca (prima intrare), creeaza si
        intoarce valorile default - vezi cerinta punctul 5."""
        await ensure_indexes()
        doc = await db.user_settings.find_one({"user_id": current["user_id"]}, {"_id": 0})
        if not doc:
            doc = default_doc(current["user_id"])
            await db.user_settings.insert_one(doc)
        else:
            # un document vechi, salvat inainte de a adauga o setare noua in SETTINGS_SCHEMA,
            # primeste automat valorile default pentru cheile lipsa - nu trebuie migrare manuala.
            doc["settings"] = {**default_settings(), **(doc.get("settings") or {})}
        return {"settings": doc["settings"]}

    @router.put("/me")
    async def save_my_settings(body: SaveSettingsBody, current=Depends(get_current_user)):
        """Salveaza (PATCH semantic) setarile playerului - trimiti doar cheile schimbate,
        restul raman neatinse. Apelat automat la fiecare modificare din UI (punctul 3:
        fara buton de Save), deci trebuie sa fie ieftin si sa nu ceara intregul obiect."""
        await ensure_indexes()
        existing = await db.user_settings.find_one({"user_id": current["user_id"]}, {"_id": 0})
        current_settings = {**default_settings(), **((existing or {}).get("settings") or {})}
        merged_raw = {**current_settings, **(body.settings or {})}
        clean = clean_settings(merged_raw)

        await db.user_settings.update_one(
            {"user_id": current["user_id"]},
            {"$set": {"settings": clean, "updated_at": now_utc()}, "$setOnInsert": {"user_id": current["user_id"]}},
            upsert=True,
        )
        return {"settings": clean}

    return router