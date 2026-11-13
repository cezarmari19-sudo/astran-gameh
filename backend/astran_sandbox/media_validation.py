"""Validarea referintelor de imagine (avatar, coperta de joc) primite de la clienti.

PROBLEMA: `avatar_url` si `thumbnail_url` erau siruri libere (avatar pana la 500.000 de caractere).
Se afiseaza altor utilizatori, deci un URL extern arbitrar = pixel de tracking (IP-ul fiecarui
spectator ajunge la atacator), iar scheme ca `file:` / `content:` / `javascript:` nu au ce cauta acolo.
Doar stdlib.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlparse

MAX_DATA_URL_CHARS = 500_000
MAX_HTTPS_URL_CHARS = 2000
_DATA_RE = re.compile(r"^data:image/(?:png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$")


def clean_image_ref(value: Optional[str], *, https_hosts: Optional[Iterable[str]] = None,
                    any_https: bool = False) -> Optional[str]:
    """Intoarce valoarea validata (sau None pentru gol). ValueError daca nu e acceptata.

    Acceptat: `data:image/(png|jpeg|webp|gif);base64,...` (<= 500.000 caractere) si URL-uri https:
    oricare daca `any_https`, altfel doar hosturile din `https_hosts` (sufix de domeniu)."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid image")
    v = value.strip()
    if v.lower().startswith("data:"):
        if len(v) > MAX_DATA_URL_CHARS:
            raise ValueError("Image is too large")
        if not _DATA_RE.match(v):
            raise ValueError("Unsupported image data")
        return v
    if len(v) > MAX_HTTPS_URL_CHARS:
        raise ValueError("Image URL is too long")
    u = urlparse(v)
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise ValueError("Image must be an https URL or an image data URL")
    host = u.hostname.lower()
    if any_https:
        return v
    for allowed in (https_hosts or ()):
        a = allowed.lower().lstrip(".")
        if host == a or host.endswith("." + a):
            return v
    raise ValueError("Image host is not allowed")