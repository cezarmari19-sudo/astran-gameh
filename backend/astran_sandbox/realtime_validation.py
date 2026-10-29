"""Validarea starii (pozitie/rotatie/animatie) trimisa de clienti pe WebSocket-ul live
(vezi backend/realtime.py). Separat intentionat intr-un modul fara nicio dependinta
externa (doar stdlib - `math`), ca logica de validare sa poata fi testata independent de
FastAPI/Starlette si de o conexiune WebSocket reala.

Ce rezolva acest modul (vezi LIMITARE REALA #2 din docstring-ul backend/realtime.py):
pozitiile trimise de un client sunt retransmise CATRE CEILALTI jucatori si aplicate
direct de ei (useRealtime.ts) - daca un client modificat trimite NaN/Infinity, coordonate
in afara lumii, sau o "teleportare" instantanee, acel gunoi ajungea, inainte de acest
modul, neschimbat la toti ceilalti din camera. Nu e inca autoritate de server completa
(nu exista o simulare de fizica server-side cu care sa se compare pozitia primita - vezi
docstring-ul backend/realtime.py), doar un filtru impotriva valorilor evident imposibile.
"""
from __future__ import annotations

import math
from typing import Any, Optional

STATE_FIELDS = ("x", "y", "z", "ry", "anim")

# Limitele lumii de joc - aceeasi ordine de marime ca si clamp-ul pozitiilor din
# scripturi (vezi runner.py: axele x/y/z sunt clampate la -10000..10000).
MAX_COORD = 10000.0

# Viteza maxima plauzibila (m/s) intre doua pachete de stare ACCEPTATE de la acelasi
# jucator - generos peste orice move_speed/jump_speed permis scripturilor (clamp
# 0..50 in runner.py), ca sa nu respingem jucatori legitimi pe o conexiune cu jitter,
# dar suficient de strans cat sa prinda teleportari/speedhack grosolane trimise direct
# de un client modificat.
MAX_SPEED_MPS = 120.0

# Sub acest interval (acelasi tick, sau doua mesaje trimise aproape simultan), nu
# verificam viteza - impartirea la un dt foarte mic ar da un fals pozitiv pentru orice
# miscare mica.
MIN_DT_FOR_SPEED_CHECK = 0.02


def finite_number(v: Any) -> Optional[float]:
    """True doar pentru un numar REAL finit. `isinstance(x, (int, float))` lasa sa
    treaca NaN si +-Infinity (sunt float valide in Python) - un client rau-intentionat
    le poate trimite ca sa strice matematica (pozitii/rotatii) la ceilalti jucatori care
    primesc si aplica aceasta stare direct. math.isfinite le prinde pe amandoua."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    return f


def clean_state(msg: dict) -> dict:
    """Pastreaza doar campurile cunoscute, de tipuri simple, in limitele lumii de joc -
    restul (orice ar trimite un client modificat/rau-intentionat: campuri necunoscute,
    NaN, Infinity, coordonate absurde) se ignora, nu se retransmite mai departe ca atare."""
    out: dict[str, Any] = {}
    for k in STATE_FIELDS:
        v = msg.get(k)
        if k == "anim":
            if isinstance(v, str) and len(v) <= 32:
                out[k] = v
            continue
        n = finite_number(v)
        if n is None:
            continue
        if k in ("x", "y", "z") and abs(n) > MAX_COORD:
            continue  # pozitie in afara lumii - clar falsificata, nu doar un jucator departe
        out[k] = n
    return out


def passes_speed_check(
    prev_state: dict, prev_at: Optional[float], new_state: dict, now: float
) -> bool:
    """Respinge o teleportare/speedhack grosolana: daca intre doua stari ACCEPTATE de la
    acelasi jucator viteza implicita de deplasare depaseste MAX_SPEED_MPS, respingem noua
    stare (jucatorul ramane, pentru ceilalti, la ultima pozitie buna) in loc s-o
    retransmitem ca atare.

    prev_state/prev_at: ultima stare acceptata si momentul (time.monotonic()) la care a
    fost acceptata, sau {} / None daca jucatorul inca n-a trimis nicio stare.
    """
    if prev_at is None or not all(k in prev_state for k in ("x", "y", "z")):
        return True  # prima stare a acestui jucator - nimic de comparat
    if not all(k in new_state for k in ("x", "y", "z")):
        return True  # update fara pozitie (ex: doar animatia) - nu verificam viteza

    dt = now - prev_at
    if dt < MIN_DT_FOR_SPEED_CHECK:
        return True
    dx = new_state["x"] - prev_state["x"]
    dy = new_state["y"] - prev_state["y"]
    dz = new_state["z"] - prev_state["z"]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    return (dist / dt) <= MAX_SPEED_MPS