"""Rute pentru mesageria privata intre prieteni: REST (/api/dm/...) si WebSocket (/ws/dm).

Intreaga logica de securitate e in dm_core.DmService; aici doar: autentificare, validarea formei
cererii, injectarea accesului la date (aceleasi colectii ca restul aplicatiei) si maparea erorilor.

Clientul TRIMITE mesaje doar prin REST (o singura cale, cu toate verificarile). WebSocket-ul e
unidirectional (server -> client) si doar livreaza ce a validat serverul.

Montare in server.py:  api.include_router(dm_api_router); app.include_router(dm_ws_router)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from astran_sandbox.chat_core import ADULT
from astran_sandbox.chat_filter import shared_store
from astran_sandbox.dm_core import DEFAULT_PAGE, MAX_PAGE, DmError, DmHub, DmService
from astran_sandbox.dm_store import MongoDmStore

log = logging.getLogger("astran.dm")

_ADULT = ADULT  # aceeasi constanta ca in chatul din joc


class OpenBody(BaseModel):
    friend_id: str = Field(min_length=1, max_length=64)


class SendBody(BaseModel):
    text: str = Field(max_length=5000)  # limita REALA (500) o aplica serverul in sanitize_dm
    client_id: Optional[str] = Field(default=None, max_length=40)


class ReportBody(BaseModel):
    reason: str = Field(default="other", max_length=32)


_MESSAGES = {
    "empty": "Message is empty", "too_long": "Message is too long", "invalid": "Invalid request",
    "rate_limited": "Too fast", "duplicate": "Duplicate message", "muted": "You cannot chat right now",
    "not_allowed": "Not allowed", "not_found": "Not found", "unavailable": "Messaging is unavailable",
}


def _http(e: DmError) -> HTTPException:
    detail = {"code": e.code, "message": _MESSAGES.get(e.code, e.code)}
    if e.retry_ms is not None:
        detail["retry_ms"] = e.retry_ms
    return HTTPException(status_code=e.status, detail=detail)


def make_dm_routers(
    get_current_user: Callable,
    resolve_user_from_token: Callable[[str], Awaitable[Optional[dict]]],
    db,
    friend_public: Callable[[dict], dict],
):
    """Returneaza (router_api, router_ws) + serviciul si hub-ul (pentru wiring Redis/teste)."""

    async def user_state(uid: str) -> Optional[dict]:
        doc = await db.users.find_one({"user_id": uid}, {"_id": 0, "age_category": 1, "chat_muted_until": 1})
        if doc is None:
            return None
        until = doc.get("chat_muted_until")
        epoch = None
        if isinstance(until, datetime):
            epoch = (until if until.tzinfo else until.replace(tzinfo=timezone.utc)).timestamp()
        return {"age_category": doc.get("age_category"), "chat_muted_until": epoch}

    async def user_ages(ids: list) -> dict:
        rows = await db.users.find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 1, "age_category": 1}).to_list(len(ids))
        return {r["user_id"]: r.get("age_category") == _ADULT for r in rows}

    async def users_public(ids: list) -> dict:
        if not ids:
            return {}
        rows = await db.users.find({"user_id": {"$in": ids}}, {"_id": 0, "password_hash": 0}).to_list(len(ids))
        return {r["user_id"]: friend_public(r) for r in rows}

    async def are_friends(a: str, b: str) -> bool:
        x, y = (a, b) if a < b else (b, a)
        return await db.friendships.find_one({"user_a": x, "user_b": y}, {"_id": 1}) is not None

    async def friend_ids(uid: str) -> set:
        edges = await db.friendships.find({"$or": [{"user_a": uid}, {"user_b": uid}]}, {"_id": 0}).to_list(500)
        return {e["user_b"] if e["user_a"] == uid else e["user_a"] for e in edges}

    async def blocked_among(uid: str, ids: list) -> set:
        if not ids:
            return set()
        rows = await db.blocks.find(
            {"$or": [
                {"blocker_id": uid, "blocked_id": {"$in": ids}},
                {"blocked_id": uid, "blocker_id": {"$in": ids}},
            ]},
            {"_id": 0, "blocker_id": 1, "blocked_id": 1},
        ).to_list(2 * len(ids) + 10)
        return {r["blocked_id"] if r["blocker_id"] == uid else r["blocker_id"] for r in rows}

    async def blocked_between(a: str, b: str) -> bool:
        return b in await blocked_among(a, [b])

    # Redis (daca e configurat pentru multiplayer) e refolosit: un canal per utilizator, "dm_<uid>".
    try:
        from realtime import hub as rt_hub
    except Exception:  # noqa: BLE001
        rt_hub = None

    async def publish(uid: str, payload: dict) -> None:
        if rt_hub is not None and rt_hub.redis is not None:
            await rt_hub.redis.publish("dm_" + uid, payload)

    hub = DmHub()
    service = DmService(
        store=MongoDmStore(db), hub=hub,
        rules_provider=shared_store(db).get_compiled,   # ACELEASI reguli ca la chatul din joc
        user_state=user_state, user_ages=user_ages, users_public=users_public,
        are_friends=are_friends, friend_ids=friend_ids,
        blocked_between=blocked_between, blocked_among=blocked_among,
        publish=publish if rt_hub is not None else None,
    )

    if rt_hub is not None:
        async def _subscribe(uid: str) -> None:
            if rt_hub.redis is not None:
                await rt_hub.redis.ensure_subscribed("dm_" + uid)

        async def _unsubscribe(uid: str) -> None:
            if rt_hub.redis is not None:
                await rt_hub.redis.unsubscribe_if_unused("dm_" + uid)

        hub.on_first_connection, hub.on_last_disconnect = _subscribe, _unsubscribe
        rt_hub.dm_relay = service.relay_remote

    def _adult(current: dict) -> bool:
        return current.get("age_category") == _ADULT   # din DB (get_current_user), nu de la client

    api = APIRouter()

    @api.post("/dm/open")
    async def dm_open(body: OpenBody, current=Depends(get_current_user)):
        try:
            return await service.open_conversation(current["user_id"], body.friend_id)
        except DmError as e:
            raise _http(e)

    @api.get("/dm/conversations")
    async def dm_conversations(current=Depends(get_current_user)):
        try:
            return {"conversations": await service.list_conversations(current["user_id"], _adult(current))}
        except DmError as e:
            raise _http(e)
        except Exception:  # noqa: BLE001
            log.warning("dm: lista conversatiilor a esuat", exc_info=True)
            raise _http(DmError("unavailable"))

    @api.get("/dm/unread")
    async def dm_unread(current=Depends(get_current_user)):
        try:
            return {"unread": await service.unread_total(current["user_id"])}
        except Exception:  # noqa: BLE001
            log.warning("dm: numararea necititelor a esuat", exc_info=True)
            raise _http(DmError("unavailable"))

    @api.get("/dm/conversations/{conversation_id}/messages")
    async def dm_history(
        conversation_id: str,
        before: Optional[str] = Query(default=None, max_length=100),
        limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        current=Depends(get_current_user),
    ):
        try:
            return await service.history(current["user_id"], _adult(current), conversation_id, before, limit)
        except DmError as e:
            raise _http(e)
        except Exception:  # noqa: BLE001
            log.warning("dm: istoricul a esuat", exc_info=True)
            raise _http(DmError("unavailable"))

    @api.post("/dm/conversations/{conversation_id}/messages")
    async def dm_send(conversation_id: str, body: SendBody, current=Depends(get_current_user)):
        try:
            # expeditorul = DOAR sesiunea autentificata; body-ul nu poate contine identitatea
            return {"message": await service.send(current["user_id"], conversation_id, body.text, body.client_id)}
        except DmError as e:
            raise _http(e)

    @api.post("/dm/conversations/{conversation_id}/read")
    async def dm_read(conversation_id: str, current=Depends(get_current_user)):
        try:
            await service.mark_read(current["user_id"], conversation_id)
            return {"ok": True}
        except DmError as e:
            raise _http(e)
        except Exception:  # noqa: BLE001
            raise _http(DmError("unavailable"))

    @api.post("/dm/messages/{message_id}/report")
    async def dm_report(message_id: str, body: ReportBody, current=Depends(get_current_user)):
        try:
            await service.report(current["user_id"], message_id, body.reason)
            return {"ok": True}
        except DmError as e:
            raise _http(e)

    ws = APIRouter()

    @ws.websocket("/ws/dm")
    async def ws_dm(websocket: WebSocket, token: str = Query(...)):
        user = await resolve_user_from_token(token)
        if user is None:
            await websocket.close(code=4401)
            return
        uid = user["user_id"]
        await websocket.accept()
        if hub.count(uid) >= DmHub.MAX_CONNECTIONS_PER_USER:
            await websocket.close(code=4429)
            return
        await hub.add(uid, websocket)
        try:
            await websocket.send_text(json.dumps({"t": "dm_ready"}))
            while True:
                raw = await websocket.receive_text()
                # clientul nu trimite mesaje pe aceasta cale; acceptam doar un ping mic de keep-alive
                if len(raw) <= 16 and raw.strip() == "ping":
                    await websocket.send_text(json.dumps({"t": "pong"}))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.debug("dm ws inchis", exc_info=True)
        finally:
            await hub.remove(uid, websocket)

    api.service, api.hub = service, hub  # type: ignore[attr-defined]
    return api, ws