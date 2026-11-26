"""Rapoarte de transparenta Art. 15 calculate din date REALE (fara cifre inventate, fara esantioane hardcodate).

Aplicabilitate: Art. 15 se aplica tuturor furnizorilor de servicii intermediare, cu exceptia microintreprinderilor/intreprinderilor mici
care nu sunt VLOP (Art. 15(2)). Starea e dedusa din configurare (dsa_applicability) si inscrisa in fiecare raport.
Perioada: anuala, 1 ianuarie - 31 decembrie; publicare in cel mult doua luni de la sfarsitul perioadei (Reg. de punere in aplicare (UE) 2024/2835,
art. 2; colectarea datelor in formatul armonizat cere inceput la 1 iulie 2025).

Sablon: Reg. 2024/2835 impune sabloanele din Anexa I (fisiere CSV/XLSX oficiale ale Comisiei). Acest modul produce JSON/CSV structurat pe sectiunile
Anexei I (1.2 ordine Art. 15(1)(a), 1.3 notificari (b), 1.4 initiativa proprie (c), 1.5 plangeri (d)). Maparea la codurile exacte ale fisierelor oficiale
NU este finalizata (nu am putut descarca sabloanele oficiale): raportul o declara in `template.mapping_status`.

Confidentialitate: doar agregate. Fara id-uri, nume, emailuri, autoritati individuale, continut. Un ultim control (`assert_no_identifiers`) blocheaza publicarea.
Indicatori NECOLECTATI sunt listati explicit (`not_collected`) in loc sa fie lasati la 0.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import authority_orders as AO
from . import dsa_applicability as APPL
from . import moderation_service as MS

COLL = "transparency_reports"
_ID_RE = re.compile(r"\b(?:usr|user|rep|dec|sor|apl|ord|game|msg|att|rsp|dlv|tdb)_[0-9a-f]{6,}\b|@[\w-]+\.[\w.]+", re.I)
VISIBILITY = (MS.ACTION_REMOVE_ILLEGAL, MS.ACTION_REMOVE_POLICY, MS.ACTION_RESTRICT_CONTENT)
ACCOUNT = (MS.ACTION_RESTRICT_ACCOUNT, MS.ACTION_SUSPEND)


def _hours(a: Any, b: Any) -> Optional[float]:
    a, b = MS.aware(a), MS.aware(b)
    return (b - a).total_seconds() / 3600 if a and b and b >= a else None


def median(vals: list) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return round(statistics.median(v), 2) if v else None


def _count(items: list, key) -> dict:
    out: dict = {}
    for i in items:
        k = key(i) or "unspecified"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _action_class(a: str) -> str:
    return ("visibility_restriction" if a in VISIBILITY else "account_restriction" if a in (MS.ACTION_RESTRICT_ACCOUNT, MS.ACTION_SUSPEND)
            else "warning" if a == MS.ACTION_WARN else "no_sanction")


def period_for_year(year: int) -> tuple:
    return datetime(year, 1, 1, tzinfo=timezone.utc), datetime(year + 1, 1, 1, tzinfo=timezone.utc)


def publication_deadline(period_end_exclusive: datetime) -> datetime:
    """Doua luni dupa sfarsitul perioadei (sfarsitul lunii februarie pentru perioada anuala)."""
    y = period_end_exclusive.year
    return datetime(y, 3, 1, tzinfo=timezone.utc)      # exclusiv: publicarea trebuie facuta inainte de 1 martie


async def build_report(db, start: datetime, end: datetime, *, env: Optional[dict] = None) -> dict:
    start, end = MS.aware(start), MS.aware(end)
    if not start or not end or start >= end:
        raise MS.ModerationError(422, "period_invalid")
    rng = {"$gte": start, "$lt": end}
    reports = await db.reports.find({"created_at": rng}, {"_id": 0, "report_id": 1, "report_kind": 1, "category": 1, "target_type": 1, "created_at": 1,
                                                          "status": 1, "notice": 1}).to_list(None)
    kind = {r["report_id"]: r.get("report_kind") or MS.KIND_RULE for r in reports}
    notices = [r for r in reports if r.get("report_kind") == MS.KIND_NOTICE]
    decs = await db.moderation_decisions.find({"created_at": rng, "status": {"$ne": MS.DEC_FAILED}}, {"_id": 0}).to_list(None)
    all_kinds = {r["report_id"]: r.get("report_kind") or MS.KIND_RULE for r in await db.reports.find({"report_id": {"$in": [d["report_id"] for d in decs]}},
                                                                                                       {"_id": 0, "report_id": 1, "report_kind": 1}).to_list(None)}
    for d in decs:
        d["_source"] = d.get("source") or MS.KIND_TO_SOURCE.get(all_kinds.get(d["report_id"], MS.KIND_RULE), MS.SRC_TERMS_REPORT)
    nid = {n["report_id"] for n in notices}
    n_decs = await db.moderation_decisions.find({"report_id": {"$in": list(nid)}, "status": {"$ne": MS.DEC_FAILED}}, {"_id": 0}).to_list(None) if nid else []
    by_report: dict = {}
    for d in n_decs:
        by_report.setdefault(d["report_id"], []).append(d)
    first = lambda n: sorted(by_report.get(n["report_id"], []), key=lambda d: MS.aware(d["created_at"]))[0] if by_report.get(n["report_id"]) else None
    decided = [(n, first(n)) for n in notices if first(n)]
    notices_sec = {
        "received_total": len(notices),
        "by_alleged_category": _count(notices, lambda n: n.get("category")),
        "by_target_type": _count(notices, lambda n: n.get("target_type")),
        "submitted_by_trusted_flaggers": sum(1 for n in notices if (n.get("notice") or {}).get("trusted_flagger")),
        "with_alleged_legal_basis_stated": sum(1 for n in notices if (n.get("notice") or {}).get("alleged_legal_basis")),
        "decided": len(decided), "awaiting_decision": len(notices) - len(decided),
        "actions_taken": _count([d for _, d in decided], lambda d: d["action"]),
        "decisions_by_ground": _count([d for _, d in decided], lambda d: (d.get("basis") or {}).get("type") or "no_action"),
        "median_hours_to_decision": median([_hours(n["created_at"], d["created_at"]) for n, d in decided]),
        "processed_by_automated_means_only": 0,
    }
    own = [d for d in decs if d["_source"] in (MS.SRC_OWN, MS.SRC_TERMS_REPORT)]
    own_sec = {
        "decisions_total": len(own),
        "by_source": _count(own, lambda d: d["_source"]),
        "by_restriction_type": _count(own, lambda d: _action_class(d["action"])),
        "by_action": _count(own, lambda d: d["action"]),
        "by_ground": _count(own, lambda d: (d.get("basis") or {}).get("type") or "no_action"),
        "note": "'terms_report' = actions following a user report under the Terms of Service (not an Art. 16 notice); classification between "
                "own-initiative and report-based moderation to be confirmed by legal review.",
    }
    all_sec = {"decisions_total": len(decs), "by_source": _count(decs, lambda d: d["_source"]), "by_restriction_type": _count(decs, lambda d: _action_class(d["action"])),
               "statements_of_reasons_issued": sum(1 for d in decs if d.get("statement_id"))}
    automated = {"decisions_with_automated_detection": sum(1 for d in decs if d.get("automated_detection")),
                 "fully_automated_decisions": sum(1 for d in decs if d.get("automated_decision")),
                 "not_collected": ["chat filter masking events (no per-event record is kept)", "accuracy/error-rate indicators of automated tools"]}
    appeals = await db.moderation_appeals.find({"created_at": rng}, {"_id": 0}).to_list(None)
    resolved = [a for a in appeals if a.get("resolved_at")]
    rev = sum(1 for a in resolved if a.get("outcome") == "reverse")
    mod = sum(1 for a in resolved if a.get("outcome") == "modify")
    complaints = {
        "received_total": len(appeals), "by_complainant_role": _count(appeals, lambda a: a.get("as_role")),
        "by_status": _count(appeals, lambda a: a.get("status")),
        "outcomes": {"decision_maintained": sum(1 for a in resolved if a.get("outcome") == "maintain"), "decision_reversed": rev, "decision_modified": mod,
                     "pending": len(appeals) - len(resolved)},
        "share_of_resolved_decisions_reversed_or_modified": round((rev + mod) / len(resolved), 4) if resolved else None,
        "median_hours_to_resolution": median([_hours(a["created_at"], a["resolved_at"]) for a in resolved]),
        "reviewed_by_humans_only": all(a.get("reviewed_by_human", True) for a in resolved) if resolved else None,
        "art_20_status": APPL.obligations(env)["art_20"]["status"],
        "not_collected": ["out-of-court dispute settlement bodies referrals/outcomes (no ODS body configured yet)"],
    }
    orders = await db[AO.COLL].find({"received_at": rng}, {"_id": 0}).to_list(None)

    def osec(t: str) -> dict:
        rows = [o for o in orders if o["order_type"] == t]
        return {"received_total": len(rows), "by_member_state": _count(rows, lambda o: (o.get("authority") or {}).get("member_state")),
                "by_status": _count(rows, lambda o: o.get("status")),
                "median_hours_to_acknowledge_receipt": median([_hours(o["received_at"], (o.get("milestones") or {}).get("acknowledged_at")) for o in rows]),
                "median_hours_to_give_effect": median([_hours(o["received_at"], (o.get("milestones") or {}).get("effect_given_at")) for o in rows]),
                "acknowledged_count": sum(1 for o in rows if (o.get("milestones") or {}).get("acknowledged_at")),
                "effect_given_count": sum(1 for o in rows if (o.get("milestones") or {}).get("effect_given_at"))}
    a9 = osec(AO.ART9)
    a9["by_illegal_content_category"] = _count([o for o in orders if o["order_type"] == AO.ART9], lambda o: o.get("illegal_content_category"))
    app = APPL.obligations(env)
    rep = {
        "schema": "astran.dsa.transparency.v1",
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat(), "granularity": "custom" if (end - start).days not in (365, 366) else "calendar_year"},
        "generated_at": MS.now_utc().isoformat(), "data_source": "live database aggregates", "personal_data_included": False,
        "template": {"regulation": "Commission Implementing Regulation (EU) 2024/2835", "annex": "Annex I (CSV/XLSX templates published by the Commission)",
                     "sections": {"orders": "1.2 Art. 15(1)(a)", "notices": "1.3 Art. 15(1)(b)", "own_initiative": "1.4 Art. 15(1)(c)", "complaints": "1.5 Art. 15(1)(d)"},
                     "mapping_status": "structure aligned to Annex I sections; exact official field codes not mapped - complete before relying on this for filing"},
        "applicability": {k: app[k] for k in ("art_9", "art_10", "art_15", "art_16", "art_17", "art_20", "art_24_5")} | {"classification": app["classification"]},
        "orders": {"art_9_illegal_content": a9, "art_10_information": osec(AO.ART10),
                   "not_collected": ["Art. 15(1)(a) breakdown by specific content type for Art. 10 orders (not applicable)"]},
        "notices_art_16": notices_sec, "own_initiative_moderation": own_sec, "all_moderation_decisions": all_sec, "automated_moderation": automated,
        "complaints_art_20": complaints,
        "qualitative_requires_manual_input": ["Art. 15(1)(c) meaningful information on content moderation rules, tools and human review",
                                              "Art. 15(1)(e) description of automated means, purposes and safeguards",
                                              "Moderator team size/qualifications and languages (template section on human resources)"],
        "excluded": ["Art. 24(2)-(3) average monthly active recipients and Art. 42 items (platform/VLOP-specific, not computed here)"],
    }
    assert_no_identifiers(rep)
    return rep


def assert_no_identifiers(rep: dict) -> None:
    blob = json.dumps(rep, ensure_ascii=False, default=str)
    m = _ID_RE.search(blob)
    if m:
        raise MS.ModerationError(500, "report_contains_identifier", "refusing to emit a report that contains an identifier-like value")


def flatten(rep: dict) -> list:
    rows: list = []

    def walk(prefix: str, v: Any) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{prefix}.{k}" if prefix else str(k), x)
        elif isinstance(v, list):
            rows.append((prefix, "; ".join(str(i) for i in v)))
        else:
            rows.append((prefix, "" if v is None else v))
    walk("", rep)
    return rows


def to_csv(rep: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["section", "indicator", "value"])
    for path, val in flatten(rep):
        head, _, rest = path.partition(".")
        sval = str(val)
        if sval[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?\d+(\.\d+)?", sval):   # evita injectarea de formule in foi de calcul
            sval = "'" + sval
        w.writerow([head, rest, sval])
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ publicare
async def ensure_indexes(db) -> None:
    await db[COLL].create_index("report_id", unique=True, name="dsa_tr_id")
    await db[COLL].create_index([("period_start", 1), ("version", -1)], name="dsa_tr_period")


def _admin(actor: dict) -> None:
    if MS.role_of(actor) != "admin":
        raise MS.ModerationError(403, "admin_required")


async def preview(db, actor: dict, start: datetime, end: datetime) -> dict:
    if not MS.role_of(actor):
        raise MS.ModerationError(403, "forbidden")
    return await build_report(db, start, end)


async def publish(db, actor: dict, start: datetime, end: datetime) -> dict:
    """Publica un instantaneu IMUABIL (versionat, cu sha256). Versiunile anterioare ramase accesibile (retentie 5 ani, Reg. 2024/2835 art. 3)."""
    _admin(actor)
    if MS.aware(end) > MS.now_utc():
        raise MS.ModerationError(422, "period_not_finished", "the reporting period has not ended yet")
    rep = await build_report(db, start, end)
    csv_text = to_csv(rep)
    n = await db[COLL].count_documents({"period_start": MS.aware(start), "period_end": MS.aware(end)})
    doc = {"report_id": MS.new_id("tr_"), "period_start": MS.aware(start), "period_end": MS.aware(end), "version": n + 1, "published_at": MS.now_utc(),
           "published_by": actor["user_id"], "sha256_json": hashlib.sha256(json.dumps(rep, sort_keys=True).encode()).hexdigest(),
           "report": rep, "csv": csv_text}
    await db[COLL].insert_one(dict(doc))
    await MS.audit(db, event_type="transparency_published", actor_id=actor["user_id"], actor_role="admin", action="publish", details={"version": doc["version"]})
    return {k: doc[k] for k in ("report_id", "period_start", "period_end", "version", "published_at", "sha256_json")}


async def list_public(db) -> list:
    rows = await db[COLL].find({}, {"_id": 0, "report": 0, "csv": 0, "published_by": 0}).sort("published_at", -1).to_list(200)
    return rows


async def get_public(db, report_id: Any) -> dict:
    d = await db[COLL].find_one({"report_id": report_id}, {"_id": 0, "published_by": 0, "csv": 0}) if isinstance(report_id, str) else None
    if not d:
        raise MS.ModerationError(404, "not_found")
    return d


async def get_public_csv(db, report_id: Any) -> str:
    d = await db[COLL].find_one({"report_id": report_id}, {"_id": 0, "csv": 1}) if isinstance(report_id, str) else None
    if not d:
        raise MS.ModerationError(404, "not_found")
    return d["csv"]
