#!/usr/bin/env bash
# Compileaza Luau + host_wasm.cpp in WebAssembly si genereaza frontend/src/sandbox/luauWasm.ts
# (glue Emscripten cu .wasm inclus, SINGLE_FILE) - runtime-ul izolat al sandbox-ului CLIENT.
#
# Cerinte: Emscripten (emcc, emcmake) activat in shell (source emsdk_env.sh), git, cmake, python3.
# Foloseste retea o singura data (git clone Luau). Ruleaza pe PC/Linux/WSL, nu in AndroidIDE.
# Folosire, din folderul backend:   bash astran_sandbox/build_wasm.sh
# OBLIGATORIU: LUAU_TAG=<tag> bash astran_sandbox/build_wasm.sh   (versiune Luau fixata, ex. 0.6xx;
# vezi tag-urile cu: git ls-remote --tags https://github.com/luau-lang/luau | tail)
set -euo pipefail

command -v emcc >/dev/null || { echo "emcc nu e in PATH (activeaza Emscripten: source emsdk_env.sh)" >&2; exit 1; }
command -v emcmake >/dev/null || { echo "emcmake nu e in PATH" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${HERE}/.build_wasm"
SRC="${WORK}/luau"
OUT="${SRC}/cmake-build-wasm"
FRONTEND_OUT="${HERE}/../../frontend/src/sandbox"
mkdir -p "${WORK}" "${FRONTEND_OUT}"

if [ ! -d "${SRC}" ]; then
  if [ -z "${LUAU_TAG:-}" ]; then
    echo "Seteaza LUAU_TAG la o versiune fixa de Luau (build reproductibil, fara cod nefixat din 'main')." >&2
    echo "Tag-uri: git ls-remote --tags https://github.com/luau-lang/luau | tail" >&2
    exit 1
  fi
  git clone --depth 1 --branch "${LUAU_TAG}" https://github.com/luau-lang/luau "${SRC}"
fi
echo "Luau: $(git -C "${SRC}" describe --tags --always) @ $(git -C "${SRC}" rev-parse HEAD)"

# -fexceptions: Luau (VM si compiler) foloseste exceptii C++ pentru erori; Emscripten le dezactiveaza implicit.
emcmake cmake -S "${SRC}" -B "${OUT}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_FLAGS="-fexceptions" \
  -DLUAU_BUILD_TESTS=OFF \
  -DLUAU_BUILD_CLI=OFF

cmake --build "${OUT}" --target Luau.VM Luau.Compiler Luau.Ast -j "$(nproc 2>/dev/null || echo 2)"

LIBS=()
for name in Compiler Ast Bytecode Common VM; do
  lib="${OUT}/libLuau.${name}.a"
  if [ -f "${lib}" ]; then
    LIBS+=("${lib}")
  fi
done
if [ ! -f "${OUT}/libLuau.VM.a" ] || [ ! -f "${OUT}/libLuau.Compiler.a" ]; then
  echo "Nu am gasit bibliotecile Luau in ${OUT}:" >&2
  ls "${OUT}" >&2 || true
  exit 1
fi

GLUE="${WORK}/luau_glue.js"
# ENVIRONMENT=worker: codul ruleaza DOAR intr-un Worker. SINGLE_FILE: .wasm e inclus in JS
# (nu se face nicio cerere de retea - CSP-ul paginii interzice orice connect-src).
# MAXIMUM_MEMORY: plafon dur la nivel de VM WASM, peste limita --mem a host-ului.
emcc -std=c++17 -O2 -fexceptions "${HERE}/host_wasm.cpp" \
  -I"${SRC}/VM/include" -I"${SRC}/Compiler/include" \
  "${LIBS[@]}" \
  -o "${GLUE}" \
  -s MODULARIZE=1 -s EXPORT_NAME=createAstranLuau \
  -s SINGLE_FILE=1 -s ENVIRONMENT=worker \
  -s EXPORTED_FUNCTIONS=_astran_run,_malloc,_free \
  -s EXPORTED_RUNTIME_METHODS=UTF8ToString,stringToUTF8,lengthBytesUTF8 \
  -s FILESYSTEM=0 -s ALLOW_MEMORY_GROWTH=1 -s INITIAL_MEMORY=33554432 -s MAXIMUM_MEMORY=268435456 \
  -s EXIT_RUNTIME=0 -s ASSERTIONS=0

python3 - "${GLUE}" "${FRONTEND_OUT}/luauWasm.ts" <<'PYEOF'
import json, sys
glue_path, out_path = sys.argv[1], sys.argv[2]
glue = open(glue_path, "r", encoding="utf-8").read()
with open(out_path, "w", encoding="utf-8") as f:
    f.write(
        "// GENERAT de astran_sandbox/build_wasm.sh - NU EDITA MANUAL.\n"
        "// Glue Emscripten (cu .wasm inclus) pentru Luau; defineste `createAstranLuau`.\n"
        "export const LUAU_GLUE_JS: string = " + json.dumps(glue) + ";\n"
    )
PYEOF

echo "Gata: ${FRONTEND_OUT}/luauWasm.ts ($(wc -c < "${FRONTEND_OUT}/luauWasm.ts") octeti)"