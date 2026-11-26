"""Ordine ale autoritatilor competente: Art. 9 (actiune impotriva continutului ilegal) si Art. 10 (furnizare de informatii).

Flux (nimic nu se executa pentru ca "a fost incarcat un document"):
  intake (personal sau punct de contact public, neverificat)
    -> validare de completitudine (sistem; NU e o decizie juridica)
    -> autenticitate (verificata de personal juridic)
    -> revizuire juridica (aprobare / respingere / cerere de informatii) - alta persoana decat cea care a inregistrat ordinul ("patru ochi")
    -> executie (Art. 9: prin motorul de decizii existent => expunere de motive, notificare, audit, drept de plangere;
                 Art. 10: pachet de informatii limitat la o lista alba, pregatit pentru livrare pe canal securizat)
    -> raspuns catre autoritate (confirmare primire / efect dat / respingere) cu livrare urmarita (dsa_delivery)
    -> inchidere, retentie, stergere.

Autorizare: DOAR `platform admin` sau `is_legal_officer` (acordat de admin). Moderatorii obisnuiti NU vad ordinele; evenimentele de audit
`order_*` sunt filtrate din vizualizarile generale. Verificarea se face in fiecare functie (nu doar in rute).

Termene: DSA cere informarea autoritatii "fara intarziere nejustificata" (Art. 9(1), 10(1)) si nu fixeaza un numar de zile; daca ordinul
indica un termen, acesta e inregistrat si urmarit; altfel NU se inventeaza unul.

LIMITE ASUMATE: fisierele sunt criptate la repaus doar daca ASTRAN_ORDERS_ENC_KEY (cheie Fernet) e setata; altfel se bazeaza pe
criptarea volumului. Jurnalul de audit e inlantuit cu hash (detecteaza modificarea), dar un administrator de baza de date cu drept de scriere
poate sterge ultimele intrari - de acoperit prin backup/WORM extern. Mesajele private nu pot fi furnizate prin acest flux (necesita analiza juridica separata).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import dsa_delivery as DL
from . import moderation_service as MS

log = logging.getLogger("astran.orders")

ART9, ART10 = "art9_illegal_content", "art10_information"
ORDER_TYPES = (ART9, ART10)
ST_INCOMPLETE, ST_PENDING, ST_APPROVED, ST_REJECTED = "incomplete", "pending_legal_review", "approved", "rejected"
ST_EXECUTED, ST_PARTIAL, ST_PREPARED, ST_CLOSED = "executed", "execution_failed", "information_prepared", "closed"
OPEN_ST = (ST_INCOMPLETE, ST_PENDING, ST_APPROVED, ST_PARTIAL, ST_PREPARED, ST_EXECUTED, ST_REJECTED)
EDITABLE = (ST_INCOMPLETE, ST_PENDING)
COLL, ATT = "authority_orders", "authority_order_attachments"
MAX_FILE = 10 * 1024 * 1024
MAX_FILES = 20
ALLOWED_EXT = {"pdf": "application/pdf", "xml": "application/xml", "json": "application/json", "txt": "text/plain"}
RECEIVED_VIA = ("contact_point_web", "email", "post", "dsc_transmission", "other")
# Art. 10: DOAR informatii deja colectate pentru furnizarea serviciului si aflate sub controlul Astran. Lista alba explicita:
ART10_FIELDS = ("user_id", "username", "display_name", "email", "created_at", "email_verified")
NEVER_DISCLOSE = ("password_hash", "tokens/sessions", "payment data", "parental-consent data", "private message content", "webauthn credentials")
_MEMBER = re.compile(r"^[A-Z]{2}$")


def order_role(user: Optional[dict]) -> Optional[str]:
    if not user:
        return None
    if user.get("is_platform_admin"):
        return "admin"
    if user.get("is_legal_officer"):
        return "legal"
    return None


def _require(actor: Optional[dict]) -> str:
    r = order_role(actor)
    if not r:
        raise MS.ModerationError(403, "forbidden", "legal/admin access required")
    return r


def can_execute(actor: dict) -> bool:
    """Executia Art. 9 foloseste motorul de moderare => necesita si capabilitate de moderator (sau admin)."""
    return bool(actor.get("is_platform_admin") or (actor.get("is_legal_officer") and actor.get("is_moderator")))


def four_eyes() -> bool:
    return os.environ.get("ASTRAN_ORDERS_FOUR_EYES", "1") != "0"


# ------------------------------------------------------------------------------------------------ stocare privata
def storage_dir() -> Path:
    base = os.environ.get("ASTRAN_ORDERS_DIR") or os.path.join(tempfile.gettempdir(), "astran_authority_orders")
    p = Path(base)
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def _fernet():
    key = (os.environ.get("ASTRAN_ORDERS_ENC_KEY") or "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def storage_info() -> dict:
    return {"encrypted_at_rest": _fernet() is not None, "directory_is_private": True,
            "note": None if _fernet() else "ASTRAN_ORDERS_ENC_KEY not set: files are stored unencrypted (rely on volume encryption)"}


def _safe_name(name: Any) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(str(name or "document")))[:120].strip(" .") or "document"
    return base


# ------------------------------------------------------------------------------------------------ validare
def _targets_needed(order_type: str) -> str:
    return "content" if order_type == ART9 else "accounts"


def validate_order(o: dict) -> list:
    """Verificare de COMPLETITUDINE a elementelor cerute de Art. 9(2) / 10(2). Nu stabileste valabilitatea juridica."""
    miss = []
    auth = o.get("authority") or {}
    for k in ("name", "member_state"):
        if not auth.get(k):
            miss.append(f"authority.{k}")
    if auth.get("member_state") and not _MEMBER.match(auth["member_state"]):
        miss.append("authority.member_state_format")
    if not (auth.get("contact_email") or o.get("authority_to_inform")):
        miss.append("authority_contact")
    for k in ("legal_basis", "statement_of_reasons", "issued_at"):
        if not o.get(k):
            miss.append(k)
    if not o.get("redress_information"):
        miss.append("redress_information")
    tg = o.get("targets") or []
    if not tg:
        miss.append("targets")
    if o.get("order_type") == ART9:
        if not o.get("territorial_scope"):
            miss.append("territorial_scope")
        for t in tg:
            if t.get("target_type") not in ("game", "message"):
                miss.append("targets:art9_requires_exact_content_identifier")
                break
    elif o.get("order_type") == ART10:
        if not o.get("requested_items"):
            miss.append("requested_items")
        for t in tg:
            if t.get("target_type") != "user":
                miss.append("targets:art10_requires_account_identifier")
                break
    return miss


def _normalise_targets(order_type: str, raw: Any) -> list:
    if not isinstance(raw, list) or len(raw) > 50:
        raise MS.ModerationError(422, "targets_invalid", "1-50 targets")
    out = []
    for t in raw:
        if not isinstance(t, dict):
            raise MS.ModerationError(422, "targets_invalid")
        tt, tid = t.get("target_type"), t.get("target_id")
        if t.get("url"):
            parsed = MS.parse_content_reference(t["url"])
            if not parsed:
                raise MS.ModerationError(422, "target_url_invalid", "unrecognised content URL")
            tt, tid = parsed
        if t.get("account_username") and not tid:
            tt, tid = "user", None
        if tt not in ("game", "message", "user"):
            raise MS.ModerationError(422, "target_type_invalid")
        item = {"target_type": tt, "target_id": MS.clean_text(tid, field="target_id", max_len=64) if tid else None,
                "account_username": MS.clean_text(t.get("account_username"), field="account_username", max_len=64),
                "url": MS.clean_text(t.get("url"), field="url", max_len=600), "state": "pending", "decision_id": None, "error": None}
        if not item["target_id"] and not item["account_username"]:
            raise MS.ModerationError(422, "target_identifier_required")
        out.append(item)
    return out


def _clean_fields(body: dict) -> dict:
    ct = MS.clean_text
    otype = body.get("order_type")
    if otype not in ORDER_TYPES:
        raise MS.ModerationError(422, "order_type_invalid")
    f: dict = {"order_type": otype}
    auth = body.get("authority") or {}
    ms_ = (auth.get("member_state") or "").strip().upper() or None
    f["authority"] = {"name": ct(auth.get("name"), field="authority.name", max_len=200), "member_state": ms_,
                      "contact_email": ct(auth.get("contact_email"), field="authority.contact_email", max_len=320),
                      "their_reference": ct(auth.get("their_reference"), field="authority.their_reference", max_len=120)}
    if f["authority"]["contact_email"] and not MS._EMAIL_RE.match(f["authority"]["contact_email"]):
        raise MS.ModerationError(422, "email_invalid")
    f["legal_basis"] = ct(body.get("legal_basis"), field="legal_basis", max_len=1000)
    f["statement_of_reasons"] = ct(body.get("statement_of_reasons"), field="statement_of_reasons", max_len=MS.MAX_TEXT)
    f["territorial_scope"] = ct(body.get("territorial_scope"), field="territorial_scope", max_len=300)
    f["authority_to_inform"] = ct(body.get("authority_to_inform"), field="authority_to_inform", max_len=300)
    cat = body.get("illegal_content_category")
    if cat not in (None, "") and cat not in MS.CATEGORIES:
        raise MS.ModerationError(422, "category_invalid")
    f["illegal_content_category"] = cat or None          # Art. 15(1)(a): tipul de continut ilegal vizat (daca il indica ordinul)
    f["redress_information"] = bool(body.get("redress_information"))
    f["non_disclosure"] = {"requested": bool((body.get("non_disclosure") or {}).get("requested")),
                           "basis": ct((body.get("non_disclosure") or {}).get("basis"), field="non_disclosure.basis", max_len=500)}
    iss = body.get("issued_at")
    if isinstance(iss, str):
        try:
            iss = datetime.fromisoformat(iss.replace("Z", "+00:00"))
        except ValueError:
            raise MS.ModerationError(422, "issued_at_invalid")
    f["issued_at"] = MS.aware(iss) if isinstance(iss, datetime) else None
    dl = body.get("response_deadline")
    if isinstance(dl, str):
        try:
            dl = datetime.fromisoformat(dl.replace("Z", "+00:00"))
        except ValueError:
            raise MS.ModerationError(422, "deadline_invalid")
    f["response_deadline"] = MS.aware(dl) if isinstance(dl, datetime) else None      # DOAR daca il indica ordinul
    f["targets"] = _normalise_targets(otype, body.get("targets") or [])
    items = body.get("requested_items") or []
    if not isinstance(items, list) or len(items) > 30 or any(not isinstance(i, str) or len(i) > 60 for i in items):
        raise MS.ModerationError(422, "requested_items_invalid")
    f["requested_items"] = [i.strip() for i in items if i.strip()]
    return f


async def ensure_indexes(db) -> None:
    await db[COLL].create_index("order_id", unique=True, name="dsa_order_id")
    await db[COLL].create_index("reference", unique=True, name="dsa_order_ref")
    await db[COLL].create_index([("status", 1), ("received_at", -1)], name="dsa_order_status")
    await db[ATT].create_index("attachment_id", unique=True, name="dsa_order_att_id")
    await db[ATT].create_index("order_id", name="dsa_order_att_order")


async def _aud(db, actor: Optional[dict], event: str, order: dict, *, action: Optional[str] = None, new_status: Optional[str] = None,
               reasoning: Optional[str] = None, **details) -> None:
    assert event in MS.ORDER_EVENT_TYPES, event
    await MS.audit(db, event_type=event, actor_id=(actor or {}).get("user_id"), actor_role=order_role(actor) or ("authority_unverified" if (not actor and event == "order_received") else "system"),
                   action=action, new_status=new_status, previous_status=order.get("status"), reasoning=reasoning,
                   details={"order_id": order["order_id"], "reference": order["reference"], **details})


# ------------------------------------------------------------------------------------------------ intake
async def _new_reference(db) -> tuple:
    year = MS.now_utc().year
    for _ in range(12):
        n = await db[COLL].count_documents({"reference": {"$regex": f"^ORD-{year}-"}}) + 1
        ref = f"ORD-{year}-{n:05d}"
        if not await db[COLL].find_one({"reference": ref}, {"_id": 1}):
            return ref
        n += 1
    raise MS.ModerationError(503, "reference_unavailable")


async def _create(db, body: dict, *, actor: Optional[dict], via: str, received_at: Optional[datetime]) -> dict:
    f = _clean_fields(body)
    now = MS.now_utc()
    if received_at is not None:
        received_at = MS.aware(received_at)
        if received_at > now or received_at < now - timedelta(days=365):
            raise MS.ModerationError(422, "received_at_invalid")
    for attempt in range(8):
        ref = await _new_reference(db)
        doc = {"order_id": MS.new_id("ord_"), "reference": ref, **f, "received_at": received_at or now, "recorded_at": now, "received_via": via,
               "created_by": (actor or {}).get("user_id"), "authenticity": {"state": "unverified", "method": None, "by": None, "at": None},
               "validation": {}, "legal_review": {"state": "pending", "reviewer_id": None, "reasoning": None, "at": None, "non_disclosure_confirmed": False},
               "responses": [], "milestones": {"received_at": received_at or now, "acknowledged_at": None, "effect_given_at": None, "closed_at": None},
               "recipient_notice": {"status": "pending" if f["order_type"] == ART10 else "handled_by_statement_of_reasons"},
               "information_package": None, "retention_until": None, "status": ST_INCOMPLETE}
        miss = validate_order(doc)
        doc["validation"] = {"state": "incomplete" if miss else "complete", "missing": miss, "checked_at": now, "checked_by": "system_completeness_check"}
        doc["status"] = ST_INCOMPLETE if miss else ST_PENDING
        try:
            await db[COLL].insert_one(dict(doc))
            break
        except Exception as e:  # noqa: BLE001
            if "Duplicate" in type(e).__name__ or "E11000" in str(e):
                continue
            raise
    else:
        raise MS.ModerationError(503, "reference_unavailable")
    await _aud(db, actor, "order_received", {**doc, "status": None}, action="create", new_status=doc["status"], via=via, order_type=doc["order_type"], missing=miss)
    doc.pop("_id", None)
    return doc


async def create_order(db, actor: dict, body: dict, *, received_at: Optional[datetime] = None, via: str = "email") -> dict:
    _require(actor)
    if via not in RECEIVED_VIA:
        raise MS.ModerationError(422, "received_via_invalid")
    return _view(await _create(db, body, actor=actor, via=via, received_at=received_at))


async def public_intake(db, body: dict) -> dict:
    """Punctul de contact electronic (Art. 11) pentru autoritati: inregistreaza ordinul ca NEVERIFICAT. Nu executa nimic, nu returneaza date.
    Raspunde doar cu referinta (confirmare de primire tehnica); confirmarea formala catre autoritate vine din fluxul juridic."""
    doc = await _create(db, body, actor=None, via="contact_point_web", received_at=None)
    return {"reference": doc["reference"], "received_at": doc["recorded_at"], "status": "received_unverified"}


# ------------------------------------------------------------------------------------------------ citire
def _view(o: dict) -> dict:
    o = {k: v for k, v in o.items() if k != "_id"}
    dl = MS.aware(o.get("response_deadline"))
    now = MS.now_utc()
    acked = o.get("milestones", {}).get("effect_given_at") or o.get("milestones", {}).get("closed_at")
    o["deadline_status"] = ("none_stated_without_undue_delay" if not dl else ("met_or_closed" if acked else ("overdue" if now > dl else "open")))
    o["four_eyes_required"] = four_eyes()
    return o


async def _get(db, order_id: str) -> dict:
    o = await db[COLL].find_one({"order_id": order_id}, {"_id": 0})
    if not o:
        raise MS.ModerationError(404, "not_found")
    return o


async def get_order(db, actor: dict, order_id: str) -> dict:
    _require(actor)
    o = await _get(db, order_id)
    atts = await db[ATT].find({"order_id": order_id}, {"_id": 0, "stored_path": 0}).sort("uploaded_at", 1).to_list(100)
    for r in o.get("responses", []):
        r["delivery"] = await _delivery(db, r["response_id"])
    events = await db.moderation_audit_log.find({"details.order_id": order_id}, {"_id": 0}).sort("seq", 1).to_list(500)
    v = _view(o)
    v.update({"attachments": atts, "history": events, "storage": storage_info(), "can_execute": can_execute(actor)})
    return v


async def _delivery(db, response_id: str) -> Optional[dict]:
    d = await db[DL.COLL].find_one({"dedupe_key": f"authresp:{response_id}"}, {"_id": 0})
    if not d:
        return None
    return {"status": d["status"], "attempts": d["attempts"], "sent_at": d.get("sent_at"), "last_error": d.get("last_error"), "next_attempt_at": d.get("next_attempt_at")}


async def list_orders(db, actor: dict, *, status: Optional[str] = None, order_type: Optional[str] = None, limit: int = 100) -> dict:
    _require(actor)
    flt: dict = {}
    if status:
        flt["status"] = status
    if order_type:
        flt["order_type"] = order_type
    rows = await db[COLL].find(flt, {"_id": 0}).sort("received_at", -1).limit(limit).to_list(limit)
    items = [{"order_id": o["order_id"], "reference": o["reference"], "order_type": o["order_type"], "status": o["status"],
              "authority": {"name": o["authority"].get("name"), "member_state": o["authority"].get("member_state")},
              "received_at": o["received_at"], "response_deadline": o.get("response_deadline"),
              "deadline_status": _view(o)["deadline_status"], "validation_state": (o.get("validation") or {}).get("state"),
              "authenticity": (o.get("authenticity") or {}).get("state")} for o in rows]
    return {"items": items, "counts": {s: await db[COLL].count_documents({"status": s}) for s in OPEN_ST + (ST_CLOSED,)}}


# ------------------------------------------------------------------------------------------------ atasamente
async def add_attachment(db, actor: dict, order_id: str, *, filename: Any, data: bytes, kind: str = "order_document") -> dict:
    _require(actor)
    o = await _get(db, order_id)
    if kind not in ("order_document", "supporting", "response_package"):
        raise MS.ModerationError(422, "attachment_kind_invalid")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise MS.ModerationError(422, "attachment_empty")
    if len(data) > MAX_FILE:
        raise MS.ModerationError(413, "attachment_too_large", f"max {MAX_FILE // (1024 * 1024)} MB")
    if await db[ATT].count_documents({"order_id": order_id}) >= MAX_FILES:
        raise MS.ModerationError(409, "too_many_attachments")
    name = _safe_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise MS.ModerationError(422, "attachment_type_not_allowed", "allowed: " + ", ".join(sorted(ALLOWED_EXT)))
    if ext == "pdf" and not bytes(data[:5]) == b"%PDF-":
        raise MS.ModerationError(422, "attachment_content_mismatch")
    if ext in ("xml", "json", "txt"):
        try:
            txt = bytes(data).decode("utf-8")
            if ext == "json":
                json.loads(txt)
        except Exception:  # noqa: BLE001
            raise MS.ModerationError(422, "attachment_content_mismatch")
    aid = MS.new_id("att_")
    rel = f"{order_id}/{aid}.bin"                    # calea vine DOAR din identificatori generati de server (fara nume de la client)
    path = storage_dir() / rel
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = _fernet()
    blob = f.encrypt(bytes(data)) if f else bytes(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    doc = {"attachment_id": aid, "order_id": order_id, "filename": name, "content_type": ALLOWED_EXT[ext], "size": len(data), "kind": kind,
           "sha256": hashlib.sha256(bytes(data)).hexdigest(), "encrypted": f is not None, "stored_path": rel,
           "uploaded_by": actor["user_id"], "uploaded_at": MS.now_utc()}
    await db[ATT].insert_one(dict(doc))
    await _aud(db, actor, "order_attachment_added", o, action="attach", attachment_id=aid, sha256=doc["sha256"], size=doc["size"], kind=kind)
    doc.pop("stored_path")
    return doc


async def read_attachment(db, actor: dict, order_id: str, attachment_id: str) -> tuple:
    _require(actor)
    o = await _get(db, order_id)
    a = await db[ATT].find_one({"attachment_id": attachment_id, "order_id": order_id}, {"_id": 0})   # legat de ordin: fara IDOR intre ordine
    if not a:
        raise MS.ModerationError(404, "not_found")
    path = (storage_dir() / a["stored_path"]).resolve()
    if storage_dir().resolve() not in path.parents or not path.exists():
        raise MS.ModerationError(404, "file_missing")
    raw = path.read_bytes()
    data = _fernet().decrypt(raw) if a.get("encrypted") else raw
    if hashlib.sha256(data).hexdigest() != a["sha256"]:
        raise MS.ModerationError(500, "attachment_integrity_failed")
    await _aud(db, actor, "order_attachment_downloaded", o, action="download", attachment_id=attachment_id)
    return a["filename"], a["content_type"], data


# ------------------------------------------------------------------------------------------------ validare / autenticitate / revizuire juridica
async def supplement(db, actor: dict, order_id: str, body: dict) -> dict:
    """Completeaza elementele lipsa (dupa ce autoritatea le-a furnizat). Nu se poate dupa aprobare."""
    _require(actor)
    o = await _get(db, order_id)
    if o["status"] not in EDITABLE:
        raise MS.ModerationError(409, "order_not_editable")
    merged = {**{k: o.get(k) for k in ("order_type", "legal_basis", "statement_of_reasons", "territorial_scope", "authority_to_inform", "redress_information",
                                       "non_disclosure", "issued_at", "response_deadline", "requested_items", "illegal_content_category")}, "authority": o["authority"], "targets": [
        {k: t.get(k) for k in ("target_type", "target_id", "account_username", "url")} for t in o["targets"]]}
    for k, v in (body or {}).items():
        if k in merged and k != "order_type":
            merged[k] = v if k != "authority" else {**o["authority"], **(v or {})}
    f = _clean_fields({**merged, "order_type": o["order_type"]})
    cand = {**o, **f}
    miss = validate_order(cand)
    now = MS.now_utc()
    upd = {**f, "validation": {"state": "incomplete" if miss else "complete", "missing": miss, "checked_at": now, "checked_by": "system_completeness_check"},
           "status": ST_INCOMPLETE if miss else ST_PENDING}
    await db[COLL].update_one({"order_id": order_id, "status": {"$in": list(EDITABLE)}}, {"$set": upd})
    await _aud(db, actor, "order_supplemented", o, action="supplement", new_status=upd["status"], missing=miss)
    return _view({**o, **upd})


async def set_authenticity(db, actor: dict, order_id: str, *, verified: bool, method: Any) -> dict:
    _require(actor)
    o = await _get(db, order_id)
    if o["status"] in (ST_CLOSED, ST_EXECUTED, ST_PREPARED):
        raise MS.ModerationError(409, "order_not_editable")
    method = MS.clean_text(method, field="method", min_len=10, max_len=500)
    st = {"state": "verified" if verified else "not_verified", "method": method, "by": actor["user_id"], "at": MS.now_utc()}
    await db[COLL].update_one({"order_id": order_id}, {"$set": {"authenticity": st}})
    await _aud(db, actor, "order_authenticity_set", o, action="authenticity", new_status=st["state"], reasoning=method)
    return {"order_id": order_id, "authenticity": st}


async def legal_review(db, actor: dict, order_id: str, *, decision: str, reasoning: Any, confirm_non_disclosure: bool = False) -> dict:
    _require(actor)
    if decision not in ("approve", "reject", "request_information"):
        raise MS.ModerationError(422, "decision_invalid")
    reasoning = MS.clean_text(reasoning, field="reasoning", min_len=20)
    o = await _get(db, order_id)
    if o["status"] not in (ST_PENDING, ST_INCOMPLETE):
        raise MS.ModerationError(409, "order_not_reviewable")
    if four_eyes() and o.get("created_by") and o["created_by"] == actor["user_id"]:
        raise MS.ModerationError(403, "reviewer_not_independent", "the person who recorded the order cannot approve it")
    if decision == "approve":
        if o["validation"].get("state") != "complete" or o["status"] != ST_PENDING:
            raise MS.ModerationError(409, "order_incomplete", "required elements are missing")
        if o["authenticity"]["state"] != "verified":
            raise MS.ModerationError(409, "authenticity_not_verified")
    new_status = {"approve": ST_APPROVED, "reject": ST_REJECTED, "request_information": ST_INCOMPLETE}[decision]
    lr = {"state": {"approve": "approved", "reject": "rejected", "request_information": "needs_information"}[decision], "reviewer_id": actor["user_id"],
          "reasoning": reasoning, "at": MS.now_utc(), "non_disclosure_confirmed": bool(confirm_non_disclosure and decision == "approve" and o["non_disclosure"]["requested"])}
    res = await db[COLL].update_one({"order_id": order_id, "status": o["status"]}, {"$set": {"legal_review": lr, "status": new_status}})
    if not res.matched_count:
        raise MS.ModerationError(409, "order_changed")
    await _aud(db, actor, "order_legal_review", o, action=decision, new_status=new_status, reasoning=reasoning, non_disclosure_confirmed=lr["non_disclosure_confirmed"])
    return {"order_id": order_id, "status": new_status, "legal_review": lr}


# ------------------------------------------------------------------------------------------------ executie Art. 9
async def execute_art9(db, actor: dict, order_id: str) -> dict:
    _require(actor)
    if not can_execute(actor):
        raise MS.ModerationError(403, "execution_requires_moderator_capability")
    o = await _get(db, order_id)
    if o["order_type"] != ART9:
        raise MS.ModerationError(409, "wrong_order_type")
    if o["status"] not in (ST_APPROVED, ST_PARTIAL) or o["legal_review"]["state"] != "approved" or o["authenticity"]["state"] != "verified":
        raise MS.ModerationError(409, "order_not_approved")
    hide = bool(o["legal_review"].get("non_disclosure_confirmed"))
    targets, errors = o["targets"], 0
    for t in targets:
        if t["state"] == "executed":
            continue                                       # idempotent: reluarea dupa esec partial nu dubleaza deciziile
        try:
            case = await MS.open_own_initiative_case(db, actor, target_type=t["target_type"], target_id=t["target_id"], category="illegal_content",
                                                     summary=f"Executed pursuant to authority order {o['reference']}", kind=MS.KIND_ORDER, order_ref=o["reference"])
            res = await MS.decide(db, actor, case["report_id"], {
                "action": MS.ACTION_REMOVE_ILLEGAL, "legal_ground": f"{o['legal_basis']} (authority order {o['reference']})"[:500],
                "reasoning": f"Content removed pursuant to Article 9 order {o['reference']} issued by {o['authority']['name']} ({o['authority']['member_state']}).",
                "_notify_affected": False if hide else True})
            t.update({"state": "executed", "decision_id": res["decision"]["decision_id"], "error": None, "executed_at": MS.now_utc()})
        except MS.ModerationError as e:
            t.update({"state": "failed", "error": e.code}); errors += 1
        except Exception as e:  # noqa: BLE001
            t.update({"state": "failed", "error": type(e).__name__}); errors += 1
    all_ok = errors == 0
    now = MS.now_utc()
    upd = {"targets": targets, "status": ST_EXECUTED if all_ok else ST_PARTIAL, "recipient_notice": {"status": "withheld_by_order" if hide else "handled_by_statement_of_reasons"}}
    if all_ok:
        upd["milestones.effect_given_at"] = now
    await db[COLL].update_one({"order_id": order_id}, {"$set": upd})
    await _aud(db, actor, "order_executed" if all_ok else "order_execution_failed", o, action="execute_art9", new_status=upd["status"],
               executed=sum(1 for t in targets if t["state"] == "executed"), failed=errors, notification_withheld=hide)
    return {"order_id": order_id, "status": upd["status"], "targets": [{k: t.get(k) for k in ("target_type", "target_id", "state", "error")} for t in targets]}


async def release_withheld_notice(db, actor: dict, order_id: str, reason: Any) -> dict:
    """Cand nu mai exista motiv de a ascunde ordinul: se trimit notificarea si expunerea de motive (cu email) persoanelor afectate."""
    _require(actor)
    reason = MS.clean_text(reason, field="reason", min_len=10)
    o = await _get(db, order_id)
    released = 0
    for t in o["targets"]:
        d = await db.moderation_decisions.find_one({"decision_id": t.get("decision_id")}, {"_id": 0}) if t.get("decision_id") else None
        if not d or not d.get("notification_withheld"):
            continue
        await MS.notify(db, d["affected_user_id"], "moderation_decision", ref={"decision_id": d["decision_id"]},
                        message="A moderation decision affects your content. Open it to read the statement of reasons.")
        await DL.enqueue(db, kind="statement_of_reasons", dedupe_key=f"sor:{d['statement_id']}", recipient={"user_id": d["affected_user_id"]},
                         ref={"sor_id": d["statement_id"], "decision_id": d["decision_id"]})
        await db.moderation_decisions.update_one({"decision_id": d["decision_id"]}, {"$set": {"notification_withheld": False, "notification_released_at": MS.now_utc()}})
        released += 1
    await db[COLL].update_one({"order_id": order_id}, {"$set": {"recipient_notice": {"status": "sent", "released_by": actor["user_id"], "released_at": MS.now_utc()}}})
    await _aud(db, actor, "order_recipient_notice", o, action="release", reasoning=reason, released=released)
    return {"order_id": order_id, "released": released}


# ------------------------------------------------------------------------------------------------ Art. 10: pachet de informatii
async def prepare_information(db, actor: dict, order_id: str) -> dict:
    _require(actor)
    o = await _get(db, order_id)
    if o["order_type"] != ART10:
        raise MS.ModerationError(409, "wrong_order_type")
    if o["status"] != ST_APPROVED or o["legal_review"]["state"] != "approved" or o["authenticity"]["state"] != "verified":
        raise MS.ModerationError(409, "order_not_approved")
    allowed = [i for i in o["requested_items"] if i in ART10_FIELDS]
    refused = [{"item": i, "reason": "outside_whitelist_or_not_collected"} for i in o["requested_items"] if i not in ART10_FIELDS]
    subjects, not_found = [], []
    for t in o["targets"]:
        u = None
        if t.get("target_id"):
            u = await db.users.find_one({"user_id": t["target_id"]}, {"_id": 0})
        if u is None and t.get("account_username"):
            u = await db.users.find_one({"username": {"$regex": f"^{re.escape(t['account_username'])}$", "$options": "i"}}, {"_id": 0})
        if u is None:
            not_found.append(t.get("target_id") or t.get("account_username"))
            continue
        rec = {k: (u.get(k).isoformat() if isinstance(u.get(k), datetime) else u.get(k)) for k in allowed if k in u}
        rec["not_available"] = [k for k in allowed if k not in u]
        subjects.append(rec)
    pkg = {"order_reference": o["reference"], "prepared_at": MS.now_utc().isoformat(), "fields_provided": allowed, "items_refused": refused,
           "never_disclosed_by_policy": list(NEVER_DISCLOSE), "subjects": subjects, "identifiers_not_found": not_found,
           "note": "Only information already collected to provide the service and under Astran's control. Prepared for delivery over the authority's secure channel."}
    data = json.dumps(pkg, ensure_ascii=False, indent=2, default=str).encode()
    att = await add_attachment(db, actor, order_id, filename=f"{o['reference']}-response.json", data=data, kind="response_package")
    info = {"attachment_id": att["attachment_id"], "prepared_by": actor["user_id"], "prepared_at": MS.now_utc(), "fields_provided": allowed,
            "subjects_found": len(subjects), "identifiers_not_found": len(not_found), "items_refused": len(refused), "manual_delivery": None}
    await db[COLL].update_one({"order_id": order_id}, {"$set": {"information_package": info, "status": ST_PREPARED}})
    await _aud(db, actor, "order_information_prepared", o, action="prepare_information", new_status=ST_PREPARED, subjects=len(subjects), fields=allowed, refused=len(refused))
    return {"order_id": order_id, "status": ST_PREPARED, "information_package": info}


async def confirm_package_delivered(db, actor: dict, order_id: str, note: Any) -> dict:
    """Livrarea pachetului se face de un om pe canalul securizat al autoritatii; aici se inregistreaza CONFIRMAREA (nu se presupune)."""
    _require(actor)
    note = MS.clean_text(note, field="note", min_len=10, max_len=500)
    o = await _get(db, order_id)
    if o["status"] != ST_PREPARED:
        raise MS.ModerationError(409, "package_not_prepared")
    now = MS.now_utc()
    await db[COLL].update_one({"order_id": order_id}, {"$set": {"information_package.manual_delivery": {"confirmed_by": actor["user_id"], "at": now, "note": note},
                                                                "milestones.effect_given_at": now}})
    await _aud(db, actor, "order_response_recorded", o, action="package_delivered", reasoning=note)
    # Art. 10(5): informam persoana vizata (daca ordinul nu cere nedivulgarea si juristul a confirmat)
    if not o["legal_review"].get("non_disclosure_confirmed"):
        for t in o["targets"]:
            u = await db.users.find_one({"user_id": t["target_id"]}, {"_id": 0, "user_id": 1}) if t.get("target_id") else None
            if u:
                await MS.notify(db, u["user_id"], "authority_order_information", message="Astran received an order from a competent authority and provided information about your account.")
        await db[COLL].update_one({"order_id": order_id}, {"$set": {"recipient_notice": {"status": "sent", "at": now}}})
    else:
        await db[COLL].update_one({"order_id": order_id}, {"$set": {"recipient_notice": {"status": "withheld_by_order"}}})
    return {"order_id": order_id, "effect_given_at": now}


# ------------------------------------------------------------------------------------------------ raspuns catre autoritate
async def authority_email(db, order_id: str) -> Optional[str]:
    o = await db[COLL].find_one({"order_id": order_id}, {"_id": 0, "authority": 1})
    return ((o or {}).get("authority") or {}).get("contact_email") or None


async def send_response(db, actor: dict, order_id: str, kind: str) -> dict:
    _require(actor)
    if kind not in ("receipt", "effect", "rejection"):
        raise MS.ModerationError(422, "response_kind_invalid")
    o = await _get(db, order_id)
    ok = {"receipt": True,
          "effect": (o["order_type"] == ART9 and o["status"] == ST_EXECUTED) or (o["order_type"] == ART10 and (o.get("information_package") or {}).get("manual_delivery")),
          "rejection": o["status"] == ST_REJECTED}[kind]
    if not ok:
        raise MS.ModerationError(409, "response_not_applicable", "the order state does not allow this response yet")
    if not (o["authority"].get("contact_email")):
        raise MS.ModerationError(409, "authority_email_missing")
    if any(r["kind"] == kind for r in o.get("responses", [])):
        raise MS.ModerationError(409, "response_already_sent")
    rid = MS.new_id("rsp_")
    resp = {"response_id": rid, "kind": kind, "created_at": MS.now_utc(), "created_by": actor["user_id"]}
    await db[COLL].update_one({"order_id": order_id}, {"$push": {"responses": resp}})
    await DL.enqueue(db, kind="authority_response", dedupe_key=f"authresp:{rid}", recipient={"order_id": order_id}, ref={"order_id": order_id, "response_id": rid})
    await _aud(db, actor, "order_response_recorded", o, action=f"response_{kind}", response_id=rid)
    return {"order_id": order_id, "response_id": rid, "delivery": "queued"}


async def render_response_email(db, ref: dict) -> Optional[tuple]:
    o = await db[COLL].find_one({"order_id": ref.get("order_id")}, {"_id": 0})
    r = next((x for x in (o or {}).get("responses", []) if x["response_id"] == ref.get("response_id")), None)
    if not o or not r:
        return None
    eff = MS.aware(o["milestones"].get("effect_given_at"))
    lines = {"receipt": "We confirm receipt of the order.",
             "effect": ("Effect was given to the order on " + eff.strftime("%Y-%m-%d %H:%M UTC") + ". " if eff else "") +
                       ("The requested content was acted upon." if o["order_type"] == ART9 else "The requested information is being delivered over a secure channel; it is not included in this email."),
             "rejection": "The order could not be executed. Reasons will be provided by our legal team through the contact point."}[r["kind"]]
    body = (f"Order reference (Astran): {o['reference']}\nYour reference: {o['authority'].get('their_reference') or '-'}\nOrder type: {o['order_type']}\n\n{lines}\n"
            "This message was sent from the Article 11 contact point and contains no personal data of service recipients.")
    return (f"Astran - order {o['reference']} - {r['kind']}", body)


async def mark_response_sent(db, ref: dict) -> None:
    """Apelata de coada de livrare DUPA confirmarea expeditorului: inregistreaza momentele pentru raportul de transparenta."""
    o = await db[COLL].find_one({"order_id": ref.get("order_id")}, {"_id": 0})
    r = next((x for x in (o or {}).get("responses", []) if x["response_id"] == ref.get("response_id")), None)
    if not o or not r:
        return
    now = MS.now_utc()
    if r["kind"] == "receipt" and not o["milestones"].get("acknowledged_at"):
        await db[COLL].update_one({"order_id": o["order_id"]}, {"$set": {"milestones.acknowledged_at": now}})
    if r["kind"] in ("effect", "rejection") and not o["milestones"].get("closed_at"):
        await db[COLL].update_one({"order_id": o["order_id"]}, {"$set": {"milestones.responded_at": now}})
    await _aud(db, None, "order_response_delivery", o, action=f"delivered_{r['kind']}", response_id=r["response_id"])


# ------------------------------------------------------------------------------------------------ inchidere / retentie
def retention_days() -> Optional[int]:
    try:
        v = int(os.environ.get("ASTRAN_ORDERS_RETENTION_DAYS", ""))
        return v if v > 0 else None
    except ValueError:
        return None


async def close_order(db, actor: dict, order_id: str, note: Any) -> dict:
    _require(actor)
    note = MS.clean_text(note, field="note", min_len=10, max_len=500)
    o = await _get(db, order_id)
    answered = {r["kind"] for r in o.get("responses", [])}
    if o["status"] not in (ST_EXECUTED, ST_PREPARED, ST_REJECTED) or "receipt" not in answered:
        raise MS.ModerationError(409, "order_not_closable", "a receipt response and a final state are required")
    if o["status"] == ST_REJECTED and "rejection" not in answered:
        raise MS.ModerationError(409, "order_not_closable", "inform the authority of the rejection first")
    if o["status"] in (ST_EXECUTED, ST_PREPARED) and "effect" not in answered:
        raise MS.ModerationError(409, "order_not_closable", "inform the authority of the effect given first")
    now = MS.now_utc()
    days = retention_days()
    await db[COLL].update_one({"order_id": order_id}, {"$set": {"status": ST_CLOSED, "milestones.closed_at": now, "closed_note": note,
                                                                "retention_until": (now + timedelta(days=days)) if days else None}})
    await _aud(db, actor, "order_closed", o, action="close", new_status=ST_CLOSED, reasoning=note, retention_days=days)
    return {"order_id": order_id, "status": ST_CLOSED, "retention_until": (now + timedelta(days=days)) if days else None, "retention_configured": bool(days)}


async def purge_expired(db, now: Optional[datetime] = None) -> int:
    """Sterge fisierele si datele personale din ordinele inchise a caror retentie a expirat; raman referinta, tipul si momentele
    (necesare statisticilor Art. 15). Jurnalul de audit nu contine date personale (doar referinte)."""
    now = now or MS.now_utc()
    rows = await db[COLL].find({"status": ST_CLOSED, "retention_until": {"$lte": now}, "purged_at": None}, {"_id": 0}).to_list(500)
    n = 0
    for o in rows:
        for a in await db[ATT].find({"order_id": o["order_id"]}, {"_id": 0}).to_list(100):
            try:
                (storage_dir() / a["stored_path"]).unlink(missing_ok=True)
            except OSError:
                log.warning("could not delete order file %s", a["attachment_id"])
        await db[ATT].delete_many({"order_id": o["order_id"]})
        scrub = {"targets": [{"target_type": t["target_type"], "state": t["state"]} for t in o["targets"]], "statement_of_reasons": None, "legal_basis": None,
                 "authority.contact_email": None, "authority_to_inform": None, "information_package": None, "purged_at": now}
        await db[COLL].update_one({"order_id": o["order_id"]}, {"$set": scrub})
        await _aud(db, None, "order_purged", o, action="purge")
        n += 1
    return n
