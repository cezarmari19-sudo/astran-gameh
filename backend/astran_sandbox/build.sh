#!/usr/bin/env bash
# Compileaza sandbox-ul Luau -> backend/astran_sandbox/bin/luau-sandbox
#
# Cerinte (Linux): git, cmake, g++ (C++17)
# Folosire, din folderul backend:   bash astran_sandbox/build.sh
#
# Optional: LUAU_TAG=<tag> bash astran_sandbox/build.sh   (implicit: ultima versiune)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${HERE}/.build"
BIN="${HERE}/bin"
SRC="${WORK}/luau"
OUT="${SRC}/cmake-build"

mkdir -p "${BIN}" "${WORK}"

if [ ! -d "${SRC}" ]; then
  if [ -n "${LUAU_TAG:-}" ]; then
    git clone --depth 1 --branch "${LUAU_TAG}" https://github.com/luau-lang/luau "${SRC}"
  else
    git clone --depth 1 https://github.com/luau-lang/luau "${SRC}"
  fi
fi

cmake -S "${SRC}" -B "${OUT}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DLUAU_BUILD_TESTS=OFF \
  -DLUAU_BUILD_CLI=OFF

cmake --build "${OUT}" --target Luau.VM Luau.Compiler Luau.Ast -j "$(nproc 2>/dev/null || echo 2)"

# In versiunile noi Luau e impartit in mai multe biblioteci (Bytecode, Common...).
# Se leaga toate cele care exista, in orice ordine (grup), ca sa mearga cu orice versiune.
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

g++ -std=c++17 -O2 "${HERE}/host.cpp" \
  -I"${SRC}/VM/include" -I"${SRC}/Compiler/include" \
  -Wl,--start-group "${LIBS[@]}" -Wl,--end-group -lm \
  -o "${BIN}/luau-sandbox"

echo "Gata: ${BIN}/luau-sandbox"

# Test rapid
echo 'print("sandbox ok")' | "${BIN}/luau-sandbox" --prelude "${HERE}/prelude.luau" -