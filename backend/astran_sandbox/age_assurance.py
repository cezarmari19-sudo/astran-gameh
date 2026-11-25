"""Age assurance pentru functii 18+ - adapter modular de provider.

NU exista un provider real conectat in acest repo. Ce exista:
  * `AgeAssuranceProvider` - interfata (start + verificarea atestarii);
  * `NullProvider` - implicit: "neconfigurat"; accesul 18+ ramane BLOCAT, nimic nu se marcheaza verificat;
  * `SignedWebhookProvider` - integrare generica: un serviciu extern (verificare ID/selfie/eID/banca/operator mobil,
    ales de operator) confirma DOAR un prag (16 sau 18; `threshold` + `threshold_met`) si trimite o atestare semnata HMAC la
    POST /api/age/assurance/callback. Se activeaza doar cu variabilele de mediu de mai jos.

Mediu:  ASTRAN_AGE_ASSURANCE_PROVIDER=signed_webhook
        ASTRAN_AGE_ASSURANCE_SECRET=<secret partajat cu providerul>
        ASTRAN_AGE_ASSURANCE_START_URL=https://provider.example/start     (unde il trimitem pe utilizator)
        ASTRAN_AGE_ASSURANCE_NAME=<nume afisat/auditat, ex. "acme-eid">

Date stocate: DOAR rezultatul (prag atins), numele providerului/metodei, data, o referinta opaca. Niciun act de
identitate, nicio imagine, nicio data de nastere venita de la provider.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import attestation


class AssuranceError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class AssuranceStart:
    provider: str
    redirect_url: str


@dataclass(frozen=True)
class AssuranceResult:
    session_id: str
    threshold_met: bool
    provider: str
    method: str
    nonce: str
    threshold: int = 18          # pragul pe care providerul l-a atestat (16 sau 18); se compara cu cel cerut la start


class AgeAssuranceProvider:
    name = "none"

    def configured(self) -> bool:
        return False

    def start(self, session_id: str, threshold: int = 18) -> AssuranceStart:
        raise AssuranceError("age_assurance_not_configured", 503)

    def parse_callback(self, body: bytes, signature: Optional[str], now: Optional[float] = None) -> AssuranceResult:
        raise AssuranceError("age_assurance_not_configured", 503)


class NullProvider(AgeAssuranceProvider):
    pass


class SignedWebhookProvider(AgeAssuranceProvider):
    def __init__(self, secret: str, start_url: str, name: str = "signed_webhook"):
        self._secret, self._start_url, self.name = secret, start_url, name

    def configured(self) -> bool:
        return bool(self._secret and self._start_url.startswith("https://"))

    def start(self, session_id: str, threshold: int = 18) -> AssuranceStart:
        if not self.configured():
            raise AssuranceError("age_assurance_not_configured", 503)
        if threshold not in (16, 18):
            raise AssuranceError("invalid_threshold", 400)
        from urllib.parse import urlencode
        sep = "&" if "?" in self._start_url else "?"
        return AssuranceStart(self.name, f"{self._start_url}{sep}{urlencode({'session': session_id, 'threshold': threshold})}")

    def parse_callback(self, body: bytes, signature: Optional[str], now: Optional[float] = None) -> AssuranceResult:
        if not self.configured():
            raise AssuranceError("age_assurance_not_configured", 503)
        try:
            attestation.verify(self._secret, body, signature, now=now)
        except attestation.SignatureError:
            raise AssuranceError("invalid_signature", 401)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise AssuranceError("invalid_payload", 400)
        if not isinstance(data, dict):
            raise AssuranceError("invalid_payload", 400)
        sid, nonce, method = data.get("session_id"), data.get("nonce"), data.get("method")
        if not (isinstance(sid, str) and 16 <= len(sid) <= 128 and isinstance(nonce, str) and 8 <= len(nonce) <= 128):
            raise AssuranceError("invalid_payload", 400)
        # Contract (definit de acest repo; un provider real are nevoie de un adaptor care il produce):
        #   {"session_id", "nonce", "method"?, "threshold": 16|18, "threshold_met": <bool strict>}
        # Forma veche {"over_18": bool} == prag 18. `threshold_met` trebuie sa fie EXACT boolean: "true"/1/"yes" NU.
        if "threshold" in data or "threshold_met" in data:
            thr, met = data.get("threshold"), data.get("threshold_met")
            if isinstance(thr, bool) or thr not in (16, 18) or not isinstance(met, bool):
                raise AssuranceError("invalid_payload", 400)
            if "over_18" in data and (not isinstance(data["over_18"], bool) or (thr == 18 and data["over_18"] != met)):
                raise AssuranceError("invalid_payload", 400)
        elif isinstance(data.get("over_18"), bool):
            thr, met = 18, data["over_18"]
        else:
            raise AssuranceError("invalid_payload", 400)
        return AssuranceResult(sid, met, self.name, str(method or "unspecified")[:40], nonce, thr)


def get_provider() -> AgeAssuranceProvider:
    if os.environ.get("ASTRAN_AGE_ASSURANCE_PROVIDER", "").strip() == "signed_webhook":
        return SignedWebhookProvider(os.environ.get("ASTRAN_AGE_ASSURANCE_SECRET", ""),
                                     os.environ.get("ASTRAN_AGE_ASSURANCE_START_URL", ""),
                                     os.environ.get("ASTRAN_AGE_ASSURANCE_NAME", "signed_webhook"))
    return NullProvider()
