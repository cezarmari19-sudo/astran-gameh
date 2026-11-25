"""Verificari automate pentru Politica de Confidentialitate si pentru deriva dintre cod si politica.

Rulare:  cd backend && python -m unittest astran_sandbox.test_privacy_policy -v

IMPORTANT: testele trecute NU inseamna conformitate GDPR. Ele prind doar: sectiuni lipsa, marcaje necompletate in
documente prezentate drept finale, structuri diferite intre limbi, afirmatii despre prelucrari nedocumentate, nesincronizare
frontend/backend si NOI colectii / hosturi / dependente / permisiuni aparute in cod fara reevaluarea politicii.
"""
import glob
import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from . import legal_docs as LD

BACKEND = Path(__file__).resolve().parent.parent
LEGAL = BACKEND / "legal"
FRONT = BACKEND.parent / "frontend"
INV = json.loads((LEGAL / "data_inventory.json").read_text(encoding="utf-8"))
MAN = json.loads((LEGAL / "manifest.json").read_text(encoding="utf-8"))["documents"]["privacy_policy"]
CUR = MAN["current"]


def _text(lang, ver=CUR):
    meta = next(v for v in MAN["versions"] if v["version"] == ver)
    return (LEGAL / meta["files"][lang]["path"]).read_text(encoding="utf-8")


def _sections(text):
    return re.findall(r"^(\d+(?:\.\d+)?)\. [A-ZĂÂÎȘȚ]", text, re.M)


def _prod_py():
    out = []
    for f in glob.glob(str(BACKEND / "**/*.py"), recursive=True):
        n = Path(f).name
        if n.startswith("test_") or "/deploy/" in f:
            continue
        out.append(Path(f))
    return out


class RequiredContent(unittest.TestCase):
    def test_same_structure_in_both_languages(self):
        self.assertEqual(_sections(_text("ro")), _sections(_text("en")))

    def test_all_mandatory_sections_exist(self):
        n = INV["required_policy_numbered_sections"]
        for lang in ("ro", "en"):
            top = [s for s in _sections(_text(lang)) if "." not in s]
            self.assertEqual(top, [str(i) for i in range(1, n + 1)], lang)

    def test_mandatory_gdpr_information_present(self):
        need = {  # art. 13 GDPR -> cuvinte cheie per limba
            "en": ["Controller", "e-mail", "legal basis", "Recipients".lower(), "international transfers", "retention",
                   "rights", "ANSPDCP", "cookies", "minors", "automated decisions", "withdraw consent", "Art. 6(1)"],
            "ro": ["Operator", "e-mail", "temei", "destinatari", "transferuri internaționale", "păstrarea datelor",
                   "drepturile", "ANSPDCP", "cookie", "minori", "decizii automate", "retrage", "art. 6"]}
        for lang, words in need.items():
            low = _text(lang).lower()
            for w in words:
                self.assertIn(w.lower(), low, f"{lang}: lipseste '{w}'")

    def test_every_processing_subsection_names_purpose_and_basis(self):
        for lang, basis in (("en", r"(Legal basis|Purpose and legal basis)"), ("ro", r"(Temei|Scop și temei)")):
            t = _text(lang)
            blocks = re.split(r"^2\.(\d+)\. ", t, flags=re.M)[2::2]
            for b in blocks:
                if b.startswith(("Data we do not", "Date pe care nu")):
                    continue
                self.assertRegex(b.split("\n\n\n")[0], basis, lang)

    def test_complaint_to_anspdcp_and_rights_procedure(self):
        for lang in ("ro", "en"):
            t = _text(lang)
            self.assertIn("www.dataprotection.ro", t)
            self.assertIn("Download Your Data", t)


class NoUndocumentedProcessing(unittest.TestCase):
    def test_forbidden_affirmative_claims_absent(self):
        for lang, pats in INV["policy_forbidden_affirmative_claims"].items():
            t = _text(lang)
            # singura formulare permisa: negarea explicita din sectiunea 1
            t = re.sub(r"does not train AI models on user data|nu antrenează modele de inteligență artificială pe datele utilizatorilor", "", t)
            for p in pats:
                self.assertIsNone(re.search(p, t, re.I), f"{lang}: afirmatie nedocumentata '{p}'")

    def test_policy_states_no_sale_no_ads_no_ai_training(self):
        self.assertRegex(_text("en"), r"does not sell or license personal data")
        self.assertRegex(_text("ro"), r"nu vinde și nu licențiază date personale")

    def test_no_old_policy_claims_in_frontend_mirrors(self):
        for lang in ("ro", "en"):
            ts = (FRONT / "src/i18n" / f"privacyPolicy.{lang}.ts").read_text(encoding="utf-8")
            self.assertNotIn("SALE AND LICENSING", ts)
            self.assertNotIn("VÂNZAREA ȘI LICENȚIEREA", ts)


