"""Registrul versiunilor documentelor juridice (Terms of Service, Privacy Policy).

Sursa de adevar pentru "ce document a fost prezentat utilizatorului" este SERVERUL, nu clientul:
  * textele stau in backend/legal/<tip>/<versiune>.<limba>.txt, iar backend/legal/manifest.json fixeaza pentru
    fiecare fisier amprenta SHA-256. La incarcare se recalculeaza amprentele; orice nepotrivire (fisier editat dupa
    publicare, fisier lipsa) opreste serverul (fail-closed) - o versiune publicata nu se modifica, se adauga alta;
  * versiunea "curenta" a fiecarui document o decide manifestul, niciodata clientul;
  * fiecare versiune spune daca e nevoie de o NOUA ACCEPTARE (requires_reacceptance=true) sau doar de informare.

Cum adaugi o versiune noua: vezi backend/legal/README.md.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

LANGS = ("ro", "en")
DEFAULT_LANG = "en"
DEFAULT_LEGAL_DIR = Path(__file__).resolve().parent.parent / "legal"

# temeiul juridic al fiecarui tip de document (NU sunt toate "consimtaminte" GDPR):
#   contract            -> Termenii si Conditiile: acceptare contractuala (Art. 6(1)(b)), nu consimtamant
#   information_notice  -> Politica de Confidentialitate: informare (Art. 13/14), utilizatorul confirma ca a luat la cunostinta
BASIS_CONTRACT = "contract"
BASIS_NOTICE = "information_notice"
BASIS_CONSENT = "consent"


class LegalDocsError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LegalRegistry:
    def __init__(self, legal_dir: Optional[Path] = None):
        self.dir = Path(legal_dir) if legal_dir else Path(os.environ.get("ASTRAN_LEGAL_DIR") or DEFAULT_LEGAL_DIR)
        self._docs: dict = {}
        self._texts: dict = {}
        self._load()

    # ------------------------------------------------------------------ incarcare + verificare
    def _load(self) -> None:
        mf = self.dir / "manifest.json"
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise LegalDocsError(f"legal manifest unreadable: {mf}: {e}") from e
        docs = manifest.get("documents")
        if not isinstance(docs, dict) or not docs:
            raise LegalDocsError("legal manifest has no documents")
        for doc_type, d in docs.items():
            versions = d.get("versions") or []
            if not versions:
                raise LegalDocsError(f"{doc_type}: no versions")
            seen = set()
            for v in versions:
                ver = str(v.get("version") or "")
                if not ver or ver in seen:
                    raise LegalDocsError(f"{doc_type}: missing or duplicate version {ver!r}")
                seen.add(ver)
                for lang in LANGS:
                    f = (v.get("files") or {}).get(lang)
                    if not f:
                        raise LegalDocsError(f"{doc_type} {ver}: missing language {lang}")
                    path = (self.dir / f["path"]).resolve()
                    if self.dir.resolve() not in path.parents:
                        raise LegalDocsError(f"{doc_type} {ver}: path escapes legal dir")
                    try:
                        raw = path.read_bytes()
                    except OSError as e:
                        raise LegalDocsError(f"{doc_type} {ver} {lang}: file missing: {e}") from e
                    digest = sha256_bytes(raw)
                    if digest != f.get("sha256"):
                        raise LegalDocsError(
                            f"{doc_type} {ver} {lang}: content hash mismatch (a published version must never be edited; "
                            f"publish a new version instead)")
                    self._texts[(doc_type, ver, lang)] = (raw.decode("utf-8"), digest)
            cur = d.get("current")
            if cur not in seen:
                raise LegalDocsError(f"{doc_type}: current version {cur!r} not in versions")
            if d.get("legal_basis") not in (BASIS_CONTRACT, BASIS_NOTICE):
                raise LegalDocsError(f"{doc_type}: invalid legal_basis")
        self._docs = docs

    # ------------------------------------------------------------------ interogari
    def doc_types(self) -> list[str]:
        return list(self._docs.keys())

    def has_doc(self, doc_type: str) -> bool:
        return doc_type in self._docs

    def legal_basis(self, doc_type: str) -> str:
        return self._docs[doc_type]["legal_basis"]

    def current_version(self, doc_type: str) -> str:
        return self._docs[doc_type]["current"]

    def versions(self, doc_type: str) -> list[dict]:
        return list(self._docs[doc_type]["versions"])

    def version_meta(self, doc_type: str, version: str) -> Optional[dict]:
        for v in self._docs.get(doc_type, {}).get("versions", []):
            if v["version"] == version:
                return v
        return None

    def text(self, doc_type: str, version: str, lang: str) -> Optional[tuple[str, str]]:
        """(text, sha256) sau None daca versiunea/limba nu exista."""
        return self._texts.get((doc_type, version, lang))

    def document_ref(self, doc_type: str, version: str) -> str:
        return f"{doc_type}@{version}"

    def normalize_lang(self, lang: Optional[str]) -> str:
        l = (lang or "").strip().lower()[:2]
        return l if l in LANGS else DEFAULT_LANG

    def reacceptance_status(self, doc_type: str, accepted_version: Optional[str]) -> str:
        """'accept' = e nevoie de acceptare (nicio acceptare sau o versiune ulterioara cere re-acceptare);
        'notice' = exista versiuni mai noi, dar NICIUNA nu cere re-acceptare -> doar informare;
        'current' = utilizatorul are versiunea curenta."""
        cur = self.current_version(doc_type)
        if accepted_version is None:
            return "accept"
        if accepted_version == cur:
            return "current"
        order = [v["version"] for v in self.versions(doc_type)]
        if accepted_version not in order:
            return "accept"   # versiune necunoscuta: nu putem dovedi nimic despre ea, cerem acceptare
        newer = self.versions(doc_type)[order.index(accepted_version) + 1:]
        if any(v.get("requires_reacceptance") for v in newer):
            return "accept"
        return "notice"

    def summary_for(self, doc_type: str, version: str, lang: str) -> Optional[str]:
        meta = self.version_meta(doc_type, version) or {}
        return (meta.get("summary") or {}).get(lang) or (meta.get("summary") or {}).get(DEFAULT_LANG)


_registry: Optional[LegalRegistry] = None


def get_registry() -> LegalRegistry:
    global _registry
    if _registry is None:
        _registry = LegalRegistry()
    return _registry


def reset_registry_for_tests(reg: Optional[LegalRegistry] = None) -> None:
    global _registry
    _registry = reg