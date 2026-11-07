"""Mesagerie privata intre prieteni (conversatii 1-la-1, persistente) - SEPARATA de chatul din joc.
Modul fara dependinte externe (doar stdlib), testabil fara FastAPI/Mongo/Redis: accesul la date
(store, utilizatori, blocari, prietenii, reguli de filtrare) intra prin obiecte/functii injectate.
Stratul HTTP/WebSocket e in dm_routes.py, stocarea MongoDB in dm_store.py.

============================================================================
CE REFOLOSESTE (nu duplica nimic din chatul din joc)
============================================================================
* chat_filter.shared_store(db).get_compiled -> ACELEASI reguli (acelasi RuleStore, aceeasi lista,
  aceleasi actiuni mask_word / mask_sentence) ca la chatul din joc. Nu exista a doua lista.
* chat_core.text_for -> UNICUL loc unde se decide ce text vede un destinatar: raw doar daca
  expeditorul SI destinatarul sunt 18+; in orice alt caz textul filtrat.
* chat_core.ChatLimiter -> anti-spam (burst, interval minim, duplicate, blocare temporara).
* users.age_category / users.chat_muted_until, db.blocks, db.friendships, db.reports.

============================================================================
GARANTII DE SECURITATE (toate aplicate PE SERVER)
============================================================================
* Expeditorul vine din sesiunea autentificata (get_current_user / token WebSocket); clientul nu
  trimite si nu poate falsifica user_id/username/ts/id.
* Accesul la o conversatie: doar cei doi participanti; pentru oricine altcineva raspunsul e
  "not_found" (nu dezvaluim nici ca exista).
* Trimiterea cere: prietenie existenta + nicio blocare (oricare directie) + utilizator nemutat.
  Citirea istoricului cere: participant + nicio blocare. Lista ascunde conversatiile blocate.
* Modul de varsta NU vine de la client: expeditorul = users.age_category din DB la FIECARE mesaj;
  spectatorul = users.age_category din DB la FIECARE citire/livrare. Lipsa/eroare => sub 18.
* Un utilizator sub 18 nu primeste NICIODATA textul nefiltrat al altcuiva, si nici NU persista
  textul brut al unui minor: pentru expeditor sub 18 se salveaza doar `safe` (raw=None).
  Textul brut (raw) se salveaza doar pentru expeditori 18+ (necesar livrarii catre alti 18+).
* Filtrarea ruleaza INAINTE de salvare/livrare; clientul nu filtreaza nimic si nu primeste raw
  daca nu are voie. Daca regulile nu pot fi citite, mesajul e REFUZAT (nu se trimite nefiltrat).
* Mesajul e validat (tip, lungime, gol, caractere invizibile/bidi); prea lung = refuzat, nu taiat.
"""
from __future__ import annotations

import json
import logging
import time
import unicodedata
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from astran_sandbox.chat_core import ADULT, REPORT_REASONS, ChatLimiter, ChatRecord, _INVISIBLE, _WS, text_for
from astran_sandbox.chat_filter import CompiledRule, filter_text

log = logging.getLogger("astran.dm")

MAX_DM_LEN = 500            # caractere dupa curatare (chatul din joc: 200)
MAX_RAW_LEN = 2000          # mai mult de atat nici nu mai procesam
MAX_CLIENT_ID_LEN = 40
DEFAULT_PAGE = 30
MAX_PAGE = 50
MAX_CONVERSATIONS = 100
MAX_LIMITERS = 5000

_ERR_STATUS = {
    "empty": 400, "too_long": 400, "invalid": 400,
    "rate_limited": 429, "duplicate": 429,
    "muted": 403, "not_allowed": 403,
    "not_found": 404, "unavailable": 503,
}


