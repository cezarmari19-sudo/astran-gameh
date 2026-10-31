#!/usr/bin/env bash
# Copiaza prelude.luau (SURSA UNICA, cea folosita de server) intr-un fisier TS pe care
# clientul il poate importa ca string. Spre deosebire de build_wasm.sh (care are nevoie
# de Emscripten + retea), acesta e doar o copiere de text - ruleaza-l de fiecare data
# cand modifici backend/astran_sandbox/prelude.luau, ca server si client sa ramana
# GARANTAT pe acelasi preludiu (nu doua copii care pot sa diverga in timp).
#
# Folosire, din folderul backend:   bash astran_sandbox/sync_client_prelude.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_OUT="${HERE}/../../frontend/src/sandbox"
mkdir -p "${FRONTEND_OUT}"

python3 - "${HERE}/prelude.luau" "${FRONTEND_OUT}/preludeSource.ts" <<'PYEOF'
import json
import sys

src_path, out_path = sys.argv[1], sys.argv[2]
with open(src_path, "r", encoding="utf-8") as f:
    source = f.read()

# json.dumps produce un literal JS/TS valid (escapeaza ghilimele, backslash-uri si
# newline-uri corect) - mai sigur decat sa incercam sa construim manual un template
# literal cu backtick-uri, care s-ar rupe daca prelude.luau ar contine vreodata ` sau ${.
with open(out_path, "w", encoding="utf-8") as f:
    f.write(
        "// GENERAT de astran_sandbox/sync_client_prelude.sh din prelude.luau - NU EDITA MANUAL.\n"
        "// Acelasi preludiu, cuvant cu cuvant, ca cel folosit de server (runner.py/host.cpp) -\n"
        "// clientul si serverul trebuie sa ramana garantat identice, altfel acelasi script s-ar\n"
        "// putea comporta diferit in functie de unde a fost ales sa ruleze.\n"
        "export const PRELUDE_SOURCE: string = " + json.dumps(source) + ";\n"
    )
PYEOF

echo "Gata: ${FRONTEND_OUT}/preludeSource.ts"