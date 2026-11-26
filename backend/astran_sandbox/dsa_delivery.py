"""Livrare pe email a comunicarilor DSA (expuneri de motive Art. 17, rezultatul plangerilor Art. 20(5), confirmari/rezultate Art. 16,
raspunsuri catre autoritati Art. 9/10) - cu stare, reincercari si audit.

Principii:
  * O livrare este `sent` DOAR dupa ce expeditorul confirma succesul. Orice esec / exceptie / SMTP neconfigurat => `failed`
    (se reincearca cu backoff) si apoi `abandoned`. Nu exista nicio cale prin care un esec sa devina "livrat".
  * Notificarea in aplicatie (moderation_notifications) ramane canalul principal si se creeaza INAINTE; emailul e suplimentar.
  * Adresa de email NU e copiata in coada: se citeste din sursa (users / report.notice / ordin) la momentul trimiterii si nu apare
    in audit, in jurnale sau in raspunsurile catre utilizator.
  * Fara adresa (Art. 17(2): obligatia exista doar daca datele de contact electronice sunt cunoscute) => `skipped_no_contact`.
  * Expeditorul e injectat (testabil); implicit foloseste SMTP-ul existent din project_security.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from . import moderation_service as MS

log = logging.getLogger("astran.delivery")

COLL = "moderation_deliveries"
PENDING, SENDING, SENT, FAILED, ABANDONED, NO_CONTACT = "pending", "sending", "sent", "failed", "abandoned", "skipped_no_contact"
RETRYABLE = (PENDING, FAILED)
STALE_CLAIM = timedelta(minutes=10)
Sender = Callable[[str, str, str], Awaitable[tuple]]


def max_attempts() -> int:
    try:
        return max(1, int(os.environ.get("ASTRAN_DELIVERY_MAX_ATTEMPTS", "6")))
    except ValueError:
        return 6


def backoff(attempts_done: int) -> timedelta:
    return timedelta(seconds=min(60 * (2 ** max(0, attempts_done - 1)), 6 * 3600))


async def ensure_indexes(db) -> None:
    await db[COLL].create_index("delivery_id", unique=True, name="dsa_delivery_id")
    await db[COLL].create_index("dedupe_key", unique=True, name="dsa_delivery_dedupe")
    await db[COLL].create_index([("status", 1), ("next_attempt_at", 1)], name="dsa_delivery_due")


async def enqueue(db, *, kind: str, dedupe_key: str, recipient: dict, ref: dict) -> dict:
    """Idempotent: aceeasi `dedupe_key` nu creeaza a doua livrare (nu se trimit doua emailuri pentru aceeasi expunere de motive)."""
    ex = await db[COLL].find_one({"dedupe_key": dedupe_key}, {"_id": 0})
    if ex:
        return ex
    now = MS.now_utc()
    doc = {"delivery_id": MS.new_id("dlv_"), "kind": kind, "channel": "email", "dedupe_key": dedupe_key, "recipient": recipient, "ref": ref,
           "status": PENDING, "attempts": 0, "attempt_log": [], "created_at": now, "next_attempt_at": now, "sent_at": None, "last_error": None}
    try:
        await db[COLL].insert_one(dict(doc))
    except Exception as e:  # noqa: BLE001
        if "Duplicate" in type(e).__name__ or "E11000" in str(e):
            return await db[COLL].find_one({"dedupe_key": dedupe_key}, {"_id": 0}) or doc
        raise
    return doc


# ------------------------------------------------------------------------------------------------ continut
_ACTION_TXT = {
    "remove_content_illegal": ("The content was removed because it is illegal.", "Conținutul a fost eliminat pentru că este ilegal."),
    "remove_content_policy": ("The content was removed because it breaks the Terms of Service.", "Conținutul a fost eliminat pentru că încalcă Termenii și condițiile."),
    "restrict_content": ("Access to the content was restricted.", "Accesul la conținut a fost restricționat."),
    "restrict_account_temporary": ("Your account or a feature of it was restricted for a limited time.", "Contul tău sau o funcție a lui a fost restricționat temporar."),
    "suspend_account_permanent": ("Your account was suspended permanently.", "Contul tău a fost suspendat definitiv."),
}
_INIT_TXT = {"user_notice": ("a notice submitted by a person or entity", "o notificare transmisă de o persoană sau entitate"),
             "user_report": ("a report by a user", "o raportare a unui utilizator"),
             "astran": ("Astran's own initiative", "inițiativa proprie a Astran"),
             "authority_order": ("an order from a competent authority", "un ordin al unei autorități competente")}


def _d(v: Any) -> str:
    return v.strftime("%Y-%m-%d %H:%M UTC") if isinstance(v, datetime) else (str(v) if v else "-")


def render_statement_text(sor: dict, *, ods_bodies: Optional[str] = None) -> tuple:
    """(subiect, corp) bilingv. NU contine identitatea notificatorului (nu exista in `sor`), note interne sau id-uri de moderatori."""
    dec, basis = sor.get("decision") or {}, sor.get("basis") or {}
    en_act, ro_act = _ACTION_TXT.get(dec.get("action"), ("A moderation measure was applied.", "A fost aplicată o măsură de moderare."))
    init_en, init_ro = _INIT_TXT.get(sor.get("initiated_by"), ("a report", "o raportare"))
    auto = sor.get("automated_means") or {}
    redress = sor.get("redress") or {}
    ic = redress.get("internal_complaint") or {}
    until = dec.get("restriction_until")
    dur_en = "Permanent" if dec.get("permanent") else (f"Until {_d(until)}" if until else "Not time-limited / not applicable")
    dur_ro = "Definitivă" if dec.get("permanent") else (f"Până la {_d(until)}" if until else "Fără limită de timp / nu se aplică")
    if basis.get("type") == "illegal_content":
        basis_en = f"The content is illegal. Legal ground: {basis.get('legal_ground') or '-'}"
        basis_ro = f"Conținutul este ilegal. Temei juridic: {basis.get('legal_ground') or '-'}"
    else:
        basis_en = f"Incompatible with the Terms of Service: {basis.get('tos_provision') or '-'} ({basis.get('tos_document') or '-'})"
        basis_ro = f"Incompatibil cu Termenii și condițiile: {basis.get('tos_provision') or '-'} ({basis.get('tos_document') or '-'})"
    auto_en = ("Automated means helped detect the content; a person took the decision." if auto.get("used_for_detection")
               else "No automated means were used.")
    auto_ro = ("Mijloace automate au ajutat la detectare; decizia a fost luată de o persoană." if auto.get("used_for_detection")
               else "Nu au fost folosite mijloace automate.")
    ods = ods_bodies or "not configured yet / încă neconfigurat"
    content = sor.get("content") or {}
    body = f"""[EN] Statement of reasons {sor.get('sor_id')} - issued {_d(sor.get('issued_at'))}

