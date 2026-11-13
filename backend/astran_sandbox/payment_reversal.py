"""Refund / chargeback pentru Astransii cumparati cu bani reali.

PROBLEMA: plata se verifica o singura data (server.py: /wallet/buy). Daca Stripe / Google rambursau
ulterior (refund, dispute/chargeback, achizitie anulata), Astransii ramaneau cheltuibili: cumperi,
cheltui, ceri banii inapoi si pastrezi tot. Acest modul retrage Astransii la o reversare.

- verify_stripe_signature: verificare HMAC a webhook-ului Stripe (fara biblioteca stripe).
- stripe_reversal_ref: din evenimentul Stripe scoate payment_intent-ul de reversat (sau None).
- google_voided_refs: din raspunsul voidedpurchases scoate purchase token-urile anulate.
- reverse_purchase: retrage Astransii (idempotent) si marcheaza randul de ledger ca 'reversed'.
Doar stdlib + money.py (testabil fara FastAPI / MongoDB).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from . import money

log = logging.getLogger("astran.reversal")

STRIPE_TOLERANCE_SECONDS = 300


def verify_stripe_signature(payload: bytes, header: str, secret: str,
                            now: Optional[float] = None, tolerance: int = STRIPE_TOLERANCE_SECONDS) -> bool:
    """Header `Stripe-Signature: t=<ts>,v1=<hmac>[,v1=<hmac>...]`; HMAC-SHA256 peste `<ts>.<payload>`."""
    if not payload or not header or not secret:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        if k and v:
            parts.setdefault(k, []).append(v)
    try:
        ts = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        return False  # replay: semnatura prea veche (sau din viitor)
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


def stripe_reversal_ref(event: dict) -> Optional[tuple[str, str]]:
    """(payment_intent_id, motiv) pentru un refund TOTAL sau un dispute nou; altfel None.

    charge.refunded cu refund PARTIAL nu se retrage automat (ar trebui calculata proportia si
    urmarita cumulativ): intoarce None si apelantul il logheaza pentru verificare manuala."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type")
    obj = ((event.get("data") or {}).get("object")) or {}
    if not isinstance(obj, dict):
        return None
    if etype == "charge.refunded":
        amount, refunded = obj.get("amount"), obj.get("amount_refunded")
        if isinstance(amount, int) and isinstance(refunded, int) and amount > 0 and refunded >= amount \
                and isinstance(obj.get("payment_intent"), str):
            return obj["payment_intent"], "stripe_refund"
        return None
    if etype == "charge.dispute.created":
        pi = obj.get("payment_intent")
        if isinstance(pi, str):
            return pi, "stripe_dispute"
    return None


def google_voided_refs(data: Any) -> list[str]:
    """Purchase token-urile din raspunsul `purchases/voidedpurchases` (doar produse in-app)."""
    out: list[str] = []
    for it in (data or {}).get("voidedPurchases") or []:
        if isinstance(it, dict) and isinstance(it.get("purchaseToken"), str) and it["purchaseToken"]:
            out.append(it["purchaseToken"])
    return out


async def reverse_purchase(db, provider_ref: str, reason: str) -> str:
    """'reversed' | 'already' | 'not_found'. Idempotent (retry / webhook livrat de doua ori)."""
    row = await db.astran_ledger.find_one({"provider_ref": provider_ref, "type": "purchase"}, {"_id": 0})
    if not row:
        return "not_found"
    h = hashlib.sha256(provider_ref.encode()).hexdigest()
    applied = await money.clawback(db, row["user_id"], int(row.get("amount", 0)), "rev_" + h[:32])
    rev = {
        "tx_id": "txrev_" + h[:24], "user_id": row["user_id"], "counterparty_id": "system",
        "type": "purchase_reversal", "amount": int(row.get("amount", 0)), "fee": 0,
        "net": int(row.get("amount", 0)), "status": "completed",
        "timestamp": datetime.now(timezone.utc), "reference": f"reversal:{reason}:{row['tx_id']}",
    }
    try:
        await db.astran_ledger.insert_one(rev)
    except DuplicateKeyError:
        pass
    await db.astran_ledger.update_one(
        {"tx_id": row["tx_id"]}, {"$set": {"status": "reversed", "reversed_at": datetime.now(timezone.utc)}})
    if applied:
        log.warning("purchase reversed: user=%s astrans=%s reason=%s", row["user_id"], rev["amount"], reason)
    return "reversed" if applied else "already"