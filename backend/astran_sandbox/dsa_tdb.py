"""Integrare cu Baza de date de transparenta DSA (Art. 24(5)) - pregatire, redactare, coada, trimitere si export manual.

STATUT: capacitate tehnica. Trimiterea e OBLIGATORIE doar pentru platforme online ne-scutite (vezi dsa_applicability); cat timp statutul
nu e stabilit explicit, nimic nu intra automat in coada. Chiar si atunci, trimiterea prin API cere:
  * onboarding prin coordonatorul serviciilor digitale (DSC) si un token personal de la Comisie (sandbox intai);
  * ASTRAN_TDB_ENABLED=1, ASTRAN_TDB_MODE=sandbox|production, ASTRAN_TDB_API_URL (https), ASTRAN_TDB_API_TOKEN.
  Documentatia oficiala (transparency.dsa.ec.europa.eu/page/api-documentation) descrie POST <baza>/statement si <baza>/statements,
  `Authorization: Bearer <token>`, raspuns 201 cu uuid/id/permalink/puid. URL-ul NU e preconfigurat aici: operatorul il seteaza explicit.
Fara configurare completa => export JSON + flux manual documentat (formularul web al Comisiei); marcarea "trimis manual" cere o
referinta externa introdusa de un om si NU e prezentata ca verificata.

LIMITA ASUMATA: valorile enumerate (DECISION_*, STATEMENT_CATEGORY_*, SOURCE_*) provin din documentatia oficiala consultata si din
cunoasterea schemei, NEvalidate impotriva sandbox-ului. Categoriile pentru care maparea nu e sigura raman `needs_review` (nu se ghiceste).

PROTECTIA DATELOR: baza de date e publica si interzice orice date cu caracter personal. Se trimit doar campuri structurate; textul liber
e generat dintr-un sablon (motivarea moderatorului NU se trimite implicit - ASTRAN_TDB_INCLUDE_REASONING=1 o include dupa redactare);
campurile libere trec prin `redact_text`, iar o verificare finala blocheaza trimiterea (`blocked_pii`) daca orice identificator cunoscut
(id/username/email/id continut) apare in payload.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from . import dsa_applicability as APPL
from . import moderation_service as MS

log = logging.getLogger("astran.tdb")

COLL = "tdb_submissions"
PENDING, SUBMITTING, SUBMITTED, SUBMITTED_MANUAL, FAILED, ABANDONED = "pending", "submitting", "submitted", "submitted_manual", "failed", "abandoned"
REJECTED, BLOCKED_PII, NEEDS_REVIEW, EXPORTED, NEEDS_RECONCILIATION = "rejected", "blocked_pii", "needs_review", "exported", "needs_reconciliation"
DUE = (PENDING, FAILED)
FINAL_OK = (SUBMITTED, SUBMITTED_MANUAL)
EU27 = ["AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]

# None = maparea nu e sigura => `needs_review` (un om alege categoria; nu ghicim)
CATEGORY_MAP: dict = {
    "child_safety": "STATEMENT_CATEGORY_CHILD_SEXUAL_ABUSE_MATERIAL",
    "hate_speech": "STATEMENT_CATEGORY_ILLEGAL_OR_HARMFUL_SPEECH",
    "violence": "STATEMENT_CATEGORY_VIOLENCE",
    "sexual_content": "STATEMENT_CATEGORY_PORNOGRAPHY_OR_SEXUALIZED_CONTENT",
    "scam_fraud": "STATEMENT_CATEGORY_SCAMS_AND_FRAUD",
    "ip_infringement": "STATEMENT_CATEGORY_INTELLECTUAL_PROPERTY_INFRINGEMENTS",
    "harassment": None, "self_harm": None, "spam": None, "other": None, "illegal_content": None,
}
_CAT_RE = re.compile(r"^STATEMENT_CATEGORY_[A-Z_]{3,80}$")


# ------------------------------------------------------------------------------------------------ configurare
def tdb_config(env: Optional[dict] = None) -> dict:
    env = os.environ if env is None else env
    url = (env.get("ASTRAN_TDB_API_URL") or "").strip().rstrip("/")
    token = (env.get("ASTRAN_TDB_API_TOKEN") or "").strip()
    mode = (env.get("ASTRAN_TDB_MODE") or "").strip().lower()
    enabled = (env.get("ASTRAN_TDB_ENABLED") or "").strip() == "1"
    missing = [n for n, ok in (("ASTRAN_TDB_ENABLED=1", enabled), ("ASTRAN_TDB_MODE=sandbox|production", mode in ("sandbox", "production")),
                               ("ASTRAN_TDB_API_URL (https)", url.startswith("https://")), ("ASTRAN_TDB_API_TOKEN", bool(token))) if not ok]
    return {"api_configured": not missing, "missing": missing, "mode": mode or None, "url": url or None, "token": token or None,
            "include_reasoning": (env.get("ASTRAN_TDB_INCLUDE_REASONING") or "") == "1"}


def public_config(env: Optional[dict] = None) -> dict:
    c = tdb_config(env)
    return {"api_configured": c["api_configured"], "missing": c["missing"], "mode": c["mode"], "url": c["url"],
            "required_by_configured_legal_status": APPL.tdb_submission_required(env),
            "applicability": APPL.obligations(env)["art_24_5"]}


# ------------------------------------------------------------------------------------------------ redactare
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|astran://|www\.)\S+", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.I)
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
_HANDLE = re.compile(r"(?<!\w)@\w{2,}")
_INTERNAL_ID = re.compile(r"\b(?:usr|user|rep|dec|sor|game|msg|apl|cor|ord)_?[0-9a-f]{6,}\b", re.I)


def redact_text(text: Any, forbidden: Optional[list] = None, *, max_len: int = 2000) -> Optional[str]:
    if not isinstance(text, str) or not text.strip():
        return None
    t = text
    for rx in (_EMAIL, _URL, _IP, _PHONE, _HANDLE, _INTERNAL_ID):
        t = rx.sub("[redacted]", t)
    for f in sorted({x for x in (forbidden or []) if isinstance(x, str) and len(x.strip()) >= 3}, key=len, reverse=True):
        t = re.sub(re.escape(f.strip()), "[redacted]", t, flags=re.I)
    return t.strip()[:max_len] or None


def leaked_identifiers(payload: dict, forbidden: list) -> list:
    blob = json.dumps(payload, ensure_ascii=False, default=str).lower()
    return sorted({f for f in forbidden if isinstance(f, str) and len(f.strip()) >= 3 and f.strip().lower() in blob})


async def _forbidden_values(db, sor: dict, decision: dict, report: dict) -> list:
    vals = [decision.get("affected_user_id"), decision.get("notifier_id"), decision.get("moderator_id"), decision.get("target_id"),
            report.get("reporter_id"), report.get("reported_user_id"), report.get("report_id"), decision.get("decision_id"),
            (report.get("notice") or {}).get("reporter_name"), (report.get("notice") or {}).get("reporter_email")]
    for uid in (decision.get("affected_user_id"), report.get("reporter_id")):
        if uid:
            u = await db.users.find_one({"user_id": uid}, {"_id": 0, "username": 1, "display_name": 1, "email": 1})
            vals += [(u or {}).get("username"), (u or {}).get("display_name"), (u or {}).get("email")]
    return [v for v in vals if isinstance(v, str) and v.strip()]


# ------------------------------------------------------------------------------------------------ payload
def _day(dt: Any) -> Optional[str]:
    d = MS.aware(dt)
    return d.strftime("%Y-%m-%d") if d else None


def build_payload(sor: dict, decision: dict, report: dict, *, forbidden: list, include_reasoning: bool = False,
                  category_override: Optional[str] = None) -> tuple:
    """-> (payload, issues). `issues` non-gol => nu se trimite automat (needs_review / blocked_pii)."""
    issues: list = []
    action, basis = decision["action"], decision.get("basis") or {}
    p: dict = {"puid": sor["sor_id"], "application_date": _day(decision.get("created_at")), "territorial_scope": list(EU27),
               "automated_detection": "Yes" if decision.get("automated_detection") else "No",
               "automated_decision": "AUTOMATED_DECISION_NOT_AUTOMATED"}
    if action in (MS.ACTION_REMOVE_ILLEGAL, MS.ACTION_REMOVE_POLICY):
        p["decision_visibility"] = ["DECISION_VISIBILITY_CONTENT_REMOVED"]
    elif action == MS.ACTION_RESTRICT_CONTENT:
        p["decision_visibility"] = ["DECISION_VISIBILITY_CONTENT_DISABLED"]
    elif action == MS.ACTION_SUSPEND:
        p.update({"decision_account": "DECISION_ACCOUNT_TERMINATED", "account_type": "ACCOUNT_TYPE_PRIVATE"})
    elif action == MS.ACTION_RESTRICT_ACCOUNT:
        end = _day(decision.get("restriction_until"))
        if decision.get("scope") == "chat":
            p.update({"decision_provision": "DECISION_PROVISION_PARTIAL_SUSPENSION", "end_date_service_restriction": end})
        else:
            p.update({"decision_account": "DECISION_ACCOUNT_SUSPENDED", "account_type": "ACCOUNT_TYPE_PRIVATE", "end_date_account_restriction": end})
    else:
        issues.append("action_not_a_restriction")
    tt = decision.get("target_type")
    if tt == "game":
        p["content_type"] = ["CONTENT_TYPE_APP"]
    elif tt == "message":
        p["content_type"] = ["CONTENT_TYPE_TEXT"]
    else:
        p.update({"content_type": ["CONTENT_TYPE_OTHER"], "content_type_other": "User account"})
    cat = category_override or CATEGORY_MAP.get(report.get("category"))
    if not cat or not _CAT_RE.match(cat):
        issues.append("category_needs_manual_mapping")
    else:
        p["category"] = cat
    reasoning = redact_text(decision.get("reasoning"), forbidden) if include_reasoning else None
    if basis.get("type") == "illegal_content":
        p["decision_ground"] = "DECISION_GROUND_ILLEGAL_CONTENT"
        p["illegal_content_legal_ground"] = redact_text(basis.get("legal_ground"), forbidden, max_len=500) or "Not specified"
        p["illegal_content_explanation"] = reasoning or "The content was assessed as illegal under the legal ground indicated."
    else:
        p["decision_ground"] = "DECISION_GROUND_INCOMPATIBLE_CONTENT"
        p["incompatible_content_ground"] = redact_text(f"{basis.get('tos_provision') or ''} ({basis.get('tos_document') or 'Terms of Service'})".strip(), forbidden, max_len=500) or "Terms of Service"
        p["incompatible_content_explanation"] = reasoning or "The content was assessed as incompatible with the Terms of Service provision indicated."
        p["incompatible_content_illegal"] = "No"
    kind = report.get("report_kind")
    if kind == MS.KIND_NOTICE:
        p["source_type"] = "SOURCE_TRUSTED_FLAGGER" if (report.get("notice") or {}).get("trusted_flagger") else "SOURCE_ARTICLE_16"
    elif kind == MS.KIND_OWN:
        p["source_type"] = "SOURCE_VOLUNTARY"
    else:
        p["source_type"] = "SOURCE_TYPE_OTHER_NOTIFICATION"      # raport pe baza T&C / ordin al unei autoritati (ipoteza documentata)
    p = {k: v for k, v in p.items() if v is not None}
    leaks = leaked_identifiers(p, forbidden)
    if leaks:
        issues.append("personal_identifier_in_payload")
    return p, issues


def payload_hash(p: dict) -> str:
    return hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ coada
async def ensure_indexes(db) -> None:
    await db[COLL].create_index("submission_id", unique=True, name="dsa_tdb_id")
    await db[COLL].create_index("sor_id", unique=True, name="dsa_tdb_sor")          # o singura inregistrare / SoR => fara duplicate
    await db[COLL].create_index([("status", 1), ("next_attempt_at", 1)], name="dsa_tdb_due")


async def _prepare(db, sor: dict, decision: dict, *, category_override: Optional[str] = None) -> dict:
    report = await db.reports.find_one({"report_id": decision["report_id"]}, {"_id": 0}) or {}
    forbidden = await _forbidden_values(db, sor, decision, report)
    payload, issues = build_payload(sor, decision, report, forbidden=forbidden, include_reasoning=tdb_config()["include_reasoning"],
                                    category_override=category_override)
    status = BLOCKED_PII if "personal_identifier_in_payload" in issues else (NEEDS_REVIEW if issues else PENDING)
    return {"payload": None if status == BLOCKED_PII else payload, "payload_sha256": payload_hash(payload) if status != BLOCKED_PII else None,
            "issues": issues, "status": status}


async def _insert(db, sor: dict, decision: dict, prep: dict, *, status: str) -> dict:
    now = MS.now_utc()
    doc = {"submission_id": MS.new_id("tdb_"), "sor_id": sor["sor_id"], "decision_id": decision["decision_id"], "status": status, "attempts": 0,
           "payload": prep["payload"], "payload_sha256": prep["payload_sha256"], "issues": prep["issues"], "created_at": now, "next_attempt_at": now,
           "submitted_at": None, "external_ref": None, "last_error": None, "exported_at": None, "mode": None}
    try:
        await db[COLL].insert_one(dict(doc))
    except Exception as e:  # noqa: BLE001
        if "Duplicate" in type(e).__name__ or "E11000" in str(e):
            return await db[COLL].find_one({"sor_id": sor["sor_id"]}, {"_id": 0})
        raise
    return doc


async def maybe_enqueue(db, sor: dict, decision: dict) -> Optional[dict]:
    """Apelata la fiecare expunere de motive noua. Pune in coada DOAR daca statutul juridic configurat o cere. Niciodata o eroare aici
    nu trebuie sa strice decizia de moderare."""
    try:
        if not APPL.tdb_submission_required():
            return None
        prep = await _prepare(db, sor, decision)
        doc = await _insert(db, sor, decision, prep, status=prep["status"])
        await MS.audit(db, event_type="tdb_enqueued", actor_id=None, actor_role="system", decision_id=decision["decision_id"], action="enqueue",
                       new_status=doc["status"], details={"submission_id": doc["submission_id"], "issues": prep["issues"]})
        return doc
    except Exception:  # noqa: BLE001
        log.exception("tdb enqueue failed for %s", sor.get("sor_id"))
        return None


# ------------------------------------------------------------------------------------------------ trimitere API
class HttpClient:
    """Client HTTP real (httpx). Injectabil in teste. Intoarce (status_code, body_json_sau_text)."""
    async def post_json(self, url: str, token: str, payload: dict) -> tuple:
        import httpx
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, json=payload, headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"})
        try:
            return r.status_code, r.json()
        except Exception:  # noqa: BLE001
            return r.status_code, r.text[:500]


def _max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("ASTRAN_TDB_MAX_ATTEMPTS", "8")))
    except ValueError:
        return 8


async def submit_one(db, submission_id: str, client, now: Optional[datetime] = None) -> dict:
    cfg = tdb_config()
    if not cfg["api_configured"]:
        return {"submission_id": submission_id, "skipped": "api_not_configured", "missing": cfg["missing"]}
    now = now or MS.now_utc()
    claim = await db[COLL].update_one({"submission_id": submission_id, "status": {"$in": list(DUE)}, "payload": {"$ne": None}},
                                      {"$set": {"status": SUBMITTING, "claimed_at": now}})
    if not claim.matched_count:
        return {"submission_id": submission_id, "skipped": "not_due"}
    s = await db[COLL].find_one({"submission_id": submission_id}, {"_id": 0})
    attempts = s["attempts"] + 1
    try:
        code, body = await client.post_json(f"{cfg['url']}/statement", cfg["token"], s["payload"])
    except Exception as e:  # noqa: BLE001
        code, body = 0, type(e).__name__
    upd: dict = {"attempts": attempts, "last_attempt_at": now, "claimed_at": None, "mode": cfg["mode"]}
    if code == 201 and isinstance(body, dict) and any(body.get(k) for k in ("uuid", "id", "permalink")):
        upd.update({"status": SUBMITTED, "submitted_at": MS.now_utc(), "last_error": None, "next_attempt_at": None,
                    "external_ref": {k: body.get(k) for k in ("uuid", "id", "permalink", "puid")}})
        ev = "tdb_submitted"
    elif code == 201:       # raspuns 201 fara identificatori: NU declaram succes
        upd.update({"status": FAILED, "last_error": "unconfirmed_response", "next_attempt_at": now + timedelta(minutes=30)})
        ev = "tdb_failed"
    elif code in (400, 404, 409, 422):      # cerere respinsa de serviciu (nu se reincearca orbeste)
        dup = "puid" in json.dumps(body, default=str).lower() if code in (409, 422) else False
        upd.update({"status": NEEDS_RECONCILIATION if dup else REJECTED, "last_error": f"http_{code}", "next_attempt_at": None,
                    "service_response": (body if isinstance(body, (dict, list)) else str(body))})
        ev = "tdb_rejected"
    else:                   # 0 (retea), 401/403 (token), 429, 5xx: reincercare cu backoff
        final = attempts >= _max_attempts()
        upd.update({"status": ABANDONED if final else FAILED, "last_error": f"http_{code}" if code else str(body)[:60],
                    "next_attempt_at": None if final else now + min(timedelta(hours=6), timedelta(seconds=60 * 2 ** (attempts - 1)))})
        ev = "tdb_abandoned" if final else "tdb_failed"
    await db[COLL].update_one({"submission_id": submission_id}, {"$set": upd})
    await MS.audit(db, event_type=ev, actor_id=None, actor_role="system", decision_id=s["decision_id"], action="tdb_submit", new_status=upd["status"],
                   outcome=upd.get("last_error"), details={"submission_id": submission_id, "attempt": attempts, "mode": cfg["mode"]})
    return {"submission_id": submission_id, "status": upd["status"], "attempts": attempts, "error": upd.get("last_error")}


async def submit_pending(db, client=None, *, now: Optional[datetime] = None, limit: int = 25) -> list:
    if not tdb_config()["api_configured"] or not APPL.tdb_submission_required():
        return []
    now = now or MS.now_utc()
    await db[COLL].update_many({"status": SUBMITTING, "claimed_at": {"$lt": now - timedelta(minutes=10)}}, {"$set": {"status": FAILED, "last_error": "stale_claim"}})
    rows = await db[COLL].find({"status": {"$in": list(DUE)}, "next_attempt_at": {"$lte": now}, "payload": {"$ne": None}}, {"_id": 0, "submission_id": 1}).sort("created_at", 1).limit(limit).to_list(limit)
    client = client or HttpClient()
    return [await submit_one(db, r["submission_id"], client, now) for r in rows]


async def tdb_worker(db, *, interval: float = 60.0) -> None:
    while True:
        try:
            await submit_pending(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("tdb worker iteration failed")
        await asyncio.sleep(interval)


# ------------------------------------------------------------------------------------------------ administrare + flux manual
def _admin(actor: dict) -> None:
    if MS.role_of(actor) != "admin":
        raise MS.ModerationError(403, "admin_required")


async def overview(db, actor: dict, *, status: Optional[str] = None, limit: int = 100) -> dict:
    _admin(actor)
    flt = {"status": status} if status else {}
    rows = await db[COLL].find(flt, {"_id": 0, "payload": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    allst = (PENDING, SUBMITTING, SUBMITTED, SUBMITTED_MANUAL, FAILED, ABANDONED, REJECTED, BLOCKED_PII, NEEDS_REVIEW, EXPORTED, NEEDS_RECONCILIATION)
    return {"config": public_config(), "counts": {s: await db[COLL].count_documents({"status": s}) for s in allst}, "items": rows}


async def export_statements(db, actor: dict, *, sor_ids: Optional[list] = None, limit: int = 200) -> dict:
    """Flux manual: produce JSON redactat pentru formularul/API-ul Comisiei si marcheaza `exported`. Merge si cand obligatia nu e stabilita."""
    _admin(actor)
    sors = (await db.statements_of_reasons.find({"sor_id": {"$in": sor_ids}} if sor_ids else {}, {"_id": 0}).sort("issued_at", 1).limit(limit).to_list(limit))
    out, skipped = [], []
    for sor in sors:
        ex = await db[COLL].find_one({"sor_id": sor["sor_id"]}, {"_id": 0})
        if ex and ex["status"] in FINAL_OK:
            skipped.append({"sor_id": sor["sor_id"], "reason": "already_submitted"})
            continue
        if ex and ex["status"] == BLOCKED_PII:
            skipped.append({"sor_id": sor["sor_id"], "reason": "blocked_pii"})
            continue
        decision = await db.moderation_decisions.find_one({"decision_id": sor["decision_id"]}, {"_id": 0})
        if not decision:
            skipped.append({"sor_id": sor["sor_id"], "reason": "decision_missing"})
            continue
        if not ex:
            prep = await _prepare(db, sor, decision)
            ex = await _insert(db, sor, decision, prep, status=prep["status"])
        if ex["status"] == BLOCKED_PII or not ex.get("payload"):
            skipped.append({"sor_id": sor["sor_id"], "reason": "blocked_pii"})
            continue
        if ex["issues"]:
            skipped.append({"sor_id": sor["sor_id"], "reason": "needs_review:" + ",".join(ex["issues"])})
            continue
        await db[COLL].update_one({"submission_id": ex["submission_id"], "status": {"$nin": list(FINAL_OK)}}, {"$set": {"status": EXPORTED, "exported_at": MS.now_utc()}})
        out.append(ex["payload"])
    await MS.audit(db, event_type="tdb_exported", actor_id=actor["user_id"], actor_role="admin", action="export", details={"count": len(out), "skipped": len(skipped)})
    return {"statements": out, "skipped": skipped,
            "instructions": "Upload via the Commission webform or POST to the statements endpoint of your onboarded environment. After the service confirms, "
                            "record the reference with /confirm-manual. Do not treat an export as a submission."}


async def confirm_manual(db, actor: dict, submission_id: str, external_reference: Any) -> dict:
    _admin(actor)
    ref = MS.clean_text(external_reference, field="external_reference", min_len=4, max_len=300)
    res = await db[COLL].update_one({"submission_id": submission_id, "status": {"$in": [EXPORTED, FAILED, ABANDONED, REJECTED, NEEDS_RECONCILIATION, NEEDS_REVIEW, PENDING]}},
                                    {"$set": {"status": SUBMITTED_MANUAL, "submitted_at": MS.now_utc(), "external_ref": {"reference": ref, "verified_with_service": False,
                                              "confirmed_by": actor["user_id"]}, "next_attempt_at": None}})
    if not res.matched_count:
        raise MS.ModerationError(409, "submission_not_confirmable")
    await MS.audit(db, event_type="tdb_manual_confirmed", actor_id=actor["user_id"], actor_role="admin", action="confirm_manual", details={"submission_id": submission_id})
    return {"submission_id": submission_id, "status": SUBMITTED_MANUAL, "verified_with_service": False}


async def set_category(db, actor: dict, submission_id: str, category: Any) -> dict:
    _admin(actor)
    if not isinstance(category, str) or not _CAT_RE.match(category):
        raise MS.ModerationError(422, "category_invalid")
    s = await db[COLL].find_one({"submission_id": submission_id}, {"_id": 0})
    if not s or s["status"] != NEEDS_REVIEW:
        raise MS.ModerationError(409, "submission_not_in_review")
    sor = await db.statements_of_reasons.find_one({"sor_id": s["sor_id"]}, {"_id": 0})
    decision = await db.moderation_decisions.find_one({"decision_id": s["decision_id"]}, {"_id": 0})
    prep = await _prepare(db, sor, decision, category_override=category)
    await db[COLL].update_one({"submission_id": submission_id}, {"$set": {"payload": prep["payload"], "payload_sha256": prep["payload_sha256"],
                                                                         "issues": prep["issues"], "status": prep["status"], "next_attempt_at": MS.now_utc()}})
    await MS.audit(db, event_type="tdb_category_set", actor_id=actor["user_id"], actor_role="admin", action="set_category", new_status=prep["status"], details={"submission_id": submission_id})
    return {"submission_id": submission_id, "status": prep["status"], "issues": prep["issues"]}


async def retry(db, actor: dict, submission_id: str) -> dict:
    _admin(actor)
    res = await db[COLL].update_one({"submission_id": submission_id, "status": {"$in": [FAILED, ABANDONED]}},
                                    {"$set": {"status": PENDING, "attempts": 0, "next_attempt_at": MS.now_utc(), "last_error": None}})
    if not res.matched_count:
        raise MS.ModerationError(409, "submission_not_retryable")
    return {"submission_id": submission_id, "status": PENDING}
