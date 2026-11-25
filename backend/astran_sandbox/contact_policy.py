"""Politica CENTRALA de contact intre categorii de varsta (prietenii + mesagerie privata).

Unica sursa de adevar pentru intrebarea "au voie acesti doi utilizatori sa intre in contact privat?".
Folosita de: cereri de prietenie, acceptare, auto-acceptare, deschiderea unui DM, trimiterea unui mesaj,
lista de conversatii, istoric, livrarea in timp real. Modul fara dependinte externe (doar stdlib).

REGULA (simetrica, aplicata PE SERVER):
  * adult   <-> adult   : permis (regulile normale ale platformei)
  * minor   <-> minor   : permis (regulile normale ale platformei)
  * adult   <-> minor   : INTERZIS, in ambele directii (adultul nu initiaza; minorul nu initiaza spre adult)

CATEGORIA vine EXCLUSIV din `users.age_category` din baza de date. Clientul nu o poate trimite si nu
conteaza ce trimite. `age_category == "adult_18"` este singura valoare considerata adult; lipsa,
None, o valoare necunoscuta sau corupta => minor (fail-closed, identic cu restul platformei:
vezi age_policy.py - "adult_18" apare doar dupa age assurance verificat).

CONFIGURARE: ASTRAN_BLOCK_ADULT_TO_MINOR_FRIEND_REQUESTS
  * ACTIV IMPLICIT: variabila lipsa, goala, "1", "true", o valoare gresita sau un typo => protectia RAMANE activa.
  * Se dezactiveaza DOAR cu o valoare explicita si inechivoca: "0", "false", "off", "no", "disabled"
    (insensibil la majuscule/spatii) - si atunci se logheaza un avertisment CRITICAL la fiecare verificare.
  * Variabila se citeste intr-un SINGUR loc (aici), la fiecare verificare - nu la import - ca o schimbare
    de mediu sau un test sa nu lase o valoare "inghetata".
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

log = logging.getLogger("astran.contact_policy")

ENV_VAR = "ASTRAN_BLOCK_ADULT_TO_MINOR_FRIEND_REQUESTS"
ADULT = "adult_18"

# Singurele valori care opresc protectia. Orice altceva (inclusiv lipsa/gol/typo) o lasa PORNITA.
_EXPLICIT_OFF = frozenset({"0", "false", "off", "no", "disabled"})

# Cod stabil + mesaj GENERIC: nu spune care dintre cei doi e minor (nu dezvaluim categoria de varsta a nimanui).
DENIED_CODE = "contact_restricted"
DENIED_MESSAGE = "You cannot contact this user because of safety restrictions."


def protection_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True = protectia Adult<->Minor e activa (implicit). False doar la dezactivare explicita."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_VAR)
    if raw is None:
        return True
    if raw.strip().lower() in _EXPLICIT_OFF:
        log.critical("%s este dezactivata explicit (%r): adultii pot contacta minori! Nu e recomandat in productie.",
                     ENV_VAR, raw)
        return False
    return True


def is_adult_category(category: Any) -> bool:
    """Strict: doar exact 'adult_18' e adult. None / '' / 'adult' / 'ADULT_18' / orice altceva => minor."""
    return category == ADULT


def contact_allowed_flags(adult_a: bool, adult_b: bool, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Varianta pe flag-uri 'e adult' (cum le intorc user_ages din DM / realtime)."""
    if not protection_enabled(environ):
        return True
    return bool(adult_a) == bool(adult_b)


def contact_allowed(category_a: Any, category_b: Any, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Pot cele doua categorii de varsta (din DB) sa intre in contact privat? Simetrica."""
    return contact_allowed_flags(is_adult_category(category_a), is_adult_category(category_b), environ)


def users_contact_allowed(user_a: Optional[dict], user_b: Optional[dict],
                          environ: Optional[Mapping[str, str]] = None) -> bool:
    """Pe documente de utilizator din DB. Un utilizator lipsa (None) => interzis (fail-closed)."""
    if user_a is None or user_b is None:
        return False
    return contact_allowed(user_a.get("age_category"), user_b.get("age_category"), environ)


# ---------------------------------------------------------------------------------------------------------------
# GRUPURI + CHAT (aceeasi politica, aceeasi sursa de adevar). Spre deosebire de prietenii/DM de mai sus (unde lipsa
# categoriei = minor, fail-closed), aici avem o stare TREI-valorica: `under_18` | `adult_18` | None (necunoscuta).
# Un cont cu varsta necunoscuta NU e presupus nici adult, nici minor: nu intra in grupuri si nu comunica deloc
# prin chat (grup / joc) - nu exista valoare implicita nesigura.
# ---------------------------------------------------------------------------------------------------------------
MINOR = "under_18"
VALID_CATEGORIES = frozenset({MINOR, ADULT})

CODE_AGE_UNKNOWN = "age_category_unknown"
CODE_AGE_MISMATCH = "contact_restricted"            # acelasi cod stabil/generic ca in DM: nu dezvaluie care parte e minor
CODE_GROUP_REVIEW = "group_age_review_required"     # grup mostenit, cu categorie nerezolvata (vezi group_age_migration)

MESSAGES = {
    CODE_AGE_UNKNOWN: "Your account does not have a verified age category yet, so this feature is unavailable.",
    CODE_AGE_MISMATCH: DENIED_MESSAGE,
    CODE_GROUP_REVIEW: "This Group is under a safety review and is not accepting new activity right now.",
}


def category_of(value: Any) -> Optional[str]:
    """Categoria STRICTA: exact 'under_18' sau 'adult_18'; orice altceva (lipsa, None, '', 'ADULT_18', 'adult', tip
    gresit) => None = necunoscuta. NU se intoarce niciodata o valoare presupusa."""
    return value if isinstance(value, str) and value in VALID_CATEGORIES else None


def user_category(user: Optional[dict]) -> Optional[str]:
    """Categoria unui document de utilizator din DB (None daca utilizatorul lipseste sau categoria e necunoscuta)."""
    return category_of(user.get("age_category")) if isinstance(user, dict) else None


def same_group_allowed(category_a: Any, category_b: Any, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Pot doua categorii sa coexiste intr-un grup / sa comunice in chat? Intoarce None daca da, altfel CODUL refuzului.
    Necunoscuta (oricare parte) => refuz CODE_AGE_UNKNOWN, INDIFERENT de variabila de mediu (nu exista un
    comportament sigur de presupus). Adult<->minor => refuz CODE_AGE_MISMATCH (cu aceeasi regula `contact_allowed`
    ca la DM/prietenii, deci inclusiv acelasi intrerupator explicit)."""
    a, b = category_of(category_a), category_of(category_b)
    if a is None or b is None:
        return CODE_AGE_UNKNOWN
    return None if contact_allowed(a, b, environ) else CODE_AGE_MISMATCH


def chat_allowed(sender_category: Any, recipient_category: Any, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Comoditate booleana pentru livrarea mesajelor (grup / joc): True doar daca ambele categorii sunt cunoscute
    si compatibile."""
    return same_group_allowed(sender_category, recipient_category, environ) is None

