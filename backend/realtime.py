"""WebSocket live, pentru sincronizarea POZITIEI/ACTIUNILOR intre jucatorii aflati in
aceeasi instanta de joc (acelasi instance_id din game_instances/instance_players -
vezi server.py, /api/games/{id}/play). Asta e piesa care lipsea ca jucatorii sa se vada
miscandu-se/sarind unii pe altii in timp real, in loc sa joace fiecare izolat, ca pana acum.

============================================================================
PROTOCOL (JSON text, cat mai mic - fiecare camp conteaza pentru latenta)
============================================================================
Client -> server, trimis la interval scurt (vezi useRealtime.ts pe frontend, ~60-80ms,
sub bugetul de 100ms cerut - cea mai mare parte a bugetului e reteaua, nu procesarea):
    {"t": "state", "x": 1.2, "y": 0, "z": -3.4, "ry": 1.57, "anim": "jump"}

Server -> client:
    {"t": "roster", "players": [{"user_id":"u1","username":"ana","x":..,"y":..,"z":..,"ry":..,"anim":".."}, ...]}
        - trimis o singura data, imediat dupa conectare: starea curenta a tuturor celorlalti
          din camera, ca noul venit sa-i poata desena imediat (nu asteapta prima lor miscare).
    {"t": "join", "user_id": "u1", "username": "ana"}
        - un jucator nou a intrat in camera (fara stare inca - abia s-a conectat).
    {"t": "state", "user_id": "u1", "x":.., "y":.., "z":.., "ry":.., "anim":".."}
        - retransmis CATRE TOTI CEILALTI, imediat ce serverul primeste un "state" de la cineva.
          Nu exista buffering/tick pe server - e un relay direct, ca sa nu adauge intarziere
          in plus fata de reteaua insasi.
    {"t": "leave", "user_id": "u1"}
        - jucatorul s-a deconectat (inchis aplicatia, pierdut conexiunea, etc).

============================================================================
CE E REAL AICI SI CE NU
============================================================================
Functional, chiar acum: jucatorii conectati la ACEEASI instanta (instance_id) chiar se
vad reciproc in timp real - pozitie, rotatie, si o eticheta de animatie simpla (de ex.
"jump", seteaza-o din client cum vrei). Camera (room) traieste in memoria procesului.

LIMITARE REALA #1 - scalare orizontala: daca ruleaza mai multe procese/masini de backend
in spatele unui load balancer, doi jucatori din aceeasi instanta care ajung pe procese
diferite NU se vad reciproc - fiecare proces are propriul dictionar de camere, in memorie,
fara nimic care sa le sincronizeze intre ele. Pentru asta ai nevoie de un broker extern
(Redis Pub/Sub e alegerea standard) care sa transmita mesajele intre procese. Nu exista
inca - de adaugat cand/daca trebuie sa rulezi mai mult de un proces de backend.

LIMITARE REALA #2 - fara autoritate de server (nu e inca pregatit pentru PvP cu mize):
pozitiile trimise de un client sunt retransmise ca atare, FARA validare - oricine conectat
poate trimite orice coordonate, inclusiv teleportare sau "a lovit pe altcineva" fara sa fie
adevarat. E suficient pentru joc cooperativ/social (te vezi cu altii, va jucati impreuna),
dar NU e suficient pentru PvP competitiv real - acolo serverul trebuie sa decida cine a
lovit pe cine, nu clientul care pretinde ca a lovit. Asta ramane un pas separat.

LIMITARE REALA #3 - fara interpolare/predictie pe client inca: frontend-ul (useRealtime.ts)
primeste pozitiile brute si le aplica direct - la 60-80ms interval + jitter de retea, alti
jucatori pot parea usor sacadati pe o conexiune proasta. Pentru miscare perfect fluida ar
trebui interpolare intre ultimele doua pozitii primite - nu e inclusa in acest prim pas.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

log = logging.getLogger("astran.realtime")

# Un mesaj de stare e mic (cateva numere) - orice mult mai mare e fie o greseala de client,
# fie abuz; il ignoram silentios, nu deconectam toata camera pentru un singur mesaj gresit.
MAX_STATE_MSG_CHARS = 2000
# Plafon per CONEXIUNE (nu per camera) - o singura conexiune rau-intentionata sau cu bug
# (buclă fara throttle) nu poate innea restul camerei cu trafic.
STATE_RATE_LIMIT_PER_SEC = 30

STATE_FIELDS = ("x", "y", "z", "ry", "anim")


@dataclass
class _RoomMember:
    ws: WebSocket
    user_id: str
    username: str
    last_state: dict = field(default_factory=dict)
    msg_times: list = field(default_factory=list)  # timestamps recente (monotonic), pt rate-limit


@dataclass
class _Room:
    members: dict = field(default_factory=dict)  # user_id -> _RoomMember


class RealtimeHub:
    """Camerele traiesc aici, in memoria procesului curent - vezi LIMITAREA REALA #1 din
    docstring-ul fisierului despre ce inseamna asta cand rulezi mai multe procese."""

    def __init__(self) -> None:
        self.rooms: dict[str, _Room] = {}

    def room(self, instance_id: str) -> _Room:
        r = self.rooms.get(instance_id)
        if r is None:
            r = _Room()
            self.rooms[instance_id] = r
        return r

    def drop_if_empty(self, instance_id: str) -> None:
        r = self.rooms.get(instance_id)
        if r is not None and not r.members:
            del self.rooms[instance_id]


hub = RealtimeHub()


def _clean_state(msg: dict) -> dict:
    """Pastreaza doar campurile cunoscute, de tipuri simple - restul (orice ar trimite un
    client modificat/rau-intentionat) se ignora, nu se retransmite mai departe ca atare."""
    out: dict[str, Any] = {}
    for k in STATE_FIELDS:
        v = msg.get(k)
        if isinstance(v, (int, float)) and k != "anim":
            out[k] = v
        elif k == "anim" and isinstance(v, str) and len(v) <= 32:
            out[k] = v
    return out


def make_realtime_router(resolve_user_from_token: Callable[[str], Awaitable[Optional[dict]]], db) -> APIRouter:
    """resolve_user_from_token: aceeasi functie din server.py folosita si pentru HTTP (vezi
    get_current_user) - un WebSocket din React Native nu poate seta header-ul Authorization
    la handshake, deci aici token-ul vine ca query param (?token=...), dar e decodat cu
    EXACT aceleasi reguli (sesiune sau JWT), fara o cale de autentificare paralela/mai slaba."""
    router = APIRouter()

    async def is_member_of_instance(instance_id: str, game_id: str, user_id: str) -> bool:
        """Jucatorul trebuie sa fi intrat deja in instanta prin fluxul HTTP normal
        (/api/games/{id}/play) - acolo se verifica daca jocul e public/privat/plin/etc.
        WebSocket-ul NU duplica regulile alea, doar confirma ca userul chiar a trecut prin ele."""
        membership = await db.instance_players.find_one({"instance_id": instance_id, "user_id": user_id})
        if membership is None:
            return False
        inst = await db.game_instances.find_one({"instance_id": instance_id, "game_id": game_id})
        return inst is not None

    @router.websocket("/ws/games/{game_id}/instances/{instance_id}")
    async def ws_instance(websocket: WebSocket, game_id: str, instance_id: str, token: str = Query(...)):
        user = await resolve_user_from_token(token)
        if user is None:
            await websocket.close(code=4401)  # 4401/4403: coduri custom in spatiul aplicatiei (4000-4999, RFC 6455)
            return
        user_id = user["user_id"]
        username = user.get("username") or ""

        if not await is_member_of_instance(instance_id, game_id, user_id):
            await websocket.close(code=4403)
            return

        await websocket.accept()
        room = hub.room(instance_id)

        # daca acelasi user se reconecteaza (ex: a pierdut conexiunea si a revenit) fara ca
        # vechiul socket sa se fi inchis inca "curat", il inlocuim - un user nu poate ocupa
        # doua sloturi in aceeasi camera.
        previous = room.members.get(user_id)
        if previous is not None:
            try:
                await previous.ws.close(code=4409)  # 4409: inlocuit de o conexiune noua a aceluiasi user
            except Exception:  # noqa: BLE001
                pass

        member = _RoomMember(ws=websocket, user_id=user_id, username=username)
        room.members[user_id] = member

        async def broadcast(payload: dict, except_user_id: Optional[str] = None) -> None:
            text = json.dumps(payload)
            for other in list(room.members.values()):
                if other.user_id == except_user_id:
                    continue
                try:
                    await other.ws.send_text(text)
                except Exception:  # noqa: BLE001
                    pass  # socket-ul lui probabil s-a inchis deja - se curata cand ii vine randul la finally

        try:
            roster = [{"user_id": m.user_id, "username": m.username, **m.last_state} for m in room.members.values() if m.user_id != user_id]
            await websocket.send_text(json.dumps({"t": "roster", "players": roster}))
            await broadcast({"t": "join", "user_id": user_id, "username": username}, except_user_id=user_id)

            while True:
                raw = await websocket.receive_text()
                if len(raw) > MAX_STATE_MSG_CHARS:
                    continue

                now = time.monotonic()
                member.msg_times = [t for t in member.msg_times if now - t < 1.0]
                if len(member.msg_times) >= STATE_RATE_LIMIT_PER_SEC:
                    continue  # ignoram mesajul (nu deconectam) - un burst scurt nu trebuie sa scoata jucatorul din joc
                member.msg_times.append(now)

                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(msg, dict) or msg.get("t") != "state":
                    continue

                state = _clean_state(msg)
                if not state:
                    continue
                member.last_state = state
                await broadcast({"t": "state", "user_id": user_id, **state}, except_user_id=user_id)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("realtime websocket crashed (user_id=%s instance_id=%s)", user_id, instance_id)
        finally:
            # doar daca slotul inca ii apartine lui (nu a fost deja inlocuit mai sus de o
            # reconectare mai noua a aceluiasi user, caz in care el nu mai e "membrul curent")
            if room.members.get(user_id) is member:
                del room.members[user_id]
                await broadcast({"t": "leave", "user_id": user_id})
            hub.drop_if_empty(instance_id)

    return router