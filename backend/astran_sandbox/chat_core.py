"""Chatul din joc (per instanta de joc), peste WebSocket-ul din backend/realtime.py.
Modul fara dependinte externe (doar stdlib), ca sa poata fi testat fara FastAPI/Mongo/Redis -
toate accesele la date (user, blocari, rapoarte, reguli) intra prin functii injectate.

============================================================================
PROTOCOL (JSON peste acelasi WebSocket ca pozitiile - vezi realtime.py)
============================================================================
Client -> server:
    {"t": "chat", "text": "salut", "cid": "<id local, max 40 car., pt. a recunoaste ecoul>"}
    {"t": "chat_report", "id": "<id mesaj>", "reason": "harassment|spam|inappropriate|other"}
Server -> client:
    {"t": "chat_info", "mode": "under_18"|"adult_18", "max_len": 200}      (la conectare; informativ)
    {"t": "chat_history", "messages": [<chat>, ...]}                        (la conectare)
    {"t": "chat", "id", "ts", "user_id", "username", "text", "cid"?}        ("cid" doar la expeditor)
    {"t": "chat_error", "code": "empty|too_long|invalid|rate_limited|duplicate|muted|unavailable",
                        "cid"?, "retry_ms"?}
    {"t": "chat_report_ack", "id": "..."}

============================================================================
GARANTII DE SECURITATE (toate aplicate PE SERVER)
============================================================================
* Identitatea expeditorului (user_id, username) vine din token-ul verificat la handshake -
  campurile "user_id"/"username"/"ts"/"id" trimise de client sunt ignorate complet.
* Modul de varsta NU vine de la client: se citeste din baza de date (users.age_category),
  la FIECARE mesaj pentru expeditor si la fiecare livrare pentru destinatari. Valoare lipsa /
  necunoscuta / eroare de DB => tratat ca sub 18 (fail-safe).
* Un destinatar primeste textul NEFILTRAT doar daca expeditorul SI destinatarul sunt 18+.
  In orice alt caz primeste textul filtrat. Un utilizator sub 18 nu primeste niciodata raw.
* Mesajele NU se salveaza in baza de date. Ele traiesc doar in memoria procesului, intr-un
  buffer mic (ultimele ROOM_BUFFER mesaje ale camerei, sters cand camera ramane goala) folosit
  pentru istoricul de la intrare si pentru raportari. Singura persistenta: un RAPORT facut
  explicit de un jucator (db.reports, cu textul mesajului raportat).
* Mesajele nu ajung la/de la jucatori cu care exista o blocare (db.blocks, oricare directie).
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional

from astran_sandbox.chat_filter import CompiledRule, filter_text

log = logging.getLogger("astran.chat")

MAX_CHAT_LEN = 200          # caractere (code points), dupa curatare
MAX_RAW_LEN = 1000          # mai mult de atat, nici nu mai procesam
ROOM_BUFFER = 60            # mesaje pastrate in memorie per camera
HISTORY_ON_JOIN = 30
MAX_CID_LEN = 40
REPORT_REASONS = ("harassment", "spam", "inappropriate", "other")

ADULT = "adult_18"

# caractere de control + zero-width + marcaje bidi (pot ascunde text / inversa afisarea)
_INVISIBLE = re.compile(
    "[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]"
)
_WS = re.compile(r"\s+")


def sanitize_message(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """(text_curat, None) sau (None, cod_eroare). Nu trunchiaza: un mesaj prea lung e REFUZAT,
    ca utilizatorul sa stie ca nu a fost trimis intreg."""
    if not isinstance(raw, str):
        return None, "invalid"
    if len(raw) > MAX_RAW_LEN:
        return None, "too_long"
    text = unicodedata.normalize("NFC", raw)
    text = _WS.sub(" ", text)           # newline/tab -> spatiu (chatul e pe o singura linie)
    text = _INVISIBLE.sub("", text).strip()
    if not text:
        return None, "empty"
    if len(text) > MAX_CHAT_LEN:
        return None, "too_long"
    return text, None


class ChatLimiter:
    """Limitare de rata PER CONEXIUNE (stare in memorie, ca si limitarea mesajelor de stare din
    realtime.py). Ceasul e injectabil pentru teste."""

    def __init__(
        self,
        mono: Callable[[], float] = time.monotonic,
        burst: int = 4,
        window: float = 6.0,
        min_gap: float = 0.35,
        dup_window: float = 15.0,
        strikes_to_lock: int = 3,
        strike_window: float = 30.0,
        lock_secs: float = 20.0,
        reports_per_min: int = 3,
    ) -> None:
        self._mono = mono
        self.burst, self.window, self.min_gap = burst, window, min_gap
        self.dup_window = dup_window
        self.strikes_to_lock, self.strike_window, self.lock_secs = strikes_to_lock, strike_window, lock_secs
        self.reports_per_min = reports_per_min
        self._sent: deque = deque()
        self._recent: dict = {}
        self._strikes: deque = deque()
        self._locked_until = 0.0
        self._report_times: deque = deque()
        self.reported: set = set()

    def check_send(self, text: str) -> Optional[tuple[str, float]]:
        """None = permis (si inregistrat). Altfel (cod, retry_after_secunde)."""
        now = self._mono()
        if now < self._locked_until:
            return "rate_limited", self._locked_until - now
        while self._sent and now - self._sent[0] >= self.window:
            self._sent.popleft()
        key = text.casefold()
        seen = self._recent.get(key)
        if seen is not None and now - seen < self.dup_window:
            return "duplicate", self.dup_window - (now - seen)
        too_fast = bool(self._sent) and now - self._sent[-1] < self.min_gap
        if too_fast or len(self._sent) >= self.burst:
            while self._strikes and now - self._strikes[0] >= self.strike_window:
                self._strikes.popleft()
            self._strikes.append(now)
            if len(self._strikes) >= self.strikes_to_lock:
                self._locked_until = now + self.lock_secs
                self._strikes.clear()
                return "rate_limited", self.lock_secs
            retry = (self.window - (now - self._sent[0])) if len(self._sent) >= self.burst else self.min_gap
            return "rate_limited", max(retry, 0.1)
        self._sent.append(now)
        self._recent[key] = now
        if len(self._recent) > 32:
            for k in [k for k, t in self._recent.items() if now - t >= self.dup_window]:
                del self._recent[k]
        return None

    def check_report(self, message_id: str) -> Optional[str]:
        now = self._mono()
        while self._report_times and now - self._report_times[0] >= 60.0:
            self._report_times.popleft()
        if message_id in self.reported:
            return "duplicate"
        if len(self._report_times) >= self.reports_per_min:
            return "rate_limited"
        self._report_times.append(now)
        self.reported.add(message_id)
        if len(self.reported) > 200:
            self.reported.clear()
        return None


@dataclass
class ChatRecord:
    id: str
    ts: int                      # milisecunde epoch, pus de SERVER
    user_id: str
    username: str
    sender_adult: bool
    raw: Optional[str]           # textul original (doar in memoria procesului; vezi text_for)
    safe: str                    # textul filtrat


def text_for(rec: ChatRecord, recipient_adult: bool) -> str:
    """UNICUL loc unde se decide ce text vede un destinatar."""
    if rec.sender_adult and recipient_adult and rec.raw is not None:
        return rec.raw
    return rec.safe


UserState = Optional[dict]


class ChatDispatcher:
    """Orchestrarea chatului unei camere. `room` trebuie sa aiba: members (dict user_id->membru),
    chat_buffer (deque), instance_id, game_id. Un membru: user_id, username, ws (send_text),
    is_adult (valoarea de la conectare), chat_limiter."""

    def __init__(
        self,
        *,
        rules_provider: Callable[[], Awaitable[list[CompiledRule]]],
        user_state: Callable[[str], Awaitable[UserState]],
        recipient_ages: Callable[[list], Awaitable[dict]],
        blocked_with: Callable[[str, list], Awaitable[set]],
        report_sink: Callable[[dict], Awaitable[None]],
        publish: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._rules = rules_provider
        self._user_state = user_state
        self._recipient_ages = recipient_ages
        self._blocked_with = blocked_with
        self._report_sink = report_sink
        self.publish = publish
        self._clock = clock

    # ---------------------------------------------------------------- helpers
    @staticmethod
    async def _send(member: Any, payload: dict) -> None:
        try:
            await member.ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - socket inchis; se curata in finally-ul conexiunii
            pass

    async def error(self, member: Any, code: str, cid: Optional[str] = None, retry_after: Optional[float] = None) -> None:
        payload: dict = {"t": "chat_error", "code": code}
        if cid:
            payload["cid"] = cid
        if retry_after is not None:
            payload["retry_ms"] = int(retry_after * 1000) + 1
        await self._send(member, payload)

    @staticmethod
    def _public(rec: ChatRecord, text: str, cid: Optional[str] = None) -> dict:
        out = {"t": "chat", "id": rec.id, "ts": rec.ts, "user_id": rec.user_id, "username": rec.username, "text": text}
        if cid:
            out["cid"] = cid
        return out

    async def _audience(self, members: list, author_id: str, author_adult: bool) -> tuple[set, dict]:
        """(id-uri blocate fata de autor, {user_id: e_adult}) pentru membrii dati. Ridica la eroare
        de DB - apelantul decide (nu livram nimic in loc sa livram gresit)."""
        others = [m.user_id for m in members if m.user_id != author_id]
        if not others:
            return set(), {}
        blocked = await self._blocked_with(author_id, others)
        ages: dict = {}
        if author_adult:
            # raw se trimite doar adultilor; verificam varsta lor ACUM, nu cea de la conectare
            candidates = [m.user_id for m in members if m.user_id != author_id and m.is_adult]
            if candidates:
                ages = await self._recipient_ages(candidates)
        return set(blocked), ages

    # ---------------------------------------------------------------- conectare
    async def on_join(self, room: Any, member: Any) -> None:
        await self._send(member, {"t": "chat_info", "mode": ADULT if member.is_adult else "under_18", "max_len": MAX_CHAT_LEN})
        recs = list(room.chat_buffer)[-HISTORY_ON_JOIN:]
        messages: list = []
        if recs:
            try:
                authors = sorted({r.user_id for r in recs if r.user_id != member.user_id})
                blocked = await self._blocked_with(member.user_id, authors) if authors else set()
                ages = await self._recipient_ages([member.user_id]) if member.is_adult else {}
            except Exception:  # noqa: BLE001
                log.warning("chat: istoricul nu a putut fi pregatit", exc_info=True)
                recs, blocked, ages = [], set(), {}
            adult = bool(ages.get(member.user_id, False))
            for r in recs:
                if r.user_id in blocked:
                    continue
                messages.append(self._public(r, text_for(r, adult)))
        await self._send(member, {"t": "chat_history", "messages": messages})

    # ---------------------------------------------------------------- mesaj
    async def handle_chat(self, room: Any, member: Any, msg: dict) -> None:
        cid = msg.get("cid")
        cid = cid if isinstance(cid, str) and 0 < len(cid) <= MAX_CID_LEN else None

        text, err = sanitize_message(msg.get("text"))
        if err or text is None:
            await self.error(member, err or "invalid", cid)
            return
        verdict = member.chat_limiter.check_send(text)
        if verdict is not None:
            await self.error(member, verdict[0], cid, verdict[1])
            return

        try:
            state = await self._user_state(member.user_id)
            compiled = await self._rules()
        except Exception:  # noqa: BLE001
            log.warning("chat: starea utilizatorului/regulile nu au putut fi citite", exc_info=True)
            state, compiled = None, None
        if not state or compiled is None:
            await self.error(member, "unavailable", cid)
            return
        muted_until = state.get("chat_muted_until")
        if isinstance(muted_until, (int, float)) and muted_until > self._clock():
            await self.error(member, "muted", cid, muted_until - self._clock())
            return

        sender_adult = state.get("age_category") == ADULT   # DOAR din DB; orice altceva = sub 18
        safe = filter_text(text, compiled).text
        rec = ChatRecord(
            id=uuid.uuid4().hex[:16], ts=int(self._clock() * 1000), user_id=member.user_id,
            username=member.username, sender_adult=sender_adult, raw=text, safe=safe,
        )
        members = list(room.members.values())
        try:
            blocked, ages = await self._audience(members, rec.user_id, sender_adult)
        except Exception:  # noqa: BLE001
            log.warning("chat: audienta nu a putut fi determinata", exc_info=True)
            await self.error(member, "unavailable", cid)
            return

        room.chat_buffer.append(rec)
        await self._fan_out(members, rec, blocked, ages, echo_cid=cid)
        if self.publish is not None:
            # catre ALTE procese (Redis): raw doar daca expeditorul e adult - textul unui minor
            # nu paraseste procesul in forma nefiltrata.
            await self.publish(room.instance_id, {
                "t": "chat_x", "id": rec.id, "ts": rec.ts, "uid": rec.user_id, "un": rec.username,
                "adult": sender_adult, "raw": text if sender_adult else None, "safe": safe,
            })

    async def _fan_out(self, members: list, rec: ChatRecord, blocked: set, ages: dict, echo_cid: Optional[str]) -> None:
        for m in members:
            if m.user_id == rec.user_id:
                await self._send(m, self._public(rec, text_for(rec, bool(rec.sender_adult)), echo_cid))  # expeditorul isi vede propriul text
                continue
            if m.user_id in blocked:
                continue
            await self._send(m, self._public(rec, text_for(rec, bool(ages.get(m.user_id, False)))))

    # ---------------------------------------------------------------- mesaj venit de la alt proces
    async def relay_remote(self, room: Any, payload: dict) -> None:
        try:
            rid, uid, un, safe = payload["id"], payload["uid"], payload["un"], payload["safe"]
            ts, adult, raw = payload["ts"], bool(payload.get("adult")), payload.get("raw")
        except (KeyError, TypeError):
            return
        if not (isinstance(rid, str) and isinstance(uid, str) and isinstance(un, str) and isinstance(safe, str)
                and isinstance(ts, int) and not isinstance(ts, bool)):
            return
        if raw is not None and not isinstance(raw, str):
            return
        if len(safe) > MAX_CHAT_LEN or len(un) > 64 or len(rid) > 64 or (raw is not None and len(raw) > MAX_CHAT_LEN):
            return
        if any(r.id == rid for r in room.chat_buffer):
            return
        rec = ChatRecord(id=rid, ts=ts, user_id=uid, username=un, sender_adult=adult, raw=raw if adult else None, safe=safe)
        members = list(room.members.values())
        try:
            blocked, ages = await self._audience(members, uid, adult)
        except Exception:  # noqa: BLE001
            log.warning("chat: livrarea mesajului remote a esuat (audienta)", exc_info=True)
            return
        room.chat_buffer.append(rec)
        for m in members:
            if m.user_id in blocked:
                continue
            await self._send(m, self._public(rec, text_for(rec, bool(ages.get(m.user_id, False)))))

    # ---------------------------------------------------------------- raportare
    async def handle_report(self, room: Any, member: Any, msg: dict) -> None:
        mid = msg.get("id")
        if not isinstance(mid, str) or not (0 < len(mid) <= 64):
            await self.error(member, "invalid")
            return
        reason = msg.get("reason")
        reason = reason if reason in REPORT_REASONS else "other"
        rec = next((r for r in room.chat_buffer if r.id == mid), None)
        if rec is None or rec.user_id == member.user_id:
            await self.error(member, "invalid")
            return
        verdict = member.chat_limiter.check_report(mid)
        if verdict == "duplicate":
            await self._send(member, {"t": "chat_report_ack", "id": mid})  # deja raportat: idempotent
            return
        if verdict is not None:
            await self.error(member, "rate_limited")
            return
        doc = {
            "report_id": "rep_" + uuid.uuid4().hex[:16],
            "reporter_id": member.user_id,
            "target_type": "message",
            "target_id": mid,
            "reason": reason,
            "details": None,
            "status": "open",
            "created_at": datetime.now(timezone.utc),
            # singurul loc unde un mesaj de chat e scris in baza de date: la cererea unui jucator
            "context": {
                "source": "game_chat", "game_id": getattr(room, "game_id", None), "instance_id": room.instance_id,
                "sender_id": rec.user_id, "sender_username": rec.username,
                "text": rec.raw if rec.raw is not None else rec.safe, "text_displayed_to_minors": rec.safe,
                "sent_at_ms": rec.ts,
            },
        }
        try:
            await self._report_sink(doc)
        except Exception:  # noqa: BLE001
            log.warning("chat: raportul nu a putut fi salvat", exc_info=True)
            member.chat_limiter.reported.discard(mid)
            await self.error(member, "unavailable")
            return
        await self._send(member, {"t": "chat_report_ack", "id": mid})