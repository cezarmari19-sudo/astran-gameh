"""Sistemul de recomandari / ordonare din Discover - sursa UNICA de adevar pentru parametrii lui (DSA Art. 27).

Termenii si conditiile descriu DOAR ce este definit aici (`PARAMETERS`); `test_recommendations.py` verifica ca fiecare parametru
si fiecare optiune apar in textul Termenilor (RO + EN), ca sa nu apara diferente intre cod si documentul juridic.

Ce face Discover (verificat in cod, `server.py: GET /games/discover`):
  * Sectiunile `for_you`, `trending`, `popular`, `new` sunt LISTE ORDONATE de jocuri publice, active.
  * Ordinea implicita NU foloseste profilul utilizatorului: `for_you` si `trending` = jocuri sortate dupa numarul de jocuri
    pornite (`total_plays`), apoi dupa aprecieri (`likes`); `popular` = aprecieri, apoi pornirile; `new` = data publicarii.
  * Filtru de siguranta: un cont fara varsta 18+ atestata nu primeste jocuri 18+ (nu e un parametru de ordonare).
  * Personalizare OPT-IN (implicit oprita): daca utilizatorul activeaza `personalized_recommendations`, in sectiunea `for_you`
    jocurile din categoriile jocurilor jucate recent de el (primele 3 categorii dupa numarul de jocuri recente, din ultimele
    50 din `recently_played`) trec inaintea celorlalte; in interiorul fiecarui grup ramane ordinea dupa popularitate.
    Nu se creeaza profil separat, nu se folosesc date de la terti, nu se inferează caracteristici personale.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

SECTIONS = ("for_you", "trending", "popular", "new")
DEFAULT_SECTION = "for_you"
CANDIDATE_POOL = 100        # cand personalizarea e activa: re-ordonam primele 100 dupa popularitate, apoi afisam 30
PAGE_SIZE = 30
RECENT_WINDOW = 50          # cate jocuri recente se iau in calcul
TOP_CATEGORIES = 3

# (cheie, [(camp, directie)]) - exact ce se trimite la MongoDB
SORT_MAP = {
    "for_you": [("total_plays", -1), ("likes", -1)],
    "trending": [("total_plays", -1), ("likes", -1)],
    "popular": [("likes", -1), ("total_plays", -1)],
    "new": [("created_at", -1)],
}

# Parametri documentati in Termeni. `relative_weight`: ordinea lexicografica reala (nu ponderi numerice inventate).
PARAMETERS = [
    {"key": "total_plays", "used_in": ["for_you", "trending", "popular"], "kind": "popularity", "personal": False, "priority": {"for_you": 1, "trending": 1, "popular": 2}},
    {"key": "likes", "used_in": ["for_you", "trending", "popular"], "kind": "popularity", "personal": False, "priority": {"for_you": 2, "trending": 2, "popular": 1}},
    {"key": "created_at", "used_in": ["new"], "kind": "recency", "personal": False, "priority": {"new": 1}},
    {"key": "age_category", "used_in": ["for_you", "trending", "popular", "new"], "kind": "safety_filter", "personal": False, "priority": {}},
    {"key": "recent_play_categories", "used_in": ["for_you"], "kind": "personalization", "personal": True, "opt_in": True, "priority": {"for_you": 0}},
]
CONTROLS = [
    {"key": "section_choice", "description": "choose the list (For You, Trending, Popular, New) that determines the ordering"},
    {"key": "personalized_recommendations", "description": "turn personalization on/off in Settings (off by default)", "default": False},
    {"key": "clear_recently_played", "description": "recently played history can be cleared, which removes the personalization signal"},
]


def sort_for(section: str) -> list:
    return SORT_MAP.get(section, SORT_MAP[DEFAULT_SECTION])


def public_info() -> dict:
    return {"parameters": PARAMETERS, "controls": CONTROLS, "sections": list(SECTIONS), "profiling_by_default": False,
            "personalization_window": {"recent_games": RECENT_WINDOW, "top_categories": TOP_CATEGORIES}}


async def personalization_enabled(db, user_id: str) -> bool:
    doc = await db.user_settings.find_one({"user_id": user_id}, {"_id": 0, "settings": 1})
    return ((doc or {}).get("settings") or {}).get("personalized_recommendations") is True


async def recent_categories(db, user_id: str) -> list:
    rows = await db.recently_played.find({"user_id": user_id}, {"_id": 0, "game_id": 1}).sort("played_at", -1).limit(RECENT_WINDOW).to_list(RECENT_WINDOW)
    ids = [r["game_id"] for r in rows if r.get("game_id")]
    if not ids:
        return []
    games = await db.games.find({"game_id": {"$in": ids}, "deleted": {"$ne": True}}, {"_id": 0, "category": 1}).to_list(RECENT_WINDOW)
    counts = Counter(g.get("category") for g in games if g.get("category"))
    return [c for c, _n in counts.most_common(TOP_CATEGORIES)]


def apply_personalization(games: list, categories: list) -> list:
    """Muta INAINTE jocurile din categoriile favorite recente; ordinea relativa (popularitate) se pastreaza in fiecare grup."""
    if not categories:
        return list(games)
    wanted = set(categories)
    first = [g for g in games if g.get("category") in wanted]
    rest = [g for g in games if g.get("category") not in wanted]
    return first + rest


async def discover_games(db, user_id: str, section: str, base_query: dict, *, attach: Any) -> dict:
    """Ordonarea din Discover. `attach` = functia care completeaza campurile derivate ale jocurilor (din server.py)."""
    section = section if section in SECTIONS else DEFAULT_SECTION
    personalize = section == "for_you" and await personalization_enabled(db, user_id)
    cats = await recent_categories(db, user_id) if personalize else []
    pool = CANDIDATE_POOL if (personalize and cats) else PAGE_SIZE
    rows = await db.games.find(base_query, {"_id": 0, "script": 0}).sort(sort_for(section)).limit(pool).to_list(pool)
    if personalize and cats:
        rows = apply_personalization(rows, cats)
    return {"section": section, "games": await attach(rows[:PAGE_SIZE]), "personalized": bool(personalize and cats),
            "personalization_available": section == "for_you"}
