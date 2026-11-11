#!/bin/sh
# Testeaza geometria Edit Mode (extrude, inset, bevel, loop cut, merge, delete...): inchis, manifold, orientat, volume corecte.
# Ruleaza din frontend/:  sh scripts/test-mesh.sh
set -e
cd "$(dirname "$0")/.."
npx tsc -p scripts/tsconfig.test.json
node scripts/test-mesh-ops.js | tail -3
node scripts/test-edit-tools.js | tail -3
rm -rf .test-build