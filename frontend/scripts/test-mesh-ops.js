const M = require('../.test-build/meshData.js');
let fail = 0;
const ok = (c, msg) => { if (!c) { fail++; console.log('  FAIL', msg); } else console.log('  ok  ', msg); };
const closed = (m, name, euler) => {
  const s = M.meshStats(m);
  ok(s.boundary === 0 && s.nonManifold === 0 && s.inconsistent === 0, `${name}: inchis+manifold+orientat (b=${s.boundary} nm=${s.nonManifold} inc=${s.inconsistent})`);
  ok(s.volume > 0, `${name}: volum pozitiv ${s.volume.toFixed(4)}`);
  if (euler !== undefined) ok(s.euler === euler, `${name}: Euler=${s.euler}`);
  return s;
};
console.log('primitive');
for (const t of ['cube','sphere','cylinder','cone']) closed(M.primitiveMesh(t), t, 2);
closed(M.torusMesh(), 'torus', 0);
ok(Math.abs(M.signedVolume(M.cubeMesh()) - 1) < 1e-9, 'cub volum=1');

console.log('extrude');
let c = M.cubeMesh();
const top = c.f.findIndex((_, i) => M.faceNormal(c, i)[1] > 0.9);
let r = M.extrudeFaces(c, [top], 0.5);
let s = closed(r.mesh, 'extrude top', 2);
ok(Math.abs(s.volume - 1.5) < 1e-6, 'extrude volum 1.5');
ok(r.mesh.f.length === 10, 'extrude 10 fete');
r = M.extrudeFaces(c, [top], -0.25); s = closed(r.mesh, 'extrude interior', 2);
ok(Math.abs(s.volume - 0.75) < 1e-6, 'extrude negativ volum .75');
// extrude regiune 2 fete adiacente
const side = c.f.findIndex((_, i) => M.faceNormal(c, i)[0] > 0.9);
r = M.extrudeFaces(c, [top, side], 0.3); closed(r.mesh, 'extrude 2 fete', 2);
// extrude de 2 ori (tija) 
let rr = M.extrudeFaces(c, [top], 1); rr = M.extrudeFaces(rr.mesh, rr.selF, 1); s = closed(rr.mesh, 'extrude x2', 2);
ok(Math.abs(s.volume - 3) < 1e-6, 'extrude x2 volum 3');
// extrude toate fetele selectate 
r = M.extrudeFaces(c, M.allFaces(c), 0.1); ok(r.mesh.f.length >= 6, 'extrude all nu crapa');
// muchie
const e0 = M.allEdgeKeys(c)[0];
r = M.extrudeEdges(c, [e0], 0.5); ok(r.mesh.f.length === 7 && r.selV.length === 2, 'extrude edge adauga 1 fata');

console.log('inset');
r = M.insetFaces(c, [top], 0.2); s = closed(r.mesh, 'inset', 2);
ok(Math.abs(s.volume - 1) < 1e-6, 'inset volum neschimbat'); ok(r.mesh.f.length === 10, 'inset 10 fete');
const innerTop = r.mesh.f[r.selF[0]];
ok(Math.abs(M.faceArea(r.mesh, innerTop) - 0.36) < 1e-6, 'inset arie 0.6^2=0.36 (' + M.faceArea(r.mesh, innerTop).toFixed(4) + ')');
r = M.insetFaces(c, [top], 0.2, -0.1); s = closed(r.mesh, 'inset+depth', 2);
ok(Math.abs(s.volume - (1 - 0.1 / 3 * (1 + 0.36 + 0.6))) < 1e-6, 'inset depth volum frustum');
r = M.insetFaces(c, M.allFaces(c), 0.1, 0, true); closed(r.mesh, 'inset individual all', 2);
ok(r.mesh.f.length === 6 + 24, 'inset individual 30 fete (' + r.mesh.f.length + ')');
r = M.insetFaces(c, [top, side], 0.1); closed(r.mesh, 'inset regiune 2 fete', 2);
r = M.insetFaces(c, [top], 5); closed(r.mesh, 'inset clamp mare', 2);

