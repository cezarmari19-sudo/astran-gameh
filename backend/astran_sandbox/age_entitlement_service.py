"""Serviciu CENTRAL de entitlement-uri (stdlib): decide, pe SERVER, ce functie poate folosi un cont.

Construit PESTE `age_policy.evaluate_access` (care ramane sursa pentru varsta/atestare sigilata/stare parentala).
Nimic din client nu intra in decizie: nici `age_category`, nici starea din UI, nici localStorage.

Decizia depinde de:
  * varsta VERIFICATA (atestare sigilata a unui provider) sau acord parental APROBAT - nu de data declarata;
  * scopuri de consimtamant parental separate (`social`, `messaging`, `groups`, `purchases`, `publish`), valide DOAR cat
    timp acordul parental curent e APROBAT: retragerea acordului le invalideaza automat (se derivă la fiecare cerere);
  * starea contului (restrans / limitat / suspendat de moderare);
  * eligibilitatea functiei (matricea `RULES`).

MATRICEA (PROPUNERE pentru Romania/UE - necesita validare juridica, vezi AGE_PARENTAL_COMPLIANCE.md):

  functie                | sub 16 (acord parental)      | 16-17 verificat           | 18+ verificat
  -----------------------+------------------------------+---------------------------+--------------
  joc potrivit varstei   | da                           | da                        | da
  friends / cautare      | scop `social`                | da                        | da
  DM                     | scop `messaging`             | da                        | da
  grupuri                | scop `groups`                | da                        | da
  wallet (vizualizare)   | da                           | da                        | da
  shop: browse           | da                           | da                        | da
  shop: cumparare        | scop `purchases`             | da (Astrans deja detinuti)| da
  bani reali (buy/Stripe)| scop `purchases`             | blocat implicit (*)       | da
  transfer intre useri   | NU                           | NU                        | da
  publicare jocuri       | scop `publish`               | da                        | da
  vanzare in shop/monet. | NU                           | NU                        | da
  continut 18+           | NU                           | NU                        | da

  (*) ASTRAN_MINOR_REAL_MONEY_REQUIRES_PARENT=1 (implicit). Un minor verificat nu are acord parental, deci nu poate
      plati cu bani reali pana nu implineste 18 ani si il atesta. Decizie juridica: capacitatea minorului (C. civ.).

Un cont fara dovada (declarat 16+, neverificat) este `limited`: primeste doar functiile din tabela cu `needs_proof=False`.
Un cont sub 16 fara acord ramane `restricted` (blocare totala) cat timp `ASTRAN_UNDER16_BLOCK_UNTIL_PARENTAL=1` (implicit,
decizie juridica); cu flag-ul oprit primeste doar functiile fara dovada, nu cele sociale/comerciale.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from . import age_policy as AP

# --------------------------------------------------------------------------------------------- functii
F_PLAY = "play"                          # joc in jocuri potrivite varstei (fara chat)
F_FRIENDS = "friends"                    # prieteni, cereri, cautare utilizatori
F_BLOCK = "block"                        # blocare/deblocare: protectie, mereu permisa unui cont ne-restrans
F_DM = "dm"
F_GROUPS = "groups"
F_WALLET_VIEW = "wallet_view"
F_SHOP_BROWSE = "shop_browse"
F_SHOP_PURCHASE = "shop_purchase"        # cheltuire de Astrans deja detinuti
F_REAL_MONEY = "real_money_purchase"     # cumparare de Astrans cu bani reali (wallet/buy, Stripe, checkout)
F_TRANSFER = "transfer"                  # transfer intre utilizatori
F_PUBLISH = "game_publish"               # creare/publicare/editare jocuri, UGC
F_MONETIZE = "creator_monetization"      # vanzare in shop / venit creator
F_ADULT = "adult_content"

ALL_FEATURES = (F_PLAY, F_FRIENDS, F_BLOCK, F_DM, F_GROUPS, F_WALLET_VIEW, F_SHOP_BROWSE, F_SHOP_PURCHASE,
                F_REAL_MONEY, F_TRANSFER, F_PUBLISH, F_MONETIZE, F_ADULT)

# scopuri de consimtamant parental (separate)
S_SOCIAL, S_MESSAGING, S_GROUPS, S_PURCHASES, S_PUBLISH = "social", "messaging", "groups", "purchases", "publish"
CONSENT_SCOPES = (S_SOCIAL, S_MESSAGING, S_GROUPS, S_PURCHASES, S_PUBLISH)

# coduri de motiv (stabile; frontend-ul le traduce)
R_OK = "ok"
R_RESTRICTED = "account_restricted"
R_SUSPENDED = "account_suspended"
R_AGE_INFO = "age_info_required"
R_PARENT_REQUIRED = "parental_consent_required"
R_PARENT_PENDING = "parental_consent_pending"
R_PARENT_REVOKED = "parental_consent_revoked"
R_VERIFY_AGE = "age_verification_required"
R_SCOPE = "parental_scope_required"
R_ADULT_ONLY = "adult_verified_only"
R_MINOR_BLOCKED = "not_available_to_minors"
R_RECIPIENT = "recipient_not_eligible"


@dataclass(frozen=True)
class Rule:
    needs_proof: bool = True           # cere varsta verificata SAU acord parental aprobat (consent_features_allowed)
    adult_only: bool = False           # doar 18+ verificat
    minor_blocked: bool = False        # niciun minor, indiferent de acord (acelasi efect ca adult_only, alt motiv)
    parental_scope: Optional[str] = None       # scop cerut cand eligibilitatea vine din acordul parental
    verified_minor_scope: Optional[str] = None  # scop cerut chiar si unui minor VERIFICAT (nu are cum sa-l obtina)


RULES: dict[str, Rule] = {
    F_PLAY: Rule(needs_proof=False),
    F_BLOCK: Rule(needs_proof=False),
    F_FRIENDS: Rule(parental_scope=S_SOCIAL),
    F_DM: Rule(parental_scope=S_MESSAGING),
    F_GROUPS: Rule(parental_scope=S_GROUPS),
    F_WALLET_VIEW: Rule(),
    F_SHOP_BROWSE: Rule(),
    F_SHOP_PURCHASE: Rule(parental_scope=S_PURCHASES),
    F_REAL_MONEY: Rule(parental_scope=S_PURCHASES, verified_minor_scope=S_PURCHASES),
    F_TRANSFER: Rule(minor_blocked=True),
    F_PUBLISH: Rule(parental_scope=S_PUBLISH),
    F_MONETIZE: Rule(adult_only=True),
    F_ADULT: Rule(adult_only=True),
}


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def minor_real_money_requires_parent() -> bool:
    return _env_flag("ASTRAN_MINOR_REAL_MONEY_REQUIRES_PARENT", True)


# --------------------------------------------------------------------------------------------- scopuri
def valid_scopes(user: Optional[dict], info: Optional[AP.AccessInfo] = None) -> frozenset:
    """Scopurile parentale VALIDE acum. Un scop contine `consent_id`; e valid doar daca (1) acordul curent al contului
    e APROBAT si (2) scopul a fost dat PE ACEST acord. Dupa retragere/respingere/acord nou, setul devine gol - fara
    nicio stergere explicita (derivat la fiecare cerere), deci nu poate ramane un scop 'agatat'."""
    u = user or {}
    parental = u.get("parental") if isinstance(u.get("parental"), dict) else {}
    if parental.get("state") != AP.PARENTAL_APPROVED:
        return frozenset()
    info = info or AP.evaluate_access(u)
    if info.band not in (AP.BAND_UNDER_16, AP.BAND_16_17):
        return frozenset()
    cid = parental.get("consent_id")
    scopes = parental.get("scopes") if isinstance(parental.get("scopes"), dict) else {}
    out = set()
    for name, rec in scopes.items():
        if name in CONSENT_SCOPES and isinstance(rec, dict) and rec.get("granted") is True and cid and rec.get("consent_id") == cid:
            out.add(name)
    return frozenset(out)


# --------------------------------------------------------------------------------------------- decizie
@dataclass(frozen=True)
class Decision:
    feature: str
    allowed: bool
    reason: str = R_OK
    next_action: Optional[str] = None
    scope: Optional[str] = None

    def detail(self) -> dict:
        return {"code": "feature_not_available", "feature": self.feature, "reason": self.reason,
                "next_action": self.next_action, "scope": self.scope}


def _request_open(user: Optional[dict], info: AP.AccessInfo) -> bool:
    """Exista o cerere parentala reala in curs (cu consent_id)? Starea PENDING fara consent_id e doar valoarea initiala
    pusa la inregistrare pentru un cont sub 16: acolo nu s-a cerut inca nimic."""
    parental = (user or {}).get("parental") if isinstance((user or {}).get("parental"), dict) else {}
    return info.parental_state in (AP.PARENTAL_PENDING, AP.PARENTAL_VERIFICATION_REQUIRED) and bool(parental.get("consent_id"))


def _proof_reason(info: AP.AccessInfo, user: Optional[dict] = None) -> tuple[str, Optional[str]]:
    """De ce un cont NU are dovada de varsta/consimtamant si ce poate face."""
    p = info.parental_state
    if info.band is None:
        return R_AGE_INFO, info.next_action or "enter_date_of_birth"
    if p == AP.PARENTAL_WITHDRAWN:
        return R_PARENT_REVOKED, info.next_action
    if _request_open(user, info):
        return R_PARENT_PENDING, info.next_action
    if info.band == AP.BAND_UNDER_16:
        return R_PARENT_REQUIRED, info.next_action
    return R_VERIFY_AGE, info.next_action


def evaluate(user: Optional[dict], feature: str, *, today: Optional[date] = None, suspended: bool = False) -> Decision:
    """Evalueaza O functie pentru un cont. Fail-closed: functie necunoscuta, user lipsa => refuz."""
    rule = RULES.get(feature)
    if rule is None or not isinstance(user, dict):
        return Decision(feature, False, R_RESTRICTED)
    if suspended:
        return Decision(feature, False, R_SUSPENDED)
    info = AP.evaluate_access(user, today)
    if info.restricted:
        reason, nxt = _proof_reason(info, user)
        return Decision(feature, False, R_RESTRICTED if info.account_state == AP.ACCOUNT_AGE_INFO_REQUIRED else reason, nxt)
    if not rule.needs_proof and not rule.adult_only and not rule.minor_blocked:
        return Decision(feature, True)
    adult = bool(info.adult_content_allowed)
    if rule.adult_only or rule.minor_blocked:
        if adult:
            return Decision(feature, True)
        if info.limited and info.band in (AP.BAND_ADULT, None):
            reason, nxt = _proof_reason(info, user)
            return Decision(feature, False, reason, nxt)
        return Decision(feature, False, R_MINOR_BLOCKED if rule.minor_blocked else R_ADULT_ONLY, None)
    # functii care cer dovada (varsta verificata sau acord parental)
    if not info.consent_features_allowed:
        reason, nxt = _proof_reason(info, user)
        return Decision(feature, False, reason, nxt)
    if adult:
        return Decision(feature, True)
    scopes = valid_scopes(user, info)
    if info.consent_eligibility == AP.ELIG_PARENTAL:
        if rule.parental_scope and rule.parental_scope not in scopes:
            return Decision(feature, False, R_SCOPE, "ask_parent_scope", rule.parental_scope)
        return Decision(feature, True)
    # minor VERIFICAT (16-17 atestat de provider), fara acord parental
    if rule.verified_minor_scope and minor_real_money_requires_parent() and rule.verified_minor_scope not in scopes:
        return Decision(feature, False, R_SCOPE, None, rule.verified_minor_scope)
    return Decision(feature, True)


def entitlements(user: Optional[dict], *, today: Optional[date] = None, suspended: bool = False) -> dict:
    """Matricea completa pentru UI: pentru fiecare functie -> permis/motiv. Doar CITIRE; serverul reaplica la fiecare cerere."""
    info = AP.evaluate_access(user or {}, today)
    return {
        "ui_status": ui_status(user, today=today),
        "valid_scopes": sorted(valid_scopes(user, info)),
        "features": {f: _public(evaluate(user, f, today=today, suspended=suspended)) for f in ALL_FEATURES},
    }


def _public(d: "Decision") -> dict:
    return {"allowed": d.allowed, "reason": d.reason, "next_action": d.next_action, "scope": d.scope}


def ui_status(user: Optional[dict], *, today: Optional[date] = None) -> str:
    """Eticheta de stare afisata in UI (cerinta 11). Una singura, derivata din starea reala de pe server."""
    info = AP.evaluate_access(user or {}, today)
    p = info.parental_state
    if info.restricted and info.account_state == AP.ACCOUNT_AGE_INFO_REQUIRED:
        return "restricted"
    if info.verified_threshold is not None and info.band is not None and not info.restricted:
        return "verified"
    if p == AP.PARENTAL_WITHDRAWN:
        return "parental_consent_revoked"
    if _request_open(user, info):
        return "parental_consent_pending"
    if info.consent_eligibility == AP.ELIG_PARENTAL:
        return "verified"
    if info.assurance_status == AP.ASSURANCE_PENDING:
        return "verification_pending"
    if info.assurance_status == AP.ASSURANCE_FAILED:
        return "verification_failed"
    if info.band == AP.BAND_UNDER_16:
        return "parental_consent_required"
    return "not_verified"


# --------------------------------------------------------------------------------------------- cereri HTTP
_PLAY = re.compile(r"^/api/games/[^/]+/(play|instance/heartbeat|instance/leave)$")
_COLLAB = re.compile(r"^/api/games/[^/]+/(collaborators|testers)(/|$)")
_SAFE = ("GET", "HEAD", "OPTIONS")
_BLOCK_PATHS = ("/api/friends/block", "/api/friends/unblock", "/api/friends/blocked")


def _under(path: str, *prefixes: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def feature_for_request(path: str, method: str = "GET") -> Optional[str]:
    """Mapeaza (cale, metoda) la functia ei. None = ruta nu cere entitlement (explorare, setari, DSA, refund...).
    Ordinea conteaza: cele mai specifice intai. Rutele necunoscute din prefixele sensibile cad pe functia generica a
    prefixului (fail-closed), nu pe 'permis'."""
    m = (method or "GET").upper()
    if path in _BLOCK_PATHS:
        return F_BLOCK
    if _under(path, "/api/friends", "/api/users/search"):
        return F_FRIENDS
    if _under(path, "/api/dm", "/ws/dm"):
        return F_DM
    if _under(path, "/api/groups"):
        return F_GROUPS
    if _under(path, "/api/wallet"):
        if path.startswith("/api/wallet/transfer"):
            return F_TRANSFER
        if path == "/api/wallet/buy" or path.startswith("/api/wallet/stripe"):
            return F_REAL_MONEY
        return F_WALLET_VIEW if m in _SAFE else F_REAL_MONEY
    if _under(path, "/api/consumer/checkout"):
        return F_REAL_MONEY
    if _under(path, "/api/shop"):
        if m in _SAFE:
            return F_SHOP_BROWSE
        if re.match(r"^/api/shop/items/[^/]+/buy$", path):
            return F_SHOP_PURCHASE
        return F_MONETIZE      # POST /models|/scripts, PATCH/DELETE items: vanzare = monetizare creator
    if _under(path, "/api/clothes"):
        return F_SHOP_BROWSE if m in _SAFE else F_SHOP_PURCHASE
    if _COLLAB.match(path):
        return F_PUBLISH
    if m not in _SAFE and _under(path, "/api/games", "/api/studio", "/api/sandbox") and not _PLAY.match(path):
        return F_PUBLISH
    return None


def check_request(user: Optional[dict], path: str, method: str = "GET", *, suspended: bool = False) -> Optional[Decision]:
    """Pentru poarta centrala: None = nimic de blocat; Decision(allowed=False) = refuz (403 cu `detail()`)."""
    feat = feature_for_request(path, method)
    if feat is None:
        return None
    d = evaluate(user, feat, suspended=suspended)
    return None if d.allowed else d


# --------------------------------------------------------------------------------------------- transferuri
def transfer_allowed(sender: Optional[dict], recipient: Optional[dict], *, recipient_suspended: bool = False) -> Decision:
    """Transfer intre utilizatori: expeditorul trebuie sa aiba F_TRANSFER (adult verificat); destinatarul nu poate fi un cont
    restrans/limitat, suspendat, in stergere sau minor (nu se muta valoare catre minori: risc de grooming/spalare)."""
    d = evaluate(sender, F_TRANSFER)
    if not d.allowed:
        return d
    r = recipient if isinstance(recipient, dict) else None
    if r is None or r.get("deletion_state"):
        return Decision(F_TRANSFER, False, R_RECIPIENT)
    rinfo = AP.evaluate_access(r)
    if rinfo.restricted or rinfo.limited or not rinfo.adult_content_allowed or recipient_suspended:
        return Decision(F_TRANSFER, False, R_RECIPIENT)
    return Decision(F_TRANSFER, True)
