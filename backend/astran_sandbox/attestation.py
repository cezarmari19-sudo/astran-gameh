"""Semnatura HMAC pentru atestari venite de la provideri externi (age assurance, verificare parentala).

Schema: antet `X-Astran-Signature: t=<unix>,v1=<hex>` unde v1 = HMAC-SHA256(secret, f"{t}.{corp_brut}").
  * comparatie in timp constant; fereastra de timp (implicit 10 min) contra replay-ului unui mesaj vechi;
  * folosirea unica a sesiunii/nonce-ului o impune apelantul (age_service / parental_consent), nu aceasta functie;
  * secretul vine DOAR din mediu; fara el providerul e "neconfigurat" si nimic nu e marcat ca verificat.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional


class SignatureError(Exception):
    pass


def sign(secret: str, body: bytes, t: Optional[int] = None) -> str:
    t = int(time.time()) if t is None else int(t)
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, body: bytes, header: Optional[str], *, now: Optional[float] = None, tolerance: int = 600) -> None:
    if not secret:
        raise SignatureError("provider not configured")
    if not header or len(header) > 300:
        raise SignatureError("missing signature")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        t = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        raise SignatureError("malformed signature")
    now = time.time() if now is None else now
    if abs(now - t) > tolerance:
        raise SignatureError("signature outside the accepted time window")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise SignatureError("bad signature")