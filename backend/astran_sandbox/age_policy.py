"""Politica de varsta: data nasterii, benzi de varsta, acces 18+ (doar stdlib + `cryptography`).

PRINCIPII (toate aplicate pe SERVER; clientul nu este o sursa de adevar):
  * Clientul trimite DOAR `date_of_birth` (ISO 8601, YYYY-MM-DD). Categoria NU se trimite si NU se accepta de la client.
  * Benzile: `under_16` | `16_17` | `18_plus`. Pragul de 16 ani (GDPR art. 8, optiunea Romaniei) e doar pragul de
    CONSIMTAMANT pentru datele personale; pragul de 18 ani (continut/functii pentru adulti) e distinct si NU se
    obtine din data nasterii declarata: cere age assurance (vezi age_assurance.py).
  * `users.age_category` ramane pentru compatibilitate (chat, DM, realtime, jocuri il citesc direct din DB) dar
    e DERIVAT: ia valoarea "adult_18" DOAR cand `users.age_assurance.status == "verified"` (scris exclusiv de
    age_service.apply_assurance_result). Orice alta stare = "under_18" (fail-closed).
  * Data nasterii se pastreaza criptat (Fernet) in `users.dob_enc`; in loguri/audit nu apare niciodata.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

log = logging.getLogger("astran.age")

# GDPR art. 8(1): 16 ani implicit; Romania a ramas la 16 (Legea 190/2018 nu coboara pragul).
DIGITAL_CONSENT_AGE = 16
ADULT_AGE = 18
MAX_PLAUSIBLE_AGE = 120

BAND_UNDER_16 = "under_16"
BAND_16_17 = "16_17"
BAND_ADULT = "18_plus"
BANDS = (BAND_UNDER_16, BAND_16_17, BAND_ADULT)

CAT_MINOR = "under_18"
CAT_ADULT = "adult_18"

# Stari ale contului din perspectiva varstei/consimtamantului parental
ACCOUNT_ACTIVE = "ACTIVE"
ACCOUNT_AGE_INFO_REQUIRED = "AGE_INFO_REQUIRED"
ACCOUNT_PARENTAL_BLOCKED = "PARENTAL_CONSENT_REQUIRED"

PARENTAL_PENDING = "PENDING_PARENTAL_CONSENT"
PARENTAL_APPROVED = "PARENTAL_CONSENT_APPROVED"
PARENTAL_REJECTED = "PARENTAL_CONSENT_REJECTED"
PARENTAL_WITHDRAWN = "PARENTAL_CONSENT_WITHDRAWN"
PARENTAL_VERIFICATION_REQUIRED = "PARENTAL_VERIFICATION_REQUIRED"
PARENTAL_STATES = (PARENTAL_PENDING, PARENTAL_APPROVED, PARENTAL_REJECTED, PARENTAL_WITHDRAWN,
                   PARENTAL_VERIFICATION_REQUIRED)

ASSURANCE_NONE = "none"
ASSURANCE_PENDING = "pending"
ASSURANCE_VERIFIED = "verified"
ASSURANCE_FAILED = "failed"


class AgeError(Exception):
    """Eroare de validare a varstei/datei nasterii; `code` e stabil (folosit de client pentru mesaje)."""

    def __init__(self, code: str, status: int = 422, message: Optional[str] = None, **extra: Any):
        self.code, self.status, self.extra = code, status, extra
        self.message = message or code
        super().__init__(self.message)

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


_MESSAGES = {
    "date_of_birth_required": "Data nasterii este obligatorie (format AAAA-LL-ZZ).",
    "date_of_birth_invalid_format": "Data nasterii trebuie sa fie in format ISO 8601: AAAA-LL-ZZ.",
    "date_of_birth_impossible": "Data nasterii nu exista in calendar.",
    "date_of_birth_in_future": "Data nasterii nu poate fi in viitor.",
    "date_of_birth_implausible": "Data nasterii nu este plauzibila.",
}

_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def parse_dob(raw: Any, today: Optional[date] = None) -> date:
    """Valideaza STRICT data nasterii. Singura forma acceptata: sir 'AAAA-LL-ZZ'. Respinge viitorul,
    datele inexistente (30 februarie), tipurile non-sir si varstele > 120 de ani."""
    today = today or _today()
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise AgeError("date_of_birth_required", 422, _MESSAGES["date_of_birth_required"])
    if not isinstance(raw, str) or not _ISO_DATE.fullmatch(raw):
        raise AgeError("date_of_birth_invalid_format", 422, _MESSAGES["date_of_birth_invalid_format"])
    y, m, d = int(raw[0:4]), int(raw[5:7]), int(raw[8:10])
    try:
        dob = date(y, m, d)
    except ValueError:
        raise AgeError("date_of_birth_impossible", 422, _MESSAGES["date_of_birth_impossible"])
    if dob > today:
        raise AgeError("date_of_birth_in_future", 422, _MESSAGES["date_of_birth_in_future"])
    if compute_age(dob, today) > MAX_PLAUSIBLE_AGE:
        raise AgeError("date_of_birth_implausible", 422, _MESSAGES["date_of_birth_implausible"])
    return dob


def compute_age(dob: date, today: Optional[date] = None) -> int:
    """Varsta in ani impliniti. Cel nascut pe 29 februarie implineste anul pe 1 martie in anii nebisecti
    (comparatia pe (luna, zi) face asta natural). Ziua curenta e cea UTC."""
    today = today or _today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def band_for_age(age: int) -> str:
    if age < DIGITAL_CONSENT_AGE:
        return BAND_UNDER_16
    if age < ADULT_AGE:
        return BAND_16_17
    return BAND_ADULT


@dataclass(frozen=True)
class AgeResult:
    dob: date
    age: int
    band: str


def evaluate_dob(raw: Any, today: Optional[date] = None) -> AgeResult:
    today = today or _today()
    dob = parse_dob(raw, today)
    age = compute_age(dob, today)
    return AgeResult(dob=dob, age=age, band=band_for_age(age))


# ----------------------------------------------------------------------------- criptare DOB + pseudonime
def _root_secret() -> bytes:
    k = os.environ.get("AGE_DATA_KEY")
    if k:
        return k.encode()
    # fallback: derivat din JWT_SECRET (deja obligatoriu in productie). Recomandat: AGE_DATA_KEY separat;
    # rotirea JWT_SECRET fara AGE_DATA_KEY face datele de nastere nedecriptabile (=> fail-closed, cont "fara varsta").
    return ("astran-age-fallback|" + os.environ.get("JWT_SECRET", "astran-dev-secret-change-in-prod")).encode()


def _derive(label: str) -> bytes:
    return hmac.new(_root_secret(), b"astran-age|" + label.encode(), hashlib.sha256).digest()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(_derive("dob-encryption")))


def encrypt_dob(dob: date) -> str:
    return "v1:" + _fernet().encrypt(dob.isoformat().encode()).decode()


def decrypt_dob(blob: Any) -> Optional[date]:
    """None daca lipseste sau nu poate fi decriptata (cheie schimbata/corupt) => tratat ca "varsta necunoscuta"."""
    if not isinstance(blob, str) or not blob.startswith("v1:"):
        return None
    try:
        return date.fromisoformat(_fernet().decrypt(blob[3:].encode()).decode())
    except Exception:  # noqa: BLE001
        log.warning("dob_enc could not be decrypted (key changed or data corrupt)")
        return None


def seal(text: str) -> str:
    """Criptare simetrica pentru date personale minime pastrate doar cat e necesar (ex. emailul parintelui)."""
    return "v1:" + _fernet().encrypt(text.encode()).decode()


def unseal(blob: Any) -> Optional[str]:
    if not isinstance(blob, str) or not blob.startswith("v1:"):
        return None
    try:
        return _fernet().decrypt(blob[3:].encode()).decode()
    except Exception:  # noqa: BLE001
        return None


def token_hash(raw: str) -> str:
    """Hash cu cheie al unui token (parental / sesiune de verificare). Tokenul brut nu se stocheaza niciodata."""
    return hmac.new(_derive("token-hash"), raw.encode(), hashlib.sha256).hexdigest()


def pseudonym(value: str, purpose: str = "audit") -> str:
    """Identificator stabil, ne-reversibil, pentru jurnale (nu se pun user_id / email in clar in audit)."""
    return hmac.new(_derive("pseudonym|" + purpose), value.encode(), hashlib.sha256).hexdigest()[:32]


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def email_fingerprint(email: str) -> str:
    return pseudonym(normalize_email(email), "email")


# ----------------------------------------------------------------------------- evaluarea contului
@dataclass(frozen=True)
class AccessInfo:
    band: Optional[str]                 # None = varsta necunoscuta
    age_category: str                   # valoarea EFECTIVA pentru codul legacy: "adult_18" doar cu age assurance
    adult_content_allowed: bool
    account_state: str                  # ACTIVE | AGE_INFO_REQUIRED | PARENTAL_CONSENT_REQUIRED
    restricted: bool                    # True => doar rutele de varsta/parental/privacy raman accesibile
    needs_age_info: bool
    parental_state: Optional[str]
    assurance_status: str
    next_action: Optional[str]          # enter_date_of_birth | parent_email | await_parent | verify_age_18 | None

    def public(self) -> dict:
        return {
            "age_band": self.band, "age_category": self.age_category,
            "adult_content_allowed": self.adult_content_allowed, "account_state": self.account_state,
            "restricted": self.restricted, "needs_age_info": self.needs_age_info,
            "parental_state": self.parental_state, "age_assurance": self.assurance_status,
            "next_action": self.next_action,
        }


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def block_under16_until_parental() -> bool:
    """Implicit ON: un cont sub 16 ani nu foloseste serviciul pana la aprobarea parentala. Motiv: capacitatea de
    exercitiu (C. civ. art. 38-41) e un temei SEPARAT de art. 8 GDPR; temeiul exact se valideaza juridic."""
    return _env_flag("ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL", True)


def _age_info_deadline() -> Optional[date]:
    raw = os.environ.get("ASTRAN_AGE_INFO_DEADLINE", "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def evaluate_access(user: Optional[dict], today: Optional[date] = None) -> AccessInfo:
    """Starea de acces a unui cont, calculata DIN NOU la fiecare cerere (fara incredere in campuri din client).
    Pozitiv, fail-closed: orice lipsa/ambiguitate => fara acces 18+."""
    today = today or _today()
    u = user or {}
    assurance = u.get("age_assurance") if isinstance(u.get("age_assurance"), dict) else {}
    a_status = assurance.get("status") or ASSURANCE_NONE
    dob = decrypt_dob(u.get("dob_enc"))
    band: Optional[str] = band_for_age(compute_age(dob, today)) if dob else None
    verified = a_status == ASSURANCE_VERIFIED

    # Un pragul-18 confirmat de provider fara DOB (metoda "doar prag") ridica banda la 18_plus; cu DOB, DOB-ul
    # trebuie sa fie compatibil (un DOB de 15 ani + "verificat 18+" = conflict => fara acces 18+).
    if band is None and verified and assurance.get("threshold") == ADULT_AGE:
        band = BAND_ADULT
    adult_ok = bool(verified and assurance.get("threshold") == ADULT_AGE and band == BAND_ADULT)

    parental = u.get("parental") if isinstance(u.get("parental"), dict) else {}
    p_state = parental.get("state")
    self_consent = bool(parental.get("self_consent_at"))

    state, restricted, needs_info, nxt = ACCOUNT_ACTIVE, False, False, None
    if band is None:
        needs_info, nxt = True, "enter_date_of_birth"
        dl = _age_info_deadline()
        if dl is not None and today > dl:
            state, restricted = ACCOUNT_AGE_INFO_REQUIRED, True
    elif band == BAND_UNDER_16:
        if p_state != PARENTAL_APPROVED:
            if block_under16_until_parental():
                state, restricted = ACCOUNT_PARENTAL_BLOCKED, True
            nxt = "parent_email" if p_state in (None, PARENTAL_REJECTED, PARENTAL_WITHDRAWN) else "await_parent"
    else:
        # 16+: isi da singur consimtamantul (GDPR art. 8). Daca exista stare parentala neaprobata, trebuie sa
        # confirme el insusi (age_service.confirm_own_consent); pana atunci ramane restrans.
        if p_state in (PARENTAL_PENDING, PARENTAL_REJECTED, PARENTAL_WITHDRAWN, PARENTAL_VERIFICATION_REQUIRED) \
                and not self_consent:
            state, restricted, nxt = ACCOUNT_PARENTAL_BLOCKED, True, "confirm_own_consent"
    if nxt is None and band != BAND_ADULT and not adult_ok:
        nxt = None  # 18+ nu e o actiune obligatorie; se ofera la cerere (verify_age_18)
    return AccessInfo(
        band=band, age_category=CAT_ADULT if adult_ok else CAT_MINOR, adult_content_allowed=adult_ok and not restricted,
        account_state=state, restricted=restricted, needs_age_info=needs_info, parental_state=p_state,
        assurance_status=a_status, next_action=nxt,
    )


def can_access_adult_content(user: Optional[dict], today: Optional[date] = None) -> bool:
    """SINGURA functie care decide accesul la continut/functii 18+. Pozitiva: fara `age_assurance` verificat
    si compatibil cu data nasterii (daca exista), raspunsul e False - indiferent de `age_category`."""
    return evaluate_access(user, today).adult_content_allowed


# Rute accesibile unui cont restrans (asteptare parentala / varsta lipsa dupa termen): doar ce trebuie ca
# utilizatorul sa iasa din starea restransa si sa-si exercite drepturile (GDPR).
RESTRICTED_ALLOWED_PREFIXES = (
    "/api/auth/me", "/api/auth/logout", "/api/age/", "/api/legal/", "/api/consent/", "/api/privacy/",
    "/api/account/data-export", "/api/parental/",
)


def path_allowed_when_restricted(path: str) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in RESTRICTED_ALLOWED_PREFIXES)
