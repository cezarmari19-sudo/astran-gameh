"""Amprenta de continut pentru itemele din Shop (anti-republicare).

PROBLEMA: cine cumpara (sau ia gratis) un model / script primeste continutul COMPLET (`_owner_content`) si il
putea publica imediat sub propriul nume, la pret propriu, sau gratuit: o copie exacta a muncii altui autor.
SOLUTIA (partiala, spunem asta deschis): la publicare se calculeaza un hash al continutului normalizat; daca un
item al ALTUI autor are deja acelasi hash, publicarea e refuzata. Opreste copiile identice (cazul comun);
NU opreste un copiator care modifica un detaliu - pentru asta ar trebui moderare / DMCA.
Praguri: continuturi mici (ex. un singur cub implicit, un script de 3 randuri) pot coincide legitim.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

MIN_PARTS_FOR_HASH = 3
MIN_SCRIPT_CHARS_FOR_HASH = 200


def _canon(v: Any) -> Any:
    if isinstance(v, bool) or v is None or isinstance(v, (str, int)):
        return v
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0])) if k not in ("id",)}
    return str(v)


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def parts_hash(parts: Any, part_count: int) -> Optional[str]:
    """Hash al unui model; None pentru modele prea mici ca sa fie semnificative. `id`-urile sunt ignorate
    (se regenereaza la copiere), iar coordonatele se rotunjesc la 3 zecimale."""
    if part_count < MIN_PARTS_FOR_HASH:
        return None
    return _digest(_canon(parts))


def files_hash(files: Any) -> Optional[str]:
    """Hash al unui script (set de fisiere name+source); None pentru scripturi foarte scurte."""
    if not isinstance(files, list):
        return None
    norm = sorted(((str(f.get("name", "")), str(f.get("source", "")).strip()) for f in files if isinstance(f, dict)))
    if sum(len(src) for _, src in norm) < MIN_SCRIPT_CHARS_FOR_HASH:
        return None
    return _digest(norm)