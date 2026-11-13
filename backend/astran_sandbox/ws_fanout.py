"""Trimitere WebSocket robusta la clienti lenti.

PROBLEMA: broadcast-urile (stare jucatori, chat) faceau `await ws.send_text(...)` SECVENTIAL, fara
timeout, catre fiecare membru al camerei. Un client care nu citeste (conexiune moarta / atacator) umplea
bufferul TCP, `send_text` se bloca, iar TOTI ceilalti din camera nu mai primeau nimic. Cu camere de pana la
10.000 de jucatori, costul era si O(n) secvential per mesaj.
ACUM: trimitere concurenta, cu timeout per client; un client care nu preia datele in timp util e
deconectat (inchiderea lui declanseaza curatarea normala din `finally`-ul conexiunii).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

log = logging.getLogger("astran.ws")

SEND_TIMEOUT = 5.0
CLOSE_TIMEOUT = 2.0


async def send_safely(ws: Any, text: str, timeout: float = SEND_TIMEOUT) -> bool:
    """True daca s-a trimis. La eroare / timeout incearca sa inchida socket-ul si intoarce False."""
    try:
        await asyncio.wait_for(ws.send_text(text), timeout)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - socket inchis, timeout sau client lent
        if isinstance(exc, asyncio.TimeoutError):
            log.warning("ws: client prea lent (> %ss), il deconectam", timeout)
            try:
                await asyncio.wait_for(ws.close(code=1013), CLOSE_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass
        return False


async def fan_out(sockets: Iterable[Any], text: str, timeout: float = SEND_TIMEOUT) -> None:
    """Trimite acelasi text tuturor socket-urilor, concurent; un client lent nu-i blocheaza pe ceilalti."""
    tasks = [send_safely(ws, text, timeout) for ws in sockets]
    if tasks:
        await asyncio.gather(*tasks)