class PlaceholdersAndPublication(unittest.TestCase):
    def test_placeholders_match_in_both_languages_and_doc_is_draft(self):
        en, ro = (LD.PLACEHOLDER_RE.findall(_text(l)) for l in ("en", "ro"))
        self.assertEqual(len(en), len(ro))
        meta = next(v for v in MAN["versions"] if v["version"] == CUR)
        if en:   # cat timp exista marcaje, documentul NU poate fi 'final'
            self.assertEqual(meta.get("status"), LD.STATUS_DRAFT)
            for l in ("ro", "en"):
                self.assertRegex(_text(l), r"(NECESITĂ COMPLETARE|NEEDS COMPLETION)")
        else:
            self.assertEqual(meta.get("status"), LD.STATUS_FINAL)
            self.assertTrue(meta.get("effective_date"))

    def _registry(self, status, effective, text_suffix=""):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        shutil.copytree(LEGAL / "privacy_policy", d / "privacy_policy")
        man = json.loads((LEGAL / "manifest.json").read_text(encoding="utf-8"))
        del man["documents"]["terms_of_service"]          # ToS 1.0 are un hash neconcordant (preexistent, vezi raport)
        v = man["documents"]["privacy_policy"]["versions"][0]
        v["status"], v["effective_date"] = status, effective
        if text_suffix:
            for l in ("ro", "en"):
                p = d / v["files"][l]["path"]
                p.write_text(p.read_text(encoding="utf-8") + text_suffix, encoding="utf-8")
                v["files"][l]["sha256"] = LD.sha256_bytes(p.read_bytes())
        (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        return LD.LegalRegistry(d)

    def test_real_policy_is_blocked_from_publication_until_completed(self):
        reg = self._registry(MAN["versions"][0].get("status"), None)
        self.assertTrue(reg.is_draft("privacy_policy"))
        if LD.PLACEHOLDER_RE.search(_text("en")):
            self.assertTrue(reg.publication_blockers())

    def test_final_status_with_placeholders_is_still_draft(self):
        reg = self._registry(LD.STATUS_FINAL, "2026-12-01")
        if LD.PLACEHOLDER_RE.search(_text("en")):
            self.assertTrue(reg.is_draft("privacy_policy"))
            self.assertTrue(any("marcaj" in b for b in reg.publication_blockers()))

    def test_complete_document_is_publishable_and_contact_is_required(self):
        # simulam un text complet: toate marcajele inlocuite
        reg = self._registry(LD.STATUS_FINAL, "2026-12-01")
        for l in ("ro", "en"):
            txt, _ = reg.text("privacy_policy", CUR, l)
            reg._texts[("privacy_policy", CUR, l)] = (LD.PLACEHOLDER_RE.sub("Valoare reala", txt), "x")
        self.assertEqual(reg.publication_blockers(), [])
        self.assertFalse(reg.is_draft("privacy_policy"))
        # fara data de intrare in vigoare nu se publica
        reg2 = self._registry(LD.STATUS_FINAL, None)
        for l in ("ro", "en"):
            txt, _ = reg2.text("privacy_policy", CUR, l)
            reg2._texts[("privacy_policy", CUR, l)] = (LD.PLACEHOLDER_RE.sub("x", txt), "x")
        self.assertTrue(any("effective_date" in b for b in reg2.publication_blockers()))

    def test_enforcement_env_refuses_startup_with_draft(self):
        import os
        reg = self._registry(LD.STATUS_DRAFT, None)
        old = (LD._registry, os.environ.get(LD.REQUIRE_FINAL_ENV))
        try:
            LD._registry = None
            os.environ[LD.REQUIRE_FINAL_ENV] = "1"
            orig = LD.LegalRegistry
            LD.LegalRegistry = lambda *a, **k: reg
            with self.assertRaises(LD.LegalDocsError):
                LD.get_registry()
        finally:
            LD.LegalRegistry = orig
            LD._registry = old[0]
            os.environ.pop(LD.REQUIRE_FINAL_ENV, None) if old[1] is None else os.environ.__setitem__(LD.REQUIRE_FINAL_ENV, old[1])


class DisplayedVersionIsVerifiedVersion(unittest.TestCase):
    def test_manifest_hashes_match_files(self):
        for v in MAN["versions"]:
            for l, f in v["files"].items():
                self.assertEqual(LD.sha256_bytes((LEGAL / f["path"]).read_bytes()), f["sha256"], l)

    def test_frontend_mirror_equals_served_text(self):
        spec = importlib.util.spec_from_file_location("sync_frontend_policy", LEGAL / "sync_frontend_policy.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for l in ("ro", "en"):
            expected = mod.ts_for(l, CUR, _text(l))
            actual = (FRONT / "src/i18n" / f"privacyPolicy.{l}.ts").read_text(encoding="utf-8")
            self.assertEqual(actual, expected, f"rulează: cd backend/legal && python sync_frontend_policy.py ({l})")

    def test_frontend_wrapper_only_exposes_supported_languages(self):
        idx = (FRONT / "src/i18n/index.ts").read_text(encoding="utf-8")
        langs = set(re.search(r'type LangCode = ([^;]+);', idx).group(1).replace('"', "").replace(" ", "").split("|"))
        w = (FRONT / "src/i18n/privacyPolicy.ts").read_text(encoding="utf-8")
        imported = set(re.findall(r'from "\./privacyPolicy\.(\w+)"', w))
        self.assertEqual(imported, langs)
        self.assertEqual(langs, set(LD.LANGS))


class CodeDriftNeedsPolicyReview(unittest.TestCase):
    """Cand codul adauga o categorie noua de colectare/transmitere, aceste teste esueaza: reevalueaza politica
    (backend/legal/privacy_policy/*), apoi adauga intrarea in backend/legal/data_inventory.json."""
    MSG = " -> REEVALUEAZA politica de confidentialitate si actualizeaza backend/legal/data_inventory.json"

    def test_mongo_collections_are_inventoried(self):
        found = set()
        for f in _prod_py():
            s = f.read_text(encoding="utf-8")
            found |= {m[0] for m in re.findall(
                r"\bdb\.([a-z_][a-z0-9_]*)\.(find|find_one|insert_one|insert_many|update_one|update_many|delete_one|"
                r"delete_many|count_documents|aggregate|create_index|find_one_and_update|replace_one|distinct|bulk_write|"
                r"find_one_and_delete)\b", s)}
            found |= set(re.findall(r"\bdb\[\s*[\"']([a-z_][a-z0-9_]*)[\"']\s*\]", s))
            found |= set(re.findall(r"^(?:C_[A-Z_]+|[A-Z_]*COLLECTION|JOURNAL)\s*=\s*[\"']([a-z_]+)[\"']", s, re.M))
        new = found - set(INV["collections"])
        self.assertFalse(new, f"colectii noi nedeclarate: {sorted(new)}" + self.MSG)

    def test_outbound_hosts_are_inventoried(self):
        hosts = set()
        for f in _prod_py():
            for h in re.findall(r"https?://([a-zA-Z0-9.-]+\.[a-z]{2,})", f.read_text(encoding="utf-8")):
                hosts.add(h.lower())
        known = set(INV["outbound_hosts"]) | set(INV["outbound_hosts_ignored_by_scan"])
        known |= {"provider.example", "astran.game", "localhost"}
        ignore = re.compile(r"(w3\.org|github\.com|eur-lex|example\.|docs\.|python\.org|mongodb\.com|stripe\.com/docs)")
        new = {h for h in hosts - known if not ignore.search(h)}
        self.assertFalse(new, f"hosturi externe noi: {sorted(new)}" + self.MSG)

    def test_backend_requirements_unchanged(self):
        reqs = {re.split(r"[=<>]", l.strip())[0].lower() for l in (BACKEND / "requirements.txt").read_text().splitlines()
                if l.strip() and not l.startswith("#")}
        reviewed = {"fastapi", "uvicorn", "websockets", "redis", "boto3", "requests-oauthlib", "cryptography", "python-dotenv",
                    "pymongo", "pydantic", "email-validator", "pyjwt", "bcrypt", "passlib", "tzdata", "motor", "python-jose",
                    "requests", "python-multipart", "httpx", "argon2-cffi", "webauthn"}
        self.assertFalse(reqs - reviewed, f"dependente backend noi: {sorted(reqs - reviewed)}" + self.MSG)

    def test_frontend_dependencies_and_permissions_unchanged(self):
        pkg = json.loads((FRONT / "package.json").read_text(encoding="utf-8"))
        deps = set(pkg["dependencies"])
        self.assertFalse(deps - set(INV["frontend_dependencies_reviewed"]),
                         f"dependente frontend noi: {sorted(deps - set(INV['frontend_dependencies_reviewed']))}" + self.MSG)
        for d in deps:
            for pat in INV["frontend_forbidden_dependency_patterns"]:
                self.assertNotIn(pat, d.lower(), f"{d}: SDK de tracking/senzori nedeclarat in politica" + self.MSG)
        app = json.loads((FRONT / "app.json").read_text(encoding="utf-8"))["expo"]
        self.assertEqual(app.get("android", {}).get("permissions", []), INV["app_permissions_declared"] or [])
        self.assertNotIn("infoPlist", app.get("ios", {}))
        plugins = [p[0] if isinstance(p, list) else p for p in app.get("plugins", [])]
        self.assertEqual(set(plugins), {"expo-router", "expo-iap", "expo-splash-screen"}, "plugin nou" + self.MSG)

    def test_inventory_collections_map_to_existing_policy_sections(self):
        secs = set(_sections(_text("en")))
        for c, sec in INV["collections"].items():
            for part in sec.split("/"):
                self.assertTrue(part in secs or part in {"6", "7"}, f"{c}: sectiune {sec} inexistenta")


if __name__ == "__main__":
    unittest.main()