What happened: {en_act}
Scope: {dec.get('scope') or 'platform-wide'} · Duration: {dur_en}
Content/account concerned: {content.get('target_type')} {content.get('target_id')} {('- ' + content['summary']) if content.get('summary') else ''}
Facts and circumstances: {sor.get('facts_and_circumstances')}
Basis: {basis_en}
Initiated by: {init_en}. {auto_en}
How to challenge it: open the app > Moderation > Decisions and submit a complaint (until {_d(ic.get('deadline'))}, at least {ic.get('months', 6)} months from the decision).
A person who was not involved in the original decision will review it. You may also use an out-of-court dispute settlement body ({ods}) or go to court.

[RO] Expunere de motive {sor.get('sor_id')} - emisă la {_d(sor.get('issued_at'))}

Ce s-a întâmplat: {ro_act}
Domeniu: {dec.get('scope') or 'întreaga platformă'} · Durată: {dur_ro}
Conținut/cont vizat: {content.get('target_type')} {content.get('target_id')} {('- ' + content['summary']) if content.get('summary') else ''}
Fapte și circumstanțe: {sor.get('facts_and_circumstances')}
Temei: {basis_ro}
Inițiată de: {init_ro}. {auto_ro}
Cum o poți contesta: deschide aplicația > Moderare > Decizii și trimite o plângere (până la {_d(ic.get('deadline'))}, cel puțin {ic.get('months', 6)} luni de la decizie).
O persoană care nu a participat la decizia inițială o va analiza. Poți apela și la un organism de soluționare extrajudiciară ({ods}) sau la instanță.
"""
    return (f"Astran - statement of reasons / expunere de motive {sor.get('sor_id')}", body)


async def _render(db, d: dict) -> Optional[tuple]:
    kind, ref = d["kind"], d.get("ref") or {}
    ci = MS.contact_info()
    if kind == "statement_of_reasons":
        sor = await db.statements_of_reasons.find_one({"sor_id": ref.get("sor_id")}, {"_id": 0})
        return render_statement_text(sor, ods_bodies=ci.get("out_of_court_bodies")) if sor else None
    if kind == "appeal_outcome":
        a = await db.moderation_appeals.find_one({"appeal_id": ref.get("appeal_id")}, {"_id": 0})
        if not a or not a.get("outcome"):
            return None
        label = {"maintain": ("The original decision was confirmed.", "Decizia inițială a fost menținută."),
                 "reverse": ("The original decision was reversed.", "Decizia inițială a fost anulată."),
                 "modify": ("The original decision was modified.", "Decizia inițială a fost modificată.")}[a["outcome"]]
        ods = ci.get("out_of_court_bodies") or "not configured yet / încă neconfigurat"
        return (f"Astran - complaint {a['appeal_id']} decided / plângere soluționată",
                f"[EN] Complaint {a['appeal_id']}: {label[0]}\nReasoning: {a.get('outcome_reasoning')}\n"
                f"The complaint was reviewed by a person. You can also use an out-of-court dispute settlement body ({ods}) or go to court.\n\n"
                f"[RO] Plângerea {a['appeal_id']}: {label[1]}\nMotivare: {a.get('outcome_reasoning')}\n"
                f"Plângerea a fost analizată de o persoană. Poți apela și la un organism de soluționare extrajudiciară ({ods}) sau la instanță.\n")
    if kind == "notice_receipt":
        r = await db.reports.find_one({"report_id": ref.get("report_id")}, {"_id": 0})
        if not r:
            return None
        return (f"Astran - notice received / notificare primită {r['report_id']}",
                f"[EN] We received your notice. Reference: {r['report_id']} (received {_d(r.get('created_at'))}). "
                f"We will tell you the outcome.\n[RO] Am primit notificarea ta. Referință: {r['report_id']} (primită {_d(r.get('created_at'))}). "
                f"Te vom informa despre rezultat.\n")
    if kind == "notice_outcome":
        dec = await db.moderation_decisions.find_one({"decision_id": ref.get("decision_id")}, {"_id": 0})
        if not dec:
            return None
        o = MS.notifier_outcome(dec)
        taken = o["outcome"] == "action_taken"
        return (f"Astran - decision on notice / decizie privind notificarea {o['report_id']}",
                f"[EN] A decision was taken on your notice {o['report_id']}: {'action was taken' if taken else 'no action was taken'}.\n"
                f"Reasoning: {o['reasoning']}\n[RO] S-a luat o decizie privind notificarea {o['report_id']}: "
                f"{'au fost luate măsuri' if taken else 'nu au fost luate măsuri'}.\nMotivare: {o['reasoning']}\n")
    if kind == "authority_response":
        from . import authority_orders as AO
        return await AO.render_response_email(db, ref)
    return None


async def _recipient_email(db, d: dict) -> Optional[str]:
    rc = d.get("recipient") or {}
    if rc.get("user_id"):
        u = await db.users.find_one({"user_id": rc["user_id"]}, {"_id": 0, "email": 1})
        return (u or {}).get("email") or None
    if rc.get("report_id"):
        r = await db.reports.find_one({"report_id": rc["report_id"]}, {"_id": 0, "notice": 1})
        return ((r or {}).get("notice") or {}).get("reporter_email") or None
    if rc.get("order_id"):
        from . import authority_orders as AO
        return await AO.authority_email(db, rc["order_id"])
    return None


# ------------------------------------------------------------------------------------------------ trimitere
async def smtp_sender(to: str, subject: str, body: str) -> tuple:
    """Expeditorul implicit: SMTP-ul existent. Intoarce (ok, cod_eroare). Fara SMTP configurat => (False, "smtp_not_configured")."""
    from . import project_security as PS
    if not PS.email_is_configured():
        return False, "smtp_not_configured"
    ok = await asyncio.to_thread(PS.send_plain_email, to, subject, body)
    return (True, None) if ok else (False, "smtp_send_failed")


async def attempt(db, delivery_id: str, sender: Sender, now: Optional[datetime] = None) -> dict:
    now = now or MS.now_utc()
    claim = await db[COLL].update_one({"delivery_id": delivery_id, "status": {"$in": list(RETRYABLE)}}, {"$set": {"status": SENDING, "claimed_at": now}})
    if not claim.matched_count:
        return {"delivery_id": delivery_id, "skipped": True}
    d = await db[COLL].find_one({"delivery_id": delivery_id}, {"_id": 0})
    ref = d.get("ref") or {}
    audit_kw = {"report_id": ref.get("report_id"), "decision_id": ref.get("decision_id")}

    async def finish(status: str, *, error: Optional[str], next_at: Optional[datetime] = None, event: str, counted: bool = True) -> dict:
        attempts = d["attempts"] + (1 if counted else 0)
        upd = {"status": status, "attempts": attempts, "last_attempt_at": now, "last_error": error, "next_attempt_at": next_at, "claimed_at": None}
        if status == SENT:
            upd["sent_at"] = MS.now_utc()
        await db[COLL].update_one({"delivery_id": delivery_id}, {"$set": upd, "$push": {"attempt_log": {"$each": [{"at": now, "status": status, "error": error}], "$slice": -20}}})
        await MS.audit(db, event_type=event, actor_id=None, actor_role="system", action=d["kind"], new_status=status, outcome=error,
                       details={"delivery_id": delivery_id, "attempt": attempts, "channel": "email"}, **{k: v for k, v in audit_kw.items() if v})
        return {"delivery_id": delivery_id, "status": status, "attempts": attempts, "error": error}

    email = await _recipient_email(db, d)
    if not email:
        return await finish(NO_CONTACT, error=None, event="delivery_skipped", counted=False)
    rendered = await _render(db, d)
    if rendered is None:
        return await finish(ABANDONED, error="source_missing", event="delivery_abandoned")
    try:
        ok, err = await sender(email, rendered[0], rendered[1])
    except Exception as e:  # noqa: BLE001
        ok, err = False, type(e).__name__
    if ok is True:
        res = await finish(SENT, error=None, event="delivery_sent")
        if d["kind"] == "authority_response":        # momentele pentru Art. 15 se inregistreaza DOAR dupa confirmarea trimiterii
            from . import authority_orders as AO
            await AO.mark_response_sent(db, ref)
        return res
    attempts = d["attempts"] + 1
    if attempts >= d.get("max_attempts", max_attempts()):
        return await finish(ABANDONED, error=err or "send_failed", event="delivery_abandoned")
    return await finish(FAILED, error=err or "send_failed", next_at=now + backoff(attempts), event="delivery_failed")


async def process_due(db, sender: Sender = smtp_sender, *, now: Optional[datetime] = None, limit: int = 50) -> list:
    now = now or MS.now_utc()
    await db[COLL].update_many({"status": SENDING, "claimed_at": {"$lt": now - STALE_CLAIM}}, {"$set": {"status": FAILED, "last_error": "stale_claim"}})
    due = await db[COLL].find({"status": {"$in": list(RETRYABLE)}, "next_attempt_at": {"$lte": now}}, {"_id": 0, "delivery_id": 1}).sort("next_attempt_at", 1).limit(limit).to_list(limit)
    return [await attempt(db, x["delivery_id"], sender, now) for x in due]


async def delivery_worker(db, *, interval: float = 30.0, sender: Sender = smtp_sender) -> None:
    while True:
        try:
            await process_due(db, sender)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("delivery worker iteration failed")
        await asyncio.sleep(interval)


# ------------------------------------------------------------------------------------------------ vizibilitate
_PUBLIC = {PENDING: "pending", SENDING: "pending", FAILED: "pending", SENT: "sent", ABANDONED: "not_sent", NO_CONTACT: "no_email_on_file"}


async def public_status(db, dedupe_key: str) -> Optional[dict]:
    """Ce vede utilizatorul: canal + stare + data. Fara cod de eroare tehnica, fara adresa."""
    d = await db[COLL].find_one({"dedupe_key": dedupe_key}, {"_id": 0})
    if not d:
        return None
    return {"channel": "email", "status": _PUBLIC.get(d["status"], "pending"), "sent_at": d.get("sent_at"), "in_app_notification": True}


async def admin_list(db, *, status: Optional[str] = None, limit: int = 100) -> dict:
    flt = {"status": status} if status else {}
    rows = await db[COLL].find(flt, {"_id": 0, "dedupe_key": 0}).sort("created_at", -1).limit(limit).to_list(limit)
    counts = {s: await db[COLL].count_documents({"status": s}) for s in (PENDING, SENDING, SENT, FAILED, ABANDONED, NO_CONTACT)}
    return {"items": rows, "counts": counts}


async def admin_retry(db, actor: dict, delivery_id: str) -> dict:
    if not MS.role_of(actor):
        raise MS.ModerationError(403, "forbidden")
    now = MS.now_utc()
    res = await db[COLL].update_one({"delivery_id": delivery_id, "status": {"$in": [FAILED, ABANDONED]}},
                                    {"$set": {"status": PENDING, "attempts": 0, "next_attempt_at": now, "last_error": None}})
    if not res.matched_count:
        raise MS.ModerationError(409, "delivery_not_retryable")
    await MS.audit(db, event_type="delivery_manual_retry", actor_id=actor["user_id"], actor_role=MS.role_of(actor), action="retry", details={"delivery_id": delivery_id})
    return {"delivery_id": delivery_id, "status": PENDING}


async def purge_old(db, days: int = 90) -> int:
    """Retentie: livrarile terminale (trimise / fara contact / abandonate) se sterg dupa `days` zile; starea relevanta ramane in SoR/audit."""
    cutoff = MS.now_utc() - timedelta(days=days)
    res = await db[COLL].delete_many({"status": {"$in": [SENT, NO_CONTACT, ABANDONED]}, "created_at": {"$lt": cutoff}})
    return res.deleted_count
