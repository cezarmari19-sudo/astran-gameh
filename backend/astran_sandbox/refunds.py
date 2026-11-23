"""Sistem de rambursari (cereri utilizator + decizie admin + executie prin procesator) pentru achizitii digitale.

Reguli de baza:
  * tranzactiile din `astran_ledger` NU se sterg si NU se modifica: rambursarea e o tranzactie NOUA (`purchase_refund`,
    `refund_in`, `refund_out`); sumele deja rambursate se deduc din `refund_requests`, nu din ledger;
  * o singura cerere ACTIVA per tranzactie (index unic sparse pe `active_key`); max. MAX_REQUESTS_PER_TX in total;
  * suma rambursata nu depaseste niciodata suma efectiv platita (cap recalculat la aprobare SI la executie);
  * soldul nu devine niciodata negativ: Astransii se retrag doar in limita soldului (debit atomic cu `$gte`);
  * executia e idempotenta (markeri de sold, cheie de idempotenta la Stripe, jurnal money.py, tranzitii conditionale);
  * fiecare actiune scrie in `consumer_audit_log` (adauga-doar).
Statusuri vizibile: pending, under_review, approved, rejected, refunded. Detaliul executiei: `processing_state`.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from . import consumer as C
from . import money

log = logging.getLogger("astran.refunds")

COL_REQ = "refund_requests"
COL_AUDIT = "consumer_audit_log"
STATUSES = ("pending", "under_review", "approved", "rejected", "refunded")
REASONS = ("withdrawal", "not_as_described", "defective", "not_received", "unauthorized_purchase", "duplicate_charge", "other")
MAX_REQUESTS_PER_TX = 3
EXPLANATION_MAX = 2000
STUCK_AFTER = timedelta(minutes=5)

# Clasificarea tranzactiilor din ledger pe tipuri de Astrans.
_BOUGHT = {"purchase"}
_SPENT_ITEM = {"shop_purchase": "shop_item", "clothes_purchase": "clothes_item"}
_REFUNDABLE_TYPES = _BOUGHT | set(_SPENT_ITEM)
_FREE = {"welcome_bonus", "gift", "bonus"}
_EARNED = {"shop_sale", "clothes_sale"}
_TRANSFER = {"transfer_in", "transfer_out"}


class RefundError(C.ConsumerError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify_tx(tx: dict) -> dict:
    t = tx.get("type", "")
    if t in _BOUGHT:
        cat = "bought_astrans"
    elif t in _SPENT_ITEM:
        cat = "spent_astrans"
    elif t in _TRANSFER:
        cat = "transferred_astrans"
    elif t in _EARNED:
        cat = "earned_astrans"
    elif t in _FREE or (tx.get("counterparty_id") == "system" and t not in ("purchase_reversal", "purchase_refund", "refund_in", "refund_out", "group_create")):
        cat = "free_astrans"
    elif t == "group_create":
        cat = "spent_astrans_unsupported"
    else:
        cat = "other"
    return {"category": cat, "refundable": t in _REFUNDABLE_TYPES and tx.get("status") == "completed"}


async def ensure_indexes(db) -> None:
    await db[COL_REQ].create_index("refund_id", unique=True)
    await db[COL_REQ].create_index("active_key", unique=True, sparse=True)
    await db[COL_REQ].create_index([("user_id", 1), ("created_at", -1)])
    await db[COL_REQ].create_index([("status", 1), ("created_at", 1)])
    await db[COL_AUDIT].create_index("event_id", unique=True)
    await db[COL_AUDIT].create_index([("refund_id", 1), ("ts", 1)])


async def audit(db, event_type: str, actor: Optional[dict], refund_id: Optional[str] = None, tx_id: Optional[str] = None,
                details: Optional[dict] = None, role: Optional[str] = None) -> None:
    await db[COL_AUDIT].insert_one({
        "event_id": "aud_" + uuid.uuid4().hex, "ts": _now(), "event_type": event_type,
        "actor_id": (actor or {}).get("user_id"), "actor_role": role or ("admin" if (actor or {}).get("is_platform_admin") else "user"),
        "refund_id": refund_id, "tx_id": tx_id, "details": details or {}})


# ----------------------------------------------------------------------------- sume deja rambursate
async def refunded_totals(db, tx_id: str, exclude_refund_id: Optional[str] = None) -> dict:
    """Ce s-a rambursat deja pentru tranzactie (cereri 'refunded' + cele in executie). Ledger-ul nu se atinge."""
    rows = await db[COL_REQ].find({"tx_id": tx_id}, {"_id": 0}).to_list(50)
    astrans = money_minor = items = 0
    for r in rows:
        if r["refund_id"] == exclude_refund_id:
            continue
        active_exec = r["status"] == "refunded" or r.get("processing_state") in ("processing", "awaiting_store_refund", "failed_retryable")
        if active_exec and r.get("plan"):
            astrans += int(r["plan"].get("astrans_to_reclaim", 0))
            money_minor += int(r["plan"].get("money_refund_minor", 0))
            items += int(r["plan"].get("buyer_credit", 0))
    return {"astrans": astrans, "money_minor": money_minor, "item_astrans": items}


# ----------------------------------------------------------------------------- cerere utilizator
async def refundable_transactions(db, user_id: str) -> list:
    txs = await db.astran_ledger.find({"user_id": user_id}, {"_id": 0}).sort("timestamp", -1).limit(100).to_list(100)
    out = []
    for tx in txs:
        cl = classify_tx(tx)
        if not cl["refundable"]:
            continue
        order = await C.get_order_for_tx(db, tx["tx_id"])
        ws = C.withdrawal_status(order, tx.get("timestamp"))
        active = await db[COL_REQ].find_one({"tx_id": tx["tx_id"], "active_key": {"$exists": True}}, {"_id": 0, "refund_id": 1, "status": 1})
        out.append({"tx_id": tx["tx_id"], "type": tx["type"], "reference": tx.get("reference"), "timestamp": tx.get("timestamp"),
                    "amount": tx.get("amount"), "price": tx.get("price"), "currency": tx.get("currency") or "ASTRANS",
                    "order_id": order["order_id"] if order else None, "withdrawal_right_applies": ws["withdrawal_right_applies"],
                    "withdrawal_deadline": ws["deadline"], "waiver_valid": ws["waiver_valid"], "active_request": active})
    return out


def _paid(tx: dict) -> dict:
    if tx["type"] == "purchase":
        if tx.get("price") is None:
            raise RefundError("tx_price_missing", 422, "This transaction has no recorded price; contact support")
        return {"unit": "money", "paid_minor": C.minor(tx["price"]), "currency": tx.get("currency", "RON")}
    return {"unit": "astrans", "paid_minor": int(tx["amount"]), "currency": "ASTRANS"}


async def create_request(db, user: dict, tx_id: str, reason: str, explanation: Optional[str]) -> dict:
    if reason not in REASONS:
        raise RefundError("invalid_reason", 422, "Invalid reason")
    explanation = (explanation or "").strip() or None
    if explanation and len(explanation) > EXPLANATION_MAX:
        raise RefundError("explanation_too_long", 422, "Explanation too long")
    # proprietarul tranzactiei: filtrul include user_id din sesiune; pentru tranzactia altcuiva raspundem 404 (nu 403)
    tx = await db.astran_ledger.find_one({"tx_id": tx_id, "user_id": user["user_id"]}, {"_id": 0})
    if not tx:
        raise RefundError("transaction_not_found", 404, "Transaction not found")
    cl = classify_tx(tx)
    if not cl["refundable"]:
        raise RefundError("transaction_not_refundable", 422, "This kind of transaction is not refundable through this system",
                          category=cl["category"])
    paid = _paid(tx)
    if tx.get("status") == "reversed":
        raise RefundError("already_reversed", 409, "This purchase was already reversed by the payment processor")
    prev = await refunded_totals(db, tx_id)
    if (paid["unit"] == "money" and prev["money_minor"] >= paid["paid_minor"]) or (paid["unit"] == "astrans" and prev["item_astrans"] >= paid["paid_minor"]):
        raise RefundError("already_refunded", 409, "This transaction was already refunded")
    if await db[COL_REQ].count_documents({"tx_id": tx_id}) >= MAX_REQUESTS_PER_TX:
        raise RefundError("too_many_requests", 429, "Too many requests for this transaction; contact support")
    order = await C.get_order_for_tx(db, tx_id)
    ws = C.withdrawal_status(order, tx.get("timestamp"))
    now = _now()
    req = {"refund_id": "rf_" + uuid.uuid4().hex[:20], "user_id": user["user_id"], "tx_id": tx_id,
           "order_id": order["order_id"] if order else None, "tx_type": tx["type"], "category": cl["category"],
           "product_type": (order or {}).get("product_type") or _SPENT_ITEM.get(tx["type"], "astran_package"),
           "product_id": (order or {}).get("product_id") or tx.get("item_id") or tx.get("package_id"),
           "product_name": (order or {}).get("product_name") or tx.get("reference"),
           "reason": reason, "explanation": explanation, "status": "pending", "processing_state": "idle",
           "paid": {**paid, "provider": tx.get("provider")},
           "eligibility": {"withdrawal_right_applies": ws["withdrawal_right_applies"], "waiver_valid": ws["waiver_valid"],
                           "withdrawal_deadline": ws["deadline"], "legacy_no_order": ws["legacy_no_order"],
                           "conformity_claim": reason in ("not_as_described", "defective", "not_received")},
           "active_key": tx_id, "admin_response": None, "plan": None,
           "history": [{"status": "pending", "at": now, "by": "user"}], "created_at": now, "updated_at": now}
    try:
        await db[COL_REQ].insert_one(dict(req))
    except DuplicateKeyError:
        existing = await db[COL_REQ].find_one({"tx_id": tx_id, "active_key": {"$exists": True}}, {"_id": 0, "refund_id": 1})
        raise RefundError("duplicate_request", 409, "There is already an active refund request for this transaction",
                          refund_id=(existing or {}).get("refund_id"))
    await audit(db, "refund_requested", user, req["refund_id"], tx_id, {"reason": reason, "tx_type": tx["type"]}, role="user")
    return public_request(req)


def public_request(r: dict) -> dict:
    keys = ("refund_id", "tx_id", "order_id", "product_name", "tx_type", "category", "reason", "explanation", "status", "processing_state",
            "admin_response", "created_at", "updated_at", "decided_at", "refunded_at", "history")
    out = {k: r.get(k) for k in keys}
    out["paid"] = {"unit": r["paid"]["unit"], "amount_minor": r["paid"]["paid_minor"], "currency": r["paid"]["currency"]}
    p = r.get("plan") or {}
    out["refund"] = ({"money_minor": p.get("money_refund_minor"), "currency": r["paid"]["currency"], "astrans_returned_to_you": p.get("buyer_credit"),
                      "astrans_reclaimed": p.get("astrans_to_reclaim")} if r["status"] in ("approved", "refunded") else None)
    out["history"] = [{"status": h["status"], "at": h["at"]} for h in r.get("history", [])]
    return out


async def list_requests(db, user_id: str) -> list:
    rows = await db[COL_REQ].find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(100).to_list(100)
    return [public_request(r) for r in rows]


async def get_own_request(db, user_id: str, refund_id: str) -> dict:
    r = await db[COL_REQ].find_one({"refund_id": refund_id, "user_id": user_id}, {"_id": 0})
    if not r:
        raise RefundError("refund_not_found", 404, "Refund request not found")
    return public_request(r)


# ----------------------------------------------------------------------------- plan (suma si Astrans)
async def _user(db, uid: str) -> Optional[dict]:
    return await db.users.find_one({"user_id": uid}, {"_id": 0, "user_id": 1, "astrans_balance": 1})


async def item_for_tx(db, tx: dict, order: Optional[dict]) -> tuple[str, str]:
    pt = (order or {}).get("product_type") or _SPENT_ITEM[tx["type"]]
    iid = (order or {}).get("product_id") or tx.get("item_id")
    if not iid:   # tranzactie veche (fara order/item_id): potrivire unica dupa nume + autor, altfel nu ghicim
        name = (tx.get("reference") or "").split(":")[-1]
        coll = db.shop_items if pt == "shop_item" else db.clothes_items
        rows = await coll.find({"name": name, "owner_id": tx.get("counterparty_id")}, {"_id": 0, "item_id": 1}).to_list(2)
        if len(rows) != 1:
            raise RefundError("item_unresolvable", 409, "The purchased item cannot be identified automatically; handle manually")
        iid = rows[0]["item_id"]
    return pt, iid


async def compute_plan(db, req: dict, *, override_full: bool = False) -> dict:
    tx = await db.astran_ledger.find_one({"tx_id": req["tx_id"]}, {"_id": 0})
    if not tx:
        raise RefundError("transaction_not_found", 404, "Transaction not found")
    prev = await refunded_totals(db, req["tx_id"], exclude_refund_id=req["refund_id"])
    buyer = await _user(db, req["user_id"])
    if tx["type"] == "purchase":
        bought = int(tx["amount"])
        paid_minor = C.minor(tx["price"])
        rem_ast, rem_money = bought - prev["astrans"], paid_minor - prev["money_minor"]
        if rem_ast <= 0 or rem_money <= 0:
            raise RefundError("nothing_refundable", 409, "Nothing left to refund for this transaction")
        balance = max(0, int((buyer or {}).get("astrans_balance", 0)))
        reclaim = min(rem_ast, balance)
        money_minor = rem_money if override_full else (rem_money * reclaim) // rem_ast   # rotunjire in jos: nu depasim niciodata suma platita
        money_minor = min(money_minor, rem_money)
        if money_minor <= 0:
            raise RefundError("nothing_refundable", 409,
                              "All purchased Astrans were already spent; nothing can be refunded in money without a full-refund override",
                              spent_astrans=rem_ast)
        return {"kind": "purchase_refund", "astrans_to_reclaim": reclaim, "money_refund_minor": money_minor,
                "currency": tx.get("currency", "RON"), "spent_astrans_unrecovered": rem_ast - reclaim, "override_full": bool(override_full),
                "provider": tx.get("provider"), "provider_ref": tx.get("provider_ref")}
    order = await C.get_order_for_tx(db, tx["tx_id"])
    pt, iid = await item_for_tx(db, tx, order)
    price = int(tx["amount"])
    if prev["item_astrans"] >= price:
        raise RefundError("already_refunded", 409, "This transaction was already refunded")
    seller_id = tx.get("counterparty_id")
    share = price - int(tx.get("fee", 0))
    seller = await _user(db, seller_id) if seller_id else None
    reclaim = min(share, max(0, int((seller or {}).get("astrans_balance", 0)))) if seller else 0
    return {"kind": "item_refund", "buyer_credit": price, "seller_id": seller_id if seller else None, "seller_reclaim": reclaim,
            "platform_absorbed": price - reclaim, "product_type": pt, "item_id": iid, "override_full": False}


# ----------------------------------------------------------------------------- admin
def require_admin_doc(u: Optional[dict]) -> dict:
    if not u or u.get("is_platform_admin") is not True or u.get("deletion_state"):
        raise RefundError("forbidden", 403, "Administrator access required")
    return u


async def admin_list(db, status: Optional[str] = None, limit: int = 100) -> list:
    q: dict = {}
    if status:
        if status not in STATUSES:
            raise RefundError("invalid_status", 422, "Invalid status")
        q["status"] = status
    rows = await db[COL_REQ].find(q, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return [{**public_request(r), "user_id": r["user_id"], "reason": r["reason"]} for r in rows]


async def admin_detail(db, actor: dict, refund_id: str) -> dict:
    r = await db[COL_REQ].find_one({"refund_id": refund_id}, {"_id": 0})
    if not r:
        raise RefundError("refund_not_found", 404, "Refund request not found")
    tx = await db.astran_ledger.find_one({"tx_id": r["tx_id"]}, {"_id": 0})
    order = await C.get_order_for_tx(db, r["tx_id"])
    consents = await C.consents_for_order(db, order) if order else []
    usage = None
    if r["tx_type"] in _SPENT_ITEM and r.get("product_id"):
        coll = db.shop_purchases if r["tx_type"] == "shop_purchase" else db.clothes_purchases
        pr = await coll.find_one({"user_id": r["user_id"], "item_id": r["product_id"]}, {"_id": 0})
        usage = {"content_first_accessed_at": (pr or {}).get("content_first_accessed_at"), "revoked": bool((pr or {}).get("revoked"))}
    plan_preview = None
    if r["status"] in ("pending", "under_review", "approved") and not r.get("plan"):
        try:
            plan_preview = await compute_plan(db, r)
        except RefundError as e:
            plan_preview = {"error": e.code}
    buyer = await _user(db, r["user_id"])
    audit_rows = await db[COL_AUDIT].find({"refund_id": refund_id}, {"_id": 0}).sort("ts", 1).limit(500).to_list(500)
    await audit(db, "admin_viewed", actor, refund_id, r["tx_id"], {"sees": "transaction+order+consents"}, role="admin")
    return {"request": {**r, "history": r.get("history", [])}, "transaction": tx, "order": order, "consents": consents, "usage": usage,
            "plan_preview": plan_preview, "user_balance": (buyer or {}).get("astrans_balance"), "audit": audit_rows}


async def _transition(db, refund_id: str, from_states: tuple, patch: dict, history_status: str, actor: dict, extra_unset: Optional[dict] = None):
    now = _now()
    upd: dict = {"$set": {**patch, "status": history_status, "updated_at": now}, "$push": {"history": {"status": history_status, "at": now, "by": actor["user_id"]}}}
    if extra_unset:
        upd["$unset"] = extra_unset
    return await db[COL_REQ].find_one_and_update({"refund_id": refund_id, "status": {"$in": list(from_states)}}, upd)


async def start_review(db, actor: dict, refund_id: str) -> None:
    r = await _load(db, refund_id)
    _no_self(actor, r)
    if not await _transition(db, refund_id, ("pending",), {"reviewer_id": actor["user_id"]}, "under_review", actor):
        raise RefundError("invalid_state", 409, "Only pending requests can be put under review", current_status=r["status"])
    await audit(db, "review_started", actor, refund_id, r["tx_id"])


async def _load(db, refund_id: str) -> dict:
    r = await db[COL_REQ].find_one({"refund_id": refund_id}, {"_id": 0})
    if not r:
        raise RefundError("refund_not_found", 404, "Refund request not found")
    return r


def _no_self(actor: dict, r: dict) -> None:
    if actor["user_id"] == r["user_id"]:
        raise RefundError("conflict_of_interest", 403, "Administrators cannot decide on their own refund requests")


async def decide(db, actor: dict, refund_id: str, decision: str, reasoning: str, *, override: bool = False) -> dict:
    reasoning = (reasoning or "").strip()
    if len(reasoning) < 3:
        raise RefundError("reasoning_required", 422, "A reason for the decision is required")
    r = await _load(db, refund_id)
    _no_self(actor, r)
    if decision == "reject":
        if not await _transition(db, refund_id, ("pending", "under_review"), {"admin_response": reasoning, "decided_by": actor["user_id"], "decided_at": _now()},
                                 "rejected", actor, extra_unset={"active_key": ""}):
            raise RefundError("invalid_state", 409, "Only pending / under-review requests can be rejected", current_status=r["status"])
        await audit(db, "refund_rejected", actor, refund_id, r["tx_id"], {"reasoning": reasoning})
        return public_request(await _load(db, refund_id))
    if decision != "approve":
        raise RefundError("invalid_decision", 422, "Invalid decision")
    if r["status"] == "approved" and r.get("processing_state") not in ("idle", "failed_retryable", "failed_definitive"):
        raise RefundError("invalid_state", 409, "Already being processed")
    if r["status"] not in ("pending", "under_review", "approved"):
        raise RefundError("invalid_state", 409, "This request can no longer be approved", current_status=r["status"])
    elig = r["eligibility"]
    # Retragerea (art. 9) nu se aproba daca dreptul nu se aplica (renuntare valida sau termen depasit), decat prin override explicit, auditat.
    if r["reason"] == "withdrawal" and not elig["withdrawal_right_applies"] and not override:
        raise RefundError("withdrawal_not_applicable", 409,
                          "The right of withdrawal does not apply (valid waiver or period expired). Approve only with an explicit override and reasoning.")
    plan = await compute_plan(db, r, override_full=override and r["paid"]["unit"] == "money")
    patch = {"admin_response": reasoning, "decided_by": actor["user_id"], "decided_at": _now(), "plan": plan, "override_used": bool(override),
             "processing_state": "idle"}
    if not await _transition(db, refund_id, ("pending", "under_review", "approved"), patch, "approved", actor):
        raise RefundError("invalid_state", 409, "State changed concurrently; reload")
    await audit(db, "refund_approved", actor, refund_id, r["tx_id"], {"reasoning": reasoning, "override": bool(override), "plan": plan})
    return public_request(await _load(db, refund_id))


# ----------------------------------------------------------------------------- procesatori
class RefundResult:
    def __init__(self, ok: bool, provider_refund_id: Optional[str] = None, transient: bool = False, detail: Optional[str] = None):
        self.ok, self.provider_refund_id, self.transient, self.detail = ok, provider_refund_id, transient, detail


class StripeProcessor:
    """Rambursare REALA prin Stripe (POST /v1/refunds) cu cheie de idempotenta = refund_id. `http_post` injectabil (teste)."""
    def __init__(self, http_post=None):
        self.http_post = http_post

    async def refund(self, *, payment_intent: str, amount_minor: int, refund_id: str) -> RefundResult:
        key = os.environ.get("STRIPE_SECRET_KEY")
        if not key:
            return RefundResult(False, transient=True, detail="Stripe is not configured")
        data = {"payment_intent": payment_intent, "amount": str(int(amount_minor)), "reason": "requested_by_customer", "metadata[refund_id]": refund_id}
        try:
            if self.http_post is not None:
                status, body = await self.http_post("https://api.stripe.com/v1/refunds", data, {"Idempotency-Key": refund_id}, key)
            else:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as h:
                    r = await h.post("https://api.stripe.com/v1/refunds", data=data, auth=(key, ""), headers={"Idempotency-Key": refund_id})
                status, body = r.status_code, r.json()
        except Exception as e:  # noqa: BLE001
            log.error("stripe refund error: %s", e)
            return RefundResult(False, transient=True, detail="Stripe unreachable")
        if status == 200 and body.get("status") in ("succeeded", "pending", "requires_action"):
            return RefundResult(True, body.get("id"))
        if status >= 500 or status == 429:
            return RefundResult(False, transient=True, detail="Stripe temporarily unavailable")
        return RefundResult(False, detail=str(((body or {}).get("error") or {}).get("code") or body.get("status") or status))


class MockProcessor:
    async def refund(self, *, payment_intent: str, amount_minor: int, refund_id: str) -> RefundResult:
        if os.environ.get("ALLOW_MOCK_PAYMENTS") != "1":
            return RefundResult(False, detail="mock refunds disabled")
        return RefundResult(True, "mockrf_" + refund_id)


DEFAULT_PROCESSORS = {"stripe": StripeProcessor(), "mock": MockProcessor()}


# ----------------------------------------------------------------------------- executie
async def _claim_exec(db, refund_id: str) -> Optional[dict]:
    now = _now()
    return await db[COL_REQ].find_one_and_update(
        {"refund_id": refund_id, "status": "approved", "$or": [
            {"processing_state": {"$in": ["idle", "failed_retryable"]}},
            {"processing_state": "processing", "processing_started_at": {"$lt": now - STUCK_AFTER}}]},
        {"$set": {"processing_state": "processing", "processing_started_at": now, "updated_at": now}})


def _tx_id(prefix: str, refund_id: str) -> str:
    return prefix + hashlib.sha256(refund_id.encode()).hexdigest()[:24]


async def execute(db, actor: dict, refund_id: str, processors: Optional[dict] = None) -> dict:
    processors = processors if processors is not None else DEFAULT_PROCESSORS
    r = await _load(db, refund_id)
    _no_self(actor, r)
    if r["status"] == "refunded":
        raise RefundError("already_refunded", 409, "Already refunded")
    if r["status"] != "approved" or not r.get("plan"):
        raise RefundError("invalid_state", 409, "Only approved requests can be executed", current_status=r["status"])
    if not await _claim_exec(db, refund_id):
        raise RefundError("already_processing", 409, "This refund is already being processed or was already executed")
    await audit(db, "execution_started", actor, refund_id, r["tx_id"], {"plan_kind": r["plan"]["kind"]})
    r = await _load(db, refund_id)
    # cap recalculat la executie: suma totala rambursata nu poate depasi suma platita
    prev = await refunded_totals(db, r["tx_id"], exclude_refund_id=refund_id)
    p = r["plan"]
    if p["kind"] == "purchase_refund" and prev["money_minor"] + p["money_refund_minor"] > r["paid"]["paid_minor"]:
        await _fail(db, refund_id, "failed_definitive", "cap_exceeded")
        raise RefundError("cap_exceeded", 409, "Refund would exceed the amount paid")
    if p["kind"] == "purchase_refund":
        return await _exec_purchase(db, actor, r, processors)
    return await _exec_item(db, actor, r)


async def _fail(db, refund_id: str, state: str, error: str) -> None:
    await db[COL_REQ].update_one({"refund_id": refund_id}, {"$set": {"processing_state": state, "last_error": error, "updated_at": _now()}})


async def _exec_purchase(db, actor: dict, r: dict, processors: dict) -> dict:
    p, rid = r["plan"], r["refund_id"]
    op = "rf_" + rid
    if p["astrans_to_reclaim"] > 0:
        st = await money.debit(db, r["user_id"], p["astrans_to_reclaim"], op)   # atomic, doar daca soldul acopera; idempotent prin marker
        if st == "insufficient":
            await _fail(db, rid, "failed_retryable", "balance_changed")
            await audit(db, "astrans_reclaim_failed", actor, rid, r["tx_id"], {"needed": p["astrans_to_reclaim"]})
            raise RefundError("balance_changed", 409, "The balance changed; approve again to recompute the refund")
        await audit(db, "astrans_reclaimed", actor, rid, r["tx_id"], {"amount": p["astrans_to_reclaim"], "step": st})
    prov = p.get("provider")
    ext_ref: Optional[str] = None
    if prov in processors:
        proc = processors[prov]
        res = await proc.refund(payment_intent=p.get("provider_ref") or "", amount_minor=p["money_refund_minor"], refund_id=rid)
        if not res.ok:
            await audit(db, "provider_refund_failed", actor, rid, r["tx_id"], {"provider": prov, "detail": res.detail, "transient": res.transient})
            if res.transient:   # Astransii raman retrasi; reincercarea e sigura (idempotenta)
                await _fail(db, rid, "failed_retryable", res.detail or "provider_unavailable")
                raise RefundError("provider_unavailable", 503, "The payment processor is unavailable; retry later (safe, idempotent)")
            if p["astrans_to_reclaim"] > 0:   # esec definitiv: compensam, utilizatorul nu pierde Astransi
                await money.credit(db, r["user_id"], p["astrans_to_reclaim"], op + ":comp")
                await audit(db, "astrans_reclaim_compensated", actor, rid, r["tx_id"], {"amount": p["astrans_to_reclaim"]})
            await _fail(db, rid, "failed_definitive", res.detail or "provider_rejected")
            raise RefundError("provider_rejected", 502, "The payment processor rejected the refund", detail_code=res.detail)
        ext_ref = res.provider_refund_id
        await audit(db, "provider_refund_succeeded", actor, rid, r["tx_id"], {"provider": prov, "provider_refund_id": ext_ref})
    else:   # Google Play / Apple: rambursarea reala o face magazinul; confirmarea o da adminul (confirm_external)
        await db[COL_REQ].update_one({"refund_id": rid}, {"$set": {"processing_state": "awaiting_store_refund", "updated_at": _now()}})
        await audit(db, "awaiting_store_refund", actor, rid, r["tx_id"], {"provider": prov})
        return public_request(await _load(db, rid))
    return await _finalize_purchase(db, actor, r, ext_ref)


async def confirm_external(db, actor: dict, refund_id: str, external_ref: str) -> dict:
    external_ref = (external_ref or "").strip()
    if len(external_ref) < 3:
        raise RefundError("reference_required", 422, "The store refund reference is required")
    r = await _load(db, refund_id)
    _no_self(actor, r)
    if r["status"] != "approved" or r.get("processing_state") != "awaiting_store_refund":
        raise RefundError("invalid_state", 409, "This request is not awaiting a store refund", current_status=r["status"])
    await audit(db, "external_refund_confirmed", actor, refund_id, r["tx_id"], {"reference": external_ref})
    return await _finalize_purchase(db, actor, r, external_ref)


async def _finalize_purchase(db, actor: dict, r: dict, ext_ref: Optional[str]) -> dict:
    p, rid = r["plan"], r["refund_id"]
    now = _now()
    row = {"tx_id": _tx_id("txrf_", rid), "user_id": r["user_id"], "counterparty_id": "system", "type": "purchase_refund",
           "amount": p["astrans_to_reclaim"], "fee": 0, "net": -p["astrans_to_reclaim"], "status": "completed", "timestamp": now,
           "reference": f"refund:{rid}:{r['tx_id']}", "refund_id": rid, "refunded_money_minor": p["money_refund_minor"], "currency": p["currency"],
           "original_tx_id": r["tx_id"]}
    try:
        await db.astran_ledger.insert_one(row)
    except DuplicateKeyError:
        pass
    ok = await _transition(db, rid, ("approved",), {"processing_state": "done", "refunded_at": now, "provider_refund_id": ext_ref}, "refunded", actor,
                           extra_unset={"active_key": ""})
    if not ok:
        raise RefundError("invalid_state", 409, "State changed concurrently")
    await audit(db, "refunded", actor, rid, r["tx_id"], {"money_minor": p["money_refund_minor"], "astrans": p["astrans_to_reclaim"], "ref": ext_ref})
    return public_request(await _load(db, rid))


async def _exec_item(db, actor: dict, r: dict) -> dict:
    """Refund in Astrans pentru un obiect: credit cumparator + retragere (in limita soldului) de la autor + revocare acces,
    totul prin jurnalul money.py (atomic la cadere). Autorul nu ajunge niciodata pe sold negativ; diferenta o absoarbe platforma."""
    rid = r["refund_id"]
    for attempt in range(3):
        p = r["plan"] if attempt == 0 else await compute_plan(db, r)
        buyer, price, reclaim = r["user_id"], p["buyer_credit"], p["seller_reclaim"]
        coll = "shop_purchases" if p["product_type"] == "shop_item" else "clothes_purchases"
        payer = p["seller_id"] if (reclaim > 0 and p.get("seller_id")) else buyer
        amount = reclaim if payer == p.get("seller_id") else 0
        now = _now()
        rows = [{"tx_id": _tx_id("txri_", f"{rid}:{attempt}"), "user_id": buyer, "counterparty_id": "system", "type": "refund_in", "amount": price,
                 "fee": 0, "net": price, "status": "completed", "timestamp": now, "reference": f"refund:{rid}:{r['tx_id']}", "refund_id": rid,
                 "original_tx_id": r["tx_id"]}]
        if amount > 0:
            rows.append({"tx_id": _tx_id("txro_", f"{rid}:{attempt}"), "user_id": payer, "counterparty_id": buyer, "type": "refund_out", "amount": amount,
                         "fee": 0, "net": -amount, "status": "completed", "timestamp": now, "reference": f"refund:{rid}:{r['tx_id']}", "refund_id": rid})
        op_id = f"rf_{rid}_{attempt}"
        pfilter = {"user_id": buyer, "item_id": p["item_id"]}
        try:
            await money.begin(db, op_id=op_id, kind="item_refund", payer_id=payer, amount=amount, credits=[{"user_id": buyer, "amount": price}],
                              inserts=[{"collection": "astran_ledger", "doc": d} for d in rows],
                              updates=[{"collection": coll, "filter": pfilter, "update": {"$set": {"revoked": True, "revoked_at": now, "refund_id": rid}}},
                                       {"collection": COL_REQ, "filter": {"refund_id": rid, "status": "approved"},
                                        "update": {"$set": {"status": "refunded", "processing_state": "done", "refunded_at": now, "plan": {**p, "seller_reclaim": amount, "platform_absorbed": price - amount}},
                                                   "$unset": {"active_key": ""}, "$push": {"history": {"status": "refunded", "at": now, "by": actor["user_id"]}}}}])
        except DuplicateKeyError:
            pass   # reluare dupa cadere: op_id exista deja, il ducem la capat mai jos
        try:
            outcome = await money.run(db, op_id)
        except money.MoneyOpFailed:
            outcome = "insufficient"
        if outcome == "done":
            await audit(db, "refunded", actor, rid, r["tx_id"], {"buyer_credit": price, "seller_reclaim": amount, "platform_absorbed": price - amount})
            return public_request(await _load(db, rid))
        await audit(db, "seller_reclaim_retry", actor, rid, r["tx_id"], {"attempt": attempt})
    await _fail(db, rid, "failed_retryable", "balance_changed")
    raise RefundError("balance_changed", 409, "Balances changed during execution; retry")
