# backend/astran_sandbox/money.py
"""Mutari de Astrans sigure la cadere intre pasi (debit -> credit -> ledger).

PROBLEMA: MongoDB nu face aici o tranzactie intre `users` (sold) si `astran_ledger`.
Daca procesul pica dupa debit, banii dispareau fara urma (transfer, shop, clothes, group).

SOLUTIA (functioneaza si pe un MongoDB standalone, fara replica set / tranzactii):

1. Inainte de ORICE modificare de sold se scrie o intrare in jurnalul `astran_money_journal`
   cu tot ce trebuie ca operatia sa poata fi dusa la capat: cine plateste, cat, cine
   primeste, ce randuri de ledger / achizitie trebuie inserate.
2. Fiecare modificare de sold e atomica PE UN SINGUR DOCUMENT: `$inc` pe sold + un marker
   `<op_id>:<pas>` in `users._money_ops`, in ACELASI update. Filtrul cere ca markerul sa
   lipseasca, deci un pas nu se poate aplica de doua ori (idempotent).
3. Un task de recuperare (`recover_pending`) ia operatiile ramase `pending`:
   - daca debitul s-a aplicat (markerul exista) -> le duce la capat (credit + ledger);
   - daca debitul NU s-a aplicat -> le abandoneaza si sterge rezervarile (ex: randul de
     purchase), deci nimeni nu primeste ceva gratis si nimeni nu pierde bani.

Operatiile "pending" mai noi de `RECOVERY_MIN_AGE_SECONDS` sunt lasate in pace: ele pot fi
inca in executie in alt request.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

log = logging.getLogger("astran.money")

JOURNAL = "astran_money_journal"
MARKER = "_money_ops"          # camp pe documentul userului: ultimele operatii aplicate
MARKER_KEEP = 200               # cate markere pastram (recuperarea ruleaza in cateva minute)
RECOVERY_MIN_AGE_SECONDS = 120
RECOVERY_INTERVAL_SECONDS = 60
JOURNAL_RETENTION_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_op_id(prefix: str = "op_") -> str:
    return f"{prefix}{uuid.uuid4().hex[:20]}"


def _m(op_id: str, step: str, user_id: str = "") -> str:
    return f"{op_id}:{step}:{user_id}" if user_id else f"{op_id}:{step}"


async def ensure_indexes(db) -> None:
    await db[JOURNAL].create_index("op_id", unique=True)
    await db[JOURNAL].create_index([("state", 1), ("created_at", 1)])
    await db[JOURNAL].create_index("expires_at", expireAfterSeconds=0)


# ---------------------------------------------------------------- pasi atomici, idempotenti

async def debit(db, user_id: str, amount: int, op_id: str) -> str:
    """'ok' | 'already' (deja aplicat pentru acest op_id) | 'insufficient'."""
    marker = _m(op_id, "d")
    res = await db.users.update_one(
        {"user_id": user_id, "astrans_balance": {"$gte": amount}, MARKER: {"$ne": marker}},
        {"$inc": {"astrans_balance": -amount},
         "$push": {MARKER: {"$each": [marker], "$slice": -MARKER_KEEP}}},
    )
    if res.matched_count == 1:
        return "ok"
    if await db.users.find_one({"user_id": user_id, MARKER: marker}, {"_id": 1}) is not None:
        return "already"
    return "insufficient"


async def credit(db, user_id: str, amount: int, marker: str) -> bool:
    """True daca s-a aplicat acum; False daca markerul exista deja (sau userul nu mai exista)."""
    if amount == 0:
        return False
    res = await db.users.update_one(
        {"user_id": user_id, MARKER: {"$ne": marker}},
        {"$inc": {"astrans_balance": amount},
         "$push": {MARKER: {"$each": [marker], "$slice": -MARKER_KEEP}}},
    )
    return res.matched_count == 1


async def _was_debited(db, op: dict) -> bool:
    return await db.users.find_one(
        {"user_id": op["payer_id"], MARKER: _m(op["op_id"], "d")}, {"_id": 1}
    ) is not None


# ---------------------------------------------------------------- jurnal

async def begin(
    db, *, op_id: str, kind: str, payer_id: str, amount: int,
    credits: Optional[list[dict]] = None,
    inserts: Optional[list[dict]] = None,
    reservations: Optional[list[dict]] = None,
) -> dict:
    """Scrie intentia in jurnal (INAINTE de orice mutare de bani). DuplicateKeyError daca op_id exista.

    credits:      [{"user_id", "amount"}]            - cine primeste bani dupa debit
    inserts:      [{"collection", "doc"}]            - de inserat la final (ledger etc.); duplicatele se ignora
    reservations: [{"collection", "filter"}]         - sterse daca operatia esueaza / e abandonata
    """
    now = _now()
    doc = {
        "op_id": op_id, "kind": kind, "payer_id": payer_id, "amount": int(amount),
        "credits": [{"user_id": c["user_id"], "amount": int(c["amount"])} for c in (credits or [])],
        "inserts": [{"collection": i["collection"], "doc": dict(i["doc"])} for i in (inserts or [])],
        "reservations": list(reservations or []),
        "state": "pending", "created_at": now, "updated_at": now,
    }
    await db[JOURNAL].insert_one(dict(doc))
    return doc


async def get_op(db, op_id: str) -> Optional[dict]:
    return await db[JOURNAL].find_one({"op_id": op_id}, {"_id": 0})


async def _set_state(db, op_id: str, state: str, only_from: tuple = ("pending",)) -> None:
    now = _now()
    patch: dict[str, Any] = {"state": state, "updated_at": now}
    if state in ("done", "refunded", "failed"):
        patch["expires_at"] = now + timedelta(days=JOURNAL_RETENTION_DAYS)
    await db[JOURNAL].update_one({"op_id": op_id, "state": {"$in": list(only_from)}}, {"$set": patch})


async def settle(db, op_id: str) -> None:
    """Duce la capat o operatie DEJA debitata: credite + inserturi. Idempotent."""
    op = await get_op(db, op_id)
    if not op or op["state"] != "pending":
        return
    for c in op["credits"]:
        await credit(db, c["user_id"], c["amount"], _m(op_id, "c", c["user_id"]))
    for ins in op["inserts"]:
        try:
            await db[ins["collection"]].insert_one(dict(ins["doc"]))
        except DuplicateKeyError:
            pass  # deja inserat (retry / recuperare)
    await _set_state(db, op_id, "done")


async def rollback(db, op_id: str, reason: str = "") -> None:
    """Anuleaza o operatie pe care NU am dus-o la capat: da banii inapoi (daca s-au luat) si
    sterge rezervarile. Nu are efect pe o operatie deja 'done' (creditele sunt aplicate)."""
    op = await get_op(db, op_id)
    if not op or op["state"] != "pending":
        return
    debited = await _was_debited(db, op)
    if debited:
        await credit(db, op["payer_id"], op["amount"], _m(op_id, "r"))
    for r in op.get("reservations", []):
        await db[r["collection"]].delete_one(r["filter"])
    await _set_state(db, op_id, "refunded" if debited else "failed")
    if reason:
        log.info("money op %s rolled back: %s", op_id, reason)


async def run(db, op_id: str) -> str:
    """debit -> settle. Intoarce 'done' sau 'insufficient' (in acest caz totul e anulat)."""
    op = await get_op(db, op_id)
    if not op:
        raise RuntimeError("money op not found")
    if op["state"] == "done":
        return "done"
    if op["state"] != "pending":
        return op["state"]
    status = await debit(db, op["payer_id"], op["amount"], op_id)
    if status == "insufficient":
        await rollback(db, op_id, "insufficient funds")
        return "insufficient"
    await settle(db, op_id)
    return "done"


# ---------------------------------------------------------------- recuperare dupa cadere

async def recover_pending(db, older_than_seconds: int = RECOVERY_MIN_AGE_SECONDS, limit: int = 100) -> int:
    """Reia operatiile ramase 'pending'. Intoarce cate au fost rezolvate."""
    cutoff = _now() - timedelta(seconds=older_than_seconds)
    ops = await db[JOURNAL].find({"state": "pending", "created_at": {"$lt": cutoff}}, {"_id": 0}).to_list(limit)
    fixed = 0
    for op in ops:
        try:
            if await _was_debited(db, op):
                await settle(db, op["op_id"])          # banii au fost luati: ducem la capat
            else:
                await rollback(db, op["op_id"], "abandoned before debit")  # nimic de dat inapoi
            fixed += 1
        except Exception:  # noqa: BLE001 - o operatie stricata nu opreste restul
            log.exception("money recovery failed for %s", op.get("op_id"))
    if fixed:
        log.warning("money recovery: %d operatii rezolvate", fixed)
    return fixed


async def recovery_loop(db) -> None:
    while True:
        try:
            await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)
            await recover_pending(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("money recovery loop error")