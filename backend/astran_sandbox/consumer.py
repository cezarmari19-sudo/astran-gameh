"""Drepturile consumatorilor UE pentru achizitii digitale (Directiva 2011/83/UE, OUG 34/2014, Directiva 2019/770).

Principii (toate pe SERVER; clientul nu este dovada):
  * pretul, moneda, produsul si textele confirmarilor vin din server (`create_checkout`), niciodata din client;
  * pentru produsele cu plata livrate imediat exista DOUA confirmari distincte (A: cerere expresa de furnizare
    imediata; B: recunoasterea pierderii dreptului de retragere), inregistrate separat, adauga-doar, in
    `consumer_consents`, cu textul exact, amprenta lui, versiunea, data/ora serverului si utilizatorul;
  * o achizitie se leaga de consimtamant prin `consumer_orders` (un rand per tranzactie, cu id-urile confirmarilor);
  * NU se presupune ca orice achizitie digitala pierde automat dreptul de retragere: `withdrawal_right.status` e "waived"
    doar daca ambele confirmari valide exista; altfel "preserved" (vezi `build_order`).
Limita cunoscuta: la plata prin Google Play / Apple plata se face in magazin INAINTE de apelul catre server, deci
serverul nu poate bloca plata; poate doar sa nu inregistreze renuntarea (dreptul de retragere ramane).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import legal_docs as LD

log = logging.getLogger("astran.consumer")

COL_CHECKOUTS = "consumer_checkouts"
COL_CONSENTS = "consumer_consents"
COL_ORDERS = "consumer_orders"
TEXTS_VERSION = "1.0"
CHECKOUT_TTL = timedelta(hours=24)
WITHDRAWAL_DAYS = 14
KIND_A = "immediate_performance_request"
KIND_B = "withdrawal_loss_acknowledgement"
REFUND_POLICY = "refund_policy"

# Categorii de produse: NU toate pierd automat dreptul de retragere. `waiver_applicable` + livrare "immediate"
# sunt conditiile pentru a cere confirmarile A/B. Orice produs cu livrare "deferred" pastreaza dreptul de retragere.
CATEGORIES: dict[str, dict] = {
    "astran_package": {"delivery": "immediate", "waiver_applicable": True, "unit": "money"},
    "shop_item": {"delivery": "immediate", "waiver_applicable": True, "unit": "astrans"},
    "clothes_item": {"delivery": "immediate", "waiver_applicable": True, "unit": "astrans"},
}
# Furnizori la care plata se face INAINTE de apelul catre server (nu o putem bloca).
SOFT_PROVIDERS = ("google_play", "apple")


class ConsumerError(Exception):
    def __init__(self, code: str, status: int = 400, message: Optional[str] = None, **extra: Any):
        self.code, self.status, self.extra = code, status, extra
        self.message = message or code
        super().__init__(self.message)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: Any) -> Optional[datetime]:
    if not isinstance(d, datetime):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _lang(lang: Optional[str]) -> str:
    return LD.get_registry().normalize_lang(lang)


# ----------------------------------------------------------------------------- configurare
_notifier: Optional[Callable[[str, str, str], Awaitable[bool]]] = None
_docs: Optional[LD.LegalRegistry] = None


def configure(notifier: Optional[Callable[[str, str, str], Awaitable[bool]]] = None) -> None:
    global _notifier
    _notifier = notifier


def docs() -> LD.LegalRegistry:
    """Registrul documentelor de consum (legal/consumer/manifest.json, amprente SHA-256 verificate)."""
    global _docs
    if _docs is None:
        base = Path(os.environ.get("ASTRAN_CONSUMER_LEGAL_DIR") or (LD.DEFAULT_LEGAL_DIR / "consumer"))
        _docs = LD.LegalRegistry(base)
    return _docs


def reset_for_tests() -> None:
    global _docs, _notifier
    _docs, _notifier = None, None


def trader_info() -> dict:
    """Identitatea comerciantului vine DOAR din configurare (nu inventam date de firma)."""
    g = lambda k: (os.environ.get(k) or "").strip() or None  # noqa: E731
    info = {"name": g("CONSUMER_TRADER_NAME"), "address": g("CONSUMER_TRADER_ADDRESS"), "email": g("CONSUMER_TRADER_EMAIL"),
            "phone": g("CONSUMER_TRADER_PHONE"), "registration_no": g("CONSUMER_TRADER_REG_NO"), "vat_id": g("CONSUMER_TRADER_VAT_ID")}
    info["configured"] = bool(info["name"] and info["address"] and info["email"])
    return info


def _require_trader() -> dict:
    t = trader_info()
    if not t["configured"] and os.environ.get("CONSUMER_ENFORCE_TRADER_INFO", "1") != "0":
        raise ConsumerError("trader_info_missing", 503, "Merchant identity is not configured; purchases are disabled.")
    return t


async def ensure_indexes(db) -> None:
    await db[COL_CHECKOUTS].create_index("checkout_id", unique=True)
    await db[COL_CHECKOUTS].create_index([("user_id", 1), ("created_at", -1)])
    await db[COL_CONSENTS].create_index("consent_id", unique=True)
    await db[COL_CONSENTS].create_index([("checkout_id", 1), ("kind", 1)], unique=True)
    await db[COL_CONSENTS].create_index([("user_id", 1), ("created_at", -1)])
    await db[COL_ORDERS].create_index("order_id", unique=True)
    await db[COL_ORDERS].create_index("tx_id", unique=True, sparse=True)
    await db[COL_ORDERS].create_index("checkout_id", unique=True, sparse=True)
    await db[COL_ORDERS].create_index([("user_id", 1), ("created_at", -1)])


# ----------------------------------------------------------------------------- produse
def minor(price: Any) -> int:
    return int(round(float(price) * 100))


async def resolve_product(db, product_type: str, product_id: str) -> dict:
    """Produsul + pretul AUTORITAR din baza de date. Pretul nu vine niciodata din client."""
    if product_type not in CATEGORIES:
        raise ConsumerError("unknown_product_type", 422, "Unknown product type")
    rules = CATEGORIES[product_type]
    if product_type == "astran_package":
        cfg = await db.platform_config.find_one({"key": "astran_packages"}, {"_id": 0}) or {}
        pkg = next((p for p in cfg.get("packages", []) if p.get("package_id") == product_id), None)
        if not pkg:
            raise ConsumerError("product_not_found", 404, "Product not found")
        return {"product_type": product_type, "product_id": product_id, "unit": "money",
                "name": f"{pkg['astrans']} Astrans ({pkg.get('label', product_id)})",
                "description": f"Pachet de monedă virtuală Astrans: {pkg['astrans']} Astrans adăugați în portofel.",
                "description_en": f"Virtual currency package: {pkg['astrans']} Astrans added to your wallet.",
                "price_amount": minor(pkg["price"]), "price_display": f"{float(pkg['price']):.2f}",
                "currency": cfg.get("currency", "RON"), "delivery": rules["delivery"], "astrans": int(pkg["astrans"]),
                "seller": None}
    coll = db.shop_items if product_type == "shop_item" else db.clothes_items
    it = await coll.find_one({"item_id": product_id}, {"_id": 0})
    if not it or it.get("delisted"):
        raise ConsumerError("product_not_found", 404, "Product not found")
    kind = "obiect de joc" if product_type == "shop_item" else "haină / accesoriu avatar"
    kind_en = "game item" if product_type == "shop_item" else "avatar clothing / accessory"
    return {"product_type": product_type, "product_id": product_id, "unit": "astrans", "name": it["name"],
            "description": (it.get("description") or "").strip() or f"{kind} creat de {it.get('owner_username', '?')}",
            "description_en": (it.get("description") or "").strip() or f"{kind_en} by {it.get('owner_username', '?')}",
            "price_amount": int(it["price"]), "price_display": str(int(it["price"])), "currency": "ASTRANS",
            "delivery": rules["delivery"], "astrans": None,
            "seller": {"user_id": it["owner_id"], "username": it.get("owner_username")}}


def needs_waiver(product: dict) -> bool:
    rules = CATEGORIES[product["product_type"]]
    return bool(rules["waiver_applicable"] and product["delivery"] == "immediate" and product["price_amount"] > 0)


def consent_texts(product: dict, lang: str) -> dict:
    """Textele EXACTE ale confirmarilor A si B (versionate). Includ produsul si pretul."""
    price = f"{product['price_display']} {product['currency']}"
    if lang == "ro":
        a = (f"Solicit în mod expres ca furnizarea conținutului digital „{product['name']}” (preț: {price}) să înceapă "
             "imediat, înainte de expirarea perioadei de retragere de 14 zile.")
        b = ("Înțeleg și confirm că, odată cu începerea furnizării conținutului digital, îmi pierd dreptul de retragere, "
             "în condițiile art. 16 lit. (m) din Directiva 2011/83/UE și OUG 34/2014.")
    else:
        a = (f"I expressly request that the supply of the digital content “{product['name']}” (price: {price}) begins "
             "immediately, before the 14-day withdrawal period expires.")
        b = ("I understand and confirm that I lose my right of withdrawal once the supply of the digital content has "
             "begun, under Article 16(m) of Directive 2011/83/EU and Romanian GEO 34/2014.")
    return {k: {"text": t, "sha256": _sha({"v": TEXTS_VERSION, "kind": kind, "lang": lang, "text": t})}
            for k, kind, t in (("A", KIND_A, a), ("B", KIND_B, b))}


# ----------------------------------------------------------------------------- checkout (informatii precontractuale)
def public_checkout(c: dict, lang: Optional[str] = None) -> dict:
    p = c["product"]
    lg = _lang(lang or c.get("language"))
    reg = docs()
    return {
        "checkout_id": c["checkout_id"], "status": c["status"], "expires_at": c["expires_at"],
        "product": {"type": p["product_type"], "id": p["product_id"], "name": p["name"],
                    "description": p["description_en"] if lg == "en" else p["description"],
                    "price": p["price_display"], "currency": p["currency"], "unit": p["unit"],
                    "additional_fees": "none", "delivery": p["delivery"], "seller": p.get("seller")},
        "delivery_note": c["delivery_note"][lg],
        "needs_consent": c["needs_consent"],
        "consent_texts": ({k: {"text": v["text"], "sha256": v["sha256"]} for k, v in c["consent_texts"].items()}
                          if c["needs_consent"] else None),
        "withdrawal": {"days": WITHDRAWAL_DAYS, "info": withdrawal_summary(c["needs_consent"], lg)},
        "terms": {"terms_of_service_version": c["terms_version"], "refund_policy_version": c["refund_policy_version"]},
        "trader": c["trader"],
    }


def withdrawal_summary(waiver_required: bool, lang: str) -> str:
    if lang == "ro":
        return ("Ai dreptul să te retragi din contract în 14 zile, fără motiv. Pentru conținut digital livrat imediat îl pierzi "
                "doar dacă ceri expres începerea furnizării și confirmi pierderea dreptului, mai jos. Pentru conținut neconform ai "
                "drepturi legale separate.") if waiver_required else (
                "Ai dreptul să te retragi din contract în 14 zile, fără motiv. Conținutul nu se livrează imediat, deci nu pierzi acest drept.")
    return ("You may withdraw from the contract within 14 days without giving a reason. For immediately supplied digital content "
            "you lose this right only if you expressly request immediate supply and acknowledge the loss below. You keep separate "
            "statutory rights if the content is not in conformity.") if waiver_required else (
            "You may withdraw from the contract within 14 days without giving a reason. The content is not supplied immediately, so you keep this right.")


async def create_checkout(db, user: dict, product_type: str, product_id: str, lang: Optional[str]) -> dict:
    lg = _lang(lang or user.get("language"))
    trader = _require_trader()
    product = await resolve_product(db, product_type, product_id)
    if product["price_amount"] <= 0:
        raise ConsumerError("free_product", 400, "Free products do not need a checkout")
    if product.get("seller") and product["seller"]["user_id"] == user["user_id"]:
        raise ConsumerError("own_product", 400, "You cannot buy your own item")
    consent = needs_waiver(product)
    now = _now()
    instant_ro = ("Livrare instantă: Astranii sunt adăugați în portofel imediat după confirmarea plății." if product_type == "astran_package"
                  else "Livrare instantă: accesul la obiect este activat imediat după achiziție.")
    instant_en = ("Instant delivery: the Astrans are added to your wallet right after the payment is confirmed." if product_type == "astran_package"
                  else "Instant delivery: access to the item is activated right after purchase.")
    c = {"checkout_id": "chk_" + uuid.uuid4().hex, "user_id": user["user_id"], "product": product, "language": lg,
         "needs_consent": consent, "consent_texts": consent_texts(product, lg) if consent else None,
         "delivery_note": {"ro": instant_ro, "en": instant_en}, "texts_version": TEXTS_VERSION,
         "terms_version": LD.get_registry().current_version("terms_of_service"),
         "refund_policy_version": docs().current_version(REFUND_POLICY),
         "trader": {k: v for k, v in trader.items() if k != "configured"}, "status": "open",
         "created_at": now, "expires_at": now + CHECKOUT_TTL}
    await db[COL_CHECKOUTS].insert_one(dict(c))
    return public_checkout(c, lg)


async def _own_checkout(db, user_id: str, checkout_id: str) -> dict:
    c = await db[COL_CHECKOUTS].find_one({"checkout_id": checkout_id, "user_id": user_id}, {"_id": 0})
    if not c:
        raise ConsumerError("checkout_not_found", 404, "Checkout not found")   # si pentru checkout-ul altuia: nu confirmam existenta
    return c


async def get_checkout(db, user: dict, checkout_id: str) -> dict:
    return public_checkout(await _own_checkout(db, user["user_id"], checkout_id))


async def confirm_checkout(db, user: dict, checkout_id: str, *, accept_immediate_performance: Any,
                           acknowledge_loss_of_withdrawal: Any, presented_sha256: Any) -> dict:
    """Inregistreaza SEPARAT fiecare confirmare. Valorile trebuie sa fie exact `True` (nu "true", nu 1)."""
    c = await _own_checkout(db, user["user_id"], checkout_id)
    if (_aware(c["expires_at"]) or _now()) < _now():
        raise ConsumerError("checkout_expired", 410, "Checkout expired; start again")
    if not c["needs_consent"]:
        return public_checkout(c)
    if accept_immediate_performance is not True or acknowledge_loss_of_withdrawal is not True:
        raise ConsumerError("confirmations_required", 422, "Both confirmations are required")
    sh = presented_sha256 if isinstance(presented_sha256, dict) else {}
    for k in ("A", "B"):
        if sh.get(k) != c["consent_texts"][k]["sha256"]:
            raise ConsumerError("texts_changed", 409, "The confirmation texts changed; reload the checkout")
    now = _now()
    for key, kind in (("A", KIND_A), ("B", KIND_B)):
        t = c["consent_texts"][key]
        doc = {"consent_id": "cns_" + uuid.uuid4().hex, "user_id": user["user_id"], "checkout_id": checkout_id, "kind": kind,
               "accepted": True, "text": t["text"], "text_sha256": t["sha256"], "texts_version": c["texts_version"],
               "language": c["language"], "product_type": c["product"]["product_type"], "product_id": c["product"]["product_id"],
               "product_name": c["product"]["name"], "price_amount": c["product"]["price_amount"], "currency": c["product"]["currency"],
               "terms_version": c["terms_version"], "refund_policy_version": c["refund_policy_version"],
               "created_at": now,   # generat de server
               "source": "checkout"}
        try:
            await db[COL_CONSENTS].insert_one(doc)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ != "DuplicateKeyError":
                raise   # deja inregistrata (dublu-click / retry): idempotent
    await db[COL_CHECKOUTS].update_one({"checkout_id": checkout_id, "status": "open"}, {"$set": {"status": "consented", "consented_at": now}})
    return public_checkout(await _own_checkout(db, user["user_id"], checkout_id))


# ----------------------------------------------------------------------------- verificare la achizitie
class PurchaseContext:
    def __init__(self, checkout: Optional[dict], consents: list, waiver_valid: bool, product: dict):
        self.checkout, self.consents, self.waiver_valid, self.product = checkout, consents, waiver_valid, product


async def _verified_consents(db, c: dict) -> list:
    """Re-citeste din DB ambele confirmari si le compara cu checkout-ul (nu ne bazam pe campul `status`)."""
    rows = await db[COL_CONSENTS].find({"checkout_id": c["checkout_id"], "user_id": c["user_id"]}, {"_id": 0}).to_list(10)
    by = {r["kind"]: r for r in rows}
    out = []
    for key, kind in (("A", KIND_A), ("B", KIND_B)):
        r = by.get(kind)
        if not r or r.get("accepted") is not True or r.get("text_sha256") != c["consent_texts"][key]["sha256"]:
            return []
        out.append(r)
    return out


async def _find_checkout(db, user_id: str, product_type: str, product_id: str, checkout_id: Optional[str]) -> Optional[dict]:
    now = _now()
    if checkout_id:
        c = await db[COL_CHECKOUTS].find_one({"checkout_id": checkout_id, "user_id": user_id}, {"_id": 0})
        cands = [c] if c else []
    else:   # reluare dupa restart (ex. plata din magazin confirmata, creditare esuata): cel mai recent checkout confirmat
        cands = await db[COL_CHECKOUTS].find({"user_id": user_id, "product.product_type": product_type, "product.product_id": product_id,
                                              "status": "consented"}, {"_id": 0}).sort("created_at", -1).limit(5).to_list(5)
    for c in cands:
        if c["product"]["product_type"] != product_type or c["product"]["product_id"] != product_id:
            continue
        if (_aware(c["expires_at"]) or now) < now:
            continue
        if await db[COL_ORDERS].find_one({"checkout_id": c["checkout_id"]}, {"_id": 1}):
            continue   # deja folosit pentru o alta comanda
        return c
    return None


async def require_checkout(db, user: dict, product_type: str, product_id: str, checkout_id: Optional[str] = None,
                           *, hard: bool = True, binding_ok: Optional[Callable[[dict], bool]] = None) -> PurchaseContext:
    """Poarta server-side a achizitiei. hard=True: fara confirmari valide -> exceptie (achizitia NU se face).
    hard=False (plata deja facuta in magazin): intoarce context cu waiver_valid=False -> dreptul de retragere se pastreaza."""
    _require_trader()
    product = await resolve_product(db, product_type, product_id)
    if not needs_waiver(product):
        return PurchaseContext(None, [], False, product)
    c = await _find_checkout(db, user["user_id"], product_type, product_id, checkout_id)
    consents = await _verified_consents(db, c) if c and c["status"] == "consented" else []
    if c and consents:
        if c["product"]["price_amount"] != product["price_amount"] or c["product"]["currency"] != product["currency"]:
            if hard:
                raise ConsumerError("price_changed", 409, "The price changed; review the purchase again")
            consents = []
        elif binding_ok is not None and not binding_ok(c):
            consents = []
    if not consents:
        if hard:
            raise ConsumerError("consumer_consent_required", 428,
                                "Both purchase confirmations are required before buying", product_type=product_type)
        return PurchaseContext(None, [], False, product)
    return PurchaseContext(c, consents, True, product)


# ----------------------------------------------------------------------------- comanda (confirmare)
def build_order(ctx: PurchaseContext, user: dict, tx_id: str, provider: str, extra: Optional[dict] = None) -> tuple[dict, Optional[dict]]:
    """(document comanda, update de checkout) pentru jurnalul money.begin (inserts/updates): se scriu atomic cu ledger-ul."""
    now = _now()
    p = ctx.product
    c = ctx.checkout
    trader = {k: v for k, v in trader_info().items() if k != "configured"}
    if ctx.waiver_valid:
        wr = {"status": "waived", "basis": "art_16_m", "consent_ids": [r["consent_id"] for r in ctx.consents]}
    else:
        wr = {"status": "preserved", "basis": ("no_waiver_required" if not needs_waiver(p) else "no_valid_confirmations"), "consent_ids": []}
    order = {"order_id": "ord_" + hashlib.sha256(tx_id.encode()).hexdigest()[:24], "tx_id": tx_id, "user_id": user["user_id"],
             "checkout_id": c["checkout_id"] if c else None, "product_type": p["product_type"], "product_id": p["product_id"],
             "product_name": p["name"], "price_amount": p["price_amount"], "price_display": p["price_display"], "currency": p["currency"],
             "unit": p["unit"], "provider": provider, "delivery": p["delivery"], "delivered_at": now,
             "withdrawal_right": wr,
             "consent_digest": _sha([[r["consent_id"], r["text_sha256"], r["created_at"].isoformat()] for r in ctx.consents]) if ctx.consents else None,
             "terms_version": c["terms_version"] if c else LD.get_registry().current_version("terms_of_service"),
             "refund_policy_version": c["refund_policy_version"] if c else docs().current_version(REFUND_POLICY),
             "texts_version": TEXTS_VERSION, "trader": trader, "created_at": now,
             "email": {"status": "pending"}, **(extra or {})}
    upd = None
    if c:
        upd = {"collection": COL_CHECKOUTS, "filter": {"checkout_id": c["checkout_id"]},
               "update": {"$set": {"status": "used", "tx_id": tx_id, "used_at": now}}}
    return order, upd


def public_order(o: Optional[dict]) -> Optional[dict]:
    if not o:
        return None
    keys = ("order_id", "tx_id", "product_type", "product_id", "product_name", "price_display", "currency", "unit", "provider", "delivery",
            "delivered_at", "withdrawal_right", "terms_version", "refund_policy_version", "trader", "created_at", "email")
    out = {k: o.get(k) for k in keys}
    out["links"] = {"terms_of_service": "/api/legal/documents/terms_of_service", "refund_policy": "/api/consumer/documents/refund_policy"}
    return out


async def get_order_for_tx(db, tx_id: str) -> Optional[dict]:
    return await db[COL_ORDERS].find_one({"tx_id": tx_id}, {"_id": 0})


async def consents_for_order(db, order: dict) -> list:
    if not order.get("checkout_id"):
        return []
    return await db[COL_CONSENTS].find({"checkout_id": order["checkout_id"]}, {"_id": 0}).sort("created_at", 1).to_list(10)


def withdrawal_status(order: Optional[dict], tx_ts: Any, now: Optional[datetime] = None) -> dict:
    """Dreptul de retragere pentru o tranzactie: lipsa comenzii (tranzactie veche) => nicio renuntare inregistrata => se pastreaza."""
    now = now or _now()
    start = _aware((order or {}).get("delivered_at")) or _aware(tx_ts) or now
    deadline = start + timedelta(days=WITHDRAWAL_DAYS)
    waived = bool(order and order.get("withdrawal_right", {}).get("status") == "waived")
    return {"waiver_valid": waived, "deadline": deadline, "within_period": now <= deadline,
            "withdrawal_right_applies": (not waived) and now <= deadline, "legacy_no_order": order is None}


# ----------------------------------------------------------------------------- email (suport durabil)
def confirmation_email(order: dict, consents: list, lang: str) -> tuple[str, str]:
    t = order["trader"]
    ro = lang == "ro"
    lines = [("Confirmarea comenzii" if ro else "Order confirmation") + f" {order['order_id']}", "",
             f"{'Tranzacție' if ro else 'Transaction'}: {order['tx_id']}",
             f"{'Produs' if ro else 'Product'}: {order['product_name']}",
             f"{'Preț' if ro else 'Price'}: {order['price_display']} {order['currency']}",
             f"{'Data și ora (UTC)' if ro else 'Date and time (UTC)'}: {_aware(order['created_at']).isoformat()}",
             f"{'Livrare' if ro else 'Delivery'}: {'imediată' if ro else 'immediate'}", ""]
    if consents:
        lines.append("Confirmările tale înregistrate:" if ro else "Your recorded confirmations:")
        for r in consents:
            lines += [f"- [{_aware(r['created_at']).isoformat()}] {r['text']}"]
        lines.append("")
    else:
        lines += [("Dreptul tău de retragere de 14 zile se păstrează pentru această achiziție." if ro else
                   "Your 14-day right of withdrawal is preserved for this purchase."), ""]
    lines += [f"{'Versiune Termeni' if ro else 'Terms version'}: {order['terms_version']}",
              f"{'Versiune Politică de rambursare' if ro else 'Refund policy version'}: {order['refund_policy_version']}",
              ("Politica de rambursare este disponibilă în aplicație (Portofel / Magazin / Setări)." if ro else
               "The refund policy is available in the app (Wallet / Shop / Settings)."), "",
              f"{'Comerciant' if ro else 'Trader'}: {t.get('name')}", f"{'Adresă' if ro else 'Address'}: {t.get('address')}",
              f"Email: {t.get('email')}"]
    if t.get("phone"):
        lines.append(f"{'Telefon' if ro else 'Phone'}: {t['phone']}")
    return (("Confirmare comandă Astran Game " if ro else "Astran Game order confirmation ") + order["order_id"]), "\n".join(lines)


async def send_confirmation(db, order: dict, user: dict, lang: Optional[str] = None) -> str:
    """Trimite confirmarea pe email (best-effort) si inregistreaza rezultatul. Nu blocheaza si nu anuleaza achizitia."""
    status = "not_configured"
    try:
        if _notifier is not None and user.get("email"):
            subject, body = confirmation_email(order, await consents_for_order(db, order), _lang(lang or user.get("language")))
            status = "sent" if await _notifier(user["email"], subject, body) else "failed"
        elif _notifier is not None:
            status = "no_email"
    except Exception:  # noqa: BLE001
        log.exception("order confirmation email failed")
        status = "failed"
    await db[COL_ORDERS].update_one({"order_id": order["order_id"]}, {"$set": {"email.status": status, "email.updated_at": _now()}})
    return status


async def list_orders(db, user_id: str, limit: int = 100) -> list:
    rows = await db[COL_ORDERS].find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    return [public_order(r) for r in rows]


async def get_order(db, user_id: str, order_id: str) -> dict:
    o = await db[COL_ORDERS].find_one({"order_id": order_id, "user_id": user_id}, {"_id": 0})
    if not o:
        raise ConsumerError("order_not_found", 404, "Order not found")
    out = public_order(o)
    out["consents"] = [{"kind": r["kind"], "text": r["text"], "text_sha256": r["text_sha256"], "created_at": r["created_at"],
                        "texts_version": r["texts_version"]} for r in await consents_for_order(db, o)]
    return out