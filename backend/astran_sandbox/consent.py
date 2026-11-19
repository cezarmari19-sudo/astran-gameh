"""Consimtamant GDPR demonstrabil (Art. 7) - registru append-only pe server.

Principii (toate aplicate pe SERVER; clientul nu este o dovada):
  * `consent_events` este un jurnal ADAUGA-DOAR: aplicatia nu are nicio cale de UPDATE/DELETE pe el (singura
    exceptie e pseudonimizarea la stergerea contului, vezi pseudonymize_for_deletion). Starea curenta se deduce din
    ultimul eveniment al fiecarui "subiect" (un document sau un scop de prelucrare);
  * data/ora (UTC), versiunea documentului si amprenta lui sunt determinate de server; ce trimite clientul
    (`presented_versions`) e doar COMPARAT cu versiunea curenta ca sa nu inregistram o versiune pe care userul n-a
    vazut-o - niciodata nu e salvat ca atare;
  * Termenii (contract) si Politica de Confidentialitate (informare) sunt acceptari/confirmari, NU consimtaminte
    GDPR si nu se "retrag" prin acest mecanism; consimtamintele reale sunt "scopuri" (PURPOSES) cu temei = consent;
  * refuzul unui scop optional nu inseamna niciun eveniment (absenta = fara consimtamant) si nu blocheaza nimic;
  * nu se stocheaza IP, user-agent sau alte date suplimentare: doar ce e necesar ca dovada.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import legal_docs as LD

log = logging.getLogger("astran.consent")

COLLECTION = "consent_events"
ACTION_ACCEPTED = "accepted"
ACTION_WITHDRAWN = "withdrawn"
SOURCES = ("register", "google_signup", "settings", "reaccept")

# ----------------------------------------------------------------------------- scopuri bazate pe consimtamant
# Se adauga aici DOAR prelucrari care chiar exista in aplicatie si au nevoie de consimtamant. Textul (wording) este
# versionat: daca il schimbi, creste `version` - amprenta salvata in dovada este a textului exact prezentat.
# `min_age_category`: categoria de varsta e doar declarata si "under_18" include copii sub varsta la care un copil
# poate consimti singur (Art. 8 GDPR; 16 ani in Romania), deci scopurile bazate pe consimtamant sunt oferite doar adultilor.
PURPOSES: dict[str, dict] = {
    "marketing_communications": {
        "version": "1.0",
        "min_age_category": "adult_18",
        "requires_confirmation_to_withdraw": False,
        "texts": {
            "ro": {
                "title": "Comunicări de marketing",
                "description": "Poți primi pe email noutăți, oferte și informații despre Astran Game. "
                               "Nu este necesar pentru a crea un cont sau a juca.",
                "consequences": "După retragere nu vei mai primi comunicări de marketing. Contul și jocurile tale nu sunt "
                                "afectate. Mesajele necesare serviciului (de exemplu confirmarea emailului sau resetarea "
                                "parolei) vor continua să fie trimise.",
            },
            "en": {
                "title": "Marketing communications",
                "description": "You may receive news, offers and information about Astran Game by email. "
                               "It is not required to create an account or play.",
                "consequences": "After withdrawal you will no longer receive marketing communications. Your account and games "
                                "are not affected. Messages needed to run the service (for example email confirmation or "
                                "password reset) will still be sent.",
            },
        },
    },
}

_MSG = {
    "consent_required": {
        "ro": "Pentru a crea un cont trebuie să accepți Termenii și Condițiile și să confirmi că ai luat la cunoștință Politica de Confidențialitate.",
        "en": "To create an account you must accept the Terms of Service and acknowledge the Privacy Policy.",
    },
    "document_version_changed": {
        "ro": "Documentele juridice au fost actualizate. Reîncarcă și citește versiunea curentă înainte de a accepta.",
        "en": "The legal documents were updated. Reload and read the current version before accepting.",
    },
    "unknown_subject": {"ro": "Document sau scop necunoscut.", "en": "Unknown document or purpose."},
    "purpose_not_available": {
        "ro": "Acest consimțământ nu este disponibil pentru contul tău.",
        "en": "This consent is not available for your account.",
    },
    "not_withdrawable": {
        "ro": "Acest document nu este un consimțământ și nu poate fi retras de aici. Dacă nu mai dorești să respecți Termenii, poți șterge contul din Setări › Confidențialitate.",
        "en": "This document is not a consent and cannot be withdrawn here. If you no longer wish to be bound by the Terms, you can delete your account in Settings › Privacy.",
    },
    "confirmation_required": {
        "ro": "Confirmă retragerea după ce ai citit consecințele.",
        "en": "Confirm the withdrawal after reading the consequences.",
    },
    "conflict": {"ro": "Operațiune concurentă. Încearcă din nou.", "en": "Concurrent operation. Try again."},
    "invalid_request": {"ro": "Cerere invalidă.", "en": "Invalid request."},
}


class ConsentError(Exception):
    def __init__(self, code: str, status: int = 400, lang: str = "en", message: Optional[str] = None, **extra: Any):
        self.code, self.status, self.extra = code, status, extra
        m = _MSG.get(code, {})
        self.message = message or m.get(lang) or m.get("en") or code
        super().__init__(self.message)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lang(lang: Optional[str]) -> str:
    return LD.get_registry().normalize_lang(lang)


def purpose_text(purpose: str, lang: str) -> dict:
    return PURPOSES[purpose]["texts"].get(lang) or PURPOSES[purpose]["texts"][LD.DEFAULT_LANG]


def purpose_wording_sha256(purpose: str, lang: str) -> str:
    """Amprenta textului EXACT al consimtamantului (titlu + descriere + consecinte), pe versiune si limba."""
    payload = {"purpose": purpose, "version": PURPOSES[purpose]["version"], "lang": lang, "text": purpose_text(purpose, lang)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def purpose_eligible(purpose: str, age_category: Optional[str]) -> bool:
    need = PURPOSES[purpose].get("min_age_category")
    return need is None or age_category == need


# ----------------------------------------------------------------------------- indexuri
async def ensure_indexes(db) -> None:
    c = db[COLLECTION]
    await c.create_index("event_id", unique=True)
    # (user, subiect, nr. de ordine) unic: arbitreaza cursele / dublu-click-urile - doua cereri paralele nu pot scrie
    # ambele evenimentul cu acelasi numar; al doilea insert esueaza si cererea e tratata idempotent.
    await c.create_index([("user_id", 1), ("subject", 1), ("subject_seq", 1)], unique=True)
    await c.create_index([("user_id", 1), ("created_at", 1)])
    await c.create_index([("account_deleted", 1), ("retain_until", 1)])


# ----------------------------------------------------------------------------- nucleu: scriere adauga-doar
def _is_dup(e: Exception) -> bool:
    return type(e).__name__ == "DuplicateKeyError"


async def _last_event(db, user_id: str, subject: str) -> Optional[dict]:
    rows = await db[COLLECTION].find({"user_id": user_id, "subject": subject}, {"_id": 0}).sort("subject_seq", -1).limit(1).to_list(1)
    return rows[0] if rows else None


async def _append(db, user_id: str, subject: str, make, retries: int = 4) -> tuple[Optional[dict], bool]:
    """Adauga UN eveniment nou pentru (user, subiect). `make(last)` intoarce campurile evenimentului sau None
    (= nimic de facut, operatie idempotenta). Returneaza (eveniment, a_fost_creat)."""
    for _ in range(retries):
        last = await _last_event(db, user_id, subject)
        fields = make(last)
        if fields is None:
            return last, False
        ev = {
            "event_id": "cev_" + uuid.uuid4().hex,
            "user_id": user_id,
            "subject": subject,
            "subject_seq": (last["subject_seq"] + 1) if last else 1,
            "created_at": _now(),   # generat de server, UTC; nu exista niciun camp de timp venit de la client
            **fields,
        }
        try:
            await db[COLLECTION].insert_one(ev)
            ev.pop("_id", None)
            return ev, True
        except Exception as e:  # noqa: BLE001
            if not _is_dup(e):
                raise
            # o cerere paralela a scris deja pentru acelasi numar: reciteste starea si reevalueaza
    raise ConsentError("conflict", 409)


def _doc_event_fields(doc_type: str, lang: str, source: str) -> dict:
    reg = LD.get_registry()
    version = reg.current_version(doc_type)
    got = reg.text(doc_type, version, lang)
    return {
        "subject_type": "document",
        "legal_basis": reg.legal_basis(doc_type),
        "action": ACTION_ACCEPTED,
        "version": version,
        "document_id": reg.document_ref(doc_type, version),
        "document_sha256": got[1],
        "document_language": lang,
        "source": source,
    }


async def record_document_acceptance(db, user_id: str, doc_type: str, *, lang: str, source: str) -> tuple[dict, bool]:
    reg = LD.get_registry()
    if not reg.has_doc(doc_type):
        raise ConsentError("unknown_subject", 404, lang)
    lang = _lang(lang)
    fields = _doc_event_fields(doc_type, lang, source)

    def make(last):
        if last and last["action"] == ACTION_ACCEPTED and last["version"] == fields["version"]:
            return None   # aceeasi versiune deja acceptata: fara eveniment duplicat
        return dict(fields)

    return await _append(db, user_id, doc_type, make)


async def grant_purpose(db, user_id: str, purpose: str, *, age_category: Optional[str], lang: str, source: str) -> tuple[dict, bool]:
    lang = _lang(lang)
    if purpose not in PURPOSES:
        raise ConsentError("unknown_subject", 404, lang)
    if not purpose_eligible(purpose, age_category):
        raise ConsentError("purpose_not_available", 403, lang)
    p = PURPOSES[purpose]
    fields = {
        "subject_type": "purpose", "legal_basis": LD.BASIS_CONSENT, "action": ACTION_ACCEPTED,
        "version": p["version"], "document_id": f"{purpose}@{p['version']}",
        "document_sha256": purpose_wording_sha256(purpose, lang), "document_language": lang, "source": source,
    }

    def make(last):
        if last and last["action"] == ACTION_ACCEPTED and last["version"] == p["version"]:
            return None
        return dict(fields)

    return await _append(db, user_id, purpose, make)


async def withdraw_purpose(db, user_id: str, purpose: str, *, confirm: bool, lang: str, source: str = "settings") -> tuple[Optional[dict], bool]:
    """Inregistreaza retragerea (eveniment NOU; dovada acceptarii ramane). Idempotent: daca nu exista un consimtamant
    activ nu scrie nimic. Nu sterge contul si nu atinge alte date."""
    lang = _lang(lang)
    if purpose in LD.get_registry().doc_types():
        raise ConsentError("not_withdrawable", 409, lang)
    if purpose not in PURPOSES:
        raise ConsentError("unknown_subject", 404, lang)
    p = PURPOSES[purpose]

    def make(last):
        if not last or last["action"] != ACTION_ACCEPTED:
            return None
        if p.get("requires_confirmation_to_withdraw") and not confirm:
            raise ConsentError("confirmation_required", 409, lang, consequences=purpose_text(purpose, lang)["consequences"])
        return {
            "subject_type": "purpose", "legal_basis": LD.BASIS_CONSENT, "action": ACTION_WITHDRAWN,
            "version": last["version"], "document_id": last["document_id"],
            "document_sha256": last.get("document_sha256"), "document_language": last.get("document_language"),
            "source": source, "withdraws_event_id": last["event_id"],
        }

    return await _append(db, user_id, purpose, make)


# ----------------------------------------------------------------------------- inregistrare
def validate_registration_consent(accept_terms: Any, accept_privacy: Any, optional: Any, presented: Any,
                                  *, age_category: Optional[str], lang: str = "en") -> dict:
    """Valideaza acceptarea de la inregistrare INAINTE de orice scriere. Nu citeste nicio versiune de la client."""
    lang = _lang(lang)
    reg = LD.get_registry()
    if accept_terms is not True or accept_privacy is not True:
        raise ConsentError("consent_required", 422, lang)
    if presented is not None:
        if not isinstance(presented, dict):
            raise ConsentError("invalid_request", 422, lang)
        for doc_type in ("terms_of_service", "privacy_policy"):
            if doc_type in presented and presented[doc_type] != reg.current_version(doc_type):
                raise ConsentError("document_version_changed", 409, lang)
    granted: list[str] = []
    if optional is not None:
        if not isinstance(optional, dict):
            raise ConsentError("invalid_request", 422, lang)
        for purpose, val in optional.items():
            if purpose not in PURPOSES:
                raise ConsentError("unknown_subject", 422, lang)
            if not isinstance(val, bool):
                raise ConsentError("invalid_request", 422, lang)
            if val:
                if not purpose_eligible(purpose, age_category):
                    raise ConsentError("purpose_not_available", 422, lang)
                granted.append(purpose)
    return {"optional": granted}


async def record_registration_consents(db, user_id: str, validated: dict, *, age_category: Optional[str], lang: str,
                                       source: str = "register") -> list[dict]:
    out = []
    for doc_type in ("terms_of_service", "privacy_policy"):
        ev, _ = await record_document_acceptance(db, user_id, doc_type, lang=lang, source=source)
        out.append(ev)
    for purpose in validated["optional"]:
        ev, _ = await grant_purpose(db, user_id, purpose, age_category=age_category, lang=lang, source=source)
        out.append(ev)
    return out


async def accept_documents(db, user_id: str, doc_types: Any, presented: Any, *, lang: str, source: str = "reaccept") -> list[dict]:
    lang = _lang(lang)
    reg = LD.get_registry()
    if not isinstance(doc_types, list) or not doc_types or any(not isinstance(d, str) for d in doc_types):
        raise ConsentError("invalid_request", 422, lang)
    for d in doc_types:
        if not reg.has_doc(d):
            raise ConsentError("unknown_subject", 404, lang)
    if not isinstance(presented, dict):
        raise ConsentError("invalid_request", 422, lang)
    for d in doc_types:   # versiunea prezentata trebuie sa existe SI sa fie cea curenta
        if presented.get(d) != reg.current_version(d):
            raise ConsentError("document_version_changed", 409, lang)
    out = []
    for d in dict.fromkeys(doc_types):
        ev, _ = await record_document_acceptance(db, user_id, d, lang=lang, source=source)
        out.append(ev)
    return out


# ----------------------------------------------------------------------------- citire
_PUBLIC = ("event_id", "subject", "subject_type", "legal_basis", "action", "version", "document_id",
           "document_sha256", "document_language", "created_at", "source", "subject_seq")


def public_event(ev: dict) -> dict:
    return {k: ev.get(k) for k in _PUBLIC}


async def history(db, user_id: str) -> list[dict]:
    rows = await db[COLLECTION].find({"user_id": user_id}, {"_id": 0}).sort("created_at", 1).to_list(5000)
    rows.sort(key=lambda r: (r["created_at"], r["subject"], r["subject_seq"]))
    return [public_event(r) for r in rows]


async def latest_by_subject(db, user_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in await db[COLLECTION].find({"user_id": user_id}, {"_id": 0}).sort("subject_seq", 1).to_list(5000):
        cur = out.get(r["subject"])
        if cur is None or r["subject_seq"] > cur["subject_seq"]:
            out[r["subject"]] = r
    return out


async def overview(db, user: dict, lang: str) -> dict:
    lang = _lang(lang)
    reg = LD.get_registry()
    latest = await latest_by_subject(db, user["user_id"])
    docs, pending, notices = [], [], []
    for doc_type in reg.doc_types():
        ev = latest.get(doc_type)
        accepted_version = ev["version"] if ev and ev["action"] == ACTION_ACCEPTED else None
        status = reg.reacceptance_status(doc_type, accepted_version)
        cur = reg.current_version(doc_type)
        item = {
            "subject": doc_type, "kind": "document", "legal_basis": reg.legal_basis(doc_type),
            "current_version": cur, "accepted_version": accepted_version,
            "accepted_at": ev["created_at"] if accepted_version else None,
            "status": {"current": "current", "accept": "acceptance_required", "notice": "update_notice"}[status],
            "withdrawable": False,
            "change_summary": reg.summary_for(doc_type, cur, lang) if status != "current" else None,
        }
        docs.append(item)
        if status == "accept":
            pending.append(doc_type)
        elif status == "notice":
            notices.append({"subject": doc_type, "version": cur, "summary": item["change_summary"]})
    purposes = []
    for p, cfg in PURPOSES.items():
        ev = latest.get(p)
        granted = bool(ev and ev["action"] == ACTION_ACCEPTED)
        t = purpose_text(p, lang)
        purposes.append({
            "subject": p, "kind": "purpose", "legal_basis": LD.BASIS_CONSENT, "version": cfg["version"],
            "title": t["title"], "description": t["description"], "consequences": t["consequences"],
            "state": "granted" if granted else ("withdrawn" if ev else "never_granted"),
            "granted_version": ev["version"] if granted else None,
            "granted_at": ev["created_at"] if granted else None,
            "withdrawn_at": ev["created_at"] if (ev and ev["action"] == ACTION_WITHDRAWN) else None,
            "eligible": purpose_eligible(p, user.get("age_category")),
            "withdrawable": granted,
            "requires_confirmation": bool(cfg.get("requires_confirmation_to_withdraw")),
        })
    return {"documents": docs, "purposes": purposes, "pending_acceptance": pending, "notices": notices}


async def export_rows(db, user_id: str) -> list[dict]:
    """Pentru exportul de date (Art. 15/20): istoricul complet al propriului utilizator."""
    return await history(db, user_id)


# ----------------------------------------------------------------------------- poarta pentru prelucrari
async def has_active_consent(db, user_id: str, purpose: str) -> bool:
    if purpose not in PURPOSES:
        return False
    ev = await _last_event(db, user_id, purpose)
    return bool(ev and ev["action"] == ACTION_ACCEPTED)


async def require_consent(db, user_id: str, purpose: str) -> None:
    """Orice cod care face o prelucrare bazata pe consimtamant TREBUIE sa treaca pe aici (sau prin purpose_audience)."""
    if not await has_active_consent(db, user_id, purpose):
        raise ConsentError("consent_not_active", 403, message="No active consent for this purpose.")


async def purpose_audience(db, purpose: str) -> list[str]:
    """Utilizatorii al caror ULTIM eveniment pentru `purpose` este 'accepted' (retragerea il scoate imediat)
    si al caror cont nu e in curs de stergere."""
    if purpose not in PURPOSES:
        return []
    last: dict[str, dict] = {}
    for r in await db[COLLECTION].find({"subject": purpose}, {"_id": 0}).sort("subject_seq", 1).to_list(None):
        cur = last.get(r["user_id"])
        if cur is None or r["subject_seq"] > cur["subject_seq"]:
            last[r["user_id"]] = r
    out = []
    for uid, r in last.items():
        if r["action"] != ACTION_ACCEPTED or r.get("account_deleted"):
            continue
        u = await db.users.find_one({"user_id": uid}, {"_id": 0, "user_id": 1, "deletion_state": 1})
        if u and not u.get("deletion_state"):
            out.append(uid)
    return out


# ----------------------------------------------------------------------------- stergere cont
async def pseudonymize_for_deletion(db, user_id: str, pseud: str, retain_until: datetime) -> None:
    """La stergerea contului dovada consimtamantului NU se sterge (Art. 7(1) / Art. 17(3)(e): demonstrarea
    consimtamantului si apararea unor pretentii), dar se pseudonimizeaza si primeste termen de pastrare."""
    await db[COLLECTION].update_many(
        {"user_id": user_id},
        {"$set": {"user_id": pseud, "account_deleted": True, "retain_until": retain_until}})