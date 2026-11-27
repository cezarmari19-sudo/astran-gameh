"""Statutul vanzatorilor din Shop / Clothes Shop si informatiile comerciantilor (DSA Art. 30-32 + informarea consumatorilor).

Nu se presupune ca un creator este comerciant. Fiecare vanzator DECLARA statutul (`non_trader` | `trader`) printr-un chestionar
cu criteriile din Directiva 2005/29/CE / 2011/83/UE (activitate in scopuri legate de comert, afacere, mestesug sau profesie);
platforma poate sa il REVIZUIASCA (semnale configurabile + reclasificare de catre personal), dar nu il deduce singura.

  * non_trader: pe pagina produsului si la checkout se afiseaza doar faptul ca vanzatorul declara ca NU este comerciant si ca
    legislatia UE de protectie a consumatorilor nu se aplica contractului cu el (obligatie de informare a pietei online).
    Nu se afiseaza nicio data personala suplimentara.
  * trader: trebuie completate nume, adresa, telefon, e-mail de contact, date de inregistrare (registrul comertului / echivalent,
    obligatoriu pentru societati) si declaratia de conformitate cu dreptul UE (Art. 30(1)(e)). Starea de verificare:
    unverified -> in_review -> verified (sau rejected). Pana la completare vanzarile comerciale (pret > 0, public) sunt blocate.
  * Datele comerciantului sunt vizibile public DOAR pentru comercianti si doar campurile cerute de Art. 30(7); restul (cine a
    verificat, note interne, TVA) vede doar personalul autorizat. Nu se stocheaza copii ale documentelor de identitate:
    verificarea inregistreaza metoda, data si rezultatul.
Colectii: `merchant_profiles`, `merchant_audit` (adauga-doar), `app_config` (`merchant_policy`).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

C_PROFILES = "merchant_profiles"
C_AUDIT = "merchant_audit"
C_CONFIG = "app_config"
POLICY_KEY = "merchant_policy"
STATUSES = ("undeclared", "non_trader", "trader")
VERIFICATION_STATES = ("unverified", "in_review", "verified", "rejected")
_STATE_RANK = {"unverified": 0, "in_review": 1, "verified": 2}
TRADER_TYPES = ("natural_person", "company")
STATEMENT_VERSION = "1.0"
ASSESSMENT_QUESTIONS = ("regular_sales", "for_profit_or_business", "professional_or_company", "hired_staff_or_resells_goods")

DEFAULT_POLICY = {
    "enforce_seller_status": True,        # fara declaratie nu se pot publica/vinde articole cu pret
    "min_verification_to_sell": "in_review",   # unverified | in_review | verified (pentru comercianti)
    "grace_until": "",                    # ISO date: pana atunci vanzatorii existenti fara declaratie pot continua
    "signal_sales_count_12m": 50,         # semnal pentru revizuire: nr. vanzari in 12 luni (0 = dezactivat)
    "signal_revenue_astrans_12m": 0,      # semnal: venit in Astrans in 12 luni (0 = dezactivat)
}
_POLICY_TYPES = {"enforce_seller_status": bool, "min_verification_to_sell": str, "grace_until": str,
                 "signal_sales_count_12m": int, "signal_revenue_astrans_12m": int}


class MerchantError(Exception):
    def __init__(self, status: int, code: str, message: str = "", **extra: Any):
        super().__init__(message or code)
        self.status, self.code, self.message, self.extra = status, code, message or code, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


# ----------------------------------------------------------------------------------------------- autorizare / config
def reviewer_role(user: Optional[dict]) -> Optional[str]:
    """Verificarea comerciantilor: platform admin sau responsabil juridic. Moderatorii de continut nu vad datele comerciantilor."""
    if not user:
        return None
    if user.get("is_platform_admin"):
        return "admin"
    if user.get("is_legal_officer"):
        return "legal"
    return None


async def get_policy(db) -> dict:
    doc = await db[C_CONFIG].find_one({"key": POLICY_KEY}, {"_id": 0})
    pol = dict(DEFAULT_POLICY)
    for k, v in ((doc or {}).get("value") or {}).items():
        if k in _POLICY_TYPES and type(v) is _POLICY_TYPES[k]:
            pol[k] = v
    return pol


async def set_policy(db, actor: dict, changes: dict) -> dict:
    if (actor or {}).get("is_platform_admin") is not True:
        raise MerchantError(403, "admin_required", "Only a platform admin can change the merchant policy")
    cur = await get_policy(db)
    for k, v in (changes or {}).items():
        if k not in _POLICY_TYPES or type(v) is not _POLICY_TYPES[k]:
            raise MerchantError(422, "invalid_setting", f"Invalid setting: {k}")
        if k == "min_verification_to_sell" and v not in _STATE_RANK:
            raise MerchantError(422, "invalid_setting", "min_verification_to_sell must be unverified, in_review or verified")
        if k == "grace_until" and v:
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise MerchantError(422, "invalid_setting", "grace_until must be an ISO date")
        if isinstance(v, int) and not isinstance(v, bool) and not (0 <= v <= 10_000_000):
            raise MerchantError(422, "invalid_setting", f"{k}: out of range")
        cur[k] = v
    await db[C_CONFIG].update_one({"key": POLICY_KEY}, {"$set": {"key": POLICY_KEY, "value": cur, "updated_at": now_utc(), "updated_by": actor["user_id"]}}, upsert=True)
    await audit(db, "policy", "policy_changed", actor, {"keys": sorted(changes)})
    return cur


async def audit(db, subject_user_id: str, action: str, actor: Optional[dict], details: Optional[dict] = None) -> None:
    await db[C_AUDIT].insert_one({"event_id": "ma_" + uuid.uuid4().hex[:16], "user_id": subject_user_id, "action": action, "ts": now_utc(),
                                  "actor_id": (actor or {}).get("user_id"), "actor_role": reviewer_role(actor) or ("self" if actor and actor.get("user_id") == subject_user_id else None),
                                  "details": details or {}})


async def ensure_indexes(db) -> None:
    await db[C_PROFILES].create_index("user_id", unique=True)
    await db[C_PROFILES].create_index([("verification.state", 1), ("updated_at", -1)])
    await db[C_AUDIT].create_index([("user_id", 1), ("ts", -1)])


# ----------------------------------------------------------------------------------------------- validare
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ().-]{5,24}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _s(v: Any, field: str, lo: int, hi: int, *, required: bool = True) -> Optional[str]:
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise MerchantError(422, "missing_field", f"{field} is required", field=field)
        return None
    if not isinstance(v, str):
        raise MerchantError(422, "invalid_field", f"{field} must be text", field=field)
    v = " ".join(v.split())
    if not (lo <= len(v) <= hi):
        raise MerchantError(422, "invalid_field", f"{field} must have between {lo} and {hi} characters", field=field)
    if any(ord(c) < 32 for c in v):
        raise MerchantError(422, "invalid_field", f"{field} contains invalid characters", field=field)
    return v


def validate_trader_details(raw: Any) -> dict:
    """Validare PE BACKEND a datelor comerciantului (Art. 30(1)). Intoarce documentul curatat sau ridica MerchantError."""
    if not isinstance(raw, dict):
        raise MerchantError(422, "invalid_field", "trader details are required")
    ttype = raw.get("trader_type")
    if ttype not in TRADER_TYPES:
        raise MerchantError(422, "invalid_field", "trader_type must be natural_person or company", field="trader_type")
    addr = raw.get("address")
    if not isinstance(addr, dict):
        raise MerchantError(422, "missing_field", "address is required", field="address")
    country = _s(addr.get("country"), "address.country", 2, 2)
    if not _COUNTRY_RE.match(country or ""):
        raise MerchantError(422, "invalid_field", "address.country must be an ISO 3166-1 alpha-2 code (e.g. RO)", field="address.country")
    phone = _s(raw.get("phone"), "phone", 6, 25)
    if not _PHONE_RE.match(phone or ""):
        raise MerchantError(422, "invalid_field", "phone is not valid", field="phone")
    email = (_s(raw.get("contact_email"), "contact_email", 5, 254) or "").lower()
    if not _EMAIL_RE.match(email):
        raise MerchantError(422, "invalid_field", "contact_email is not valid", field="contact_email")
    reg = raw.get("registry") if isinstance(raw.get("registry"), dict) else {}
    is_company = ttype == "company"
    registry = {"register_name": _s(reg.get("register_name"), "registry.register_name", 2, 120, required=is_company),
                "number": _s(reg.get("number"), "registry.number", 2, 60, required=is_company),
                "not_applicable_reason": _s(reg.get("not_applicable_reason"), "registry.not_applicable_reason", 10, 300, required=False)}
    if not is_company:
        if bool(registry["register_name"]) != bool(registry["number"]):
            raise MerchantError(422, "invalid_field", "Provide both the register name and the number", field="registry")
        if not registry["number"] and not registry["not_applicable_reason"]:
            raise MerchantError(422, "missing_field", "Provide the registration data or explain why it does not apply", field="registry")
    stmt = raw.get("eu_compliance_statement")
    if stmt is not True:
        raise MerchantError(422, "statement_required", "You must confirm the EU compliance statement (Art. 30(1)(e))", field="eu_compliance_statement")
    return {"trader_type": ttype, "name": _s(raw.get("name"), "name", 2, 160),
            "address": {"line1": _s(addr.get("line1"), "address.line1", 3, 160), "line2": _s(addr.get("line2"), "address.line2", 1, 160, required=False),
                        "city": _s(addr.get("city"), "address.city", 2, 80), "postal_code": _s(addr.get("postal_code"), "address.postal_code", 2, 20),
                        "country": country},
            "phone": phone, "contact_email": email, "registry": registry,
            "vat_id": _s(raw.get("vat_id"), "vat_id", 4, 24, required=False),
            "eu_compliance_statement": {"accepted": True, "version": STATEMENT_VERSION}}


def validate_assessment(raw: Any) -> dict:
    if not isinstance(raw, dict) or set(raw) - set(ASSESSMENT_QUESTIONS):
        raise MerchantError(422, "invalid_field", "assessment answers are invalid")
    out = {}
    for q in ASSESSMENT_QUESTIONS:
        if not isinstance(raw.get(q), bool):
            raise MerchantError(422, "missing_field", f"assessment.{q} is required", field=f"assessment.{q}")
        out[q] = raw[q]
    return out


# ----------------------------------------------------------------------------------------------- profil
async def get_profile(db, user_id: str) -> dict:
    return (await db[C_PROFILES].find_one({"user_id": user_id}, {"_id": 0})) or {"user_id": user_id, "seller_status": "undeclared"}


def _complete(profile: dict) -> bool:
    t = profile.get("trader")
    try:
        return bool(t) and validate_trader_details({**t, "contact_email": t["contact_email"], "eu_compliance_statement": bool((t.get("eu_compliance_statement") or {}).get("accepted"))}) is not None
    except MerchantError:
        return False


async def declare_status(db, user: dict, body: dict) -> dict:
    """Declaratia vanzatorului. `trader` cere datele complete si trece in `in_review`; `non_trader` cere chestionarul.
    Un cont reclasificat de personal drept comerciant (`forced_trader`) NU se poate auto-declara non_trader."""
    if user.get("deletion_state"):
        raise MerchantError(409, "account_closing", "The account is being deleted")
    status = body.get("seller_status")
    if status not in ("non_trader", "trader"):
        raise MerchantError(422, "invalid_field", "seller_status must be non_trader or trader", field="seller_status")
    assessment = validate_assessment(body.get("assessment"))
    prof = await get_profile(db, user["user_id"])
    now = now_utc()
    patch: dict = {"user_id": user["user_id"], "seller_status": status, "assessment": assessment, "declared_at": now, "updated_at": now}
    if status == "non_trader":
        if prof.get("forced_trader"):
            raise MerchantError(409, "reclassified_as_trader", "Your account was reclassified as a trader; complete the trader details")
        if assessment["professional_or_company"] or assessment["for_profit_or_business"] and assessment["regular_sales"]:
            raise MerchantError(409, "likely_trader", "Your answers indicate that you act as a trader; declare the trader status and complete the details")
        patch["trader"] = None
        patch["verification"] = {"state": "unverified"}
    else:
        trader = validate_trader_details(body.get("trader"))
        old = prof.get("trader")
        patch["trader"] = trader
        material_change = (old != trader)
        v = prof.get("verification") or {"state": "unverified"}
        patch["verification"] = ({"state": "in_review", "submitted_at": now} if (material_change or v.get("state") in ("unverified", "rejected"))
                                 else v)
    await db[C_PROFILES].update_one({"user_id": user["user_id"]}, {"$set": patch}, upsert=True)
    await audit(db, user["user_id"], "status_declared", user, {"seller_status": status, "verification": patch["verification"]["state"] if patch.get("verification") else None})
    return await get_profile(db, user["user_id"])


async def review(db, actor: dict, user_id: str, decision: str, note: Optional[str], *, method: str = "manual_review") -> dict:
    """Verificarea informatiilor (Art. 30(2)-(3)): in_review | verified | rejected. Se inregistreaza cine, cand, metoda, nota.
    NU se cer / nu se pastreaza copii ale documentelor de identitate."""
    if not reviewer_role(actor):
        raise MerchantError(403, "forbidden", "Not authorized")
    if decision not in ("in_review", "verified", "rejected"):
        raise MerchantError(422, "invalid_field", "decision must be in_review, verified or rejected")
    if actor["user_id"] == user_id:
        raise MerchantError(403, "self_review_forbidden", "You cannot review your own details")
    note = (note or "").strip() or None
    if note and len(note) > 1000:
        raise MerchantError(422, "invalid_field", "note is too long")
    if decision == "rejected" and (not note or len(note) < 10):
        raise MerchantError(422, "reason_required", "A reason is required when rejecting")
    prof = await db[C_PROFILES].find_one({"user_id": user_id}, {"_id": 0})
    if not prof or prof.get("seller_status") != "trader" or not prof.get("trader"):
        raise MerchantError(409, "no_trader_details", "The seller has not submitted trader details")
    ver = {"state": decision, "reviewed_by": actor["user_id"], "reviewed_at": now_utc(), "method": str(method)[:40], "note": note}
    await db[C_PROFILES].update_one({"user_id": user_id, "seller_status": "trader"}, {"$set": {"verification": ver, "updated_at": now_utc()}})
    await audit(db, user_id, "verification_" + decision, actor, {"method": ver["method"]})
    return await get_profile(db, user_id)


async def reclassify_as_trader(db, actor: dict, user_id: str, reason: str) -> dict:
    if not reviewer_role(actor):
        raise MerchantError(403, "forbidden", "Not authorized")
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 1000:
        raise MerchantError(422, "reason_required", "A reason (10-1000 characters) is required")
    now = now_utc()
    await db[C_PROFILES].update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "seller_status": "trader", "forced_trader": True,
                                                                    "reclassified_by": actor["user_id"], "reclassified_at": now, "updated_at": now},
                                                           "$setOnInsert": {"trader": None, "verification": {"state": "unverified"}}}, upsert=True)
    await audit(db, user_id, "reclassified_as_trader", actor, {"reason": reason})
    return await get_profile(db, user_id)


# ----------------------------------------------------------------------------------------------- poarta de vanzare
async def sale_eligibility(db, seller_id: str, *, now: Optional[datetime] = None, policy: Optional[dict] = None) -> dict:
    """Poate acest vanzator sa publice / vanda articole CU PRET? {allowed, reason, seller_status}. Decis doar pe server."""
    pol = policy or await get_policy(db)
    prof = await get_profile(db, seller_id)
    status = prof.get("seller_status", "undeclared")
    if not pol["enforce_seller_status"]:
        return {"allowed": True, "reason": None, "seller_status": status}
    if status == "undeclared":
        grace = pol.get("grace_until")
        if grace:
            try:
                if (now or now_utc()).date() <= datetime.fromisoformat(grace).date():
                    return {"allowed": True, "reason": None, "seller_status": status, "grace": True}
            except ValueError:
                pass
        return {"allowed": False, "reason": "seller_status_required", "seller_status": status}
    if status == "non_trader" and not prof.get("forced_trader"):
        return {"allowed": True, "reason": None, "seller_status": status}
    if not prof.get("trader") or not _complete(prof):
        return {"allowed": False, "reason": "trader_details_required", "seller_status": "trader"}
    state = (prof.get("verification") or {}).get("state", "unverified")
    if state == "rejected" or _STATE_RANK.get(state, 0) < _STATE_RANK[pol["min_verification_to_sell"]]:
        return {"allowed": False, "reason": "trader_verification_required", "seller_status": "trader", "verification_state": state}
    return {"allowed": True, "reason": None, "seller_status": "trader", "verification_state": state}


async def require_sale_eligibility(db, seller_id: str) -> None:
    res = await sale_eligibility(db, seller_id)
    if not res["allowed"]:
        raise MerchantError(409, res["reason"], "Complete the seller information before selling", next_action="seller_onboarding")


# ----------------------------------------------------------------------------------------------- afisare publica (Art. 30(7))
def public_seller_info(profile: Optional[dict]) -> dict:
    """Ce vede cumparatorul pe pagina produsului / la checkout. Datele apar DOAR pentru comercianti; vanzatorii care nu sunt
    comercianti (sau nedeclarati) au doar eticheta de statut, fara date personale suplimentare."""
    p = profile or {}
    status = p.get("seller_status", "undeclared")
    if status != "trader" or not p.get("trader"):
        return {"seller_status": status if status != "trader" else "undeclared",
                "consumer_rights_notice": "non_trader" if status == "non_trader" else None, "trader": None}
    t = p["trader"]
    a = t["address"]
    return {"seller_status": "trader", "consumer_rights_notice": None,
            "verification_state": (p.get("verification") or {}).get("state", "unverified"),
            "trader": {"name": t["name"], "trader_type": t["trader_type"],
                       "address": ", ".join(x for x in (a["line1"], a.get("line2"), a["postal_code"] + " " + a["city"], a["country"]) if x),
                       "phone": t["phone"], "email": t["contact_email"],
                       "registry": ({"name": t["registry"]["register_name"], "number": t["registry"]["number"]} if t["registry"].get("number") else None),
                       "eu_compliance_statement": True}}


async def seller_info_for_items(db, seller_ids: list) -> dict:
    ids = sorted({s for s in seller_ids if s})
    if not ids:
        return {}
    rows = await db[C_PROFILES].find({"user_id": {"$in": ids}}, {"_id": 0}).to_list(len(ids))
    by = {r["user_id"]: r for r in rows}
    return {i: public_seller_info(by.get(i)) for i in ids}


# ----------------------------------------------------------------------------------------------- vedere proprie / personal
def own_view(profile: dict) -> dict:
    out = {k: v for k, v in profile.items() if k not in ("_id", "assessment")}
    v = dict(out.get("verification") or {"state": "unverified"})
    v.pop("reviewed_by", None)
    out["verification"] = v
    out["assessment"] = profile.get("assessment")
    return out


async def signals(db, seller_id: str, *, policy: Optional[dict] = None) -> dict:
    """Semnale pentru REVIZUIRE umana (nu decid statutul): numar de vanzari / venit in ultimele 12 luni."""
    pol = policy or await get_policy(db)
    since = now_utc() - timedelta(days=365)
    # vanzarile apar in jurnalul Astrans (`astran_ledger`, tip `shop_sale`) pentru autor
    txs = await db.astran_ledger.find({"user_id": seller_id, "type": "shop_sale", "timestamp": {"$gt": since}}, {"_id": 0, "amount": 1}).to_list(100000)
    count, revenue = len(txs), sum(int(t.get("amount", 0)) for t in txs)
    flagged = bool((pol["signal_sales_count_12m"] and count >= pol["signal_sales_count_12m"])
                   or (pol["signal_revenue_astrans_12m"] and revenue >= pol["signal_revenue_astrans_12m"]))
    return {"sales_12m": count, "revenue_astrans_12m": revenue, "review_suggested": flagged}


async def admin_list(db, actor: dict, *, state: Optional[str] = None, limit: int = 100) -> list:
    if not reviewer_role(actor):
        raise MerchantError(403, "forbidden", "Not authorized")
    flt: dict = {"seller_status": "trader"}
    if state:
        if state not in VERIFICATION_STATES:
            raise MerchantError(422, "invalid_field", "invalid state")
        flt["verification.state"] = state
    rows = await db[C_PROFILES].find(flt, {"_id": 0}).sort("updated_at", -1).limit(max(1, min(limit, 200))).to_list(200)
    return rows


async def admin_get(db, actor: dict, user_id: str) -> dict:
    if not reviewer_role(actor):
        raise MerchantError(403, "forbidden", "Not authorized")
    prof = await get_profile(db, user_id)
    hist = await db[C_AUDIT].find({"user_id": user_id}, {"_id": 0}).sort("ts", -1).limit(100).to_list(100)
    return {"profile": prof, "signals": await signals(db, user_id), "history": hist}
