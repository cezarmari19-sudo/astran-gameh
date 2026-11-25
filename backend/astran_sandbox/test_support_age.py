"""Fixture-uri de TEST pentru categoria de varsta. In productie `age_category` NU mai e crezut fara dovada: adult_18 cere
o atestare sigilata (age_policy.sealed_assurance) + DOB compatibil, iar un minor ELIGIBIL cere o atestare de prag 16.
Aceste functii construiesc documente care respecta regulile (aceleasi functii de sigilare ca in productie)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from astran_sandbox import age_policy as AP
from astran_sandbox import age_service as AS


def _dob(years: int) -> str:
    t = date.today()
    return date(t.year - years, t.month, 1).isoformat()


def fields(uid: str, cat):
    """Campuri de varsta pentru un document de utilizator, ca si cum contul ar fi trecut verificarea reala."""
    if cat == "adult_18":
        return {**AS.registration_fields(_dob(30)), "age_category": "adult_18",
                "age_assurance": AP.sealed_assurance(uid, threshold=18, provider="test", method="test",
                                                     verified_at=datetime.now(timezone.utc), ref="t")}
    if cat == "under_18":
        return {**AS.registration_fields(_dob(17)),
                "age_assurance": AP.sealed_assurance(uid, threshold=16, provider="test", method="test",
                                                     verified_at=datetime.now(timezone.utc), ref="t")}
    return {"age_category": cat}          # None / garbage: fara DOB si fara atestare => categorie necunoscuta


def set(doc: dict, cat) -> dict:        # noqa: A001
    for k in ("dob_enc", "age_band", "age_assurance", "parental", "age_category"):
        doc.pop(k, None)
    doc.update(fields(doc.get("user_id") or "", cat) if doc.get("user_id") else {"age_category": cat})
    return doc