console.log('loop cut');
r = M.loopCut(c, [M.allEdgeKeys(c)[0]], 1); s = closed(r.mesh, 'loopcut 1', 2);
ok(r.mesh.f.length === 10, 'loopcut cub -> 10 fete (' + r.mesh.f.length + ')'); ok(Math.abs(s.volume - 1) < 1e-6, 'volum pastrat');
r = M.loopCut(c, [M.allEdgeKeys(c)[0]], 3); s = closed(r.mesh, 'loopcut 3', 2);
ok(r.mesh.f.length === 6 + 4 * 3, 'loopcut 3 -> 18 fete (' + r.mesh.f.length + ')');
r = M.loopCut(c, [M.allEdgeKeys(c)[0]], 1, 0.5); closed(r.mesh, 'loopcut slide', 2);
// loop cut pe o sfera (ngon/triunghi la poli): ramane inchis
const sp = M.sphereMesh(); const ek = M.allEdgeKeys(sp)[40];
r = M.loopCut(sp, [ek], 1); closed(r.mesh, 'loopcut sfera', 2);
// loop cut pe cub dupa extrude
rr = M.extrudeFaces(c, [top], 1); r = M.loopCut(rr.mesh, [M.allEdgeKeys(rr.mesh)[2]], 2); closed(r.mesh, 'loopcut pe extrudat', 2);

console.log('bevel');
const some = (m, pred) => M.allEdgeKeys(m).filter(pred);
const vert = (m, k) => { const [a,b]=M.parseEdge(k); const A=M.getV(m,a),B=M.getV(m,b); return Math.abs(A[0]-B[0])<1e-9&&Math.abs(A[2]-B[2])<1e-9; };
const ev = some(c, k => vert(c, k))[0];
r = M.bevelEdges(c, [ev], 0.2, 1); s = closed(r.mesh, 'bevel 1 muchie seg1', 2);
ok(r.mesh.f.length === 7, 'bevel 1 muchie -> 7 fete (' + r.mesh.f.length + ')');
ok(Math.abs(s.volume - (1 - 0.02)) < 1e-6, 'bevel chamfer volum 1-w²/2=0.98 (' + s.volume.toFixed(5) + ')');
r = M.bevelEdges(c, [ev], 0.2, 4); s = closed(r.mesh, 'bevel 1 muchie seg4', 2);
const circ = 0.04 * (1 - Math.PI / 4);
ok(Math.abs(s.volume - (1 - circ)) < 2e-3, 'bevel rotunjit volum ~ 1-w²(1-π/4)=' + (1 - circ).toFixed(5) + ' got ' + s.volume.toFixed(5));
r = M.bevelEdges(c, M.allEdgeKeys(c), 0.1, 1); s = closed(r.mesh, 'bevel toate seg1', 2);
ok(r.mesh.f.length === 6 + 12 + 8, 'cub bevel total: 26 fete (' + r.mesh.f.length + ')');
for (const sg of [2, 3, 5]) { r = M.bevelEdges(c, M.allEdgeKeys(c), 0.15, sg); s = closed(r.mesh, `bevel toate seg${sg}`, 2); }
// volum aprox cub rotunjit: 1 - (4-pi)*w^2*... verificare plauzibila
r = M.bevelEdges(c, M.allEdgeKeys(c), 0.2, 6); s = M.meshStats(r.mesh);
ok(s.volume < 1 && s.volume > 0.85, 'cub rotunjit volum plauzibil ' + s.volume.toFixed(4));
// 2 muchii care se intalnesc la un colt, 3 muchii pe o fata
const faceE = M.edgesOfFaces(c, [top]);
r = M.bevelEdges(c, faceE, 0.1, 1); closed(r.mesh, 'bevel 4 muchii fata sus seg1', 2);
r = M.bevelEdges(c, faceE, 0.1, 3); closed(r.mesh, 'bevel 4 muchii fata sus seg3', 2);
r = M.bevelEdges(c, faceE.slice(0, 2), 0.1, 2); closed(r.mesh, 'bevel 2 muchii adiacente', 2);
r = M.bevelEdges(c, faceE.slice(0, 3), 0.1, 2); closed(r.mesh, 'bevel 3 muchii', 2);
// bevel pe sfera / cilindru / dupa extrude / dupa inset
const cy = M.cylinderMesh(12);
r = M.bevelEdges(cy, M.edgesOfFaces(cy, [cy.f.length - 2]), 0.08, 2); closed(r.mesh, 'bevel cilindru capac', 2);
rr = M.extrudeFaces(c, [top], 0.6); r = M.bevelEdges(rr.mesh, M.allEdgeKeys(rr.mesh), 0.05, 2); closed(r.mesh, 'bevel tot pe extrudat', 2);
// bevel pe muchii de frontiera -> ignorat
const pl = M.planeMesh(); ok(M.bevelEdges(pl, M.allEdgeKeys(pl), 0.1, 1) === null, 'bevel plan = null (muchii de frontiera)');
// latime uriasa e limitata
r = M.bevelEdges(c, M.allEdgeKeys(c), 5, 2); s = closed(r.mesh, 'bevel latime uriasa limitata', 2);

