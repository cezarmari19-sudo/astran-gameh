"""Notificari Art. 16 pentru persoane NEINREGISTRATE (Art. 16(1): "orice persoana sau entitate"), cu protectie anti-abuz.

  * Fara cont: cer nume + email (cu exceptia categoriei child_safety, Art. 16(2)(c)); declaratie de buna-credinta obligatorie.
  * Doar continut public (jocuri, conturi): mesajele private NU pot fi raportate anonim (nu confirmam existenta lor).
  * Anti-abuz: limita pe IP (in ruta), plafon zilnic + dedup pe hash de email, camp-capcana; hash-urile sunt HMAC (cheie secreta),
    se pastreaza doar cat e nevoie (`purge_abuse_metadata`) si nu se afiseaza nimanui.
  * Urmarire: referinta (report_id) + token secret afisat O SINGURA DATA; in DB se pastreaza doar sha256(token).
  * Confirmarea de primire si rezultatul se trimit pe email prin coada urmarita (dsa_delivery).
  * Limitare: notificatorii anonimi nu pot depune plangere Art. 20 prin aplicatie (nu au cont): pot folosi canalul de contact.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional

from . import moderation_service as MS

_PROCESS_KEY = secrets.token_bytes(32)           # implicit: hash-urile nu raman stabile la repornire (mai sigur decat o cheie implicita cunoscuta)
PUBLIC_TARGETS = ("game", "user")
DEFAULT_DAILY_CAP = 10


def _key() -> bytes:
    k = os.environ.get("ASTRAN_ABUSE_HASH_KEY")
    return k.encode() if k else _PROCESS_KEY


def abuse_hash(value: str) -> str:
    return hmac.new(_key(), value.strip().lower().encode(), hashlib.sha256).hexdigest()


def _daily_cap() -> int:
    try:
        return max(1, int(os.environ.get("ASTRAN_PUBLIC_NOTICE_DAILY_CAP", DEFAULT_DAILY_CAP)))
    except ValueError:
        return DEFAULT_DAILY_CAP


async def _resolve_target(db, body: dict) -> tuple:
    ref = body.get("content_url") or body.get("content_id_ref")
    tt, tid = body.get("target_type"), body.get("target_id")
    if ref:
        parsed = MS.parse_content_reference(ref)
        if not parsed:
            raise MS.ModerationError(422, "content_reference_invalid", "unrecognised content link")
        tt, tid = parsed
    elif body.get("account_username") and not tid:
        u = await db.users.find_one({"username": {"$regex": "^" + __import__("re").escape(str(body["account_username"]).strip()[:64]) + "$", "$options": "i"}},
                                    {"_id": 0, "user_id": 1})
        if not u:
            raise MS.ModerationError(404, "target_not_found")
        tt, tid = "user", u["user_id"]
    if tt not in PUBLIC_TARGETS or not isinstance(tid, str) or not (1 <= len(tid) <= 64):
        raise MS.ModerationError(422, "target_invalid", "provide a link to a public game, or an account username")
    return tt, tid


async def submit_public_notice(db, body: dict, *, ip: str, target_exists: Callable[[str, str, dict], Awaitable[bool]]) -> dict:
    if body.get("hp"):                                         # camp-capcana: un om nu il completeaza
        raise MS.ModerationError(422, "submission_rejected")
    tt, tid = await _resolve_target(db, body)
    if not await target_exists(tt, tid, {"user_id": None}):
        raise MS.ModerationError(404, "target_not_found")
    if tt == "game":      # anonim: doar continut PUBLIC (nu confirmam existenta jocurilor private/draft)
        g = await db.games.find_one({"game_id": tid}, {"_id": 0, "is_public": 1})
        if not g or g.get("is_public") is False:
            raise MS.ModerationError(404, "target_not_found")
    category = body.get("category") if body.get("category") in MS.CATEGORIES else "illegal_content"
    email = MS.clean_text(body.get("reporter_email"), field="reporter_email", max_len=320)
    ehash = abuse_hash(email) if email else None
    since = MS.now_utc() - timedelta(hours=24)
    if ehash:
        dup = await db.reports.find_one({"submitter.email_hash": ehash, "target_type": tt, "target_id": tid, "category": category,
                                         "created_at": {"$gte": since}}, {"_id": 0, "report_id": 1})
        if dup:
            raise MS.ModerationError(409, "duplicate_notice", f"a notice for this content was already received ({dup['report_id']})")
        if await db.reports.count_documents({"submitter.email_hash": ehash, "created_at": {"$gte": since}}) >= _daily_cap():
            raise MS.ModerationError(429, "notice_limit_reached", "daily limit of notices for this contact reached")
    token = secrets.token_urlsafe(24)
    rid = MS.new_id("rep_")
    doc = {"report_id": rid, "reporter_id": None, "target_type": tt, "target_id": tid, "reason": "illegal_content", "details": None,
           "status": "open", "created_at": MS.now_utc(),
           "submitter": {"email_hash": ehash, "ip_hash": abuse_hash(ip or "unknown")},
           "public_access": {"token_hash": hashlib.sha256(token.encode()).hexdigest()}}
    notice = {k: body.get(k) for k in ("explanation", "alleged_legal_basis", "content_location", "reporter_name", "reporter_email", "identity_exempt",
                                       "good_faith_declaration")}
    notice["content_location"] = notice.get("content_location") or body.get("content_url")
    receipt = await MS.register_report(db, doc, kind=MS.KIND_NOTICE, category=category, notice=notice)
    return {"report_id": rid, "tracking_token": token, "receipt": receipt,
            "token_notice": "Keep this token: it is shown only once and is required to check the status."}


async def public_status(db, report_id: Any, token: Any) -> dict:
    if not isinstance(report_id, str) or not isinstance(token, str) or len(token) > 200:
        raise MS.ModerationError(404, "not_found")
    r = await db.reports.find_one({"report_id": report_id, "reporter_id": None, "report_kind": MS.KIND_NOTICE}, {"_id": 0})
    th = ((r or {}).get("public_access") or {}).get("token_hash")
    if not r or not th or not hmac.compare_digest(th, hashlib.sha256(token.encode()).hexdigest()):
        raise MS.ModerationError(404, "not_found")                   # acelasi raspuns pentru "nu exista" si "token gresit"
    v = MS.public_report_view(r)
    decs = await db.moderation_decisions.find({"report_id": report_id}, {"_id": 0}).sort("created_at", 1).to_list(10)
    v["outcomes"] = [MS.notifier_outcome(d) for d in decs]
    return v


async def purge_abuse_metadata(db, days: int = 30) -> int:
    """Retentie: dupa `days` zile hash-urile anti-abuz (IP/email) se sterg din rapoarte; rapoartele raman."""
    cutoff = MS.now_utc() - timedelta(days=days)
    res = await db.reports.update_many({"submitter": {"$exists": True}, "created_at": {"$lt": cutoff}}, {"$unset": {"submitter": ""}})
    return res.modified_count
