"""Moderare de continut (DSA): rapoarte, notificari Art. 16, decizii, expuneri de motive Art. 17, plangeri Art. 20,
jurnal de audit append-only cu lant de hash-uri.

Straturi: acest modul NU depinde de FastAPI (doar de un obiect `db` tip Motor/FakeDB) ca sa poata fi testat fara retea.
Rutele subtiri sunt in moderation_routes.py.

Colectii noi (colectia existenta `reports` ramane compatibila: campurile noi sunt optionale, statusul vechi "open" == "pending"):
  report_evidence          instantaneu imuabil al continutului raportat (la momentul raportului)
  report_internal_notes    notele interne ale moderatorilor (append-only; NICIODATA expuse utilizatorilor)
  moderation_decisions     o decizie = o actiune; campurile de motivare nu se modifica; doar `status` (in_effect/reversed)
                           se schimba si DOAR prin apeluri care scriu un eveniment de audit
  statements_of_reasons    expunerea de motive Art. 17 (imuabila, cu sha256)
  moderation_corrections   corectii ulterioare (adaugare, nu editare)
  moderation_appeals       plangeri interne Art. 20
  moderation_notifications notificari in aplicatie catre utilizator
  moderation_audit_log     jurnal append-only, `seq` unic + hash inlantuit (tamper-evident)
  contact_messages         mesaje primite prin pagina de contact (Art. 11/12)

LIMITARE ASUMATA: nu se folosesc tranzactii multi-document (necesita replica set; restul codului nu le foloseste).
Ordinea scrierilor este aleasa ca o intrerupere sa lase urme auditabile (decizia e creata ca "applying", apoi
"in_effect"/"failed"), iar revendicarea raportului este atomica (un singur moderator poate decide).
"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("astran.moderation")

# ------------------------------------------------------------------------------------------------ constante
STATUS_PENDING, STATUS_REVIEW, STATUS_RESOLVED, STATUS_REJECTED = "pending", "under_review", "resolved", "rejected"
STATUSES = (STATUS_PENDING, STATUS_REVIEW, STATUS_RESOLVED, STATUS_REJECTED)
LEGACY_OPEN = "open"                      # status scris de codul vechi (server.py / dm_core.py) == pending
OPEN_STATUSES = (STATUS_PENDING, LEGACY_OPEN, STATUS_REVIEW)

KIND_RULE = "rule_violation"              # raport obisnuit (incalcare a regulilor platformei) - NU este notificare Art. 16
KIND_NOTICE = "illegal_content_notice"    # notificare de continut presupus ilegal (Art. 16)
KIND_OWN = "own_initiative"               # caz deschis de Astran din proprie initiativa (nu exista notificator)
KIND_ORDER = "authority_order"            # caz deschis la executarea unui ordin Art. 9 (vezi authority_orders.py)
KINDS = (KIND_RULE, KIND_NOTICE, KIND_OWN, KIND_ORDER)
# Valori `decision.source` (folosite si de raportul de transparenta Art. 15)
SRC_NOTICE, SRC_TERMS_REPORT, SRC_OWN, SRC_ORDER = "art16_notice", "terms_report", "own_initiative", "authority_order"
KIND_TO_SOURCE = {KIND_NOTICE: SRC_NOTICE, KIND_RULE: SRC_TERMS_REPORT, KIND_OWN: SRC_OWN, KIND_ORDER: SRC_ORDER}
# evenimentele de audit legate de ordine ale autoritatilor: vizibile doar personalului cu rol juridic (nu tuturor moderatorilor)
ORDER_EVENT_TYPES = ("order_received", "order_supplemented", "order_attachment_added", "order_attachment_downloaded",
                     "order_authenticity_set", "order_validated", "order_legal_review", "order_executed", "order_execution_failed",
                     "order_information_prepared", "order_recipient_notice", "order_response_recorded", "order_response_delivery",
                     "order_closed", "order_purged", "order_legal_role_changed")
CATEGORIES = ("illegal_content", "child_safety", "harassment", "hate_speech", "violence", "sexual_content",
              "self_harm", "scam_fraud", "ip_infringement", "spam", "other")
CSAM_CATEGORY = "child_safety"            # singura categorie pentru care identitatea notificatorului poate lipsi (Art. 16(2)(c))

ACTION_NO_ACTION = "no_action"            # raport respins
ACTION_CLOSE = "close_resolved"           # rezolvat fara sanctiune
ACTION_WARN = "warn"
ACTION_REMOVE_ILLEGAL = "remove_content_illegal"
ACTION_REMOVE_POLICY = "remove_content_policy"
ACTION_RESTRICT_CONTENT = "restrict_content"
ACTION_RESTRICT_ACCOUNT = "restrict_account_temporary"
ACTION_SUSPEND = "suspend_account_permanent"
ACTIONS = (ACTION_NO_ACTION, ACTION_CLOSE, ACTION_WARN, ACTION_REMOVE_ILLEGAL, ACTION_REMOVE_POLICY,
           ACTION_RESTRICT_CONTENT, ACTION_RESTRICT_ACCOUNT, ACTION_SUSPEND)
CONTENT_ACTIONS = (ACTION_REMOVE_ILLEGAL, ACTION_REMOVE_POLICY, ACTION_RESTRICT_CONTENT)
ACCOUNT_ACTIONS = (ACTION_WARN, ACTION_RESTRICT_ACCOUNT, ACTION_SUSPEND)
# Art. 17(1): restrictii de vizibilitate a continutului, suspendarea serviciului, suspendarea/inchiderea contului.
# Avertismentul NU e listat in Art. 17(1): primeste notificare, nu expunere de motive (de confirmat juridic).
SOR_ACTIONS = CONTENT_ACTIONS + (ACTION_RESTRICT_ACCOUNT, ACTION_SUSPEND)
ADMIN_ONLY_ACTIONS = (ACTION_SUSPEND,)    # suspendarea permanenta cere platform admin

DEC_APPLYING, DEC_IN_EFFECT, DEC_REVERSED, DEC_FAILED = "applying", "in_effect", "reversed", "failed"
APPEAL_RECEIVED, APPEAL_REVIEW, APPEAL_MAINTAINED, APPEAL_REVERSED = "received", "under_review", "decision_maintained", "decision_reversed"
APPEAL_MODIFIED = "decision_modified"
DEC_MODIFIED = "modified"                 # decizie inlocuita de una noua in urma unei plangeri (ramane in istoric)
APPEAL_OPEN = (APPEAL_RECEIVED, APPEAL_REVIEW)

COMPLAINT_PERIOD_MONTHS = 6               # Art. 20(1): cel putin 6 luni de la decizie
MAX_TEXT = 4000
MAX_URLS = 5

CONTENT_FIELDS = {
    "game": ("game_id", "name", "title", "description", "owner_id", "created_at", "is_public", "age_category"),
    "user": ("user_id", "username", "display_name", "avatar_url", "created_at"),
    "message": ("message_id", "text", "content", "body", "raw", "safe", "sender_id", "user_id", "group_id",
                "conversation_id", "created_at", "ts"),
}


class ModerationError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message or code


# ------------------------------------------------------------------------------------------------ utilitare
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: Any) -> Optional[datetime]:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:16]


def add_months(dt: datetime, n: int) -> datetime:
    y = dt.year + (dt.month - 1 + n) // 12
    m = (dt.month - 1 + n) % 12 + 1
    return dt.replace(year=y, month=m, day=min(dt.day, calendar.monthrange(y, m)[1]))


def norm_status(s: Any) -> str:
    return STATUS_PENDING if s in (None, LEGACY_OPEN) else str(s)


def _clip(v: Any, n: int) -> Any:
    return v[:n] if isinstance(v, str) else v


def clean_text(v: Any, *, field: str, min_len: int = 0, max_len: int = MAX_TEXT, required: bool = False) -> Optional[str]:
    if v is None or (isinstance(v, str) and not v.strip()):
        if required or min_len:
            raise ModerationError(422, "field_required", f"{field} is required")
        return None
    if not isinstance(v, str):
        raise ModerationError(422, "field_invalid", f"{field} must be text")
    s = v.strip()
    if len(s) < min_len:
        raise ModerationError(422, "field_too_short", f"{field} must have at least {min_len} characters")
    if len(s) > max_len:
        raise ModerationError(422, "field_too_long", f"{field} must have at most {max_len} characters")
    return s


def clean_urls(urls: Any) -> list[str]:
    if urls in (None, []):
        return []
    if not isinstance(urls, list) or len(urls) > MAX_URLS:
        raise ModerationError(422, "urls_invalid", f"at most {MAX_URLS} links")
    out = []
    for u in urls:
        if not isinstance(u, str) or not re.fullmatch(r"https://[^\s<>\"']{4,490}", u.strip()):
            raise ModerationError(422, "url_invalid", "evidence links must be https URLs")
        out.append(u.strip())
    return out


_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


def role_of(user: Optional[dict]) -> Optional[str]:
    """Rolul se citeste DOAR din documentul de utilizator incarcat de server din DB la fiecare cerere."""
    if not user:
        return None
    if user.get("is_platform_admin"):
        return "admin"
    if user.get("is_moderator"):
        return "moderator"
    return None


def account_block(user: Optional[dict], now: Optional[datetime] = None) -> Optional[dict]:
    """Daca contul e suspendat de moderare, intoarce {permanent, until}; altfel None."""
    if not user:
        return None
    if user.get("moderation_suspended"):
        return {"permanent": True, "until": None}
    until = aware(user.get("moderation_suspended_until"))
    if until and until > (now or now_utc()):
        return {"permanent": False, "until": until}
    return None


# ------------------------------------------------------------------------------------------------ configurare contact
CONTACT_ENV = {
    "legal_entity_name": "ASTRAN_LEGAL_ENTITY_NAME",
    "postal_address": "ASTRAN_LEGAL_ADDRESS",
    "registration_info": "ASTRAN_COMPANY_REGISTRATION",
    "authorities_email": "ASTRAN_DSA_AUTHORITIES_EMAIL",           # Art. 11
    "authorities_languages": "ASTRAN_DSA_AUTHORITIES_LANGUAGES",   # Art. 11(3)
    "users_email": "ASTRAN_DSA_USERS_EMAIL",                       # Art. 12
    "users_languages": "ASTRAN_DSA_USERS_LANGUAGES",
    "trusted_flaggers_email": "ASTRAN_TRUSTED_FLAGGERS_EMAIL",
    "eu_legal_representative": "ASTRAN_EU_LEGAL_REPRESENTATIVE",   # Art. 13 (doar furnizori din afara UE)
    "out_of_court_bodies": "ASTRAN_ODS_BODIES",                    # Art. 21 - organisme de solutionare extrajudiciara
}
CONTACT_REQUIRED = ("legal_entity_name", "postal_address", "authorities_email", "authorities_languages",
                    "users_email", "users_languages")


def contact_info(env: Optional[dict] = None) -> dict:
    env = os.environ if env is None else env
    vals = {k: (env.get(v) or "").strip() or None for k, v in CONTACT_ENV.items()}
    missing = [k for k in CONTACT_REQUIRED if not vals[k]]
    return {**vals, "configured": not missing, "missing_fields": missing,
            "env_vars": {k: CONTACT_ENV[k] for k in missing},
            "electronic_channel": "in_app_form",      # formularul din aplicatie functioneaza mereu (omul citeste mesajele)
            "automated_only": False}


# ------------------------------------------------------------------------------------------------ indexuri
async def ensure_indexes(db) -> None:
    await db.reports.create_index("report_id", unique=True)
    await db.reports.create_index([("status", 1), ("created_at", -1)])
    await db.reports.create_index("reporter_id")
    await db.report_evidence.create_index("report_id", unique=True)
    await db.report_internal_notes.create_index("report_id")
    await db.moderation_decisions.create_index("decision_id", unique=True)
    await db.moderation_decisions.create_index("report_id")
    await db.moderation_decisions.create_index("affected_user_id")
    await db.statements_of_reasons.create_index("decision_id", unique=True)
    await db.moderation_appeals.create_index("appeal_id", unique=True)
    await db.moderation_appeals.create_index([("decision_id", 1), ("user_id", 1)], unique=True)
    await db.moderation_notifications.create_index([("user_id", 1), ("created_at", -1)])
    await db.moderation_audit_log.create_index("seq", unique=True)
    await db.moderation_audit_log.create_index("report_id")
    await db.contact_messages.create_index("message_id", unique=True)


# ------------------------------------------------------------------------------------------------ audit (append-only)
_AUDIT_FIELDS = ("seq", "event_id", "ts", "event_type", "actor_id", "actor_role", "report_id", "decision_id", "appeal_id",
                 "previous_status", "new_status", "action", "reasoning", "outcome", "details", "prev_hash")


def _audit_hash(doc: dict) -> str:
    body = {k: (doc.get(k).isoformat() if isinstance(doc.get(k), datetime) else doc.get(k)) for k in _AUDIT_FIELDS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


async def audit(db, *, event_type: str, actor_id: Optional[str], actor_role: Optional[str] = None, report_id: Optional[str] = None,
                decision_id: Optional[str] = None, appeal_id: Optional[str] = None, previous_status: Optional[str] = None,
                new_status: Optional[str] = None, action: Optional[str] = None, reasoning: Optional[str] = None,
                outcome: Optional[str] = None, details: Optional[dict] = None) -> dict:
    """Singura cale de scriere in jurnal: doar INSERT. Nu exista functie de update/delete pentru aceasta colectie."""
    for _ in range(8):
        last = await db.moderation_audit_log.find({}, {"_id": 0, "seq": 1, "hash": 1}).sort("seq", -1).limit(1).to_list(1)
        seq = (last[0]["seq"] + 1) if last else 1
        doc = {"seq": seq, "event_id": new_id("aud_"), "ts": now_utc(), "event_type": event_type, "actor_id": actor_id,
               "actor_role": actor_role, "report_id": report_id, "decision_id": decision_id, "appeal_id": appeal_id,
               "previous_status": previous_status, "new_status": new_status, "action": action,
               "reasoning": reasoning, "outcome": outcome, "details": details or {},
               "prev_hash": last[0]["hash"] if last else "GENESIS"}
        doc["hash"] = _audit_hash(doc)
        try:
            await db.moderation_audit_log.insert_one(doc)
            return doc
        except Exception as e:  # noqa: BLE001  (DuplicateKeyError de pymongo SAU al FakeDB: cursa pe `seq` -> reincercam)
            if "Duplicate" not in type(e).__name__ and "E11000" not in str(e):
                raise
    raise ModerationError(503, "audit_unavailable", "could not append audit event")


async def verify_audit_chain(db) -> dict:
    docs = await db.moderation_audit_log.find({}, {"_id": 0}).sort("seq", 1).to_list(None)
    prev = "GENESIS"
    for i, d in enumerate(docs, start=1):
        if d.get("seq") != i or d.get("prev_hash") != prev or d.get("hash") != _audit_hash(d):
            return {"ok": False, "broken_at_seq": d.get("seq"), "checked": i}
        prev = d["hash"]
    return {"ok": True, "checked": len(docs), "broken_at_seq": None}


async def audit_history(db, *, report_id: Optional[str] = None, decision_id: Optional[str] = None, limit: int = 500,
                        include_orders: bool = False) -> list[dict]:
    """`include_orders=False` (implicit) ascunde evenimentele despre ordinele autoritatilor: ele sunt sensibile si le vede doar
    personalul cu rol juridic (rutele /orders). Filtrarea se face in interogare, nu dupa limita."""
    flt: dict = {}
    if not include_orders:
        flt["event_type"] = {"$nin": list(ORDER_EVENT_TYPES)}
    if report_id:
        flt["report_id"] = report_id
    if decision_id:
        flt["decision_id"] = decision_id
    return await db.moderation_audit_log.find(flt, {"_id": 0}).sort("seq", 1).limit(limit).to_list(limit)


# ------------------------------------------------------------------------------------------------ notificari
async def notify(db, user_id: str, kind: str, *, message: str, ref: Optional[dict] = None) -> dict:
    n = {"notification_id": new_id("mnt_"), "user_id": user_id, "kind": kind, "message": message,
         "ref": ref or {}, "created_at": now_utc(), "read_at": None}
    await db.moderation_notifications.insert_one(n)
    n.pop("_id", None)
    return n


async def list_notifications(db, user_id: str, limit: int = 100) -> list[dict]:
    return await db.moderation_notifications.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)


async def mark_notification_read(db, user_id: str, notification_id: str) -> None:
    res = await db.moderation_notifications.update_one(
        {"notification_id": notification_id, "user_id": user_id}, {"$set": {"read_at": now_utc()}})
    if not res.matched_count:
        raise ModerationError(404, "not_found")


# ------------------------------------------------------------------------------------------------ rapoarte + notificari Art. 16
async def resolve_reported_user(db, target_type: str, target_id: str) -> Optional[str]:
    if target_type == "user":
        return target_id
    if target_type == "game":
        g = await db.games.find_one({"game_id": target_id}, {"_id": 0, "owner_id": 1})
        return (g or {}).get("owner_id")
    m = await db.dm_messages.find_one({"message_id": target_id}, {"_id": 0, "sender_id": 1})
    if m:
        return m.get("sender_id")
    m = await db.group_chat_messages.find_one({"message_id": target_id}, {"_id": 0, "user_id": 1})
    return (m or {}).get("user_id")


async def capture_evidence(db, target_type: str, target_id: str) -> dict:
    """Instantaneu al continutului raportat (doar campuri din lista alba; niciodata parole/emailuri)."""
    snap: dict = {}
    src = None
    if target_type == "game":
        src = await db.games.find_one({"game_id": target_id}, {"_id": 0})
    elif target_type == "user":
        src = await db.users.find_one({"user_id": target_id}, {"_id": 0})
    else:
        src = (await db.dm_messages.find_one({"message_id": target_id}, {"_id": 0})
               or await db.group_chat_messages.find_one({"message_id": target_id}, {"_id": 0}))
        if src is not None:
            snap["_source"] = "dm" if "conversation_id" in src else "group_chat"
    for f in CONTENT_FIELDS.get(target_type, ()):
        if src and f in src and src[f] is not None:
            v = src[f]
            snap[f] = _clip(v, 2000) if isinstance(v, str) else v
    return snap


def _location(target_type: str, target_id: str) -> str:
    return f"astran://{target_type}/{target_id}"


def validate_notice(notice: dict, category: str) -> dict:
    """Art. 16(2): explicatie suficient de substantiata, locatie exacta, identitate (cu exceptia CSAM), declaratie de buna-credinta."""
    if not isinstance(notice, dict):
        raise ModerationError(422, "notice_required")
    if notice.get("good_faith_declaration") is not True:
        raise ModerationError(422, "good_faith_required", "good-faith declaration is required")
    explanation = clean_text(notice.get("explanation"), field="explanation", min_len=30, max_len=MAX_TEXT)
    legal_basis = clean_text(notice.get("alleged_legal_basis"), field="alleged_legal_basis", max_len=500)
    location_hint = clean_text(notice.get("content_location"), field="content_location", max_len=500)
    name = clean_text(notice.get("reporter_name"), field="reporter_name", max_len=120)
    email = clean_text(notice.get("reporter_email"), field="reporter_email", max_len=320)
    if email and not _EMAIL_RE.match(email):
        raise ModerationError(422, "email_invalid")
    identity_exempt = category == CSAM_CATEGORY and bool(notice.get("identity_exempt"))
    if not identity_exempt and (not name or not email):
        raise ModerationError(422, "reporter_identity_required", "name and email are required for this notice")
    return {"explanation": explanation, "alleged_legal_basis": legal_basis, "content_location": location_hint,
            "reporter_name": name, "reporter_email": email, "good_faith_declaration": True,
            "identity_exempt": identity_exempt,
            "assessment": {"state": "not_assessed", "assessed_by": None, "assessed_at": None, "reasoning": None}}


def build_receipt(report: dict, evidence: Optional[dict] = None) -> dict:
    kind = report.get("report_kind") or KIND_RULE
    return {
        "report_id": report["report_id"],
        "received_at": report["created_at"],
        "kind": kind,
        "is_dsa_notice": kind == KIND_NOTICE,
        "target_type": report["target_type"],
        "target_id": report["target_id"],
        "reported_content": {"location": _location(report["target_type"], report["target_id"]),
                             "summary": _summary(evidence or {})},
        "status": norm_status(report.get("status")),
        "received": True,
        "track": {"path": f"/moderation/me/reports/{report['report_id']}"},
    }


def _summary(ev: dict) -> Optional[str]:
    for k in ("title", "name", "text", "content", "body", "display_name", "username", "safe"):
        if isinstance(ev.get(k), str) and ev[k].strip():
            return ev[k].strip()[:160]
    return None


async def register_report(db, doc: dict, *, kind: str = KIND_RULE, category: Optional[str] = None,
                          notice: Optional[dict] = None) -> dict:
    """Completeaza si salveaza un raport (ruta existenta POST /reports si noua POST /moderation/notices o apeleaza).
    `doc` contine deja report_id/reporter_id/target_*/reason/details/status/created_at. Intoarce confirmarea de primire."""
    category = category if category in CATEGORIES else ("illegal_content" if kind == KIND_NOTICE else "other")
    if kind == KIND_NOTICE:
        doc["notice"] = validate_notice(notice or {}, category)
        if doc.get("reporter_id"):
            rep_user = await db.users.find_one({"user_id": doc["reporter_id"]}, {"_id": 0, "is_trusted_flagger": 1})
            doc["notice"]["trusted_flagger"] = bool((rep_user or {}).get("is_trusted_flagger"))   # statut atribuit DOAR de un admin
        else:
            doc["notice"]["trusted_flagger"] = False
    ev = await capture_evidence(db, doc["target_type"], doc["target_id"])
    doc.update({"report_kind": kind, "category": category, "status": STATUS_PENDING,
                "reported_user_id": await resolve_reported_user(db, doc["target_type"], doc["target_id"]),
                "assigned_to": None, "reviewed_at": None, "receipt_sent_at": now_utc()})
    await db.reports.insert_one(doc)
    try:
        await db.report_evidence.insert_one({"report_id": doc["report_id"], "captured_at": now_utc(), "content": ev,
                                             "sha256": hashlib.sha256(json.dumps(ev, sort_keys=True, default=str).encode()).hexdigest()})
    except Exception:  # noqa: BLE001
        log.warning("moderation: evidence snapshot failed for %s", doc["report_id"], exc_info=True)
    await audit(db, event_type="report_received", actor_id=doc["reporter_id"], actor_role="reporter", report_id=doc["report_id"],
                new_status=STATUS_PENDING, action="submit_notice" if kind == KIND_NOTICE else "submit_report",
                details={"kind": kind, "category": category, "target_type": doc["target_type"]})
    receipt = build_receipt(doc, ev)
    if doc.get("reporter_id"):
        n = await notify(db, doc["reporter_id"], "report_receipt", ref={"report_id": doc["report_id"]},
                         message="We received your notice." if kind == KIND_NOTICE else "We received your report.")
        await db.reports.update_one({"report_id": doc["report_id"]}, {"$push": {"notifications_sent": n["notification_id"]}})
    if kind == KIND_NOTICE and (doc.get("notice") or {}).get("reporter_email"):
        # Art. 16(4): confirmare de primire pe email cand notificarea contine date de contact electronice (si pentru notificatori anonimi)
        from . import dsa_delivery as DL
        await DL.enqueue(db, kind="notice_receipt", dedupe_key=f"receipt:{doc['report_id']}", recipient={"report_id": doc["report_id"]},
                         ref={"report_id": doc["report_id"]})
    return receipt


def public_report_view(r: dict) -> dict:
    """Ce vede REPORTERUL: fara note interne, fara moderatori, fara datele contului raportat."""
    notice = r.get("notice") or {}
    return {"report_id": r["report_id"], "kind": r.get("report_kind") or KIND_RULE, "category": r.get("category") or "other",
            "target_type": r["target_type"], "target_id": r["target_id"], "reason": r.get("reason"), "details": r.get("details"),
            "status": norm_status(r.get("status")), "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
            "is_dsa_notice": (r.get("report_kind") == KIND_NOTICE),
            "notice": ({"explanation": notice.get("explanation"), "content_location": notice.get("content_location"),
                        "alleged_legal_basis": notice.get("alleged_legal_basis")} if notice else None)}


async def my_reports(db, user_id: str, limit: int = 100) -> list[dict]:
    rows = await db.reports.find({"reporter_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    out = []
    for r in rows:
        v = public_report_view(r)
        dec = await db.moderation_decisions.find({"report_id": r["report_id"], "notifier_id": user_id}, {"_id": 0, "decision_id": 1}).to_list(5)
        v["outcome_decision_ids"] = [d["decision_id"] for d in dec]
        out.append(v)
    return out


async def my_report(db, user_id: str, report_id: str) -> dict:
    r = await db.reports.find_one({"report_id": report_id, "reporter_id": user_id}, {"_id": 0})   # IDOR: 404 si pentru rapoarte straine
    if not r:
        raise ModerationError(404, "not_found")
    v = public_report_view(r)
    dec = await db.moderation_decisions.find({"report_id": report_id, "notifier_id": user_id}, {"_id": 0}).to_list(10)
    v["outcomes"] = [notifier_outcome(d) for d in dec]
    return v


# ------------------------------------------------------------------------------------------------ dashboard admin
def _admin_filter(*, status=None, category=None, target_type=None, kind=None, assigned_to=None, date_from=None, date_to=None,
                  q=None) -> dict:
    flt: dict = {}
    if status:
        if status not in STATUSES:
            raise ModerationError(422, "status_invalid")
        flt["status"] = {"$in": [STATUS_PENDING, LEGACY_OPEN]} if status == STATUS_PENDING else status
    if category:
        flt["category"] = category
    if target_type:
        flt["target_type"] = target_type
    if kind:
        flt["report_kind"] = kind
    if assigned_to:
        flt["assigned_to"] = assigned_to
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            rng["$lte"] = date_to
        flt["created_at"] = rng
    if q:
        pat = {"$regex": re.escape(q.strip()[:100]), "$options": "i"}
        flt["$or"] = [{"report_id": pat}, {"reason": pat}, {"details": pat}, {"target_id": pat}, {"reporter_id": pat},
                      {"reported_user_id": pat}]
    return flt


async def list_reports(db, *, page: int = 1, page_size: int = 25, **filters) -> dict:
    page, page_size = max(1, int(page)), min(100, max(1, int(page_size)))
    flt = _admin_filter(**filters)
    total = await db.reports.count_documents(flt)
    rows = await db.reports.find(flt, {"_id": 0}).sort("created_at", -1).limit(page * page_size).to_list(page * page_size)
    rows = rows[(page - 1) * page_size:]
    items = [{"report_id": r["report_id"], "kind": r.get("report_kind") or KIND_RULE, "category": r.get("category") or "other",
              "target_type": r["target_type"], "target_id": r["target_id"], "reason": r.get("reason"),
              "status": norm_status(r.get("status")), "assigned_to": r.get("assigned_to"),
              "reporter_id": r.get("reporter_id"), "reported_user_id": r.get("reported_user_id"),
              "created_at": r.get("created_at")} for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def _user_card(db, uid: Optional[str]) -> Optional[dict]:
    if not uid:
        return None
    u = await db.users.find_one({"user_id": uid}, {"_id": 0})
    if not u:
        return {"user_id": uid, "missing": True}
    return {"user_id": uid, "username": u.get("username"), "display_name": u.get("display_name"), "created_at": u.get("created_at"),
            "warning_count": u.get("moderation_warning_count", 0), "suspended": bool(account_block(u)),
            "chat_muted_until": u.get("chat_muted_until")}


async def report_detail(db, report_id: str) -> dict:
    r = await db.reports.find_one({"report_id": report_id}, {"_id": 0})
    if not r:
        raise ModerationError(404, "not_found")
    ev = await db.report_evidence.find_one({"report_id": report_id}, {"_id": 0})
    decisions = await db.moderation_decisions.find({"report_id": report_id}, {"_id": 0}).sort("created_at", 1).to_list(50)
    notes = await db.report_internal_notes.find({"report_id": report_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
    prior = await db.reports.count_documents({"reported_user_id": r.get("reported_user_id")}) if r.get("reported_user_id") else 0
    r["status"] = norm_status(r.get("status"))
    r.setdefault("report_kind", KIND_RULE)
    return {"report": r, "evidence": (ev or {}).get("content") or r.get("context") or {},
            "evidence_sha256": (ev or {}).get("sha256"), "reporter": await _user_card(db, r.get("reporter_id")),
            "reported_user": await _user_card(db, r.get("reported_user_id")), "reports_against_user": prior,
            "decisions": decisions, "internal_notes": notes, "history": await audit_history(db, report_id=report_id)}


async def assign_report(db, actor: dict, report_id: str, assignee_id: str) -> dict:
    assignee = await db.users.find_one({"user_id": assignee_id}, {"_id": 0})
    if not assignee or not role_of(assignee):
        raise ModerationError(422, "assignee_not_moderator")
    r = await db.reports.find_one({"report_id": report_id}, {"_id": 0})
    if not r:
        raise ModerationError(404, "not_found")
    if norm_status(r.get("status")) in (STATUS_RESOLVED, STATUS_REJECTED):
        raise ModerationError(409, "report_closed")
    await db.reports.update_one({"report_id": report_id}, {"$set": {"assigned_to": assignee_id, "updated_at": now_utc()}})
    await audit(db, event_type="report_assigned", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=report_id,
                action="assign", details={"assigned_to": assignee_id, "previous_assignee": r.get("assigned_to")})
    return {"report_id": report_id, "assigned_to": assignee_id}


async def add_note(db, actor: dict, report_id: str, text: Any) -> dict:
    text = clean_text(text, field="note", min_len=1, max_len=MAX_TEXT)
    if not await db.reports.find_one({"report_id": report_id}, {"_id": 1}):
        raise ModerationError(404, "not_found")
    note = {"note_id": new_id("note_"), "report_id": report_id, "author_id": actor["user_id"], "text": text, "created_at": now_utc()}
    await db.report_internal_notes.insert_one(note)
    note.pop("_id", None)
    await audit(db, event_type="internal_note_added", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=report_id,
                action="add_note", details={"note_id": note["note_id"]})
    return note


async def set_status(db, actor: dict, report_id: str, new_status: str, reasoning: Any = None) -> dict:
    """pending / under_review (+redeschidere). resolved/rejected trec OBLIGATORIU prin decide() (motivare + notificari)."""
    if new_status in (STATUS_RESOLVED, STATUS_REJECTED):
        return await decide(db, actor, report_id, {"action": ACTION_CLOSE if new_status == STATUS_RESOLVED else ACTION_NO_ACTION,
                                                   "reasoning": reasoning})
    if new_status not in (STATUS_PENDING, STATUS_REVIEW):
        raise ModerationError(422, "status_invalid")
    r = await db.reports.find_one({"report_id": report_id}, {"_id": 0})
    if not r:
        raise ModerationError(404, "not_found")
    prev = norm_status(r.get("status"))
    if prev in (STATUS_RESOLVED, STATUS_REJECTED):
        reasoning = clean_text(reasoning, field="reasoning", min_len=10)       # redeschidere: motiv obligatoriu
    else:
        reasoning = clean_text(reasoning, field="reasoning", max_len=MAX_TEXT)
    if prev == new_status:
        return {"report_id": report_id, "status": prev}
    upd = {"status": new_status, "updated_at": now_utc()}
    if new_status == STATUS_REVIEW and not r.get("reviewed_at"):
        upd["reviewed_at"] = now_utc()
    res = await db.reports.update_one({"report_id": report_id, "status": r.get("status")}, {"$set": upd})
    if not res.matched_count:
        raise ModerationError(409, "status_changed")
    await audit(db, event_type="status_changed", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=report_id,
                previous_status=prev, new_status=new_status, action="set_status", reasoning=reasoning)
    return {"report_id": report_id, "status": new_status}


async def assess_notice(db, actor: dict, report_id: str, state: str, reasoning: Any) -> dict:
    """Evaluarea preciziei/substantierii notificarii Art. 16 (sufficient / insufficient)."""
    if state not in ("sufficient", "insufficient"):
        raise ModerationError(422, "assessment_invalid")
    reasoning = clean_text(reasoning, field="reasoning", min_len=10)
    r = await db.reports.find_one({"report_id": report_id}, {"_id": 0})
    if not r:
        raise ModerationError(404, "not_found")
    if r.get("report_kind") != KIND_NOTICE:
        raise ModerationError(409, "not_a_notice")
    prev = (r.get("notice") or {}).get("assessment", {}).get("state")
    await db.reports.update_one({"report_id": report_id}, {"$set": {"notice.assessment": {
        "state": state, "assessed_by": actor["user_id"], "assessed_at": now_utc(), "reasoning": reasoning}}})
    await audit(db, event_type="notice_assessed", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=report_id,
                action="assess_notice", previous_status=prev, new_status=state, reasoning=reasoning)
    return {"report_id": report_id, "assessment": state}


# ------------------------------------------------------------------------------------------------ efecte (aplicare / anulare)
async def _apply_effect(db, action: str, report: dict, decision: dict, params: dict) -> dict:
    tt, tid, uid = report["target_type"], report["target_id"], decision.get("affected_user_id")
    eff: dict = {"type": action}
    if action in CONTENT_ACTIONS:
        if tt == "game":
            g = await db.games.find_one({"game_id": tid}, {"_id": 0, "is_public": 1, "status": 1})
            if not g:
                raise ModerationError(404, "content_not_found")
            eff["previous"] = {"is_public": g.get("is_public"), "status": g.get("status")}
            state = "restricted" if action == ACTION_RESTRICT_CONTENT else "removed"
            await db.games.update_one({"game_id": tid}, {"$set": {
                "is_public": False, "status": "moderated", "moderation_state": state, "moderation_decision_id": decision["decision_id"]}})
            eff["collection"] = "games"
        elif tt == "message":
            for coll, key in (("dm_messages", "message_id"), ("group_chat_messages", "message_id")):
                res = await db[coll].update_one({key: tid}, {"$set": {"moderation_hidden": True,
                                                                      "moderation_decision_id": decision["decision_id"]}})
                if res.matched_count:
                    eff["collection"] = coll
                    break
            else:
                raise ModerationError(404, "content_not_found")
        else:
            raise ModerationError(422, "content_action_not_applicable", "use an account-level action for user reports")
        return eff
    if action == ACTION_WARN:
        await db.users.update_one({"user_id": uid}, {"$inc": {"moderation_warning_count": 1}, "$set": {"last_moderation_warning_at": now_utc()}})
    elif action == ACTION_RESTRICT_ACCOUNT:
        until = now_utc() + timedelta(days=params["days"])
        u = await db.users.find_one({"user_id": uid}, {"_id": 0, "chat_muted_until": 1, "moderation_suspended_until": 1})
        if params["scope"] == "chat":
            eff["previous"] = {"chat_muted_until": (u or {}).get("chat_muted_until")}
            await db.users.update_one({"user_id": uid}, {"$set": {"chat_muted_until": until}})
        else:
            eff["previous"] = {"moderation_suspended_until": (u or {}).get("moderation_suspended_until")}
            await db.users.update_one({"user_id": uid}, {"$set": {"moderation_suspended_until": until}})
        eff.update({"scope": params["scope"], "until": until})
    elif action == ACTION_SUSPEND:
        await db.users.update_one({"user_id": uid}, {"$set": {"moderation_suspended": True, "moderation_decision_id": decision["decision_id"]}})
        await db.user_sessions.delete_many({"user_id": uid})      # sesiunile server-side se inchid; JWT-urile sunt blocate de poarta din get_current_user
    return eff


async def _revert_effect(db, decision: dict) -> None:
    eff, uid = decision.get("effect") or {}, decision.get("affected_user_id")
    t = eff.get("type")
    if t in CONTENT_ACTIONS:
        if eff.get("collection") == "games":
            prev = eff.get("previous") or {}
            await db.games.update_one({"game_id": decision["target_id"], "moderation_decision_id": decision["decision_id"]}, {
                "$set": {"is_public": bool(prev.get("is_public")), "status": prev.get("status") or "active"},
                "$unset": {"moderation_state": "", "moderation_decision_id": ""}})
        elif eff.get("collection"):
            await db[eff["collection"]].update_one({"message_id": decision["target_id"]}, {"$unset": {"moderation_hidden": "", "moderation_decision_id": ""}})
    elif t == ACTION_RESTRICT_ACCOUNT:
        key = "chat_muted_until" if eff.get("scope") == "chat" else "moderation_suspended_until"
        await db.users.update_one({"user_id": uid}, {"$unset": {key: ""}})
    elif t == ACTION_SUSPEND:
        await db.users.update_one({"user_id": uid}, {"$unset": {"moderation_suspended": "", "moderation_decision_id": ""}})
    elif t == ACTION_WARN:
        await db.users.update_one({"user_id": uid, "moderation_warning_count": {"$gt": 0}}, {"$inc": {"moderation_warning_count": -1}})


# ------------------------------------------------------------------------------------------------ decizii + Art. 17
def _redress(decided_at: datetime) -> dict:
    ci = contact_info()
    return {"internal_complaint": {"available": True, "deadline": add_months(decided_at, COMPLAINT_PERIOD_MONTHS),
                                   "months": COMPLAINT_PERIOD_MONTHS, "how": "app:/moderation/decisions"},
            "out_of_court_settlement": {"bodies": ci["out_of_court_bodies"], "configured": bool(ci["out_of_court_bodies"])},
            "judicial_redress": True}


def build_statement(decision: dict, report: dict, evidence: dict, tos_ref: str) -> dict:
    basis_illegal = decision["basis"]["type"] == "illegal_content"
    sor = {
        "sor_id": new_id("sor_"), "decision_id": decision["decision_id"], "report_id": decision["report_id"],
        "affected_user_id": decision["affected_user_id"], "issued_at": decision["created_at"],
        "decision": {"action": decision["action"], "scope": decision.get("scope"), "restriction_until": decision.get("restriction_until"),
                     "territorial_scope": "platform_wide", "permanent": decision["action"] == ACTION_SUSPEND},
        "content": {"target_type": decision["target_type"], "target_id": decision["target_id"], "summary": _summary(evidence)},
        "facts_and_circumstances": decision["reasoning"],
        "basis": {**decision["basis"], "tos_document": tos_ref},
        "source": {"type": {KIND_NOTICE: "notice", KIND_OWN: "own_initiative", KIND_ORDER: "authority_order"}.get(report.get("report_kind"), "report"),
                   "notice_received": report.get("report_kind") == KIND_NOTICE},   # identitatea notificatorului NU se include (Art. 17(3)(b))
        "initiated_by": {KIND_NOTICE: "user_notice", KIND_OWN: "astran", KIND_ORDER: "authority_order"}.get(report.get("report_kind"), "user_report"),
        "order_reference": report.get("order_ref") if report.get("report_kind") == KIND_ORDER else None,
        "automated_means": {"used_for_detection": bool(decision.get("automated_detection")), "used_for_decision": False},
        "redress": _redress(decision["created_at"]),
        "illegal_content_decision": basis_illegal,
        "retention": {"policy": os.environ.get("MODERATION_RETENTION_POLICY") or "NOT CONFIGURED (see MODERATION_RETENTION_POLICY)"},
    }
    sor["sha256"] = hashlib.sha256(json.dumps(sor, sort_keys=True, default=str).encode()).hexdigest()
    return sor


def notifier_outcome(d: dict) -> dict:
    """Ce primeste NOTIFICATORUL (Art. 16(5)): rezultatul + motivare destinata lui; nu detaliile sanctiunii altui utilizator."""
    return {"decision_id": d["decision_id"], "report_id": d["report_id"], "decided_at": d["created_at"],
            "outcome": "action_taken" if d["action"] not in (ACTION_NO_ACTION, ACTION_CLOSE) else d["action"],
            "reasoning": d.get("notifier_reasoning") or "See the outcome above.", "status": d["status"],
            "redress": _redress(d["created_at"])}


async def decide(db, actor: dict, report_id: str, body: dict) -> dict:
    role = role_of(actor)
    if not role:
        raise ModerationError(403, "forbidden")
    action = body.get("action")
    if action not in ACTIONS:
        raise ModerationError(422, "action_invalid")
    if action in ADMIN_ONLY_ACTIONS and role != "admin":
        raise ModerationError(403, "admin_required", "permanent suspension requires an administrator")
    reasoning = clean_text(body.get("reasoning"), field="reasoning", min_len=10)
    r = await db.reports.find_one({"report_id": report_id}, {"_id": 0})
    if not r:
        raise ModerationError(404, "not_found")
    if norm_status(r.get("status")) in (STATUS_RESOLVED, STATUS_REJECTED):
        raise ModerationError(409, "report_closed")

    kind = r.get("report_kind") or KIND_RULE
    uid = r.get("reported_user_id")
    params: dict = {}
    basis: dict = {"type": "terms", "legal_ground": None, "tos_provision": None, "tos_version": None}
    if action in (ACTION_REMOVE_ILLEGAL,):
        basis = {"type": "illegal_content", "tos_provision": clean_text(body.get("tos_provision"), field="tos_provision", max_len=300),
                 "legal_ground": clean_text(body.get("legal_ground"), field="legal_ground", min_len=3, max_len=500), "tos_version": None}
    elif action not in (ACTION_NO_ACTION, ACTION_CLOSE):
        basis = {"type": "terms", "legal_ground": clean_text(body.get("legal_ground"), field="legal_ground", max_len=500),
                 "tos_provision": clean_text(body.get("tos_provision"), field="tos_provision", min_len=2, max_len=300), "tos_version": None}
    if action in ACCOUNT_ACTIONS:
        if not uid:
            raise ModerationError(422, "no_reported_user")
        tgt = await db.users.find_one({"user_id": uid}, {"_id": 0, "is_platform_admin": 1, "is_platform_owner": 1, "is_moderator": 1})
        if tgt is None:   # (NU `not tgt`: proiectia poate intoarce {} pentru un utilizator existent)
            raise ModerationError(404, "user_not_found")
        if uid == actor["user_id"] or tgt.get("is_platform_admin") or tgt.get("is_platform_owner") or tgt.get("is_moderator"):
            raise ModerationError(403, "target_protected", "cannot act against staff accounts or yourself")
    if action in CONTENT_ACTIONS and r["target_type"] == "user":
        raise ModerationError(422, "content_action_not_applicable", "use an account-level action for user reports")
    if action == ACTION_RESTRICT_ACCOUNT:
        days, scope = body.get("days"), (body.get("scope") or "full")
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365 or scope not in ("chat", "full"):
            raise ModerationError(422, "restriction_invalid", "days (1-365) and scope (chat|full) are required")
        params = {"days": days, "scope": scope}
    if action in CONTENT_ACTIONS and uid and uid == actor["user_id"]:
        raise ModerationError(403, "target_protected")

    # revendicare atomica: un singur moderator decide
    claim = await db.reports.update_one({"report_id": report_id, "status": {"$in": list(OPEN_STATUSES)}, "deciding": {"$ne": True}},
                                        {"$set": {"deciding": True}})
    if not claim.matched_count:
        raise ModerationError(409, "decision_in_progress")
    try:
        from . import legal_docs as LD
        try:
            reg = LD.get_registry()
            tos_ver = reg.current_version("terms_of_service")
        except Exception:  # noqa: BLE001
            tos_ver = None
        basis["tos_version"] = tos_ver
        tos_ref = f"terms_of_service@{tos_ver}" if tos_ver else "terms_of_service@unknown"
        now = now_utc()
        decision = {
            "decision_id": new_id("dec_"), "report_id": report_id, "moderator_id": actor["user_id"], "moderator_role": role,
            "action": action, "scope": params.get("scope"), "restriction_until": (now + timedelta(days=params["days"])) if params else None,
            "target_type": r["target_type"], "target_id": r["target_id"],
            "affected_user_id": uid if action not in (ACTION_NO_ACTION, ACTION_CLOSE) else None,
            "notifier_id": r.get("reporter_id") if kind == KIND_NOTICE else None,
            "reasoning": reasoning, "notifier_reasoning": clean_text(body.get("notifier_reasoning"), field="notifier_reasoning", max_len=2000),
            "basis": basis, "automated_detection": bool(body.get("automated_detection")), "automated_decision": False,
            "source": KIND_TO_SOURCE.get(kind, SRC_TERMS_REPORT), "order_ref": r.get("order_ref") if kind == KIND_ORDER else None,
            "notification_withheld": body.get("_notify_affected") is False,
            "supersedes": r.get("reopened_from_decision"), "status": DEC_APPLYING, "created_at": now, "effect": None, "statement_id": None}
        await db.moderation_decisions.insert_one(dict(decision))
        prev_status = norm_status(r.get("status"))
        sor = None
        try:
            if action not in (ACTION_NO_ACTION, ACTION_CLOSE):
                decision["effect"] = await _apply_effect(db, action, r, decision, params)
            if action in SOR_ACTIONS:
                ev = (await db.report_evidence.find_one({"report_id": report_id}, {"_id": 0}) or {}).get("content") or r.get("context") or {}
                sor = build_statement(decision, r, ev, tos_ref)
                await db.statements_of_reasons.insert_one(dict(sor))
                decision["statement_id"] = sor["sor_id"]
            await db.moderation_decisions.update_one({"decision_id": decision["decision_id"]}, {"$set": {
                "status": DEC_IN_EFFECT, "effect": decision["effect"], "statement_id": decision["statement_id"]}})
            decision["status"] = DEC_IN_EFFECT
        except Exception as e:
            await db.moderation_decisions.update_one({"decision_id": decision["decision_id"]}, {"$set": {"status": DEC_FAILED}})
            await audit(db, event_type="decision_failed", actor_id=actor["user_id"], actor_role=role, report_id=report_id,
                        decision_id=decision["decision_id"], action=action, details={"error": type(e).__name__})
            raise
        new_status = STATUS_REJECTED if action == ACTION_NO_ACTION else STATUS_RESOLVED
        await db.reports.update_one({"report_id": report_id}, {"$set": {"status": new_status, "updated_at": now,
                                                                         "reviewed_at": r.get("reviewed_at") or now,
                                                                         "decided_at": now, "decision_id": decision["decision_id"]},
                                                              "$unset": {"reopened_from_decision": ""}})
        sent: list[str] = []
        if decision["affected_user_id"] and not decision["notification_withheld"]:
            kindn = "moderation_decision" if sor else "moderation_warning"
            n = await notify(db, decision["affected_user_id"], kindn, ref={"decision_id": decision["decision_id"]},
                             message="A moderation decision affects your account or content. Open it to read the statement of reasons."
                             if sor else "You received a warning from the moderation team.")
            sent.append(n["notification_id"])
            if sor:   # Art. 17: si pe email, daca exista adresa; livrarea e urmarita si reincercata (dsa_delivery), nu marcata ca trimisa pe ghicite
                from . import dsa_delivery as DL
                await DL.enqueue(db, kind="statement_of_reasons", dedupe_key=f"sor:{sor['sor_id']}", recipient={"user_id": decision["affected_user_id"]},
                                 ref={"sor_id": sor["sor_id"], "decision_id": decision["decision_id"]})
        if sor:
            from . import dsa_tdb as TDB     # Art. 24(5): se pune in coada DOAR daca statutul juridic o cere (vezi dsa_applicability)
            await TDB.maybe_enqueue(db, sor, decision)
        if r.get("reporter_id"):
            n = await notify(db, r["reporter_id"], "notice_outcome" if kind == KIND_NOTICE else "report_outcome",
                             ref={"report_id": report_id, "decision_id": decision["decision_id"]},
                             message="A decision was taken on your notice." if kind == KIND_NOTICE else "Your report was reviewed.")
            sent.append(n["notification_id"])
        if kind == KIND_NOTICE and not r.get("reporter_id") and (r.get("notice") or {}).get("reporter_email"):
            from . import dsa_delivery as DL     # notificator neinregistrat: Art. 16(5) - rezultatul se comunica pe email
            await DL.enqueue(db, kind="notice_outcome", dedupe_key=f"outcome:{decision['decision_id']}", recipient={"report_id": report_id},
                             ref={"report_id": report_id, "decision_id": decision["decision_id"]})
        await audit(db, event_type="decision_made", actor_id=actor["user_id"], actor_role=role, report_id=report_id,
                    decision_id=decision["decision_id"], previous_status=prev_status, new_status=new_status, action=action,
                    reasoning=reasoning, details={"statement_id": decision["statement_id"], "notifications": sent,
                                                  "basis": basis["type"], "affected_user_id": decision["affected_user_id"]})
        await db.moderation_decisions.update_one({"decision_id": decision["decision_id"]}, {"$set": {"notifications_sent": sent}})
        decision.pop("_id", None)
        decision["notifications_sent"] = sent
        return {"decision": decision, "statement_of_reasons": sor, "report_status": new_status}
    finally:
        await db.reports.update_one({"report_id": report_id}, {"$unset": {"deciding": ""}})


async def add_correction(db, actor: dict, decision_id: str, text: Any) -> dict:
    """Corectie la o decizie: eveniment NOU (nu modifica decizia sau expunerea de motive originala)."""
    text = clean_text(text, field="correction", min_len=10)
    d = await db.moderation_decisions.find_one({"decision_id": decision_id}, {"_id": 0})
    if not d:
        raise ModerationError(404, "not_found")
    c = {"correction_id": new_id("cor_"), "decision_id": decision_id, "author_id": actor["user_id"], "text": text, "created_at": now_utc()}
    await db.moderation_corrections.insert_one(c)
    c.pop("_id", None)
    await audit(db, event_type="decision_correction", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                decision_id=decision_id, action="correction", reasoning=text)
    if d.get("affected_user_id"):
        await notify(db, d["affected_user_id"], "decision_correction", ref={"decision_id": decision_id},
                     message="A correction was added to a decision concerning you.")
    return c


# ------------------------------------------------------------------------------------------------ vedere utilizator: decizii + SoR
async def my_decisions(db, user_id: str) -> list[dict]:
    rows = await db.moderation_decisions.find({"affected_user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(200).to_list(200)
    return [user_decision_view(d) for d in rows]


def user_decision_view(d: dict) -> dict:
    """Fara moderator_id, fara note interne, fara identitatea notificatorului."""
    return {"decision_id": d["decision_id"], "action": d["action"], "created_at": d["created_at"], "status": d["status"],
            "target_type": d["target_type"], "target_id": d["target_id"], "has_statement": bool(d.get("statement_id")),
            "restriction_until": d.get("restriction_until"), "supersedes": d.get("supersedes"),
            "complaint_deadline": add_months(aware(d["created_at"]), COMPLAINT_PERIOD_MONTHS) if aware(d.get("created_at")) else None}


async def my_decision(db, user_id: str, decision_id: str) -> dict:
    d = await db.moderation_decisions.find_one({"decision_id": decision_id}, {"_id": 0})
    if not d or user_id not in (d.get("affected_user_id"), d.get("notifier_id")):
        raise ModerationError(404, "not_found")                       # IDOR: nicio diferenta intre "nu exista" si "nu e al tau"
    corr = await db.moderation_corrections.find({"decision_id": decision_id}, {"_id": 0, "author_id": 0}).sort("created_at", 1).to_list(50)
    appeal = await db.moderation_appeals.find_one({"decision_id": decision_id, "user_id": user_id}, {"_id": 0})
    if d.get("affected_user_id") == user_id:
        sor = await db.statements_of_reasons.find_one({"decision_id": decision_id}, {"_id": 0})
        from . import dsa_delivery as DL
        delivery = await DL.public_status(db, f"sor:{sor['sor_id']}") if sor else None   # doar canal+stare; fara eroare tehnica / adresa
        return {"role": "affected", "decision": user_decision_view(d), "statement_of_reasons": sor, "corrections": corr,
                "delivery": delivery, "appeal": _appeal_view(appeal) if appeal else None, "redress": _redress(d["created_at"])}
    return {"role": "notifier", "outcome": notifier_outcome(d), "corrections": corr,
            "appeal": _appeal_view(appeal) if appeal else None}


# ------------------------------------------------------------------------------------------------ plangeri Art. 20
def _appeal_view(a: dict) -> dict:
    return {k: a.get(k) for k in ("appeal_id", "decision_id", "status", "grounds", "evidence_text", "evidence_urls", "created_at",
                                  "review_started_at", "resolved_at", "outcome", "outcome_reasoning", "modification", "new_decision_id")}


async def submit_appeal(db, user: dict, decision_id: str, *, grounds: Any, evidence_text: Any = None, evidence_urls: Any = None) -> dict:
    uid = user["user_id"]
    d = await db.moderation_decisions.find_one({"decision_id": decision_id}, {"_id": 0})
    if not d or uid not in (d.get("affected_user_id"), d.get("notifier_id")) or d["status"] == DEC_FAILED:
        raise ModerationError(404, "not_found")
    if d["status"] in (DEC_REVERSED, DEC_MODIFIED) and d.get("affected_user_id") == uid:
        raise ModerationError(409, "decision_not_appealable", "this decision was already reversed or replaced")
    grounds = clean_text(grounds, field="grounds", min_len=20)
    evidence_text = clean_text(evidence_text, field="evidence_text", max_len=MAX_TEXT)
    urls = clean_urls(evidence_urls)
    decided = aware(d["created_at"])
    if now_utc() > add_months(decided, COMPLAINT_PERIOD_MONTHS):
        raise ModerationError(409, "complaint_period_expired", "the 6-month complaint period has ended")
    if await db.moderation_appeals.find_one({"decision_id": decision_id, "user_id": uid}, {"_id": 1}):
        raise ModerationError(409, "appeal_exists")
    a = {"appeal_id": new_id("apl_"), "decision_id": decision_id, "report_id": d["report_id"], "user_id": uid,
         "as_role": "affected" if d.get("affected_user_id") == uid else "notifier", "status": APPEAL_RECEIVED,
         "grounds": grounds, "evidence_text": evidence_text, "evidence_urls": urls, "created_at": now_utc(),
         "reviewer_id": None, "resolved_at": None, "outcome": None, "outcome_reasoning": None}
    try:
        await db.moderation_appeals.insert_one(dict(a))
    except Exception as e:  # noqa: BLE001
        if "Duplicate" in type(e).__name__ or "E11000" in str(e):
            raise ModerationError(409, "appeal_exists")
        raise
    await audit(db, event_type="appeal_submitted", actor_id=uid, actor_role="appellant", report_id=d["report_id"], decision_id=decision_id,
                appeal_id=a["appeal_id"], action="submit_appeal", new_status=APPEAL_RECEIVED, details={"as": a["as_role"]})
    await notify(db, uid, "appeal_received", ref={"appeal_id": a["appeal_id"]}, message="We received your complaint and it will be reviewed by a person.")
    return _appeal_view(a)


async def my_appeals(db, user_id: str) -> list[dict]:
    rows = await db.moderation_appeals.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1).limit(200).to_list(200)
    return [_appeal_view(a) for a in rows]


async def my_appeal(db, user_id: str, appeal_id: str) -> dict:
    a = await db.moderation_appeals.find_one({"appeal_id": appeal_id, "user_id": user_id}, {"_id": 0})
    if not a:
        raise ModerationError(404, "not_found")
    return _appeal_view(a)


def internal_target_days(var: str, env: Optional[dict] = None) -> Optional[int]:
    """Obiectiv INTERN optional (zile). Art. 20(4) NU fixeaza un termen universal ("in timp util, in mod nediscriminatoriu, diligent si
    nearbitrar") si nici Art. 16(6) pentru notificari: nu se inventeaza niciun termen. Daca variabila nu e setata, nu exista obiectiv."""
    raw = (os.environ if env is None else env).get(var)
    try:
        v = int(raw) if raw not in (None, "") else None
    except ValueError:
        return None
    return v if v and v > 0 else None


def appeal_timing(a: dict, now: Optional[datetime] = None, env: Optional[dict] = None) -> dict:
    now = now or now_utc()
    created, resolved = aware(a.get("created_at")), aware(a.get("resolved_at"))
    end = resolved or now
    age_days = round(((end - created).total_seconds() / 86400), 2) if created else None
    target = internal_target_days("ASTRAN_APPEAL_TARGET_DAYS", env)
    return {"submitted_at": created, "review_started_at": aware(a.get("review_started_at")), "resolved_at": resolved, "age_days": age_days,
            "internal_target_days": target, "internal_target_exceeded": (age_days is not None and target is not None and age_days > target),
            "legal_deadline": None,    # Art. 20 nu prevede un numar fix de zile; vezi comentariul de mai sus
            "legal_standard": "timely, non-discriminatory, diligent and non-arbitrary (Art. 20(4)); outcome without undue delay (Art. 20(5))"}


async def list_appeals(db, *, status: Optional[str] = None, limit: int = 100) -> list[dict]:
    flt = {"status": status} if status else {}
    rows = await db.moderation_appeals.find(flt, {"_id": 0}).sort("created_at", 1).limit(limit).to_list(limit)   # cele mai vechi primele: ordine de tratare
    for a in rows:
        a["timing"] = appeal_timing(a)
    return rows


async def appeal_detail(db, appeal_id: str) -> dict:
    a = await db.moderation_appeals.find_one({"appeal_id": appeal_id}, {"_id": 0})
    if not a:
        raise ModerationError(404, "not_found")
    d = await db.moderation_decisions.find_one({"decision_id": a["decision_id"]}, {"_id": 0})
    sor = await db.statements_of_reasons.find_one({"decision_id": a["decision_id"]}, {"_id": 0})
    a["timing"] = appeal_timing(a)
    return {"appeal": a, "decision": d, "statement_of_reasons": sor, "history": await audit_history(db, decision_id=a["decision_id"])}


def _check_independent(actor: dict, decision: dict) -> None:
    if not role_of(actor):
        raise ModerationError(403, "forbidden")
    if decision.get("moderator_id") == actor["user_id"]:
        raise ModerationError(403, "reviewer_not_independent", "an appeal must be reviewed by someone other than the original decision-maker")


async def start_appeal_review(db, actor: dict, appeal_id: str) -> dict:
    a = await db.moderation_appeals.find_one({"appeal_id": appeal_id}, {"_id": 0})
    if not a:
        raise ModerationError(404, "not_found")
    d = await db.moderation_decisions.find_one({"decision_id": a["decision_id"]}, {"_id": 0})
    _check_independent(actor, d)
    res = await db.moderation_appeals.update_one({"appeal_id": appeal_id, "status": APPEAL_RECEIVED},
                                                 {"$set": {"status": APPEAL_REVIEW, "reviewer_id": actor["user_id"], "review_started_at": now_utc()}})
    if not res.matched_count:
        raise ModerationError(409, "appeal_not_receivable")
    await audit(db, event_type="appeal_review_started", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                decision_id=d["decision_id"], appeal_id=appeal_id, previous_status=APPEAL_RECEIVED, new_status=APPEAL_REVIEW, action="start_review")
    return {"appeal_id": appeal_id, "status": APPEAL_REVIEW}


def _validate_modification(a: dict, d: dict, modification: Any) -> dict:
    """`modify` e permis DOAR pentru restrictii/suspendari de cont ale persoanei afectate (se inlocuieste cu o restrictie temporara).
    Pentru continut (eliminare/restrictionare) alternativele sunt `maintain` / `reverse` - limitare documentata."""
    if a["as_role"] != "affected" or d["status"] != DEC_IN_EFFECT or d["action"] not in (ACTION_RESTRICT_ACCOUNT, ACTION_SUSPEND):
        raise ModerationError(422, "modify_not_supported", "only account restrictions/suspensions in effect can be modified")
    m = modification if isinstance(modification, dict) else {}
    days, scope = m.get("days"), (m.get("scope") or "full")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365 or scope not in ("chat", "full"):
        raise ModerationError(422, "restriction_invalid", "days (1-365) and scope (chat|full) are required for a modification")
    return {"days": days, "scope": scope}


async def _apply_modification(db, actor: dict, a: dict, d: dict, reasoning: str, params: dict) -> dict:
    """Inlocuieste decizia cu una NOUA (restrictie temporara), cu propria expunere de motive; originalul ramane in istoric."""
    r = await db.reports.find_one({"report_id": d["report_id"]}, {"_id": 0}) or {"report_id": d["report_id"], "target_type": d["target_type"],
                                                                                   "target_id": d["target_id"]}
    old_sor = await db.statements_of_reasons.find_one({"decision_id": d["decision_id"]}, {"_id": 0}) or {}
    tos_ref = (old_sor.get("basis") or {}).get("tos_document") or "terms_of_service@unknown"
    await _revert_effect(db, d)
    now = now_utc()
    new = {"decision_id": new_id("dec_"), "report_id": d["report_id"], "moderator_id": actor["user_id"], "moderator_role": role_of(actor),
           "action": ACTION_RESTRICT_ACCOUNT, "scope": params["scope"], "restriction_until": now + timedelta(days=params["days"]),
           "target_type": d["target_type"], "target_id": d["target_id"], "affected_user_id": d["affected_user_id"], "notifier_id": None,
           "reasoning": f"Modified after complaint {a['appeal_id']}: {reasoning}", "notifier_reasoning": None,
           "basis": dict(d.get("basis") or {}), "automated_detection": False, "automated_decision": False, "source": d.get("source"),
           "order_ref": d.get("order_ref"), "supersedes": d["decision_id"], "appeal_id": a["appeal_id"], "status": DEC_APPLYING,
           "created_at": now, "effect": None, "statement_id": None}
    await db.moderation_decisions.insert_one(dict(new))
    try:
        new["effect"] = await _apply_effect(db, ACTION_RESTRICT_ACCOUNT, r, new, params)
        ev = (await db.report_evidence.find_one({"report_id": d["report_id"]}, {"_id": 0}) or {}).get("content") or {}
        sor = build_statement(new, r, ev, tos_ref)
        await db.statements_of_reasons.insert_one(dict(sor))
        new["statement_id"] = sor["sor_id"]
        await db.moderation_decisions.update_one({"decision_id": new["decision_id"]}, {"$set": {
            "status": DEC_IN_EFFECT, "effect": new["effect"], "statement_id": sor["sor_id"]}})
    except Exception as e:  # noqa: BLE001
        await db.moderation_decisions.update_one({"decision_id": new["decision_id"]}, {"$set": {"status": DEC_FAILED}})
        await audit(db, event_type="decision_failed", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                    decision_id=new["decision_id"], appeal_id=a["appeal_id"], action=ACTION_RESTRICT_ACCOUNT, details={"error": type(e).__name__})
        raise
    await db.moderation_decisions.update_one({"decision_id": d["decision_id"]}, {"$set": {
        "status": DEC_MODIFIED, "modified_at": now, "modified_by_appeal": a["appeal_id"], "replaced_by": new["decision_id"]}})
    await audit(db, event_type="decision_modified", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                decision_id=d["decision_id"], appeal_id=a["appeal_id"], previous_status=DEC_IN_EFFECT, new_status=DEC_MODIFIED, action="modify",
                reasoning=reasoning, details={"new_decision_id": new["decision_id"], "statement_id": sor["sor_id"], "days": params["days"],
                                              "scope": params["scope"]})
    n = await notify(db, d["affected_user_id"], "moderation_decision", ref={"decision_id": new["decision_id"]},
                     message="Your restriction was modified after your complaint. Open the new decision to read its statement of reasons.")
    from . import dsa_delivery as DL
    await DL.enqueue(db, kind="statement_of_reasons", dedupe_key=f"sor:{sor['sor_id']}", recipient={"user_id": d["affected_user_id"]},
                     ref={"sor_id": sor["sor_id"], "decision_id": new["decision_id"]})
    from . import dsa_tdb as TDB
    await TDB.maybe_enqueue(db, sor, new)
    return {"new_decision_id": new["decision_id"], "notification_id": n["notification_id"]}


async def resolve_appeal(db, actor: dict, appeal_id: str, *, outcome: str, reasoning: Any, modification: Any = None) -> dict:
    """Art. 20(4): decizia o ia un OM calificat (rol de personal), independent de autorul deciziei initiale - niciodata automat.
    Rezultate: `maintain` (confirma), `reverse` (anuleaza), `modify` (inlocuieste cu o restrictie mai usoara, doar pentru conturi)."""
    if outcome not in ("maintain", "reverse", "modify"):
        raise ModerationError(422, "outcome_invalid")
    reasoning = clean_text(reasoning, field="reasoning", min_len=20)
    a = await db.moderation_appeals.find_one({"appeal_id": appeal_id}, {"_id": 0})
    if not a:
        raise ModerationError(404, "not_found")
    d = await db.moderation_decisions.find_one({"decision_id": a["decision_id"]}, {"_id": 0})
    _check_independent(actor, d)
    if a["status"] == APPEAL_REVIEW and a.get("reviewer_id") not in (None, actor["user_id"]):
        raise ModerationError(403, "assigned_to_other_reviewer")
    mod_params = _validate_modification(a, d, modification) if outcome == "modify" else None   # inainte de revendicare: nu lasam o plangere "inchisa" fara efect
    final = {"reverse": APPEAL_REVERSED, "modify": APPEAL_MODIFIED}.get(outcome, APPEAL_MAINTAINED)
    claim = await db.moderation_appeals.update_one({"appeal_id": appeal_id, "status": {"$in": list(APPEAL_OPEN)}},
                                                   {"$set": {"status": final, "reviewer_id": actor["user_id"], "outcome": outcome,
                                                             "outcome_reasoning": reasoning, "resolved_at": now_utc(),
                                                             "reviewed_by_human": True, "modification": mod_params}})
    if not claim.matched_count:
        raise ModerationError(409, "appeal_already_resolved")
    if outcome == "modify":
        res = await _apply_modification(db, actor, a, d, reasoning, mod_params)
        await db.moderation_appeals.update_one({"appeal_id": appeal_id}, {"$set": {"new_decision_id": res["new_decision_id"]}})
    elif outcome == "reverse":
        if a["as_role"] == "affected" and d["status"] == DEC_IN_EFFECT:
            await _revert_effect(db, d)
            await db.moderation_decisions.update_one({"decision_id": d["decision_id"]}, {"$set": {"status": DEC_REVERSED, "reversed_at": now_utc(),
                                                                                                "reversed_by_appeal": appeal_id}})
            await audit(db, event_type="decision_reversed", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                        decision_id=d["decision_id"], appeal_id=appeal_id, previous_status=DEC_IN_EFFECT, new_status=DEC_REVERSED,
                        action="reverse", reasoning=reasoning)
        elif a["as_role"] == "notifier":
            # reversarea unei respingeri a notificarii: raportul se redeschide pentru o decizie NOUA (care o inlocuieste pe cea veche)
            await db.moderation_decisions.update_one({"decision_id": d["decision_id"]}, {"$set": {"status": DEC_REVERSED, "reversed_at": now_utc(),
                                                                                                "reversed_by_appeal": appeal_id}})
            await db.reports.update_one({"report_id": d["report_id"]}, {"$set": {"status": STATUS_REVIEW, "reopened_from_decision": d["decision_id"],
                                                                                 "updated_at": now_utc()}})
            await audit(db, event_type="report_reopened", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                        decision_id=d["decision_id"], appeal_id=appeal_id, previous_status=STATUS_REJECTED, new_status=STATUS_REVIEW,
                        action="reopen_after_appeal", reasoning=reasoning)
    await audit(db, event_type="appeal_resolved", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=d["report_id"],
                decision_id=d["decision_id"], appeal_id=appeal_id, previous_status=a["status"], new_status=final, action="resolve_appeal",
                reasoning=reasoning, outcome=outcome)
    await notify(db, a["user_id"], "appeal_outcome", ref={"appeal_id": appeal_id, "decision_id": d["decision_id"]},
                 message="Your complaint was decided. Open it to read the decision and its reasoning.")
    from . import dsa_delivery as DL     # Art. 20(5): rezultatul motivat + informatii despre solutionarea extrajudiciara, fara intarziere nejustificata
    await DL.enqueue(db, kind="appeal_outcome", dedupe_key=f"appeal:{appeal_id}", recipient={"user_id": a["user_id"]},
                     ref={"appeal_id": appeal_id, "decision_id": d["decision_id"]})
    return {"appeal_id": appeal_id, "status": final, "outcome": outcome}


# ------------------------------------------------------------------------------------------------ roluri + contact
async def set_moderator(db, actor: dict, user_id: str, enabled: bool) -> dict:
    if role_of(actor) != "admin":
        raise ModerationError(403, "admin_required")
    res = await db.users.update_one({"user_id": user_id}, {"$set": {"is_moderator": bool(enabled)}})
    if not res.matched_count:
        raise ModerationError(404, "user_not_found")
    await audit(db, event_type="moderator_role_changed", actor_id=actor["user_id"], actor_role="admin", action="set_moderator",
                new_status="enabled" if enabled else "disabled", details={"user_id": user_id})
    return {"user_id": user_id, "is_moderator": bool(enabled)}


STAFF_FLAGS = {"legal_officer": "is_legal_officer", "trusted_flagger": "is_trusted_flagger"}


async def set_flag(db, actor: dict, user_id: str, flag: str, enabled: bool) -> dict:
    """Roluri/statute suplimentare acordate DOAR de un platform admin: `legal_officer` (ordine Art. 9/10) si `trusted_flagger`
    (statut atribuit de coordonatorul serviciilor digitale, Art. 22 - Astran doar il inregistreaza dupa ce este atribuit)."""
    if role_of(actor) != "admin":
        raise ModerationError(403, "admin_required")
    if flag not in STAFF_FLAGS:
        raise ModerationError(422, "flag_invalid")
    res = await db.users.update_one({"user_id": user_id}, {"$set": {STAFF_FLAGS[flag]: bool(enabled)}})
    if not res.matched_count:
        raise ModerationError(404, "user_not_found")
    await audit(db, event_type="order_legal_role_changed" if flag == "legal_officer" else "moderator_role_changed", actor_id=actor["user_id"],
                actor_role="admin", action=f"set_{flag}", new_status="enabled" if enabled else "disabled", details={"user_id": user_id})
    return {"user_id": user_id, flag: bool(enabled)}


async def open_own_initiative_case(db, actor: dict, *, target_type: str, target_id: str, category: str, summary: Any,
                                   automated_detection: bool = False, kind: str = KIND_OWN, order_ref: Optional[str] = None) -> dict:
    """Caz deschis de Astran (personal) din proprie initiativa, fara notificator. Decizia se ia apoi prin decide(), deci primeste
    aceeasi expunere de motive, notificare, audit si drept de plangere ca orice alta decizie. Nu clasifica nimic ca ilegal."""
    if not role_of(actor):
        raise ModerationError(403, "forbidden")
    if target_type not in ("user", "game", "message"):
        raise ModerationError(422, "target_invalid")
    summary = clean_text(summary, field="summary", min_len=10, max_len=MAX_TEXT)
    if category not in CATEGORIES:
        raise ModerationError(422, "category_invalid")
    rid = new_id("rep_")
    doc = {"report_id": rid, "reporter_id": None, "target_type": target_type, "target_id": target_id, "reason": "own_initiative",
           "details": summary, "status": STATUS_PENDING, "created_at": now_utc(), "opened_by": actor["user_id"],
           "report_kind": kind, "category": category, "automated_detection": bool(automated_detection)}
    if kind == KIND_ORDER:
        doc["order_ref"] = order_ref       # doar referinta ordinului; continutul ordinului ramane in authority_orders (acces restrictionat)
    ev = await capture_evidence(db, target_type, target_id)
    if not ev:
        raise ModerationError(404, "target_not_found")
    doc.update({"reported_user_id": await resolve_reported_user(db, target_type, target_id), "assigned_to": None, "reviewed_at": None})
    await db.reports.insert_one(doc)
    await db.report_evidence.insert_one({"report_id": rid, "captured_at": now_utc(), "content": ev,
                                         "sha256": hashlib.sha256(json.dumps(ev, sort_keys=True, default=str).encode()).hexdigest()})
    await audit(db, event_type="report_received", actor_id=actor["user_id"], actor_role=role_of(actor), report_id=rid, new_status=STATUS_PENDING,
                action="open_own_initiative" if kind == KIND_OWN else "open_order_case", details={"kind": kind, "category": category, "target_type": target_type,
                                                       "automated_detection": bool(automated_detection)})
    return {"report_id": rid, "status": STATUS_PENDING, "kind": kind}


CONTACT_CHANNELS = ("user", "authority", "commission_or_dsc", "trusted_flagger", "other")


async def submit_contact_message(db, *, channel: Any, name: Any, email: Any, subject: Any, message: Any, user_id: Optional[str] = None) -> dict:
    if channel not in CONTACT_CHANNELS:
        raise ModerationError(422, "channel_invalid")
    email = clean_text(email, field="email", required=True, max_len=320)
    if not _EMAIL_RE.match(email):
        raise ModerationError(422, "email_invalid")
    m = {"message_id": new_id("msg_"), "channel": channel, "name": clean_text(name, field="name", max_len=120),
         "email": email, "subject": clean_text(subject, field="subject", min_len=3, max_len=200),
         "message": clean_text(message, field="message", min_len=10, max_len=MAX_TEXT), "user_id": user_id,
         "created_at": now_utc(), "status": "new"}
    await db.contact_messages.insert_one(dict(m))
    return {"message_id": m["message_id"], "received_at": m["created_at"]}


async def list_contact_messages(db, limit: int = 100) -> list[dict]:
    return await db.contact_messages.find({}, {"_id": 0}).sort("created_at", -1).limit(limit).to_list(limit)


# ------------------------------------------------------------------------------------------------ referinte de continut (URL / ID / cont)
_ID = r"[A-Za-z0-9_-]{1,64}"
_REF_PATTERNS = (
    (re.compile(rf"^astran://(game|user|message)/({_ID})/?$"), None),
    (re.compile(rf"^https://[A-Za-z0-9.-]{{3,253}}(?::\d{{2,5}})?/(?:game|games|play)/({_ID})/?(?:[?#].*)?$"), "game"),
    (re.compile(rf"^https://[A-Za-z0-9.-]{{3,253}}(?::\d{{2,5}})?/(?:user|users|profile)/({_ID})/?(?:[?#].*)?$"), "user"),
    (re.compile(rf"^https://[A-Za-z0-9.-]{{3,253}}(?::\d{{2,5}})?/(?:message|messages)/({_ID})/?(?:[?#].*)?$"), "message"),
)


def parse_content_reference(ref: Any) -> Optional[tuple]:
    """URL de continut / referinta interna -> (target_type, target_id) sau None. Fara cereri de retea (nu urmarim linkuri)."""
    if not isinstance(ref, str):
        return None
    r = ref.strip()
    if not r or len(r) > 600:
        return None
    for rx, fixed in _REF_PATTERNS:
        m = rx.match(r)
        if m:
            return (fixed, m.group(1)) if fixed else (m.group(1), m.group(2))
    return None
