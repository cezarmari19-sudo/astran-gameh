"""Poarta de acces 18+ (stdlib): decizia se ia pe BACKEND la fiecare acces relevant, nu la incarcarea aplicatiei.

Reguli:
  * un joc e "pentru toate varstele" doar daca `age_category == "under_18"` explicit; orice altceva (adult_18, lipsa,
    valoare necunoscuta) e tratat ca 18+ (fail-closed - vechiul `age_category == "adult_18" and user == "under_18"`
    lasa sa treaca utilizatorii al caror camp lipsea);
  * accesul se acorda DOAR cu `age_policy.can_access_adult_content(user)` (age assurance verificat + compatibil cu
    data nasterii + cont ne-restrans). `age_category` din documentul userului NU e suficient;
  * proprietarul isi poate administra propriul joc (authoring) chiar daca nu e verificat, dar nu il poate
    JUCA/rula/descarca (client-bundle, run, instante) ca neverificat.
"""
from __future__ import annotations

import time
from typing import Optional

from . import age_audit as AU
from . import age_policy as AP


class AdultAccessDenied(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    def detail(self) -> dict:
        return {"code": self.code, "message": "Age-restricted content"}


def is_all_ages_game(game: Optional[dict]) -> bool:
    return bool(game) and game.get("age_category") == AP.CAT_MINOR


def check_game_access(game: dict, user: dict, *, authoring: bool = False, is_owner: bool = False) -> None:
    if is_all_ages_game(game):
        return
    if authoring and is_owner:
        return
    info = AP.evaluate_access(user)
    if info.adult_content_allowed:
        return
    can_verify = info.band in (AP.BAND_ADULT, None) and not info.restricted
    raise AdultAccessDenied("age_assurance_required" if can_verify else "age_restricted")


def check_adult_feature(user: dict) -> None:
    """Pentru functii 18+ care nu sunt un joc (ex. chat 18+, publicarea de continut 18+)."""
    info = AP.evaluate_access(user)
    if not info.adult_content_allowed:
        can_verify = info.band in (AP.BAND_ADULT, None) and not info.restricted
        raise AdultAccessDenied("age_assurance_required" if can_verify else "age_restricted")


_last_logged: dict = {}


async def record_denied(db, user: dict, kind: str, resource_id: Optional[str] = None, *, throttle_s: int = 60) -> None:
    """Jurnalizeaza incercarea de acces neautorizat; max 1 eveniment / (user, tip) / minut ca sa nu se poata umple jurnalul."""
    key = (user.get("user_id"), kind)
    now = time.monotonic()
    if now - _last_logged.get(key, -1e9) < throttle_s:
        return
    if len(_last_logged) > 50000:
        _last_logged.clear()
    _last_logged[key] = now
    await AU.record(db, AU.UNAUTHORIZED_ACCESS, subject_user_id=user.get("user_id"), actor="user",
                    details={"resource": kind, "resource_id": (resource_id or "")[:64]})