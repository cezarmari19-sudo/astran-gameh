"""Consimtamant parental pentru utilizatori sub 16 ani (GDPR art. 8) - masina de stari, tokenuri, panou parental.

Ce DEMONSTREAZA acest modul si ce NU:
  * Link-ul din email + o confirmare dovedesc doar ca cineva controleaza adresa de email si a declarat explicit
    (cu acknowledgements) ca e parinte/tutore. NU dovedesc autoritatea parentala.
  * De aceea `PARENTAL_CONSENT_APPROVED` se atinge EXCLUSIV dupa atestarea unui provider de verificare suplimentara
    (parental_verification.py). Fara provider configurat contul ramane in PARENTAL_VERIFICATION_REQUIRED si
    nicaieri nu se afirma ca verificarea ar fi reusit.

Tokenuri: 256 biti aleatori (`secrets.token_urlsafe(32)`), stocate DOAR ca HMAC; expira; single-use (consum atomic cu
`update_one` conditionat); nu apar in loguri/audit. Emailul parintelui se pastreaza criptat (+ amprenta HMAC pentru
potrivire), nu se stocheaza documente de identitate.

Stari: PENDING_PARENTAL_CONSENT -> PARENTAL_VERIFICATION_REQUIRED -> PARENTAL_CONSENT_APPROVED
       PENDING -> PARENTAL_CONSENT_REJECTED;  (PENDING|VERIFICATION_REQUIRED|APPROVED) -> PARENTAL_CONSENT_WITHDRAWN.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from . import age_audit as AU
from . import age_policy as AP
from .parental_verification import ParentalVerifier, VerificationResult, VerifierError

C_CONSENTS = "parental_consents"
C_TOKENS = "parental_tokens"
C_LINKS = "parental_links"
C_SESSIONS = "parental_verification_sessions"
C_REQUESTS = "parental_data_requests"

DECISION_TTL = timedelta(days=7)
CLAIM_TTL = timedelta(days=30)
WITHDRAW_TTL = timedelta(days=365)
VERIFICATION_TTL = timedelta(hours=1)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_REQUESTS_PER_CHILD_24H = 3
MAX_REQUESTS_PER_PARENT_24H = 5

NOTICE_VERSION = "1.0"
REQUIRED_ACKS = ("read_notice", "is_parent_or_guardian", "understands_withdrawal")

Notifier = Callable[[str, str, str], Awaitable[bool]]


class ParentalError(Exception):
    def __init__(self, code: str, status: int = 400, **extra: Any):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra

    def detail(self) -> dict:
        return {"code": self.code, **self.extra}


# ----------------------------------------------------------------------------- notificarea catre parinte (versionata)
# Temeiurile juridice de mai jos sunt PROPUNERI tehnice si necesita validare juridica inainte de productie.
PROCESSING_ACTIVITIES = [
    {"id": "account_and_service", "basis": "contract_art6_1b",
     "ro": "Cont, autentificare, jocuri, Studio, Shop cu Astrans virtuali, grupuri",
     "en": "Account, sign-in, games, Studio, Shop with virtual Astrans, groups",
     "note": "Pentru sub 16 ani accesul e conditionat de acordul parental (capacitate de exercitiu - de validat juridic)."},
    {"id": "chat_dm", "basis": "contract_art6_1b",
     "ro": "Chat in joc, mesaje private si de grup, cu filtrare pentru minori",
     "en": "In-game chat, private and group messages, with minor-safe filtering", "note": ""},
    {"id": "safety_moderation", "basis": "legitimate_interest_art6_1f_and_legal_obligation_art6_1c",
     "ro": "Siguranta minorilor, rapoarte, moderare, prevenirea abuzului", "en": "Child safety, reports, moderation, abuse prevention",
     "note": ""},
    {"id": "parental_consent_records", "basis": "legal_obligation_art6_1c_art7_1",
     "ro": "Evidenta acordului parental (data, versiune, metoda)", "en": "Record of the parental consent (date, version, method)",
     "note": ""},
    {"id": "marketing", "basis": "consent_art6_1a",
     "ro": "Comunicari de marketing - NU se ofera copiilor sub 16 ani", "en": "Marketing - NOT offered to children under 16",
     "note": ""},
]

_NOTICE = {
    "ro": {
        "title": "Acord parental pentru Astran Game",
        "what": "Astran Game este o platformă de jocuri 3D create de utilizatori: copilul poate juca, crea jocuri în Studio, "
                "vorbi în chat și mesaje private (cu filtrare pentru minori), intra în grupuri și folosi un Shop cu monedă virtuală (Astrans).",
        "data": "Datele prelucrate: nume de utilizator și nume afișat, email, data nașterii (păstrată criptat), avatar, conținutul jocurilor create, "
                "mesaje (chat, mesaje private, grupuri), istoricul Astrans, sesiuni și acceptările legale. Nu se stochează documente de identitate.",
        "purposes": "Scopuri: furnizarea contului și a serviciului, siguranța minorilor și moderarea, respectarea obligațiilor legale și păstrarea dovezii acestui acord.",
        "features": "Dacă aprobați, copilul poate folosi funcțiile pentru minori. Conținutul și chat-ul 18+ rămân blocate.",
        "how": "Puteți autoriza sau refuza din linkul primit. Aprobarea se confirmă separat printr-o verificare suplimentară a calității de părinte/tutore; "
               "fără această verificare contul rămâne în așteptare.",
        "withdraw": "Puteți retrage oricând acordul (din panoul parental sau din linkul de retragere primit după aprobare); retragerea nu afectează legalitatea prelucrării de până atunci, "
                    "iar contul copilului este restricționat. Puteți cere și informații despre date sau ștergerea lor.",
    },
    "en": {
        "title": "Parental consent for Astran Game",
        "what": "Astran Game is a 3D platform for user-created games: your child can play, build games in Studio, chat and send private messages "
                "(with minor-safe filtering), join groups and use a Shop with a virtual currency (Astrans).",
        "data": "Data processed: username and display name, email, date of birth (stored encrypted), avatar, game content, messages (chat, private, group), "
                "Astrans history, sessions and legal acceptances. No identity documents are stored.",
        "purposes": "Purposes: providing the account and service, child safety and moderation, legal obligations and keeping the record of this consent.",
        "features": "If you approve, your child can use the features for minors. 18+ content and 18+ chat stay blocked.",
        "how": "You can authorise or refuse from the link you received. Approval is confirmed separately by an additional check that you are the parent/guardian; "
               "without that check the account stays pending.",
        "withdraw": "You can withdraw consent at any time (from the parental panel or the withdrawal link sent after approval); withdrawal does not affect "
                    "earlier lawful processing, and the child's account is restricted. You can also request information about the data or its erasure.",
    },
}


def _notice_lang(lang: Optional[str]) -> str:
    return "ro" if (lang or "").lower().startswith("ro") else "en"


def notice(lang: Optional[str]) -> dict:
    lg = _notice_lang(lang)
    body = _NOTICE[lg]
    sha = hashlib.sha256(json.dumps({"v": NOTICE_VERSION, "lang": lg, "t": body, "a": [a["id"] + a["basis"] for a in PROCESSING_ACTIVITIES]},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"version": NOTICE_VERSION, "language": lg, "sha256": sha, **body,
            "activities": [{"id": a["id"], "basis": a["basis"], "text": a[lg], "note": a["note"]} for a in PROCESSING_ACTIVITIES]}


def _legal_versions() -> dict:
    try:
        from . import legal_docs as LD
        reg = LD.get_registry()
        return {"terms": reg.current_version("terms_of_service"), "privacy": reg.current_version("privacy_policy")}
    except Exception:  # noqa: BLE001
        return {"terms": None, "privacy": None}


# ----------------------------------------------------------------------------- utilitare
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if isinstance(dt, datetime) and dt.tzinfo is None else dt


_EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")


def mask_email(email: str) -> str:
    local, _, dom = email.partition("@")
    return (local[:1] + "***@" + dom) if local else "***"


async def ensure_indexes(db) -> None:
    await db[C_CONSENTS].create_index("consent_id", unique=True)
    await db[C_CONSENTS].create_index([("child_user_id", 1), ("requested_at", -1)])
    await db[C_CONSENTS].create_index([("parent_email_fp", 1), ("requested_at", -1)])
    await db[C_TOKENS].create_index("token_hash", unique=True)
    await db[C_TOKENS].create_index("expires_at", expireAfterSeconds=60 * 60 * 24 * 30)
    await db[C_LINKS].create_index([("parent_user_id", 1), ("consent_id", 1)], unique=True)
    await db[C_SESSIONS].create_index("session_hash", unique=True)
    await db[C_REQUESTS].create_index("request_id", unique=True)


async def _issue_token(db, consent_id: str, purpose: str, ttl: timedelta, now: datetime) -> str:
    raw = secrets.token_urlsafe(32)
    await db[C_TOKENS].insert_one({
        "token_hash": AP.token_hash(raw), "consent_id": consent_id, "purpose": purpose,
        "created_at": now, "expires_at": now + ttl, "used_at": None,
    })
    return raw


async def _consume_token(db, raw: Any, purpose: str, now: datetime) -> dict:
    """Consum ATOMIC si single-use. Doua cereri paralele cu acelasi token: doar una castiga (matched_count == 1)."""
    if not isinstance(raw, str) or not (20 <= len(raw) <= 128):
        raise ParentalError("token_invalid", 400)
    h = AP.token_hash(raw)
    res = await db[C_TOKENS].update_one(
        {"token_hash": h, "purpose": purpose, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}})
    if res.matched_count == 1:
        return await db[C_TOKENS].find_one({"token_hash": h}, {"_id": 0})
    row = await db[C_TOKENS].find_one({"token_hash": h, "purpose": purpose}, {"_id": 0})
    if not row:
        raise ParentalError("token_invalid", 400)
    if row.get("used_at") is not None:
        raise ParentalError("token_used", 409)
    raise ParentalError("token_expired", 410)


async def _peek_token(db, raw: Any, purpose: str, now: datetime) -> dict:
    if not isinstance(raw, str) or not (20 <= len(raw) <= 128):
        raise ParentalError("token_invalid", 400)
    row = await db[C_TOKENS].find_one({"token_hash": AP.token_hash(raw), "purpose": purpose}, {"_id": 0})
    if not row:
        raise ParentalError("token_invalid", 400)
    if row.get("used_at") is not None:
        raise ParentalError("token_used", 409)
    if _aware(row["expires_at"]) <= now:
        raise ParentalError("token_expired", 410)
    return row


async def _revoke_tokens(db, consent_id: str, now: datetime) -> None:
    await db[C_TOKENS].update_many({"consent_id": consent_id, "used_at": None}, {"$set": {"used_at": now}})


async def _sync_user(db, consent: dict) -> None:
    # chei cu punct: nu suprascriem `parental.self_consent_at` (confirmarea proprie a unui utilizator de 16+)
    await db.users.update_one({"user_id": consent["child_user_id"]}, {"$set": {
        "parental.state": consent["state"], "parental.consent_id": consent["consent_id"],
        "parental.updated_at": consent.get("updated_at")}})


async def _transition(db, consent_id: str, from_states: tuple, to_state: str, fields: dict, now: datetime) -> dict:
    res = await db[C_CONSENTS].update_one(
        {"consent_id": consent_id, "state": {"$in": list(from_states)}},
        {"$set": {"state": to_state, "updated_at": now, **fields}})
    if res.matched_count != 1:
        raise ParentalError("invalid_state_transition", 409)
    c = await db[C_CONSENTS].find_one({"consent_id": consent_id}, {"_id": 0})
    await _sync_user(db, c)
    return c


async def _get_consent(db, consent_id: str) -> dict:
    c = await db[C_CONSENTS].find_one({"consent_id": consent_id}, {"_id": 0})
    if not c:
        raise ParentalError("consent_not_found", 404)
    return c


# ----------------------------------------------------------------------------- email catre parinte
def _app_url(path: str, token: str, base: Optional[str]) -> Optional[str]:
    return f"{base.rstrip('/')}{path}?token={token}" if base else None


def _request_email(lang: str, child_label: str, url: str, expires: datetime) -> tuple[str, str]:
    n = notice(lang)
    if n["language"] == "ro":
        subj = "Astran Game: cerere de acord parental"
        body = (f"Bună,\n\nCineva a creat în Astran Game contul „{child_label}” și a indicat această adresă ca părinte/tutore.\n\n"
                f"{n['what']}\n\n{n['data']}\n\n{n['purposes']}\n\n{n['features']}\n\n{n['how']}\n\n{n['withdraw']}\n\n"
                f"Pentru a citi informarea completă și a autoriza sau refuza, deschideți (valabil până la {expires:%Y-%m-%d %H:%M} UTC, o singură utilizare):\n{url}\n\n"
                "Dacă nu cunoașteți această solicitare, ignorați mesajul: contul rămâne blocat.\n")
    else:
        subj = "Astran Game: parental consent request"
        body = (f"Hello,\n\nSomeone created the Astran Game account \"{child_label}\" and listed this address as parent/guardian.\n\n"
                f"{n['what']}\n\n{n['data']}\n\n{n['purposes']}\n\n{n['features']}\n\n{n['how']}\n\n{n['withdraw']}\n\n"
                f"To read the full notice and authorise or refuse, open (valid until {expires:%Y-%m-%d %H:%M} UTC, single use):\n{url}\n\n"
                "If you do not recognise this request, ignore this message: the account stays blocked.\n")
    return subj, body


def _approved_email(lang: str, child_label: str, withdraw_url: Optional[str]) -> tuple[str, str]:
    if _notice_lang(lang) == "ro":
        return ("Astran Game: acord parental confirmat",
                f"Acordul parental pentru contul „{child_label}” a fost confirmat.\n"
                + (f"Pentru a-l retrage oricând (o singură utilizare): {withdraw_url}\n" if withdraw_url else "")
                + "Îl puteți retrage și din panoul parental din aplicație.\n")
    return ("Astran Game: parental consent confirmed",
            f"Parental consent for the account \"{child_label}\" has been confirmed.\n"
            + (f"To withdraw it at any time (single use): {withdraw_url}\n" if withdraw_url else "")
            + "You can also withdraw it from the parental panel in the app.\n")


# ----------------------------------------------------------------------------- 1) cererea (copil)
async def request_consent(db, child: dict, parent_email: Any, *, lang: str = "ro", notifier: Optional[Notifier] = None,
                          app_base_url: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    info = AP.evaluate_access(child, now.date())
    # sub 16: obligatoriu. 16-17 DECLARAT si neverificat: poate alege acordul parental ca alternativa la verificarea
    # varstei (data declarata nu dovedeste ca are 16+). Adultul declarat nu are aceasta cale (verifica varsta).
    if not (info.band == AP.BAND_UNDER_16 or (info.band == AP.BAND_16_17 and info.parental_option
                                               and info.consent_eligibility == AP.ELIG_UNVERIFIED)):
        raise ParentalError("parental_consent_not_required", 409)
    if not isinstance(parent_email, str) or not _EMAIL_RX.match(parent_email.strip()):
        raise ParentalError("parent_email_invalid", 422)
    email = AP.normalize_email(parent_email)
    if email == AP.normalize_email(child.get("email") or ""):
        raise ParentalError("parent_email_same_as_child", 422)
    fp = AP.email_fingerprint(email)
    day_ago = now - timedelta(hours=24)

    mine = await db[C_CONSENTS].find({"child_user_id": child["user_id"], "requested_at": {"$gt": day_ago}}, {"_id": 0}).sort("requested_at", -1).to_list(50)
    if mine and (now - _aware(mine[0]["requested_at"])) < RESEND_COOLDOWN:
        raise ParentalError("too_many_requests", 429, retry_after=int(RESEND_COOLDOWN.total_seconds()))
    if len(mine) >= MAX_REQUESTS_PER_CHILD_24H:
        raise ParentalError("too_many_requests", 429, retry_after=3600)
    to_parent = await db[C_CONSENTS].count_documents({"parent_email_fp": fp, "requested_at": {"$gt": day_ago}})
    if to_parent >= MAX_REQUESTS_PER_PARENT_24H:
        raise ParentalError("too_many_requests", 429, retry_after=3600)

    # cererile vechi neinchise ale acestui copil se inlocuiesc (nu raman doua link-uri valide in paralel)
    for old in await db[C_CONSENTS].find({"child_user_id": child["user_id"], "state": {"$in": [AP.PARENTAL_PENDING, AP.PARENTAL_VERIFICATION_REQUIRED]}}, {"_id": 0}).to_list(20):
        await db[C_CONSENTS].update_one({"consent_id": old["consent_id"]}, {"$set": {"superseded": True, "updated_at": now}})
        await _revoke_tokens(db, old["consent_id"], now)

    n = notice(lang)
    versions = _legal_versions()
    consent_id = "pc_" + secrets.token_urlsafe(12)
    consent = {
        "consent_id": consent_id, "child_user_id": child["user_id"], "state": AP.PARENTAL_PENDING,
        "parent_email_enc": AP.seal(email), "parent_email_fp": fp, "parent_email_hint": mask_email(email),
        "requested_at": now, "expires_at": now + DECISION_TTL, "updated_at": now, "decided_at": None, "approved_at": None,
        "withdrawn_at": None, "notice_version": n["version"], "notice_sha256": n["sha256"], "notice_language": n["language"],
        "terms_version": versions["terms"], "privacy_version": versions["privacy"],
        "verification_method": None, "verification_status": "none", "verified_at": None, "verification_ref": None,
        "parent_declaration": None, "superseded": False, "delivery": "not_attempted",
    }
    await db[C_CONSENTS].insert_one(dict(consent))
    await _sync_user(db, consent)
    token = await _issue_token(db, consent_id, "decision", DECISION_TTL, now)
    url = _app_url("/parental/decision", token, app_base_url)
    delivery = "not_configured"
    if url and notifier is not None:
        subj, body = _request_email(n["language"], child.get("username") or "?", url, consent["expires_at"])
        try:
            delivery = "sent" if await notifier(email, subj, body) else "failed"
        except Exception:  # noqa: BLE001
            delivery = "failed"
    await db[C_CONSENTS].update_one({"consent_id": consent_id}, {"$set": {"delivery": delivery}})
    await AU.record(db, AU.PARENTAL_REQUESTED, subject_user_id=child["user_id"], actor="user",
                    details={"consent_id": consent_id, "notice_version": n["version"], "delivery": delivery}, now=now)
    return {"consent_id": consent_id, "state": AP.PARENTAL_PENDING, "delivery": delivery,
            "parent_email_hint": consent["parent_email_hint"], "expires_at": consent["expires_at"]}


# ----------------------------------------------------------------------------- 2) parintele: citeste si decide
async def peek_notice(db, token: Any, lang: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    row = await _peek_token(db, token, "decision", now)
    c = await _get_consent(db, row["consent_id"])
    if c["state"] != AP.PARENTAL_PENDING or c.get("superseded"):
        raise ParentalError("consent_not_pending", 409)
    child = await db.users.find_one({"user_id": c["child_user_id"]}, {"_id": 0, "username": 1}) or {}
    return {"consent_id": c["consent_id"], "child_label": child.get("username"), "notice": notice(lang or c.get("notice_language")),
            "expires_at": row["expires_at"], "required_acknowledgements": list(REQUIRED_ACKS)}


async def _start_verification(db, consent: dict, verifier: ParentalVerifier, now: datetime) -> Optional[dict]:
    if not verifier.configured():
        await db[C_CONSENTS].update_one({"consent_id": consent["consent_id"]}, {"$set": {"verification_status": "not_configured"}})
        return None
    vid = secrets.token_urlsafe(24)
    start = verifier.start(vid)
    await db[C_SESSIONS].insert_one({"session_hash": AP.token_hash(vid), "consent_id": consent["consent_id"], "created_at": now,
                                     "expires_at": now + VERIFICATION_TTL, "used_at": None, "method": start.method})
    await db[C_CONSENTS].update_one({"consent_id": consent["consent_id"]},
                                    {"$set": {"verification_status": "pending", "verification_method": start.method}})
    return {"method": start.method, "redirect_url": start.redirect_url}


def _check_acks(acks: Any) -> dict:
    if not isinstance(acks, dict) or any(acks.get(k) is not True for k in REQUIRED_ACKS):
        raise ParentalError("acknowledgement_required", 422, required=list(REQUIRED_ACKS))
    return {k: True for k in REQUIRED_ACKS}


async def decide(db, token: Any, decision: Any, acks: Any, *, verifier: ParentalVerifier, parent_user: Optional[dict] = None,
                 now: Optional[datetime] = None) -> dict:
    now = now or _now()
    if decision not in ("approve", "reject"):
        raise ParentalError("invalid_decision", 422)
    ack_ok = _check_acks(acks) if decision == "approve" else {}
    row = await _consume_token(db, token, "decision", now)
    c = await _get_consent(db, row["consent_id"])
    return await _apply_decision(db, c, decision, ack_ok, verifier=verifier, parent_user=parent_user, actor="parent", now=now)


async def _apply_decision(db, c: dict, decision: str, ack_ok: dict, *, verifier: ParentalVerifier, parent_user: Optional[dict],
                          actor: str, now: datetime) -> dict:
    if c.get("superseded") or c["state"] != AP.PARENTAL_PENDING:
        raise ParentalError("consent_not_pending", 409)
    if _aware(c["expires_at"]) <= now:
        raise ParentalError("token_expired", 410)
    if decision == "reject":
        c2 = await _transition(db, c["consent_id"], (AP.PARENTAL_PENDING,), AP.PARENTAL_REJECTED, {"decided_at": now}, now)
        await _revoke_tokens(db, c["consent_id"], now)
        await AU.record(db, AU.PARENTAL_REJECTED, subject_user_id=c["child_user_id"], actor="parent",
                        details={"consent_id": c["consent_id"], "notice_version": c["notice_version"]}, now=now)
        return {"state": c2["state"], "verification": None, "claim_token": None}
    decl = {"at": now, "acknowledged": ack_ok, "notice_sha256": c["notice_sha256"], "notice_version": c["notice_version"]}
    c2 = await _transition(db, c["consent_id"], (AP.PARENTAL_PENDING,), AP.PARENTAL_VERIFICATION_REQUIRED,
                           {"decided_at": now, "parent_declaration": decl}, now)
    start = await _start_verification(db, c2, verifier, now)
    claim = await _issue_token(db, c["consent_id"], "claim", CLAIM_TTL, now)
    if parent_user is not None:
        await _link_if_eligible(db, parent_user, c2, now)
    await AU.record(db, AU.PARENTAL_VERIFICATION_STARTED, subject_user_id=c["child_user_id"], actor=actor,
                    details={"consent_id": c["consent_id"], "method": (start or {}).get("method") or "not_configured",
                             "status": "pending" if start else "not_configured"}, now=now)
    return {"state": c2["state"], "verification": start, "verification_configured": start is not None, "claim_token": claim}


# ----------------------------------------------------------------------------- 3) verificarea suplimentara (provider)
async def complete_verification(db, result: VerificationResult, *, notifier: Optional[Notifier] = None,
                                app_base_url: Optional[str] = None, on_approved: Optional[Callable[[str], Awaitable[None]]] = None,
                                now: Optional[datetime] = None) -> dict:
    """Apelat DOAR dupa ce atestarea semnata a fost validata (parse_callback). Sesiunea e single-use; nonce-ul e inregistrat."""
    now = now or _now()
    h = AP.token_hash(result.verification_id)
    sess = await db[C_SESSIONS].find_one({"session_hash": h}, {"_id": 0})
    if not sess or sess["consent_id"] != result.consent_id:
        raise VerifierError("unknown_session", 404)
    res = await db[C_SESSIONS].update_one({"session_hash": h, "used_at": None, "expires_at": {"$gt": now}}, {"$set": {"used_at": now, "nonce": result.nonce}})
    if res.matched_count != 1:
        raise VerifierError("session_used_or_expired", 409)
    c = await _get_consent(db, result.consent_id)
    if not result.parent_verified:
        await db[C_CONSENTS].update_one({"consent_id": c["consent_id"]}, {"$set": {"verification_status": "failed", "updated_at": now}})
        await AU.record(db, AU.PARENTAL_VERIFICATION_FAILED, subject_user_id=c["child_user_id"], actor="provider",
                        details={"consent_id": c["consent_id"], "method": result.method}, now=now)
        return {"state": c["state"], "approved": False}
    c2 = await _transition(db, c["consent_id"], (AP.PARENTAL_VERIFICATION_REQUIRED,), AP.PARENTAL_APPROVED, {
        "approved_at": now, "verified_at": now, "verification_method": result.method, "verification_status": "verified",
        "verification_ref": AP.pseudonym(result.verification_id, "verification")}, now)
    await AU.record(db, AU.PARENTAL_APPROVED, subject_user_id=c["child_user_id"], actor="provider",
                    details={"consent_id": c["consent_id"], "method": result.method, "notice_version": c["notice_version"],
                             "notice_sha256": c["notice_sha256"], "terms_version": c.get("terms_version"), "privacy_version": c.get("privacy_version")}, now=now)
    if on_approved is not None:
        try:
            await on_approved(c["child_user_id"])
        except Exception:  # noqa: BLE001 - un hook esuat nu anuleaza aprobarea deja inregistrata
            pass
    if notifier is not None:
        email = AP.unseal(c.get("parent_email_enc"))
        child = await db.users.find_one({"user_id": c["child_user_id"]}, {"_id": 0, "username": 1}) or {}
        wtoken = await _issue_token(db, c["consent_id"], "withdraw", WITHDRAW_TTL, now)
        subj, body = _approved_email(c.get("notice_language") or "ro", child.get("username") or "?", _app_url("/parental/withdraw", wtoken, app_base_url))
        try:
            if email:
                await notifier(email, subj, body)
        except Exception:  # noqa: BLE001
            pass
    return {"state": c2["state"], "approved": True}


# ----------------------------------------------------------------------------- 4) retragere
async def _do_withdraw(db, c: dict, actor: str, now: datetime, on_withdrawn: Optional[Callable[[str], Awaitable[None]]]) -> dict:
    c2 = await _transition(db, c["consent_id"], (AP.PARENTAL_PENDING, AP.PARENTAL_VERIFICATION_REQUIRED, AP.PARENTAL_APPROVED),
                           AP.PARENTAL_WITHDRAWN, {"withdrawn_at": now}, now)
    await _revoke_tokens(db, c["consent_id"], now)
    await AU.record(db, AU.PARENTAL_WITHDRAWN, subject_user_id=c["child_user_id"], actor=actor,
                    details={"consent_id": c["consent_id"], "from_state": c["state"]}, now=now)
    if on_withdrawn is not None:
        try:
            await on_withdrawn(c["child_user_id"])
        except Exception:  # noqa: BLE001
            pass
    return {"state": c2["state"]}


async def withdraw_by_token(db, token: Any, *, on_withdrawn=None, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    row = await _consume_token(db, token, "withdraw", now)
    return await _do_withdraw(db, await _get_consent(db, row["consent_id"]), "parent", now, on_withdrawn)


# ----------------------------------------------------------------------------- 5) panou parental (cont logat)
def _parent_account_ok(parent_user: dict, now: datetime) -> None:
    if not parent_user.get("email_verified") or not parent_user.get("email"):
        raise ParentalError("parent_email_not_verified", 403)
    info = AP.evaluate_access(parent_user, now.date())
    # cont de parinte = adult DOVEDIT (atestare 18 sigilata); o data de nastere declarata nu e suficienta, altfel un copil
    # si-ar putea crea un al doilea cont "adult" si si-ar aproba singur consimtamantul.
    if info.age_category != AP.CAT_ADULT:
        raise ParentalError("parent_account_not_adult", 403)


def _email_matches(parent_user: dict, c: dict) -> bool:
    return bool(parent_user.get("email")) and AP.email_fingerprint(parent_user["email"]) == c.get("parent_email_fp")


async def _link_if_eligible(db, parent_user: dict, c: dict, now: datetime) -> bool:
    try:
        _parent_account_ok(parent_user, now)
    except ParentalError:
        return False
    if parent_user["user_id"] == c["child_user_id"] or not _email_matches(parent_user, c):
        return False
    try:
        await db[C_LINKS].insert_one({"parent_user_id": parent_user["user_id"], "consent_id": c["consent_id"],
                                      "child_user_id": c["child_user_id"], "created_at": now})
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ != "DuplicateKeyError":
            raise
        return True
    await AU.record(db, AU.PARENTAL_LINKED, subject_user_id=c["child_user_id"], actor="parent", details={"consent_id": c["consent_id"]}, now=now)
    return True


async def claim_link(db, parent_user: dict, claim_token: Any, now: Optional[datetime] = None) -> dict:
    """Leaga contul logat de copil. Cere ambele: tokenul emis la decizie SI email verificat identic cu cel din cerere."""
    now = now or _now()
    _parent_account_ok(parent_user, now)
    row = await _peek_token(db, claim_token, "claim", now)
    c = await _get_consent(db, row["consent_id"])
    if parent_user["user_id"] == c["child_user_id"] or not _email_matches(parent_user, c):
        raise ParentalError("forbidden", 403)
    await _consume_token(db, claim_token, "claim", now)
    await _link_if_eligible(db, parent_user, c, now)
    return {"consent_id": c["consent_id"]}


async def _authorize(db, parent_user: dict, consent_id: str, now: datetime, *, allow_unlinked_pending: bool = False) -> dict:
    """Autorizare pe backend la FIECARE actiune de panou: link existent + email verificat potrivit. Altfel 404 (nu 403,
    ca sa nu se poata sonda existenta consimtamintelor altora)."""
    _parent_account_ok(parent_user, now)
    c = await db[C_CONSENTS].find_one({"consent_id": str(consent_id)[:64]}, {"_id": 0})
    if not c or parent_user["user_id"] == c["child_user_id"] or not _email_matches(parent_user, c):
        raise ParentalError("consent_not_found", 404)
    link = await db[C_LINKS].find_one({"parent_user_id": parent_user["user_id"], "consent_id": c["consent_id"]}, {"_id": 0})
    if not link and not (allow_unlinked_pending and c["state"] == AP.PARENTAL_PENDING):
        raise ParentalError("consent_not_found", 404)
    return c


def _public_consent(c: dict, child: Optional[dict] = None) -> dict:
    return {
        "consent_id": c["consent_id"], "state": c["state"], "requested_at": c["requested_at"], "decided_at": c.get("decided_at"),
        "approved_at": c.get("approved_at"), "withdrawn_at": c.get("withdrawn_at"), "expires_at": c.get("expires_at"),
        "notice_version": c.get("notice_version"), "terms_version": c.get("terms_version"), "privacy_version": c.get("privacy_version"),
        "verification_method": c.get("verification_method"), "verification_status": c.get("verification_status"),
        "child": {"username": (child or {}).get("username"), "display_name": (child or {}).get("display_name")} if child else None,
    }


async def list_children(db, parent_user: dict, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    _parent_account_ok(parent_user, now)
    out, seen = [], set()
    links = await db[C_LINKS].find({"parent_user_id": parent_user["user_id"]}, {"_id": 0}).to_list(200)
    for l in links:
        c = await db[C_CONSENTS].find_one({"consent_id": l["consent_id"]}, {"_id": 0})
        if c and _email_matches(parent_user, c):
            child = await db.users.find_one({"user_id": c["child_user_id"]}, {"_id": 0, "username": 1, "display_name": 1})
            out.append(_public_consent(c, child)); seen.add(c["consent_id"])
    fp = AP.email_fingerprint(parent_user["email"])
    pend = await db[C_CONSENTS].find({"parent_email_fp": fp, "state": AP.PARENTAL_PENDING, "superseded": False}, {"_id": 0}).to_list(50)
    pending = []
    for c in pend:
        if c["consent_id"] in seen or _aware(c["expires_at"]) <= now:
            continue
        child = await db.users.find_one({"user_id": c["child_user_id"]}, {"_id": 0, "username": 1, "display_name": 1})
        pending.append(_public_consent(c, child))
    return {"children": out, "pending_requests": pending}


async def panel_decide(db, parent_user: dict, consent_id: str, decision: Any, acks: Any, *, verifier: ParentalVerifier,
                       now: Optional[datetime] = None) -> dict:
    now = now or _now()
    if decision not in ("approve", "reject"):
        raise ParentalError("invalid_decision", 422)
    ack_ok = _check_acks(acks) if decision == "approve" else {}
    c = await _authorize(db, parent_user, consent_id, now, allow_unlinked_pending=True)
    await _link_if_eligible(db, parent_user, c, now)
    # tokenurile de decizie ale acestei cereri devin inutile: o singura decizie, indiferent pe ce cale
    await db[C_TOKENS].update_many({"consent_id": c["consent_id"], "purpose": "decision", "used_at": None}, {"$set": {"used_at": now}})
    return await _apply_decision(db, c, decision, ack_ok, verifier=verifier, parent_user=parent_user, actor="parent", now=now)


async def panel_restart_verification(db, parent_user: dict, consent_id: str, verifier: ParentalVerifier, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    c = await _authorize(db, parent_user, consent_id, now)
    if c["state"] != AP.PARENTAL_VERIFICATION_REQUIRED:
        raise ParentalError("invalid_state_transition", 409)
    start = await _start_verification(db, c, verifier, now)
    return {"state": c["state"], "verification": start, "verification_configured": start is not None}


async def panel_withdraw(db, parent_user: dict, consent_id: str, *, on_withdrawn=None, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    c = await _authorize(db, parent_user, consent_id, now)
    return await _do_withdraw(db, c, "parent", now, on_withdrawn)


async def child_data_summary(db, child_id: str) -> dict:
    return {
        "categories": ["account (username, display name, email, avatar)", "date of birth (encrypted; only the derived age band is used)",
                       "games and Studio content", "chat / private / group messages", "Astrans ledger", "consents and audit records"],
        "counts": {"games": await db.games.count_documents({"owner_id": child_id}),
                   "consent_events": await db.consent_events.count_documents({"user_id": child_id})},
        "legal_bases": [{"id": a["id"], "basis": a["basis"]} for a in PROCESSING_ACTIVITIES],
        "note": "Pentru o copie completa a datelor se foloseste exportul de date al contului (GDPR art. 15/20).",
    }


async def create_data_request(db, parent_user: dict, consent_id: str, kind: Any, *, confirm: bool = False,
                              erase_child: Optional[Callable[[str], Awaitable[Any]]] = None, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    if kind not in ("access", "erasure"):
        raise ParentalError("invalid_request", 422)
    c = await _authorize(db, parent_user, consent_id, now)
    rid = "pr_" + secrets.token_urlsafe(12)
    status, summary = "received", None
    if kind == "access":
        summary, status = await child_data_summary(db, c["child_user_id"]), "fulfilled"
    else:
        if confirm is not True:
            raise ParentalError("confirmation_required", 422)
        # Stergerea automata doar daca acordul a fost verificat cu o metoda reala; altfel cerere inregistrata
        # pentru verificare manuala (un email singur nu justifica stergerea contului altcuiva).
        if c.get("verification_method") and c.get("verified_at") and c["state"] in (AP.PARENTAL_APPROVED, AP.PARENTAL_WITHDRAWN) and erase_child:
            if c["state"] == AP.PARENTAL_APPROVED:
                await _do_withdraw(db, c, "parent", now, None)
            await erase_child(c["child_user_id"])
            status = "erasure_started"
        else:
            status = "pending_manual_review"
    await db[C_REQUESTS].insert_one({"request_id": rid, "consent_id": c["consent_id"], "child_user_id": c["child_user_id"],
                                     "parent_user_id": parent_user["user_id"], "kind": kind, "status": status, "created_at": now})
    await AU.record(db, AU.PARENTAL_DATA_REQUEST, subject_user_id=c["child_user_id"], actor="parent",
                    details={"consent_id": c["consent_id"], "kind": kind, "status": status}, now=now)
    return {"request_id": rid, "kind": kind, "status": status, "summary": summary}


async def child_status(db, child: dict) -> Optional[dict]:
    rows = await db[C_CONSENTS].find({"child_user_id": child["user_id"], "superseded": False}, {"_id": 0}).sort("requested_at", -1).limit(1).to_list(1)
    if not rows:
        return None
    c = rows[0]
    return {"consent_id": c["consent_id"], "state": c["state"], "requested_at": c["requested_at"], "expires_at": c.get("expires_at"),
            "approved_at": c.get("approved_at"), "parent_email_hint": c.get("parent_email_hint"), "delivery": c.get("delivery"),
            "verification_status": c.get("verification_status"), "verification_method": c.get("verification_method")}
