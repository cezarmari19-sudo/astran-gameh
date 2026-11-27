"""Gestionarea incidentelor de securitate si a incalcarilor securitatii datelor cu caracter personal (GDPR Art. 33-34).

Principii:
  * Aici se DOCUMENTEAZA si se PREGATESTE. Nimic nu se trimite automat catre autoritatea de supraveghere (ANSPDCP) si
    nimic nu se trimite automat persoanelor vizate: sistemul pastreaza proiectul de notificare, decizia si dovada
    (data, referinta, cine a depus) introduse de personalul autorizat, care depune notificarea prin canalul oficial.
  * Termenul de 72 de ore (Art. 33(1)) curge de la `discovered_at` (momentul in care operatorul a luat cunostinta) si
    este configurabil (`incident_policy`). Notificarea tardiva cere motive scrise (Art. 33(1) ultima teza).
  * `not_notify` este permis doar daca evaluarea de risc spune ca incalcarea NU este susceptibila sa genereze un risc
    (Art. 33(1)); fiecare decizie are motive si autor.
  * Art. 34: comunicarea catre persoanele vizate e obligatorie la risc ridicat, cu exceptiile din Art. 34(3).
  * Jurnal de audit ADAUGA-DOAR, inlantuit prin hash (tamper-evident), cu un rand pentru fiecare actiune.
  * Minimizare: se tin NUMERE si categorii, nu liste de persoane afectate si nu continutul datelor compromise.
Colectii: `security_incidents`, `security_incident_audit`, `app_config` (cheia `incident_policy`).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

C_INCIDENTS = "security_incidents"
C_AUDIT = "security_incident_audit"
C_CONFIG = "app_config"
POLICY_KEY = "incident_policy"

INCIDENT_TYPES = ("unauthorized_access", "data_exfiltration", "credential_compromise", "malware_ransomware", "accidental_disclosure",
                  "loss_or_theft_of_device", "vendor_processor_breach", "service_unavailability", "other")
DATA_CATEGORIES = ("account_identifiers", "email_addresses", "password_hashes", "date_of_birth_age", "child_or_parental_data",
                   "payment_or_transaction_data", "user_content_messages", "ip_or_device_data", "moderation_or_legal_records",
                   "special_category_data", "other")
BREACH_PROPERTIES = ("confidentiality", "integrity", "availability")
SEVERITIES = ("low", "medium", "high", "critical")
RISK_LEVELS = ("unlikely", "risk", "high_risk")       # Art. 33: improbabil / risc / Art. 34: risc ridicat
STATUSES = ("open", "assessed", "authority_pending", "authority_notified", "no_notification_needed", "closed")
ART34_EXEMPTIONS = ("encryption_or_unintelligible", "subsequent_measures_remove_high_risk", "disproportionate_effort_public_communication")
CHANNELS = ("email", "in_app", "public_notice", "other")

DEFAULT_POLICY = {
    "notify_deadline_hours": 72,          # Art. 33(1)
    "due_soon_hours": 12,                 # cand se marcheaza "aproape de termen"
    "require_separate_approver": False,   # patru ochi: cel care aproba != cel care a pregatit proiectul
    "min_reason_chars": 20,
    "child_data_forces_high_severity": True,   # date de minori/parentale => severitate minima "high" la clasificare
}
_POLICY_LIMITS = {"notify_deadline_hours": (1, 720), "due_soon_hours": (0, 168), "min_reason_chars": (5, 500)}


class IncidentError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def parse_dt(raw: Any, field: str) -> datetime:
    if isinstance(raw, datetime):
        return _aware(raw)  # type: ignore[return-value]
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 40:
        raise IncidentError(422, "invalid_datetime", f"{field}: invalid ISO 8601 date-time")
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise IncidentError(422, "invalid_datetime", f"{field}: invalid ISO 8601 date-time")
    return _aware(dt)  # type: ignore[return-value]


# ----------------------------------------------------------------------------------------------- autorizare
def staff_role(user: Optional[dict]) -> Optional[str]:
    """Doar personal autorizat: platform admin, responsabil juridic (`is_legal_officer`) sau responsabil de securitate
    (`is_security_officer`). Moderatorii de continut NU au acces. Rolul se citeste din documentul incarcat din DB."""
    if not user:
        return None
    # `is True` strict: un camp scris gresit in DB ("yes", 1) NU acorda acces la datele despre incidente
    if user.get("is_platform_admin") is True:
        return "admin"
    if user.get("is_legal_officer") is True:
        return "legal"
    if user.get("is_security_officer") is True:
        return "security"
    return None


def require_staff(user: Optional[dict]) -> str:
    role = staff_role(user)
    if not role:
        raise IncidentError(403, "forbidden", "Not authorized")
    return role


def require_approver(user: Optional[dict]) -> str:
    """Aprobarea deciziei privind notificarea autoritatii: responsabilul (admin / responsabil juridic), nu orice angajat."""
    role = staff_role(user)
    if role not in ("admin", "legal"):
        raise IncidentError(403, "approver_role_required", "Only an admin or legal officer can approve")
    return role


# ----------------------------------------------------------------------------------------------- configurare
async def get_policy(db) -> dict:
    doc = await db[C_CONFIG].find_one({"key": POLICY_KEY}, {"_id": 0})
    pol = dict(DEFAULT_POLICY)
    for k, v in ((doc or {}).get("value") or {}).items():
        if k in DEFAULT_POLICY and type(v) is type(DEFAULT_POLICY[k]):
            pol[k] = v
    return pol


async def set_policy(db, actor: dict, changes: dict) -> dict:
    if staff_role(actor) != "admin":
        raise IncidentError(403, "admin_required", "Only a platform admin can change the incident policy")
    cur = await get_policy(db)
    for k, v in (changes or {}).items():
        if k not in DEFAULT_POLICY:
            raise IncidentError(422, "unknown_setting", f"Unknown setting: {k}")
        if type(v) is not type(DEFAULT_POLICY[k]):
            raise IncidentError(422, "invalid_setting", f"{k}: wrong type")
        if k in _POLICY_LIMITS and not (_POLICY_LIMITS[k][0] <= v <= _POLICY_LIMITS[k][1]):
            raise IncidentError(422, "invalid_setting", f"{k}: out of range")
        cur[k] = v
    await db[C_CONFIG].update_one({"key": POLICY_KEY}, {"$set": {"key": POLICY_KEY, "value": cur, "updated_at": now_utc(),
                                                                "updated_by": actor["user_id"]}}, upsert=True)
    return cur


# ----------------------------------------------------------------------------------------------- audit (adauga-doar)
def _audit_hash(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


async def audit(db, incident_id: str, action: str, actor: Optional[dict], details: Optional[dict] = None) -> dict:
    for _ in range(8):
        last = await db[C_AUDIT].find({"incident_id": incident_id}, {"_id": 0, "seq": 1, "hash": 1}).sort("seq", -1).limit(1).to_list(1)
        doc = {"event_id": "ia_" + uuid.uuid4().hex[:16], "incident_id": incident_id, "seq": (last[0]["seq"] + 1) if last else 1,
               "ts": now_utc(), "action": action, "actor_id": (actor or {}).get("user_id"), "actor_role": staff_role(actor),
               "details": details or {}, "prev_hash": last[0]["hash"] if last else "GENESIS"}
        doc["hash"] = _audit_hash(doc)
        try:
            await db[C_AUDIT].insert_one(doc)
            return doc
        except Exception as e:  # noqa: BLE001 - cursa pe (incident_id, seq): reincercam
            if "Duplicate" not in type(e).__name__ and "E11000" not in str(e):
                raise
    raise IncidentError(503, "audit_unavailable", "could not append audit event")


async def verify_audit_chain(db, incident_id: str) -> dict:
    rows = await db[C_AUDIT].find({"incident_id": incident_id}, {"_id": 0}).sort("seq", 1).to_list(None)
    prev = "GENESIS"
    for r in rows:
        if r["prev_hash"] != prev or _audit_hash(r) != r["hash"]:
            return {"ok": False, "broken_at_seq": r["seq"], "events": len(rows)}
        prev = r["hash"]
    return {"ok": True, "events": len(rows)}


async def ensure_indexes(db) -> None:
    await db[C_INCIDENTS].create_index("incident_id", unique=True)
    await db[C_INCIDENTS].create_index([("status", 1), ("deadline_at", 1)])
    await db[C_AUDIT].create_index([("incident_id", 1), ("seq", 1)], unique=True)
    await db[C_CONFIG].create_index("key", unique=True)


# ----------------------------------------------------------------------------------------------- validare
def _text(v: Any, field: str, *, min_len: int = 0, max_len: int = 4000, required: bool = False) -> Optional[str]:
    if v is None:
        if required:
            raise IncidentError(422, "missing_field", f"{field} is required")
        return None
    if not isinstance(v, str):
        raise IncidentError(422, "invalid_field", f"{field} must be text")
    v = v.strip()
    if (required or v) and len(v) < min_len:
        raise IncidentError(422, "too_short", f"{field} must have at least {min_len} characters")
    if len(v) > max_len:
        raise IncidentError(422, "too_long", f"{field} is too long")
    if required and not v:
        raise IncidentError(422, "missing_field", f"{field} is required")
    return v


def _enum(v: Any, allowed: tuple, field: str) -> str:
    if v not in allowed:
        raise IncidentError(422, "invalid_field", f"{field} must be one of: {', '.join(allowed)}")
    return v


def _enum_list(v: Any, allowed: tuple, field: str, *, max_items: int = 20) -> list:
    if not isinstance(v, list) or len(v) > max_items:
        raise IncidentError(422, "invalid_field", f"{field} must be a list")
    out = []
    for x in v:
        _enum(x, allowed, field)
        if x not in out:
            out.append(x)
    return out


def _count(v: Any, field: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 2_000_000_000:
        raise IncidentError(422, "invalid_field", f"{field} must be a non-negative integer")
    return v


# ----------------------------------------------------------------------------------------------- termen 72h
def deadline_status(inc: dict, now: Optional[datetime] = None, policy: Optional[dict] = None) -> dict:
    """Evidenta termenului Art. 33(1). Stari: not_assessed | not_required | pending | due_soon | overdue | met | late."""
    now = now or now_utc()
    pol = policy or DEFAULT_POLICY
    deadline = _aware(inc.get("deadline_at"))
    dec = (inc.get("authority_decision") or {}).get("decision")
    sub = (inc.get("authority_notification") or {}).get("submitted_at")
    out = {"deadline_at": deadline, "hours_remaining": None, "state": "not_assessed", "applies": None}
    if deadline is None:
        return out
    if dec == "not_notify":
        out.update(state="not_required", applies=False)
        return out
    out["applies"] = True if dec == "notify" else None
    if sub:
        out["state"] = "met" if _aware(sub) <= deadline else "late"
        return out
    remaining = (deadline - now).total_seconds() / 3600
    out["hours_remaining"] = round(remaining, 2)
    out["state"] = "overdue" if remaining < 0 else ("due_soon" if remaining <= pol["due_soon_hours"] else "pending")
    return out


def public_view(inc: dict, policy: Optional[dict] = None) -> dict:
    out = {k: v for k, v in inc.items() if k != "_id"}
    out["deadline"] = deadline_status(inc, policy=policy)
    return out


# ----------------------------------------------------------------------------------------------- ciclu de viata
async def create_incident(db, actor: dict, body: dict) -> dict:
    require_staff(actor)
    pol = await get_policy(db)
    now = now_utc()
    discovered = parse_dt(body.get("discovered_at"), "discovered_at")
    if discovered > now + timedelta(minutes=5):
        raise IncidentError(422, "invalid_datetime", "discovered_at cannot be in the future")
    occurred = parse_dt(body["occurred_at"], "occurred_at") if body.get("occurred_at") else None
    if occurred and occurred > discovered:
        raise IncidentError(422, "invalid_datetime", "occurred_at cannot be after discovered_at")
    est = body.get("affected_users_estimate")
    unknown = bool(body.get("affected_users_unknown", False))
    if est is None and not unknown:
        raise IncidentError(422, "missing_field", "affected_users_estimate or affected_users_unknown=true is required")
    inc = {
        "incident_id": "inc_" + uuid.uuid4().hex[:16],
        "reference": f"INC-{now.year}-{secrets.token_hex(3).upper()}",
        "title": _text(body.get("title"), "title", min_len=5, max_len=160, required=True),
        "description": _text(body.get("description"), "description", min_len=10, max_len=4000, required=True),
        "incident_type": _enum(body.get("incident_type"), INCIDENT_TYPES, "incident_type"),
        "breach_properties": _enum_list(body.get("breach_properties", []), BREACH_PROPERTIES, "breach_properties", max_items=3),
        "data_categories": _enum_list(body.get("data_categories", []), DATA_CATEGORIES, "data_categories"),
        "affected_users_estimate": None if est is None else _count(est, "affected_users_estimate"),
        "affected_users_unknown": unknown or est is None,
        "affected_records_estimate": _count(body["affected_records_estimate"], "affected_records_estimate") if body.get("affected_records_estimate") is not None else None,
        "discovered_at": discovered, "occurred_at": occurred,
        "deadline_at": discovered + timedelta(hours=pol["notify_deadline_hours"]),
        "deadline_hours": pol["notify_deadline_hours"],
        "status": "open", "severity": None, "risk_assessment": None, "authority_decision": None,
        "authority_notification": None, "data_subjects": None, "measures": [], "closure": None,
        "created_by": actor["user_id"], "created_at": now, "updated_at": now, "version": 1,
    }
    await db[C_INCIDENTS].insert_one(inc)
    await audit(db, inc["incident_id"], "incident_created", actor, {"incident_type": inc["incident_type"], "deadline_at": inc["deadline_at"].isoformat()})
    return public_view(inc, pol)


async def _load(db, incident_id: str) -> dict:
    inc = await db[C_INCIDENTS].find_one({"incident_id": incident_id}, {"_id": 0})
    if not inc:
        raise IncidentError(404, "not_found", "Incident not found")
    return inc


async def _save(db, inc: dict, patch: dict) -> dict:
    """Actualizare optimista (`version`): doua persoane nu isi suprascriu reciproc modificarile."""
    patch = dict(patch)
    patch["updated_at"] = now_utc()
    res = await db[C_INCIDENTS].update_one({"incident_id": inc["incident_id"], "version": inc["version"]},
                                           {"$set": patch, "$inc": {"version": 1}})
    if res.matched_count != 1:
        raise IncidentError(409, "version_conflict", "The incident was modified by someone else; reload and retry")
    return await _load(db, inc["incident_id"])


async def get_incident(db, actor: dict, incident_id: str) -> dict:
    require_staff(actor)
    return public_view(await _load(db, incident_id), await get_policy(db))


async def list_incidents(db, actor: dict, *, status: Optional[str] = None, limit: int = 100) -> list:
    require_staff(actor)
    if status is not None:
        _enum(status, STATUSES, "status")
    pol = await get_policy(db)
    rows = await db[C_INCIDENTS].find({"status": status} if status else {}, {"_id": 0}).sort("discovered_at", -1).limit(max(1, min(limit, 200))).to_list(200)
    return [public_view(r, pol) for r in rows]


async def update_facts(db, actor: dict, incident_id: str, body: dict) -> dict:
    """Actualizeaza faptele (date afectate, numar estimat, masuri luate). Adaugarea unei masuri e un eveniment separat in audit."""
    require_staff(actor)
    inc = await _load(db, incident_id)
    if inc["status"] == "closed":
        raise IncidentError(409, "incident_closed", "Closed incidents cannot be modified")
    patch: dict = {}
    changed: list = []
    if "data_categories" in body:
        patch["data_categories"] = _enum_list(body["data_categories"], DATA_CATEGORIES, "data_categories")
    if "breach_properties" in body:
        patch["breach_properties"] = _enum_list(body["breach_properties"], BREACH_PROPERTIES, "breach_properties", max_items=3)
    if "affected_users_estimate" in body:
        patch["affected_users_estimate"] = _count(body["affected_users_estimate"], "affected_users_estimate")
        patch["affected_users_unknown"] = False
    if "affected_records_estimate" in body:
        patch["affected_records_estimate"] = _count(body["affected_records_estimate"], "affected_records_estimate")
    if "description" in body:
        patch["description"] = _text(body["description"], "description", min_len=10, max_len=4000, required=True)
    if "discovered_at" in body:      # corectarea momentului descoperirii reface termenul; ramane in audit cu valoarea veche
        new = parse_dt(body["discovered_at"], "discovered_at")
        if new > now_utc() + timedelta(minutes=5):
            raise IncidentError(422, "invalid_datetime", "discovered_at cannot be in the future")
        patch["discovered_at"] = new
        patch["deadline_at"] = new + timedelta(hours=inc.get("deadline_hours", 72))
        changed.append("discovered_at")
    measure = body.get("add_measure")
    if measure is not None:
        m = {"at": now_utc(), "by": actor["user_id"], "text": _text(measure, "add_measure", min_len=5, max_len=1000, required=True)}
        patch["measures"] = list(inc.get("measures") or []) + [m]
        changed.append("measure_added")
    if not patch:
        raise IncidentError(422, "nothing_to_update", "No updatable field provided")
    changed += [k for k in patch if k not in ("measures", "discovered_at", "deadline_at")]
    new_inc = await _save(db, inc, patch)
    await audit(db, incident_id, "facts_updated", actor, {"fields": changed,
                "previous_discovered_at": inc["discovered_at"].isoformat() if "discovered_at" in body else None})
    return public_view(new_inc, await get_policy(db))


async def assess(db, actor: dict, incident_id: str, body: dict) -> dict:
    """Clasificarea gravitatii + evaluarea riscului pentru drepturile si libertatile persoanelor (Art. 33(1), 34(1))."""
    require_staff(actor)
    pol = await get_policy(db)
    inc = await _load(db, incident_id)
    if inc["status"] == "closed":
        raise IncidentError(409, "incident_closed", "Closed incidents cannot be modified")
    severity = _enum(body.get("severity"), SEVERITIES, "severity")
    risk = _enum(body.get("risk_level"), RISK_LEVELS, "risk_level")
    reasoning = _text(body.get("reasoning"), "reasoning", min_len=pol["min_reason_chars"], max_len=4000, required=True)
    consequences = _text(body.get("likely_consequences"), "likely_consequences", max_len=2000)
    if pol["child_data_forces_high_severity"] and "child_or_parental_data" in inc["data_categories"] and SEVERITIES.index(severity) < SEVERITIES.index("high"):
        raise IncidentError(422, "severity_too_low", "Incidents involving children's/parental data must be classified at least 'high'")
    if "special_category_data" in inc["data_categories"] and risk == "unlikely":
        raise IncidentError(422, "risk_too_low", "Special category data cannot be assessed as 'unlikely' to cause risk without further review")
    ra = {"level": risk, "reasoning": reasoning, "likely_consequences": consequences, "assessed_by": actor["user_id"], "assessed_at": now_utc()}
    status = inc["status"] if inc["status"] != "open" else "assessed"
    new_inc = await _save(db, inc, {"severity": severity, "risk_assessment": ra, "status": status})
    await audit(db, incident_id, "assessed", actor, {"severity": severity, "risk_level": risk, "previous_risk": (inc.get("risk_assessment") or {}).get("level")})
    return public_view(new_inc, pol)


async def decide_authority_notification(db, actor: dict, incident_id: str, body: dict) -> dict:
    """Inregistreaza decizia privind notificarea autoritatii. `notify` = se va pregati/depune notificarea; `not_notify` doar
    daca riscul e evaluat ca improbabil. Decizia este aprobata de un responsabil (sau de acelasi, daca politica permite)."""
    require_staff(actor)
    pol = await get_policy(db)
    inc = await _load(db, incident_id)
    if not inc.get("risk_assessment"):
        raise IncidentError(409, "assessment_required", "Assess severity and risk before deciding on notification")
    if inc["status"] == "closed":
        raise IncidentError(409, "incident_closed", "Closed incidents cannot be modified")
    if (inc.get("authority_notification") or {}).get("submitted_at"):
        raise IncidentError(409, "already_notified", "The notification was already submitted; the decision can no longer change")
    decision = _enum(body.get("decision"), ("notify", "not_notify"), "decision")
    reasons = _text(body.get("reasons"), "reasons", min_len=pol["min_reason_chars"], max_len=4000, required=True)
    if decision == "not_notify" and inc["risk_assessment"]["level"] != "unlikely":
        raise IncidentError(409, "notification_required", "Art. 33(1): notification is required unless the breach is unlikely to result in a risk")
    dec = {"decision": decision, "reasons": reasons, "decided_by": actor["user_id"], "decided_at": now_utc(),
           "approved_by": None, "approved_at": None}
    new_inc = await _save(db, inc, {"authority_decision": dec,
                                    "status": "authority_pending" if decision == "notify" else "no_notification_needed"})
    await audit(db, incident_id, "authority_decision_recorded", actor, {"decision": decision})
    return public_view(new_inc, pol)


async def approve_authority_decision(db, actor: dict, incident_id: str) -> dict:
    require_approver(actor)
    pol = await get_policy(db)
    inc = await _load(db, incident_id)
    dec = inc.get("authority_decision")
    if not dec:
        raise IncidentError(409, "decision_required", "No decision to approve")
    if dec.get("approved_by"):
        return public_view(inc, pol)
    if pol["require_separate_approver"] and actor["user_id"] in (dec["decided_by"], (inc.get("authority_notification") or {}).get("draft_by")):
        raise IncidentError(403, "separate_approver_required", "A different person must approve")
    dec = {**dec, "approved_by": actor["user_id"], "approved_at": now_utc()}
    new_inc = await _save(db, inc, {"authority_decision": dec})
    await audit(db, incident_id, "authority_decision_approved", actor, {"decision": dec["decision"]})
    return public_view(new_inc, pol)


# ----------------------------------------------------------------------------------------------- proiecte de notificare
def build_authority_draft(inc: dict, *, dpo_contact: str, consequences: str, controller: str = "Astran Game") -> str:
    """Continutul minim al Art. 33(3)(a)-(d), din faptele incidentului. Este un PROIECT pentru revizuire umana."""
    cats = ", ".join(inc.get("data_categories") or []) or "to be completed"
    users = "unknown (to be completed)" if inc.get("affected_users_unknown") else str(inc.get("affected_users_estimate"))
    recs = inc.get("affected_records_estimate")
    measures = "\n".join(f"- {m['text']}" for m in inc.get("measures") or []) or "- (to be completed)"
    return (f"DRAFT - personal data breach notification (GDPR Art. 33) - NOT SENT\n"
            f"Controller: {controller}    Internal reference: {inc['reference']}\n"
            f"Became aware at (UTC): {_aware(inc['discovered_at']).isoformat()}\n\n"
            f"(a) Nature of the breach: {inc['incident_type']}; affected properties: {', '.join(inc.get('breach_properties') or []) or 'to be completed'}.\n"
            f"    {inc['description']}\n"
            f"    Categories of personal data concerned: {cats}.\n"
            f"    Approximate number of data subjects: {users}; approximate number of records: {recs if recs is not None else 'unknown'}.\n"
            f"(b) Contact point (DPO or other): {dpo_contact}\n"
            f"(c) Likely consequences: {consequences}\n"
            f"(d) Measures taken or proposed to address the breach and mitigate its effects:\n{measures}\n"
            f"\nIf this notification is made later than 72 hours after awareness, state the reasons for the delay.")


async def prepare_authority_notification(db, actor: dict, incident_id: str, body: dict) -> dict:
    require_staff(actor)
    inc = await _load(db, incident_id)
    dec = inc.get("authority_decision")
    if not dec or dec["decision"] != "notify":
        raise IncidentError(409, "notification_not_decided", "Record the decision 'notify' first")
    if (inc.get("authority_notification") or {}).get("submitted_at"):
        raise IncidentError(409, "already_notified", "Already submitted")
    dpo = _text(body.get("dpo_contact"), "dpo_contact", min_len=5, max_len=300, required=True)
    cons = _text(body.get("likely_consequences") or (inc.get("risk_assessment") or {}).get("likely_consequences"),
                 "likely_consequences", min_len=10, max_len=2000, required=True)
    override = _text(body.get("draft_text"), "draft_text", max_len=12000)
    text = override or build_authority_draft(inc, dpo_contact=dpo, consequences=cons)
    note = {**(inc.get("authority_notification") or {}), "draft_text": text, "draft_prepared_at": now_utc(), "draft_by": actor["user_id"],
            "dpo_contact": dpo, "submitted_at": None}
    new_inc = await _save(db, inc, {"authority_notification": note})
    await audit(db, incident_id, "authority_draft_prepared", actor, {"edited_manually": bool(override)})
    return public_view(new_inc, await get_policy(db))


async def record_authority_submission(db, actor: dict, incident_id: str, body: dict) -> dict:
    """Inregistreaza FAPTUL ca responsabilul a depus notificarea prin canalul oficial (portalul autoritatii). Sistemul nu trimite nimic."""
    require_approver(actor)
    pol = await get_policy(db)
    inc = await _load(db, incident_id)
    dec = inc.get("authority_decision")
    if not dec or dec["decision"] != "notify" or not dec.get("approved_by"):
        raise IncidentError(409, "approval_required", "The decision to notify must be approved before recording a submission")
    note = inc.get("authority_notification") or {}
    if not note.get("draft_text"):
        raise IncidentError(409, "draft_required", "Prepare the notification draft first")
    if note.get("submitted_at"):
        raise IncidentError(409, "already_notified", "Submission already recorded")
    submitted = parse_dt(body.get("submitted_at"), "submitted_at")
    if submitted > now_utc() + timedelta(minutes=5):
        raise IncidentError(422, "invalid_datetime", "submitted_at cannot be in the future")
    if submitted < _aware(inc["discovered_at"]):
        raise IncidentError(422, "invalid_datetime", "submitted_at cannot be before discovered_at")
    ref = _text(body.get("authority_reference"), "authority_reference", max_len=120)
    late = submitted > _aware(inc["deadline_at"])
    late_reason = _text(body.get("late_reason"), "late_reason", min_len=pol["min_reason_chars"], max_len=2000, required=late)
    note = {**note, "submitted_at": submitted, "authority_reference": ref, "submitted_by": actor["user_id"], "recorded_at": now_utc(),
            "late": late, "late_reason": late_reason if late else None, "phased": bool(body.get("phased", False))}
    new_inc = await _save(db, inc, {"authority_notification": note, "status": "authority_notified"})
    await audit(db, incident_id, "authority_submission_recorded", actor, {"late": late, "has_reference": bool(ref)})
    return public_view(new_inc, pol)


def build_subject_draft(inc: dict, lang: str, *, contact: str, consequences: str, advice: str) -> str:
    """Art. 34(2): limbaj clar si simplu: natura incalcarii, contact, consecinte probabile, masuri."""
    cats = ", ".join(inc.get("data_categories") or [])
    measures = "; ".join(m["text"] for m in inc.get("measures") or []) or "-"
    if lang == "ro":
        return (f"Subiect: Incident de securitate care iti poate afecta datele - Astran Game\n\n"
                f"Ti-am descoperit la {_aware(inc['discovered_at']).date().isoformat()} un incident de securitate: {inc['description']}\n"
                f"Datele care pot fi afectate: {cats or 'se completeaza'}.\n"
                f"Consecinte probabile: {consequences}\nMasuri luate de noi: {measures}\n"
                f"Ce poti face: {advice}\nContact: {contact}\n")
    return (f"Subject: Security incident that may affect your data - Astran Game\n\n"
            f"On {_aware(inc['discovered_at']).date().isoformat()} we discovered a security incident: {inc['description']}\n"
            f"Data that may be affected: {cats or 'to be completed'}.\n"
            f"Likely consequences: {consequences}\nMeasures we took: {measures}\n"
            f"What you can do: {advice}\nContact: {contact}\n")


async def decide_data_subject_communication(db, actor: dict, incident_id: str, body: dict) -> dict:
    """Art. 34: decizia privind informarea persoanelor vizate + proiectul mesajului (RO/EN). NU trimite nimic."""
    require_staff(actor)
    pol = await get_policy(db)
    inc = await _load(db, incident_id)
    if not inc.get("risk_assessment"):
        raise IncidentError(409, "assessment_required", "Assess risk first")
    if inc["status"] == "closed":
        raise IncidentError(409, "incident_closed", "Closed incidents cannot be modified")
    if (inc.get("data_subjects") or {}).get("communicated_at"):
        raise IncidentError(409, "already_communicated", "Communication already recorded")
    required = body.get("required")
    if not isinstance(required, bool):
        raise IncidentError(422, "invalid_field", "required must be true/false")
    reasons = _text(body.get("reasons"), "reasons", min_len=pol["min_reason_chars"], max_len=4000, required=True)
    high = inc["risk_assessment"]["level"] == "high_risk"
    exemption = body.get("exemption")
    if not required:
        if high and exemption is None:
            raise IncidentError(409, "communication_required", "Art. 34: high risk requires communication unless an Art. 34(3) exemption applies")
        if exemption is not None:
            _enum(exemption, ART34_EXEMPTIONS, "exemption")
    ds: dict = {"required": required, "reasons": reasons, "exemption": exemption if not required else None,
                "decided_by": actor["user_id"], "decided_at": now_utc(), "communicated_at": None}
    if required:
        contact = _text(body.get("contact"), "contact", min_len=5, max_len=300, required=True)
        cons = _text(body.get("likely_consequences") or inc["risk_assessment"].get("likely_consequences"), "likely_consequences", min_len=10, max_len=2000, required=True)
        advice = _text(body.get("advice"), "advice", min_len=5, max_len=1500, required=True)
        ds["draft"] = {"ro": build_subject_draft(inc, "ro", contact=contact, consequences=cons, advice=advice),
                       "en": build_subject_draft(inc, "en", contact=contact, consequences=cons, advice=advice)}
    new_inc = await _save(db, inc, {"data_subjects": ds})
    await audit(db, incident_id, "data_subject_decision_recorded", actor, {"required": required, "exemption": exemption})
    return public_view(new_inc, pol)


async def record_data_subject_communication(db, actor: dict, incident_id: str, body: dict) -> dict:
    require_approver(actor)
    inc = await _load(db, incident_id)
    ds = inc.get("data_subjects")
    if not ds or not ds.get("required") or not ds.get("draft"):
        raise IncidentError(409, "communication_not_prepared", "No prepared communication to record")
    if ds.get("communicated_at"):
        raise IncidentError(409, "already_communicated", "Already recorded")
    at = parse_dt(body.get("communicated_at"), "communicated_at")
    if at > now_utc() + timedelta(minutes=5):
        raise IncidentError(422, "invalid_datetime", "communicated_at cannot be in the future")
    ds = {**ds, "communicated_at": at, "channel": _enum(body.get("channel"), CHANNELS, "channel"),
          "recipients_count": _count(body.get("recipients_count"), "recipients_count"), "communicated_by": actor["user_id"]}
    new_inc = await _save(db, inc, {"data_subjects": ds})
    await audit(db, incident_id, "data_subject_communication_recorded", actor, {"channel": ds["channel"], "recipients_count": ds["recipients_count"]})
    return public_view(new_inc, await get_policy(db))


async def close_incident(db, actor: dict, incident_id: str, body: dict) -> dict:
    require_approver(actor)
    pol = await get_policy(db)
    inc = await _load(db, incident_id)
    if inc["status"] == "closed":
        return public_view(inc, pol)
    dec = inc.get("authority_decision")
    if not dec or not dec.get("approved_by"):
        raise IncidentError(409, "decision_pending", "The authority notification decision must be recorded and approved")
    if dec["decision"] == "notify" and not (inc.get("authority_notification") or {}).get("submitted_at"):
        raise IncidentError(409, "notification_pending", "Record the authority submission before closing")
    ds = inc.get("data_subjects")
    if ds is None:
        raise IncidentError(409, "data_subject_decision_pending", "Record the Art. 34 decision before closing")
    if ds["required"] and not ds.get("communicated_at"):
        raise IncidentError(409, "communication_pending", "Record the communication to data subjects before closing")
    summary = _text(body.get("lessons_learned"), "lessons_learned", min_len=pol["min_reason_chars"], max_len=4000, required=True)
    new_inc = await _save(db, inc, {"status": "closed", "closure": {"closed_by": actor["user_id"], "closed_at": now_utc(), "lessons_learned": summary}})
    await audit(db, incident_id, "incident_closed", actor, {})
    return public_view(new_inc, pol)


async def audit_history(db, actor: dict, incident_id: str) -> dict:
    require_staff(actor)
    await _load(db, incident_id)
    rows = await db[C_AUDIT].find({"incident_id": incident_id}, {"_id": 0}).sort("seq", 1).to_list(2000)
    return {"events": rows, "chain": await verify_audit_chain(db, incident_id)}


async def dashboard_summary(db, actor: dict) -> dict:
    """Pentru ecranul de administrare: incidente deschise cu termen apropiat/depasit."""
    require_staff(actor)
    pol = await get_policy(db)
    rows = await db[C_INCIDENTS].find({"status": {"$ne": "closed"}}, {"_id": 0}).to_list(500)
    states: dict = {}
    for r in rows:
        s = deadline_status(r, policy=pol)["state"]
        states[s] = states.get(s, 0) + 1
    return {"open": len(rows), "deadline_states": states, "policy": pol}
