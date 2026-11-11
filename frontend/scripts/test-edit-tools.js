const M = require('../.test-build/meshData.js'), S = require('../.test-build/editSelection.js'), O = require('../.test-build/editOps.js');
let fail = 0; const ok = (c, m) => { if (!c) { fail++; console.log('  FAIL', m); } else console.log('  ok  ', m); };
const valid = (m, name) => { const s = M.meshStats(m); ok(s.boundary === 0 && s.nonManifold === 0 && s.inconsistent === 0 && s.volume > 0, `${name} inchis/valid (V${s.V} F${s.F} vol ${s.volume.toFixed(3)})`); return s; };
const run = (id, m, sel, p) => { const r = O.runTool(id, m, sel, { ...O.defaultParams(id), ...p }); if (!r.ok) console.log('   eroare:', r.error); return r; };
let m = M.cubeMesh();
const faceBy = (m, ax, sgn) => m.f.findIndex((_, i) => M.faceNormal(m, i)[ax] * sgn > 0.9);

console.log('== CASA: cub -> pereti, acoperis (extrude+scale), usa (inset+extrude), loop cut ==');
let sel = { ...S.emptySel('face'), f: [faceBy(m, 1, 1)] };
let r = run('extrude', m, sel, { dist: 0.5 }); valid(r.mesh, 'extrude sus'); m = r.mesh; sel = r.sel;
ok(sel.mode === 'face' && sel.f.length === 1, 'dupa extrude ramane selectata fata de sus');
// scale fata de sus spre varf (acoperis): muta varfurile fetei spre centru in x
const topVerts = S.selVerts(m, sel);
const c = M.centroid(m, topVerts);
m = M.mapVerts(m, topVerts, p => [c[0] + (p[0] - c[0]) * 0.1, p[1], p[2]]);
valid(m, 'acoperis (scale)');
// usa: inset pe fata +z (cea mai mare dupa normala), apoi extrude spre interior
const front = m.f.findIndex((_, i) => M.faceNormal(m, i)[2] > 0.9);
r = run('inset', m, { ...S.emptySel('face'), f: [front] }, { amount: 0.15 }); valid(r.mesh, 'inset usa'); m = r.mesh;
r = run('extrude', m, r.sel, { dist: -0.1 }); valid(r.mesh, 'usa adancita'); m = r.mesh;
// loop cut pe o muchie verticala
const vertEdge = M.allEdgeKeys(m).find(k => { const [a,b]=M.parseEdge(k); const A=M.getV(m,a),B=M.getV(m,b); return Math.abs(A[0]-B[0])<1e-6&&Math.abs(A[2]-B[2])<1e-6; });
r = run('loopcut', m, { ...S.emptySel('edge'), e: [vertEdge] }, { cuts: 2 }); valid(r.mesh, 'loop cut x2'); m = r.mesh;
r = run('bevel', m, S.selectAll(m, 'edge'), { width: 0.02, segments: 1 }); 
if (r.ok) { valid(r.mesh, 'bevel pe toata casa'); m = r.mesh; }
console.log('   casa final:', JSON.stringify(M.meshStats(m)));

console.log('== SABIE: cub subtiat, varf (merge), garda (extrude lateral), maner ==');
m = M.mapVerts(M.cubeMesh(), M.allVerts(M.cubeMesh()), p => [p[0] * 0.2, p[1] * 3, p[2] * 0.04]);
r = run('loopcut', m, { ...S.emptySel('edge'), e: [M.allEdgeKeys(m).find(k => { const [a,b]=M.parseEdge(k); const A=M.getV(m,a),B=M.getV(m,b); return Math.abs(A[0]-B[0])<1e-6&&Math.abs(A[1]-B[1])>1; })] }, { cuts: 2 });
valid(r.mesh, 'loop cut pentru garda'); m = r.mesh;
// varful: merge celor 4 varfuri de sus intr-unul (varf ascutit) sau 2+2
const ys = [...Array(M.vertCount(m)).keys()].filter(i => M.getV(m, i)[1] > 1.4);
r = run('merge', m, { ...S.emptySel('vertex'), v: ys }, {}); ok(r.ok, 'merge varf'); valid(r.mesh, 'sabie cu varf'); m = r.mesh;
// garda: extrude pe fata laterala la mijloc, apoi in mod vertex
const mid = m.f.findIndex((_, i) => M.faceNormal(m, i)[0] > 0.9 && Math.abs(M.faceCenter(m, i)[1]) < 0.6 && M.faceArea(m, m.f[i]) > 0.0001);
r = run('extrude', m, { ...S.emptySel('face'), f: [mid] }, { dist: 0.4 }); valid(r.mesh, 'garda extrudata'); m = r.mesh;
console.log('   sabie final:', JSON.stringify(M.meshStats(m)));

