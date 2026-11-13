"""Validarea scenei unui joc (obiectele din Studio) la scriere.

PROBLEMA: `scene` era un dict fara schema si fara limite (server.py: GameCreateBody / GameUpdateBody). Era
trimis tuturor jucatorilor (client-bundle) si randat direct de ecranul Play. Un owner / editor putea salva
un milion de obiecte, culori invalide sau numere uriase si bloca aplicatia fiecarui jucator care intra in joc.
Aici: structura abuziva e RESPINSA (ValueError -> 400), iar valorile gresite sunt CURATATE (implicit /
clamp), ca scenele vechi cu date murdare sa poata fi salvate in continuare. Doar stdlib.
"""
from __future__ import annotations

import math
import re
from typing import Any

MAX_SCENE_OBJECTS = 1000          # acelasi plafon ca runner.scene_file
MAX_COORD = 1_000_000.0
MAX_SCALE = 10_000.0
GROUND_MIN, GROUND_MAX = 4.0, 2000.0   # ca GROUND_MIN / GROUND_MAX din frontend/src/studio/sceneShared.ts
DEFAULT_OBJECT_COLOR = "#a3a3a3"
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}\Z")
_TYPE = re.compile(r"^[A-Za-z0-9_-]{1,20}\Z")
_NUM_KEYS = ("x", "y", "z", "rx", "ry", "rz")
_SCALE_KEYS = ("scale", "sx", "sy", "sz")
_BOOL_KEYS = ("visible", "solid", "initial", "enabled")


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return max(lo, min(hi, float(v)))


def _color(v: Any, default: str) -> str:
    return v if isinstance(v, str) and _COLOR.match(v) else default


def clean_scene(scene: Any) -> dict:
    if not isinstance(scene, dict):
        raise ValueError("Scene must be an object")
    objects = scene.get("objects", [])
    if not isinstance(objects, list):
        raise ValueError("Scene objects must be a list")
    if len(objects) > MAX_SCENE_OBJECTS:
        raise ValueError(f"Too many scene objects (max {MAX_SCENE_OBJECTS})")
    out_objects = []
    for o in objects:
        if not isinstance(o, dict):
            continue
        oid, otype = o.get("id"), o.get("type")
        if not isinstance(oid, str) or not _ID.match(oid) or not isinstance(otype, str) or not _TYPE.match(otype):
            continue   # fara id/type valid obiectul nu poate fi nici randat, nici referit
        c: dict = {"id": oid, "type": otype, "color": _color(o.get("color"), DEFAULT_OBJECT_COLOR)}
        for k in _NUM_KEYS:
            if k in o:
                c[k] = _num(o[k], 0.0, -MAX_COORD, MAX_COORD)
        for k in ("x", "y", "z"):
            c.setdefault(k, 0.0)
        for k in _SCALE_KEYS:
            if k in o:
                c[k] = _num(o[k], 1.0, 0.0, MAX_SCALE)
        c.setdefault("scale", 1.0)
        for k in _BOOL_KEYS:
            if isinstance(o.get(k), bool):
                c[k] = o[k]
        for k, mx in (("name", 64), ("spawnKind", 20), ("teamId", 64)):
            if isinstance(o.get(k), str):
                c[k] = o[k][:mx]
        out_objects.append(c)
    out: dict = {"objects": out_objects,
                 "sky": _color(scene.get("sky"), "#0F1012"),
                 "ground": _color(scene.get("ground"), "#1A1D21")}
    for k in ("groundWidth", "groundDepth"):
        if k in scene:
            out[k] = _num(scene[k], 100.0, GROUND_MIN, GROUND_MAX)
    return out