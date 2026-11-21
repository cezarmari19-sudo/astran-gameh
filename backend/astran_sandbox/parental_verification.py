"""Verificarea ca aprobarea parentala vine de la titularul raspunderii parintesti - adapter modular.

REALITATEA: un link trimis pe email dovedeste doar ca cineva controleaza acea adresa; NU dovedeste autoritatea
parentala. De aceea, in acest cod, link-ul de email singur NU duce niciodata la PARENTAL_CONSENT_APPROVED.
Starea APPROVED se atinge DOAR dupa o atestare semnata de un provider de verificare suplimentara configurat de
operator (ex.: micro-plata cu card al parintelui, eID, apel video cu un operator, formular semnat calificat).

NU exista un provider real conectat in repo. `NotConfiguredVerifier` (implicit) lasa contul in
PARENTAL_VERIFICATION_REQUIRED. `SignedAttestationVerifier` e integrarea generica (HMAC, ca la age assurance).

Mediu:  ASTRAN_PARENTAL_VERIFIER=signed_attestation
        ASTRAN_PARENTAL_VERIFIER_SECRET=<secret partajat>
        ASTRAN_PARENTAL_VERIFIER_START_URL=https://provider.example/parent-verify
        ASTRAN_PARENTAL_VERIFIER_METHOD=<ex. card_micro_charge | eid | video_call | qualified_signature>

Nu se stocheaza documente de identitate ale parintelui: doar metoda, data si o referinta opaca a providerului.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from . import attestation


class VerifierError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class VerificationStart:
    method: str
    redirect_url: str


@dataclass(frozen=True)
class VerificationResult:
    verification_id: str
    consent_id: str
    parent_verified: bool
    method: str
    nonce: str


class ParentalVerifier:
    name = "not_configured"
    method = "none"

    def configured(self) -> bool:
        return False

    def start(self, verification_id: str) -> VerificationStart:
        raise VerifierError("parental_verifier_not_configured", 503)

    def parse_callback(self, body: bytes, signature: Optional[str], now: Optional[float] = None) -> VerificationResult:
        raise VerifierError("parental_verifier_not_configured", 503)


class NotConfiguredVerifier(ParentalVerifier):
    pass


class SignedAttestationVerifier(ParentalVerifier):
    def __init__(self, secret: str, start_url: str, method: str):
        self._secret, self._start_url, self.method = secret, start_url, method
        self.name = "signed_attestation"

    def configured(self) -> bool:
        return bool(self._secret and self._start_url.startswith("https://") and self.method)

    def start(self, verification_id: str) -> VerificationStart:
        if not self.configured():
            raise VerifierError("parental_verifier_not_configured", 503)
        from urllib.parse import urlencode
        sep = "&" if "?" in self._start_url else "?"
        return VerificationStart(self.method, f"{self._start_url}{sep}{urlencode({'verification': verification_id})}")

    def parse_callback(self, body: bytes, signature: Optional[str], now: Optional[float] = None) -> VerificationResult:
        if not self.configured():
            raise VerifierError("parental_verifier_not_configured", 503)
        try:
            attestation.verify(self._secret, body, signature, now=now)
        except attestation.SignatureError:
            raise VerifierError("invalid_signature", 401)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise VerifierError("invalid_payload", 400)
        vid, cid, nonce = data.get("verification_id"), data.get("consent_id"), data.get("nonce")
        if not (isinstance(vid, str) and 16 <= len(vid) <= 128 and isinstance(cid, str) and 8 <= len(cid) <= 128
                and isinstance(nonce, str) and 8 <= len(nonce) <= 128 and isinstance(data.get("parent_verified"), bool)):
            raise VerifierError("invalid_payload", 400)
        return VerificationResult(vid, cid, data["parent_verified"], self.method, nonce)


def get_verifier() -> ParentalVerifier:
    if os.environ.get("ASTRAN_PARENTAL_VERIFIER", "").strip() == "signed_attestation":
        return SignedAttestationVerifier(os.environ.get("ASTRAN_PARENTAL_VERIFIER_SECRET", ""),
                                         os.environ.get("ASTRAN_PARENTAL_VERIFIER_START_URL", ""),
                                         os.environ.get("ASTRAN_PARENTAL_VERIFIER_METHOD", "").strip()[:40])
    return NotConfiguredVerifier()