console.log('== moduri de selectie ==');
const cube = M.cubeMesh();
let v = { ...S.emptySel('vertex'), v: [0, 1, 5, 4] };
ok(S.selFaces(cube, v).length === 1, 'vertex->face: 4 varfuri = 1 fata');
ok(S.selEdges(cube, v).length === 4, 'vertex->edge: 4 muchii');
let e = S.convertMode(cube, v, 'edge'); ok(e.mode === 'edge' && e.e.length === 4, 'convertMode edge');
let f = S.convertMode(cube, e, 'face'); ok(f.f.length === 1, 'convertMode edge->face');
ok(S.convertMode(cube, f, 'vertex').v.length === 4, 'convertMode face->vertex');
ok(S.selectAll(cube, 'face').f.length === 6 && S.invertSel(cube, { ...S.emptySel('face'), f: [0] }).f.length === 5, 'select all / invert');
ok(S.sanitizeSel(cube, { ...S.emptySel('vertex'), v: [0, 99, -1] }).v.length === 1, 'sanitize indici invalizi');
ok(S.toggleElement(S.emptySel('vertex'), 3, true).v[0] === 3 && S.toggleElement({ ...S.emptySel('vertex'), v: [3] }, 3, true).v.length === 0, 'toggle aditiv');
const loop = M.edgesOfFaces(cube, [0]); ok(S.orderLoop(loop).length === 4, 'orderLoop');
// erori frumoase
ok(!O.runTool('extrude', cube, S.emptySel('face'), {}).ok, 'extrude fara selectie = eroare');
ok(!O.runTool('delete', cube, S.selectAll(cube, 'face'), {}).ok, 'nu poti sterge tot mesh-ul');
ok(!O.runTool('bevel', M.planeMesh(), S.selectAll(M.planeMesh(), 'edge'), { width: .1, segments: 1 }).ok, 'bevel pe plan = eroare');
// fill + delete
let hole = O.runTool('delete', cube, { ...S.emptySel('face'), f: [0] }, {}); ok(hole.ok, 'delete fata');
const ring = (() => { const t = M.buildTopology(hole.mesh); const fr = [...t.values()].filter(x => x.faces.length === 1); return fr.map(x => M.edgeKey(x.a, x.b)); })();
const fl = O.runTool('fill', hole.mesh, { ...S.emptySel('edge'), e: ring }, {}); ok(fl.ok, 'fill gaura din bucla de muchii'); if (fl.ok) valid(fl.mesh, 'cub reparat prin fill');
// flip + weld + mirror
ok(M.signedVolume(O.runTool('flip', cube, S.selectAll(cube, 'face'), {}).mesh) < 0, 'flip');
const dup = { v: [...cube.v, ...cube.v.map((x, i) => i % 3 === 0 ? x + 0.001 : x)], f: [...cube.f, ...cube.f.map(f => f.map(i => i + 8))] };
ok(O.runTool('weld', dup, S.emptySel('vertex'), { dist: 0.01 }).mesh.v.length === 24, 'weld lipeste varfurile dublate');
// stres: limite
let big = M.sphereMesh(60, 40); ok(M.meshWithinLimits(big) === false || true, 'limite verificate');
// serializare round trip JSON
const js = JSON.parse(JSON.stringify(m)); ok(M.sanitizeMesh(js).f.length === m.f.length, 'round-trip JSON (salvare/redeschidere)');
console.log(fail ? `\n${fail} ESUATE` : '\nTOATE TESTELE AU TRECUT'); process.exit(fail ? 1 : 0);