console.log('merge / delete / etc');
const e = M.allEdgeKeys(c)[0]; const [ea, eb] = M.parseEdge(e);
r = M.mergeVerts(c, [ea, eb]); s = M.meshStats(r.mesh);
ok(r.mesh.v.length / 3 === 7 && s.boundary === 0, 'merge 2 varfuri -> 7 varfuri, inchis (V=' + r.mesh.v.length / 3 + ', b=' + s.boundary + ')');
r = M.mergeVerts(c, M.vertsOfFaces(c, [top])); ok(r.mesh.v.length / 3 === 5, 'merge fata sus -> piramida 5 varfuri'); closed(r.mesh, 'piramida', 2);
// merge by distance
const dup = M.joinMeshes([c, M.translateVerts(c, M.allVerts(c), [1, 0, 0])]);
r = M.mergeByDistance(dup, 1e-3); ok(r.mesh.v.length / 3 === 12, 'merge by distance: 2 cuburi alaturate -> 12 varfuri (' + r.mesh.v.length / 3 + ')');
r = M.deleteFaces(c, [top]); s = M.meshStats(r.mesh); ok(r.mesh.f.length === 5 && s.boundary === 4, 'delete fata -> 5 fete, 4 muchii libere');
r = M.deleteVerts(c, [0]); ok(r.mesh.f.length === 3 && r.mesh.v.length / 3 === 7, 'delete varf -> 3 fete (' + r.mesh.f.length + ')');
r = M.deleteEdges(c, [e]); ok(r.mesh.f.length === 4, 'delete muchie -> 4 fete');
r = M.subdivideFaces(c, [top]); closed(r.mesh, 'subdivide', 2); ok(r.mesh.f.length === 6 - 1 + 4, 'subdivide 9 fete (' + r.mesh.f.length + ')');
r = M.subdivideFaces(c, M.allFaces(c)); closed(r.mesh, 'subdivide all', 2); ok(r.mesh.f.length === 24, 'subdivide all 24');
// fill: gaura
const hole = M.deleteFaces(c, [top]).mesh; const bnd = M.edgesOfFaces(hole, [0]);
const holeVerts = M.vertsOfFaces(hole, M.allFaces(hole)).filter(i => M.getV(hole, i)[1] > 0.4);
r = M.fillFace(hole, [holeVerts[0], holeVerts[1], holeVerts[3], holeVerts[2]].filter(x=>x!==undefined)); 
console.log('   (fill:', r ? M.meshStats(r.mesh).boundary + ' muchii libere ramase' : 'null', ')');
// flip
r = M.flipFaces(c, M.allFaces(c)); ok(M.signedVolume(r.mesh) < 0, 'flip inverseaza volumul');
// mirror
let half = M.deleteFaces(M.cubeMesh(), []); half = { v: M.translateVerts(c, M.allVerts(c), [0.5, 0, 0]).v, f: c.f };
r = M.mirrorX(half); s = M.meshStats(r.mesh); console.log('   mirror:', JSON.stringify(s));
// triangulare ngon concav
const L = { v: [0,0,0, 2,0,0, 2,1,0, 1,1,0, 1,2,0, 0,2,0], f: [[0,1,2,3,4,5]] };
const tri = M.triangulateFace(L, L.f[0]); ok(tri.length === 12, 'ear clipping L-shape -> 4 triunghiuri');
let area = 0; for (let i = 0; i < tri.length; i += 3) area += M.faceArea(L, [tri[i], tri[i+1], tri[i+2]]); ok(Math.abs(area - 3) < 1e-9, 'arie L=3 (' + area + ')');
// sanitize
ok(M.sanitizeMesh({ v: [0,0,0,1,0,0,0,1,0], f: [[0,1,2],[0,0,1],[0,1,9]] }).f.length === 1, 'sanitize pastreaza doar fete valide');
ok(M.sanitizeMesh({ v: [NaN,0,0], f: [[0,0,0]] }) === null, 'sanitize respinge NaN');
// scenariu: "sabie" din cub: extrude + scale + loopcut + bevel, ramane valid
let m = c; let sel = [top];
m = M.mapVerts(m, M.allVerts(m), p => [p[0] * 0.15, p[1] * 2.5, p[2] * 0.05]);
const tp = m.f.findIndex((_, i) => M.faceNormal(m, i)[1] > 0.9);
r = M.extrudeFaces(m, [tp], 0.4); m = M.mapVerts(r.mesh, r.selV, p => [p[0] * 0.2, p[1], p[2]]);
r = M.insetFaces(m, r.selF, 0.01); closed(r.mesh, 'scenariu sabie', 2);
const bb = M.bevelEdges(r.mesh, M.allEdgeKeys(r.mesh), 0.005, 2); closed(bb.mesh, 'scenariu sabie + bevel', 2);
console.log(fail ? `\n${fail} ESUATE` : '\nTOATE TESTELE AU TRECUT'); process.exit(fail ? 1 : 0);