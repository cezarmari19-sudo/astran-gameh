"""Recalculeaza amprentele SHA-256 din manifest.json pentru fisierele DEJA listate.

FOLOSESTE-L DOAR la prima instalare (de exemplu dupa ce ai copiat fisierele .txt prin clipboard si finalurile de
rand / ultimul rand nou s-au schimbat) sau cand pregatesti o versiune NOUA, nepublicata inca.
NU il rula ca sa "repari" o versiune deja acceptata de utilizatori: ar ascunde o modificare a unui text publicat.

    cd backend/legal && python rehash_manifest.py
"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
mf = root / "manifest.json"
man = json.loads(mf.read_text(encoding="utf-8"))
changed = 0
for doc_type, d in man["documents"].items():
    for v in d["versions"]:
        for lang, f in v["files"].items():
            digest = hashlib.sha256((root / f["path"]).read_bytes()).hexdigest()
            if digest != f["sha256"]:
                print(f"{doc_type} {v['version']} {lang}: {f['sha256'][:12]} -> {digest[:12]}")
                f["sha256"] = digest
                changed += 1
mf.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"done, {changed} hash(es) updated")