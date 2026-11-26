"""Aplicabilitatea obligatiilor DSA pentru Astran Game - determinata din CONFIGURARE EXPLICITA, nu presupusa de cod.

Codul nu poate stabili statutul juridic al unei companii (dimensiune, tip de serviciu). Acest modul transforma decizia
juridica a operatorului (variabile de mediu) intr-o matrice de obligatii; cat timp decizia lipseste, rezultatul este
`undetermined` si NIMIC obligatoriu nu este activat automat (ex.: trimiterea la Baza de date de transparenta).

Variabile:
  ASTRAN_DSA_SERVICE_CLASS     hosting | online_platform           (lipsa = nedeterminat)
  ASTRAN_DSA_ENTERPRISE_SIZE   micro_small | not_micro_small       (lipsa = nedeterminat)  - Recomandarea 2003/361/CE
  ASTRAN_DSA_VLOP              1 daca a fost desemnata platforma foarte mare (Art. 33); implicit nu

Reguli (DSA, Regulamentul (UE) 2022/2065) - de CONFIRMAT de un jurist:
  * Art. 9, 10, 11, 12: toti furnizorii de servicii intermediare (indiferent de dimensiune).
  * Art. 15 (rapoarte de transparenta): toti, CU EXCEPTIA microintreprinderilor/intreprinderilor mici care nu sunt VLOP (Art. 15(2)).
  * Art. 16, 17: furnizorii de servicii de stocare (hosting), inclusiv platformele online (sectiunea 2).
  * Art. 20 si Art. 24(5): doar platformele online; Art. 19 le exclude pe cele micro/mici (care nu sunt VLOP).
    (Art. 19(2): o intreprindere care depaseste pragul beneficiaza de o perioada de gratie de 12 luni - de verificat manual.)
  * Art. 24(5) presupune si onboarding prin coordonatorul serviciilor digitale (DSC) - vezi dsa_tdb.py.
"""
from __future__ import annotations

import os
from typing import Optional

APPLIES, NOT_APPLICABLE, UNDETERMINED = "applies", "not_applicable", "undetermined"
CLASSES = ("hosting", "online_platform")
SIZES = ("micro_small", "not_micro_small")


def classification(env: Optional[dict] = None) -> dict:
    env = os.environ if env is None else env
    cls = (env.get("ASTRAN_DSA_SERVICE_CLASS") or "").strip().lower()
    size = (env.get("ASTRAN_DSA_ENTERPRISE_SIZE") or "").strip().lower()
    return {"service_class": cls if cls in CLASSES else None, "enterprise_size": size if size in SIZES else None,
            "vlop": (env.get("ASTRAN_DSA_VLOP") or "").strip() == "1",
            "invalid_values": [k for k, v, ok in (("ASTRAN_DSA_SERVICE_CLASS", cls, CLASSES), ("ASTRAN_DSA_ENTERPRISE_SIZE", size, SIZES))
                               if v and v not in ok]}


def _exempt_small(c: dict) -> Optional[bool]:
    """True = micro/mica si nu VLOP (scutita unde legea o scuteste); False = nu e scutita; None = nedeterminat."""
    if c["vlop"]:
        return False
    if c["enterprise_size"] is None:
        return None
    return c["enterprise_size"] == "micro_small"


def obligations(env: Optional[dict] = None) -> dict:
    c = classification(env)
    small = _exempt_small(c)
    is_host = c["service_class"] in CLASSES                    # hosting sau platforma online (platforma online e un tip de hosting)
    is_platform = c["service_class"] == "online_platform"

    def tri(cond: Optional[bool]) -> str:
        return UNDETERMINED if cond is None else (APPLIES if cond else NOT_APPLICABLE)

    def and_(*xs: Optional[bool]) -> Optional[bool]:
        if any(x is False for x in xs):
            return False
        return None if any(x is None for x in xs) else True

    host_known: Optional[bool] = None if c["service_class"] is None else is_host
    plat_known: Optional[bool] = None if c["service_class"] is None else is_platform
    not_small: Optional[bool] = None if small is None else (not small)
    rows = {
        "art_9": (APPLIES, "all intermediary services"),
        "art_10": (APPLIES, "all intermediary services"),
        "art_11_12": (APPLIES, "all intermediary services"),
        "art_15": (tri(not_small), "all providers except micro/small enterprises that are not VLOPs (Art. 15(2))"),
        "art_16": (tri(host_known), "hosting services, including online platforms"),
        "art_17": (tri(host_known), "hosting services, including online platforms"),
        "art_20": (tri(and_(plat_known, not_small)), "online platforms, unless micro/small (Art. 19)"),
        "art_24_5": (tri(and_(plat_known, not_small)), "online platforms, unless micro/small (Art. 19); requires onboarding via the DSC"),
    }
    out = {k: {"status": st, "basis": why} for k, (st, why) in rows.items()}
    out["classification"] = c
    out["legal_review_required"] = True
    out["note"] = ("Derived from operator configuration only. Technical capability is built regardless; whether it is MANDATORY is a legal "
                   "determination. Nothing here is legal advice or a certification.")
    return out


def tdb_submission_required(env: Optional[dict] = None) -> bool:
    """True SOLO cand operatorul a stabilit explicit ca Art. 24(5) se aplica. `undetermined` => False (nu se activeaza implicit)."""
    return obligations(env)["art_24_5"]["status"] == APPLIES


def article_20_applies(env: Optional[dict] = None) -> Optional[bool]:
    st = obligations(env)["art_20"]["status"]
    return None if st == UNDETERMINED else st == APPLIES