class DmError(Exception):
    def __init__(self, code: str, retry_after: Optional[float] = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = _ERR_STATUS.get(code, 400)
        self.retry_ms = int(retry_after * 1000) + 1 if retry_after is not None else None


def sanitize_dm(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """(text_curat, None) sau (None, cod_eroare). Nu trunchiaza: un mesaj prea lung e REFUZAT."""
    if not isinstance(raw, str):
        return None, "invalid"
    if len(raw) > MAX_RAW_LEN:
        return None, "too_long"
    text = unicodedata.normalize("NFC", raw)
    text = _WS.sub(" ", text)
    text = _INVISIBLE.sub("", text).strip()
    if not text:
        return None, "empty"
    if len(text) > MAX_DM_LEN:
        return None, "too_long"
    return text, None


def pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def parse_cursor(value: Optional[str]) -> Optional[tuple[int, str]]:
    """'<ts_ms>:<message_id>' -> (ts, id). Orice altceva => DmError('invalid')."""
    if value is None or value == "":
        return None
    try:
        ts_s, mid = value.split(":", 1)
        ts = int(ts_s)
    except (ValueError, AttributeError):
        raise DmError("invalid")
    if ts < 0 or not (0 < len(mid) <= 64):
        raise DmError("invalid")
    return ts, mid


def make_cursor(msg: dict) -> str:
    return f"{msg['ts']}:{msg['message_id']}"


def _record(msg: dict) -> ChatRecord:
    return ChatRecord(
        id=msg["message_id"], ts=int(msg["ts"]), user_id=msg["sender_id"], username="",
        sender_adult=bool(msg.get("sender_adult")), raw=msg.get("raw"), safe=msg.get("safe", ""),
    )


def view_message(msg: dict, viewer_id: str, viewer_adult: bool) -> dict:
    """Mesajul asa cum il vede UN spectator. Decizia raw/safe e in chat_core.text_for."""
    return {
        "id": msg["message_id"],
        "conversation_id": msg["conversation_id"],
        "sender_id": msg["sender_id"],
        "ts": int(msg["ts"]),
        "text": text_for(_record(msg), viewer_adult),
        "mine": msg["sender_id"] == viewer_id,
    }


# --------------------------------------------------------------------------------------
# Livrare in timp real (conexiunile WebSocket ale acestui proces)
# --------------------------------------------------------------------------------------
class DmHub:
    """Conexiunile WebSocket /ws/dm ale acestui proces: user_id -> {socket}. Un utilizator poate
    avea mai multe dispozitive. Nu ia decizii de securitate - doar trimite ce i se da."""

    MAX_CONNECTIONS_PER_USER = 5

    def __init__(self) -> None:
        self._conns: dict[str, set] = {}
        self.on_first_connection: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_last_disconnect: Optional[Callable[[str], Awaitable[None]]] = None

    def has(self, uid: str) -> bool:
        return bool(self._conns.get(uid))

    def count(self, uid: str) -> int:
        return len(self._conns.get(uid, ()))

    async def add(self, uid: str, ws: Any) -> None:
        first = uid not in self._conns or not self._conns[uid]
        self._conns.setdefault(uid, set()).add(ws)
        if first and self.on_first_connection is not None:
            try:
                await self.on_first_connection(uid)
            except Exception:  # noqa: BLE001
                log.warning("dm: abonarea cross-process a esuat", exc_info=True)

    async def remove(self, uid: str, ws: Any) -> None:
        s = self._conns.get(uid)
        if not s:
            return
        s.discard(ws)
        if not s:
            del self._conns[uid]
            if self.on_last_disconnect is not None:
                try:
                    await self.on_last_disconnect(uid)
                except Exception:  # noqa: BLE001
                    pass

    async def send(self, uid: str, payload: dict) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        for ws in list(self._conns.get(uid, ())):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001 - socket inchis; se curata in finally-ul conexiunii
                pass


# --------------------------------------------------------------------------------------
# Serviciul
# --------------------------------------------------------------------------------------
class DmService:
    """Orchestrarea mesageriei private. Dependentele sunt injectate:

    store                      - vezi dm_store.MongoDmStore (aceeasi interfata in teste)
    rules_provider()           - async -> list[CompiledRule]  (chat_filter.shared_store(db).get_compiled)
    user_state(uid)            - async -> {"age_category", "chat_muted_until"(epoch s|None)} | None
    user_ages(ids)             - async -> {uid: bool adult}
    users_public(ids)          - async -> {uid: profil public (user_id, username, display_name, avatar_url, ...)}
    are_friends(a, b)          - async -> bool
    friend_ids(uid)            - async -> set[str]
    blocked_between(a, b)      - async -> bool (oricare directie)
    blocked_among(uid, ids)    - async -> set[str] (ids cu blocare fata de uid, oricare directie)
    publish(uid, payload)      - async, optional: catre ALTE procese (Redis)
    """

    def __init__(
        self,
        *,
        store: Any,
        hub: DmHub,
        rules_provider: Callable[[], Awaitable[list[CompiledRule]]],
        user_state: Callable[[str], Awaitable[Optional[dict]]],
        user_ages: Callable[[list], Awaitable[dict]],
        users_public: Callable[[list], Awaitable[dict]],
        are_friends: Callable[[str, str], Awaitable[bool]],
        friend_ids: Callable[[str], Awaitable[set]],
        blocked_between: Callable[[str, str], Awaitable[bool]],
        blocked_among: Callable[[str, list], Awaitable[set]],
        publish: Optional[Callable[[str, dict], Awaitable[None]]] = None,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store, self.hub = store, hub
        self._rules, self._user_state, self._user_ages = rules_provider, user_state, user_ages
        self._users_public, self._are_friends, self._friend_ids = users_public, are_friends, friend_ids
        self._blocked_between, self._blocked_among = blocked_between, blocked_among
        self._publish = publish
        self._clock, self._mono = clock, mono
        self._limiters: "OrderedDict[str, ChatLimiter]" = OrderedDict()

    # ------------------------------------------------------------------ helpers
    def _limiter(self, uid: str) -> ChatLimiter:
        lim = self._limiters.get(uid)
        if lim is None:
            lim = ChatLimiter(mono=self._mono)
            self._limiters[uid] = lim
            while len(self._limiters) > MAX_LIMITERS:
                self._limiters.popitem(last=False)
        else:
            self._limiters.move_to_end(uid)
        return lim

    async def _viewer_adult(self, uid: str) -> bool:
        """Varsta spectatorului din DB; orice eroare/valoare lipsa => sub 18 (fail-safe)."""
        try:
            return bool((await self._user_ages([uid])).get(uid, False))
        except Exception:  # noqa: BLE001
            log.warning("dm: varsta destinatarului nu a putut fi citita - tratat ca sub 18", exc_info=True)
            return False

    async def _conversation_for(self, uid: str, cid: Any) -> dict:
        if not isinstance(cid, str) or not (0 < len(cid) <= 64):
            raise DmError("not_found")
        conv = await self.store.get_conversation(cid)
        if conv is None or uid not in (conv["user_a"], conv["user_b"]):
            raise DmError("not_found")  # acelasi raspuns: nu dezvaluim existenta conversatiei
        return conv

    @staticmethod
    def _other(conv: dict, uid: str) -> str:
        return conv["user_b"] if conv["user_a"] == uid else conv["user_a"]

    @staticmethod
    def _slot(conv: dict, uid: str) -> str:
        return "a" if conv["user_a"] == uid else "b"

    # ------------------------------------------------------------------ deschidere
    async def open_conversation(self, uid: str, friend_id: Any) -> dict:
        if not isinstance(friend_id, str) or not (0 < len(friend_id) <= 64) or friend_id == uid:
            raise DmError("invalid")
        try:
            allowed = await self._are_friends(uid, friend_id) and not await self._blocked_between(uid, friend_id)
            users = await self._users_public([friend_id]) if allowed else {}
        except Exception:  # noqa: BLE001
            log.warning("dm: open_conversation - verificarea a esuat", exc_info=True)
            raise DmError("unavailable")
        if not allowed or friend_id not in users:
            raise DmError("not_allowed")
        a, b = pair(uid, friend_id)
        try:
            conv = await self.store.get_or_create(a, b, "dm_" + uuid.uuid4().hex[:16], datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            log.warning("dm: crearea conversatiei a esuat", exc_info=True)
            raise DmError("unavailable")
        return {"conversation_id": conv["conversation_id"], "friend": users[friend_id], "can_send": True}

    # ------------------------------------------------------------------ lista
    async def list_conversations(self, uid: str, viewer_adult: bool) -> list[dict]:
        convs = await self.store.list_conversations(uid, MAX_CONVERSATIONS)
        if not convs:
            return []
        others = [self._other(c, uid) for c in convs]
        blocked = await self._blocked_among(uid, others)
        friends = await self._friend_ids(uid)
        convs = [c for c in convs if self._other(c, uid) not in blocked]
        others = [self._other(c, uid) for c in convs]
        users = await self._users_public(others)
        last = await self.store.get_messages_by_ids([c["last_message_id"] for c in convs if c.get("last_message_id")])
        out = []
        for c in convs:
            other = self._other(c, uid)
            if other not in users:
                continue
            m = last.get(c.get("last_message_id"))
            out.append({
                "conversation_id": c["conversation_id"],
                "friend": users[other],
                "last_message": view_message(m, uid, viewer_adult) if m else None,
                "unread": int(c.get("unread_" + self._slot(c, uid), 0) or 0),
                "can_send": other in friends,
                "updated_at": int(c.get("last_message_at") or 0),
            })
        return out  # deja sortate de store dupa last_message_at desc

    async def unread_total(self, uid: str) -> int:
        convs = await self.store.unread_conversations(uid)
        if not convs:
            return 0
        blocked = await self._blocked_among(uid, [self._other(c, uid) for c in convs])
        return sum(int(c.get("unread_" + self._slot(c, uid), 0) or 0) for c in convs if self._other(c, uid) not in blocked)

    # ------------------------------------------------------------------ istoric
    async def history(self, uid: str, viewer_adult: bool, cid: Any, before: Optional[str], limit: int) -> dict:
        conv = await self._conversation_for(uid, cid)
        cursor = parse_cursor(before)
        limit = max(1, min(int(limit), MAX_PAGE))
        if await self._blocked_between(uid, self._other(conv, uid)):
            raise DmError("not_allowed")
        rows = await self.store.page_messages(conv["conversation_id"], cursor, limit + 1)  # desc
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "messages": [view_message(m, uid, viewer_adult) for m in rows],  # cele mai noi primele
            "has_more": has_more,
            "next_before": make_cursor(rows[-1]) if rows and has_more else None,
        }

    async def mark_read(self, uid: str, cid: Any) -> None:
        conv = await self._conversation_for(uid, cid)
        await self.store.mark_read(conv["conversation_id"], self._slot(conv, uid))
        await self.hub.send(uid, {"t": "dm_sync"})  # celelalte dispozitive ale aceluiasi cont isi reimprospateaza necititele

    # ------------------------------------------------------------------ trimitere
    async def send(self, uid: str, cid: Any, raw_text: Any, client_id: Any = None) -> dict:
        conv = await self._conversation_for(uid, cid)
        text, err = sanitize_dm(raw_text)
        if err or text is None:
            raise DmError(err or "invalid")
        other = self._other(conv, uid)
        try:
            state = await self._user_state(uid)
            compiled = await self._rules()
            allowed = state is not None and await self._are_friends(uid, other) and not await self._blocked_between(uid, other)
        except Exception:  # noqa: BLE001
            log.warning("dm: starea/regulile/permisiunile nu au putut fi citite", exc_info=True)
            raise DmError("unavailable")
        if state is None or compiled is None:
            raise DmError("unavailable")
        muted_until = state.get("chat_muted_until")
        if isinstance(muted_until, (int, float)) and muted_until > self._clock():
            raise DmError("muted", muted_until - self._clock())
        if not allowed:
            raise DmError("not_allowed")  # nu prieteni sau blocat - acelasi raspuns, fara detalii

        verdict = self._limiter(uid).check_send(f"{conv['conversation_id']}:{text}")
        if verdict is not None:
            raise DmError(verdict[0], verdict[1])

        sender_adult = state.get("age_category") == ADULT   # DOAR din DB; orice altceva = sub 18
        safe = filter_text(text, compiled).text
        msg = {
            "message_id": "dmm_" + uuid.uuid4().hex[:20],
            "conversation_id": conv["conversation_id"],
            "sender_id": uid,
            "ts": int(self._clock() * 1000),
            "sender_adult": sender_adult,
            "raw": text if sender_adult else None,   # textul brut al unui minor NU se pastreaza
            "safe": safe,
        }
        try:
            await self.store.insert_message(msg)
        except Exception:  # noqa: BLE001
            log.warning("dm: salvarea mesajului a esuat", exc_info=True)
            raise DmError("unavailable")
        try:
            await self.store.bump_conversation(conv["conversation_id"], msg["message_id"], msg["ts"], self._slot(conv, other))
        except Exception:  # noqa: BLE001 - mesajul e salvat; doar lista/necititele pot intarzia
            log.warning("dm: actualizarea conversatiei a esuat (mesajul e salvat)", exc_info=True)

        await self._deliver(msg, [other, uid])
        out = view_message(msg, uid, sender_adult)
        if isinstance(client_id, str) and 0 < len(client_id) <= MAX_CLIENT_ID_LEN:
            out["client_id"] = client_id
        return out

    # ------------------------------------------------------------------ livrare
    async def _push(self, uid: str, msg: dict) -> None:
        if not self.hub.has(uid):
            return
        adult = await self._viewer_adult(uid)   # varsta CURENTA din DB, pentru fiecare destinatar
        await self.hub.send(uid, {"t": "dm", "conversation_id": msg["conversation_id"], "message": view_message(msg, uid, adult)})

    async def _deliver(self, msg: dict, targets: list[str]) -> None:
        for uid in targets:
            try:
                await self._push(uid, msg)
            except Exception:  # noqa: BLE001 - o livrare esuata nu strica trimiterea (mesajul e salvat)
                log.warning("dm: livrarea locala a esuat", exc_info=True)
        if self._publish is not None:
            # catre ALTE procese: raw doar daca expeditorul e adult - textul unui minor nu paraseste
            # procesul nefiltrat (si oricum nu exista, raw=None pentru minori).
            payload = {
                "t": "dm_x", "cid": msg["conversation_id"], "id": msg["message_id"], "ts": msg["ts"],
                "sid": msg["sender_id"], "adult": msg["sender_adult"],
                "raw": msg["raw"] if msg["sender_adult"] else None, "safe": msg["safe"],
            }
            for uid in targets:
                try:
                    await self._publish(uid, payload)
                except Exception:  # noqa: BLE001
                    log.warning("dm: publicarea cross-process a esuat", exc_info=True)

    async def relay_remote(self, uid: str, payload: dict) -> None:
        """Mesaj venit de la ALT proces (Redis) pentru un utilizator conectat la acesta. Validam
        strict; varsta spectatorului se reciteste din DB (nu ne bazam pe nimic din payload)."""
        try:
            cid, mid, sid, ts, safe = payload["cid"], payload["id"], payload["sid"], payload["ts"], payload["safe"]
            adult, raw = bool(payload.get("adult")), payload.get("raw")
        except (KeyError, TypeError):
            return
        if not (isinstance(cid, str) and isinstance(mid, str) and isinstance(sid, str) and isinstance(safe, str)
                and isinstance(ts, int) and not isinstance(ts, bool)):
            return
        if raw is not None and not isinstance(raw, str):
            return
        if len(safe) > MAX_DM_LEN or len(cid) > 64 or len(mid) > 64 or len(sid) > 64 or (raw is not None and len(raw) > MAX_DM_LEN):
            return
        msg = {"message_id": mid, "conversation_id": cid, "sender_id": sid, "ts": ts,
               "sender_adult": adult, "raw": raw if adult else None, "safe": safe}
        try:
            await self._push(uid, msg)
        except Exception:  # noqa: BLE001
            log.warning("dm: livrarea mesajului remote a esuat", exc_info=True)

    # ------------------------------------------------------------------ raportare
    async def report(self, uid: str, message_id: Any, reason: Any) -> None:
        if not isinstance(message_id, str) or not (0 < len(message_id) <= 64):
            raise DmError("invalid")
        msg = await self.store.get_message(message_id)
        if msg is None:
            raise DmError("not_found")
        conv = await self._conversation_for(uid, msg["conversation_id"])   # doar participantii pot raporta
        if msg["sender_id"] == uid:
            raise DmError("invalid")
        reason = reason if reason in REPORT_REASONS else "other"
        verdict = self._limiter(uid).check_report(message_id)
        if verdict == "duplicate":
            return  # deja raportat: idempotent
        if verdict is not None:
            raise DmError("rate_limited")
        doc = {
            "report_id": "rep_" + uuid.uuid4().hex[:16],
            "reporter_id": uid,
            "target_type": "message",
            "target_id": message_id,
            "reason": reason,
            "details": None,
            "status": "open",
            "created_at": datetime.now(timezone.utc),
            # singurul loc unde un mesaj privat e copiat intr-un alt document: la cererea explicita
            # a unui participant, pentru moderare (acelasi tipar ca raportul din chatul din joc)
            "context": {
                "source": "private_dm", "conversation_id": conv["conversation_id"],
                "sender_id": msg["sender_id"],
                "text": msg["raw"] if msg.get("raw") is not None else msg["safe"],
                "text_displayed_to_minors": msg["safe"], "sent_at_ms": int(msg["ts"]),
            },
        }
        try:
            await self.store.insert_report(doc)
        except Exception:  # noqa: BLE001
            self._limiter(uid).reported.discard(message_id)
            log.warning("dm: raportul nu a putut fi salvat", exc_info=True)
            raise DmError("unavailable")