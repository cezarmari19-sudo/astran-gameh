"""Genereaza frontend/src/i18n/privacyPolicy.{ro,en}.ts din textele versionate din backend/legal (versiunea CURENTA).

Sursa de adevar este backend/legal/privacy_policy/<versiune>.<limba>.txt (cu SHA-256 in manifest). Fisierele TS sunt
DOAR o oglinda generata; test_privacy_policy.py esueaza daca nu coincid.
    cd backend/legal && python sync_frontend_policy.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONT = ROOT.parent.parent / "frontend" / "src" / "i18n"
HEADER = "// GENERAT din backend/legal/privacy_policy/{ver}.{lang}.txt de backend/legal/sync_frontend_policy.py - NU edita manual.\n"


def ts_for(lang: str, version: str, text: str) -> str:
    esc = text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return HEADER.format(ver=version, lang=lang) + f"export const PRIVACY_POLICY_{lang.upper()} = `{esc}`;\n"


def main() -> None:
    man = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["documents"]["privacy_policy"]
    ver = man["current"]
    meta = next(v for v in man["versions"] if v["version"] == ver)
    for lang in ("ro", "en"):
        text = (ROOT / meta["files"][lang]["path"]).read_text(encoding="utf-8")
        (FRONT / f"privacyPolicy.{lang}.ts").write_text(ts_for(lang, ver, text), encoding="utf-8")
        print("written", lang)


if __name__ == "__main__":
    main()
