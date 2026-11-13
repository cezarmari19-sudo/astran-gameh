"""Reguli suplimentare pentru Editorii cu acces RESTRANS pe foldere (allowed_paths / denied_paths).

PROBLEMA: `target` ('server' / 'client') decide unde ruleaza un fisier si e independent de folder.
Un Editor limitat la `client/` putea crea acolo un fisier cu target='server' (sau muta unul din client
in server) si astfel sa execute cod pe server, desi owner-ul i-a dat acces doar la partea de client.
REGULA: un editor restrans nu poate ADAUGA fisiere server si nu poate MUTA un fisier din client in server.
Poate in continuare sa editeze sursa fisierelor server care sunt deja in domeniul lui (owner-ul i le-a dat).
"""
from __future__ import annotations

from typing import Optional


def target_escalation(restricted: bool, old_in_scope: dict, new_in_scope: dict,
                      added: list, changed: list) -> Optional[str]:
    """old/new_in_scope: {path: (source, target)}. Intoarce path-ul care incalca regula sau None."""
    if not restricted:
        return None
    for p in added:
        if new_in_scope[p][1] == "server":
            return p
    for p in changed:
        if old_in_scope[p][1] != "server" and new_in_scope[p][1] == "server":
            return p
    return None