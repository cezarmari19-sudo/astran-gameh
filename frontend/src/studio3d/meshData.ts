// frontend/src/studio3d/meshData.ts
// Mesh poligonal EDITABIL (geometrie reala): varfuri + fete (poligoane CCW, privite din exterior).
// Fisier pur (fara three.js / React), ca sa poata fi testat direct si folosit la fel in Studio si in jocuri.
// Fiecare operatie intoarce un mesh NOU (nu modifica intrarea), deci Undo/Redo merge direct pe lista de piese.

export type MeshData = { v: number[]; f: number[][] }; // v = [x,y,z, x,y,z, ...]; f = liste de indici de varfuri
export type Vec = [number, number, number];
export type OpResult = { mesh: MeshData; selV: number[]; selF: number[] };

export const MESH_LIMITS = { maxVerts: 6000, maxFaces: 6000, maxFaceVerts: 64, maxCoord: 1000 };

// ---------- vectori ----------
const add = (a: Vec, b: Vec): Vec => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: Vec, b: Vec): Vec => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: Vec, s: number): Vec => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec, b: Vec): Vec => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const vlen = (a: Vec) => Math.sqrt(dot(a, a));
const norm = (a: Vec, fallback: Vec = [0, 1, 0]): Vec => {
  const l = vlen(a);
  return l < 1e-12 ? fallback : [a[0] / l, a[1] / l, a[2] / l];
};
const lerp = (a: Vec, b: Vec, t: number): Vec => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const r4 = (x: number) => Math.round(x * 10000) / 10000;

export const getV = (m: MeshData, i: number): Vec => [m.v[i * 3], m.v[i * 3 + 1], m.v[i * 3 + 2]];
export const vertCount = (m: MeshData) => m.v.length / 3;
export const cloneMesh = (m: MeshData): MeshData => ({ v: m.v.slice(), f: m.f.map(f => f.slice()) });

// ---------- topologie ----------
export const edgeKey = (a: number, b: number) => (a < b ? `${a}_${b}` : `${b}_${a}`);
export function parseEdge(k: string): [number, number] {
  const i = k.indexOf("_");
  return [parseInt(k.slice(0, i), 10), parseInt(k.slice(i + 1), 10)];
}

export type EdgeInfo = { a: number; b: number; faces: { f: number; fwd: boolean }[] }; // a<b; fwd = fata parcurge a->b
export function buildTopology(m: MeshData): Map<string, EdgeInfo> {
  const edges = new Map<string, EdgeInfo>();
  m.f.forEach((face, fi) => {
    for (let j = 0; j < face.length; j++) {
      const p = face[j], q = face[(j + 1) % face.length];
      if (p === q) continue;
      const k = edgeKey(p, q);
      let e = edges.get(k);
      if (!e) { e = { a: Math.min(p, q), b: Math.max(p, q), faces: [] }; edges.set(k, e); }
      e.faces.push({ f: fi, fwd: p < q });
    }
  });
  return edges;
}

export function faceNormalV(m: MeshData, face: number[]): Vec {
  // Newell: robust si pentru poligoane neplane
  let nx = 0, ny = 0, nz = 0;
  for (let i = 0; i < face.length; i++) {
    const a = getV(m, face[i]), b = getV(m, face[(i + 1) % face.length]);
    nx += (a[1] - b[1]) * (a[2] + b[2]);
    ny += (a[2] - b[2]) * (a[0] + b[0]);
    nz += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return norm([nx, ny, nz]);
}
export const faceNormal = (m: MeshData, fi: number) => faceNormalV(m, m.f[fi]);

export function faceCenter(m: MeshData, fi: number): Vec {
  const f = m.f[fi];
  let c: Vec = [0, 0, 0];
  f.forEach(i => { c = add(c, getV(m, i)); });
  return mul(c, 1 / f.length);
}

export function faceArea(m: MeshData, face: number[]): number {
  const o = getV(m, face[0]);
  let s: Vec = [0, 0, 0];
  for (let i = 1; i < face.length - 1; i++) s = add(s, cross(sub(getV(m, face[i]), o), sub(getV(m, face[i + 1]), o)));
  return vlen(s) / 2;
}

export function signedVolume(m: MeshData): number {
  let vol = 0;
  m.f.forEach(face => {
    const t = triangulateFace(m, face);
    for (let i = 0; i < t.length; i += 3) {
      vol += dot(getV(m, t[i]), cross(getV(m, t[i + 1]), getV(m, t[i + 2]))) / 6;
    }
  });
  return vol;
}

// ---------- triangulare (ear clipping) ----------
export function triangulateFace(m: MeshData, face: number[]): number[] {
  const n = face.length;
  if (n < 3) return [];
  if (n === 3) return [face[0], face[1], face[2]];
  const nrm = faceNormalV(m, face);
  const ax = Math.abs(nrm[0]), ay = Math.abs(nrm[1]), az = Math.abs(nrm[2]);
  const drop = ax >= ay && ax >= az ? 0 : ay >= az ? 1 : 2;
  const p2 = (i: number): [number, number] => {
    const p = getV(m, i);
    return drop === 0 ? [p[1], p[2]] : drop === 1 ? [p[2], p[0]] : [p[0], p[1]];
  };
  const sgn = (drop === 0 ? nrm[0] : drop === 1 ? nrm[1] : nrm[2]) >= 0 ? 1 : -1;
  const cr = (a: [number, number], b: [number, number], c: [number, number]) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const idx = face.slice();
  const out: number[] = [];
  let guard = idx.length * idx.length + 10;
  while (idx.length > 3 && guard-- > 0) {
    let clipped = false;
    for (let i = 0; i < idx.length; i++) {
      const ia = idx[(i + idx.length - 1) % idx.length], ib = idx[i], ic = idx[(i + 1) % idx.length];
      const a = p2(ia), b = p2(ib), c = p2(ic);
      if (cr(a, b, c) * sgn <= 1e-12) continue; // coltul nu e convex
      let inside = false;
      for (const j of idx) {
        if (j === ia || j === ib || j === ic) continue;
        const p = p2(j);
        if (cr(a, b, p) * sgn >= -1e-12 && cr(b, c, p) * sgn >= -1e-12 && cr(c, a, p) * sgn >= -1e-12) { inside = true; break; }
      }
      if (inside) continue;
      out.push(ia, ib, ic);
      idx.splice(i, 1);
      clipped = true;
      break;
    }
    if (!clipped) { // poligon degenerat: evantai
      for (let i = 1; i < idx.length - 1; i++) out.push(idx[0], idx[i], idx[i + 1]);
      return out;
    }
  }
  if (idx.length === 3) out.push(idx[0], idx[1], idx[2]);
  return out;
}

// Buffere pentru randare: shading plat (normala pe fata), neindexat. faceOfTri[t] = fata din care vine triunghiul t.
export function buildRenderBuffers(m: MeshData): { positions: Float32Array; normals: Float32Array; faceOfTri: Int32Array } {
  const tris: number[] = [];
  const owner: number[] = [];
  m.f.forEach((face, fi) => {
    const t = triangulateFace(m, face);
    for (let i = 0; i < t.length; i += 3) { tris.push(t[i], t[i + 1], t[i + 2]); owner.push(fi); }
  });
  const positions = new Float32Array(tris.length * 3);
  const normals = new Float32Array(tris.length * 3);
  for (let t = 0; t < tris.length; t += 3) {
    const a = getV(m, tris[t]), b = getV(m, tris[t + 1]), c = getV(m, tris[t + 2]);
    const n = norm(cross(sub(b, a), sub(c, a)));
    [a, b, c].forEach((p, k) => {
      const o = (t + k) * 3;
      positions[o] = p[0]; positions[o + 1] = p[1]; positions[o + 2] = p[2];
      normals[o] = n[0]; normals[o + 1] = n[1]; normals[o + 2] = n[2];
    });
  }
  return { positions, normals, faceOfTri: Int32Array.from(owner) };
}

// ---------- curatare / validare ----------
// Scoate varfurile nefolosite si fetele degenerate; reindexeaza. map[old] = new (sau -1).
export function compact(m: MeshData): { mesh: MeshData; map: number[] } {
  const faces = m.f.filter(f => f.length >= 3 && new Set(f).size === f.length);
  const map = new Array<number>(vertCount(m)).fill(-1);
  const v: number[] = [];
  const f = faces.map(face => face.map(i => {
    if (map[i] < 0) { map[i] = v.length / 3; v.push(m.v[i * 3], m.v[i * 3 + 1], m.v[i * 3 + 2]); }
    return map[i];
  }));
  return { mesh: { v, f }, map };
}

export function roundMesh(m: MeshData): MeshData {
  return { v: m.v.map(r4), f: m.f };
}

// Date venite de la server / fisiere vechi: intoarce un mesh valid sau null
export function sanitizeMesh(raw: any): MeshData | null {
  if (!raw || !Array.isArray(raw.v) || !Array.isArray(raw.f)) return null;
  const nv = Math.floor(raw.v.length / 3);
  if (nv === 0 || nv > MESH_LIMITS.maxVerts || raw.f.length > MESH_LIMITS.maxFaces) return null;
  const v: number[] = [];
  for (let i = 0; i < nv * 3; i++) {
    const x = raw.v[i];
    if (typeof x !== "number" || !isFinite(x)) return null;
    v.push(Math.max(-MESH_LIMITS.maxCoord, Math.min(MESH_LIMITS.maxCoord, x)));
  }
  const f: number[][] = [];
  for (const face of raw.f) {
    if (!Array.isArray(face) || face.length < 3 || face.length > MESH_LIMITS.maxFaceVerts) continue;
    if (!face.every((i: any) => Number.isInteger(i) && i >= 0 && i < nv)) continue;
    if (new Set(face).size !== face.length) continue;
    f.push(face.slice());
  }
  if (f.length === 0) return null;
  return compact({ v, f }).mesh;
}

export function meshWithinLimits(m: MeshData): boolean {
  return vertCount(m) <= MESH_LIMITS.maxVerts && m.f.length <= MESH_LIMITS.maxFaces;
}

// ---------- primitive ----------
function orientOutward(m: MeshData): MeshData {
  return signedVolume(m) < 0 ? { v: m.v, f: m.f.map(f => f.slice().reverse()) } : m;
}

export function cubeMesh(): MeshData {
  const h = 0.5;
  const v = [-h, -h, -h, h, -h, -h, h, h, -h, -h, h, -h, -h, -h, h, h, -h, h, h, h, h, -h, h, h];
  const f = [[4, 5, 6, 7], [1, 0, 3, 2], [5, 1, 2, 6], [0, 4, 7, 3], [7, 6, 2, 3], [0, 1, 5, 4]];
  return orientOutward({ v, f });
}

export function planeMesh(): MeshData {
  return { v: [-0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 0, 0.5, -0.5, 0, 0.5], f: [[3, 2, 1, 0]] }; // normala +Y
}

export function cylinderMesh(seg = 16, rTop = 0.5, rBot = 0.5): MeshData {
  const v: number[] = [];
  for (let i = 0; i < seg; i++) { const a = (i / seg) * Math.PI * 2; v.push(Math.cos(a) * rBot, -0.5, Math.sin(a) * rBot); }
  for (let i = 0; i < seg; i++) { const a = (i / seg) * Math.PI * 2; v.push(Math.cos(a) * rTop, 0.5, Math.sin(a) * rTop); }
  const f: number[][] = [];
  for (let i = 0; i < seg; i++) { const j = (i + 1) % seg; f.push([i, j, seg + j, seg + i]); }
  f.push(Array.from({ length: seg }, (_, i) => seg + i));
  f.push(Array.from({ length: seg }, (_, i) => seg - 1 - i));
  return orientOutward({ v, f });
}

export function coneMesh(seg = 16): MeshData {
  const v: number[] = [];
  for (let i = 0; i < seg; i++) { const a = (i / seg) * Math.PI * 2; v.push(Math.cos(a) * 0.5, -0.5, Math.sin(a) * 0.5); }
  v.push(0, 0.5, 0);
  const f: number[][] = [];
  for (let i = 0; i < seg; i++) f.push([i, (i + 1) % seg, seg]);
  f.push(Array.from({ length: seg }, (_, i) => seg - 1 - i));
  return orientOutward({ v, f });
}

export function sphereMesh(seg = 16, rings = 10): MeshData {
  const v: number[] = [0, 0.5, 0];
  for (let r = 1; r < rings; r++) {
    const phi = (r / rings) * Math.PI;
    for (let i = 0; i < seg; i++) {
      const a = (i / seg) * Math.PI * 2;
      v.push(Math.cos(a) * Math.sin(phi) * 0.5, Math.cos(phi) * 0.5, Math.sin(a) * Math.sin(phi) * 0.5);
    }
  }
  v.push(0, -0.5, 0);
  const bottom = v.length / 3 - 1;
  const ring = (r: number, i: number) => 1 + (r - 1) * seg + (i % seg);
  const f: number[][] = [];
  for (let i = 0; i < seg; i++) f.push([0, ring(1, i + 1), ring(1, i)]);
  for (let r = 1; r < rings - 1; r++) for (let i = 0; i < seg; i++) f.push([ring(r, i), ring(r, i + 1), ring(r + 1, i + 1), ring(r + 1, i)]);
  for (let i = 0; i < seg; i++) f.push([bottom, ring(rings - 1, i), ring(rings - 1, i + 1)]);
  return orientOutward({ v, f });
}

export function torusMesh(seg = 20, tube = 10, R = 0.4, r = 0.1): MeshData {
  const v: number[] = [];
  for (let i = 0; i < seg; i++) {
    const a = (i / seg) * Math.PI * 2;
    for (let j = 0; j < tube; j++) {
      const b = (j / tube) * Math.PI * 2;
      const rad = R + r * Math.cos(b);
      v.push(Math.cos(a) * rad, Math.sin(a) * rad, r * Math.sin(b));
    }
  }
  const f: number[][] = [];
  for (let i = 0; i < seg; i++) for (let j = 0; j < tube; j++) {
    const i2 = (i + 1) % seg, j2 = (j + 1) % tube;
    f.push([i * tube + j, i2 * tube + j, i2 * tube + j2, i * tube + j2]);
  }
  return orientOutward({ v, f });
}

export function primitiveMesh(type: string): MeshData {
  switch (type) {
    case "sphere": return sphereMesh();
    case "cylinder": return cylinderMesh();
    case "cone": return coneMesh();
    case "plane": return planeMesh();
    case "torus": return torusMesh();
    default: return cubeMesh();
  }
}

// ---------- selectie: conversii ----------
export const allVerts = (m: MeshData) => Array.from({ length: vertCount(m) }, (_, i) => i);
export const allFaces = (m: MeshData) => m.f.map((_, i) => i);
export const allEdgeKeys = (m: MeshData) => Array.from(buildTopology(m).keys());

export function vertsOfEdges(keys: string[]): number[] {
  const s = new Set<number>();
  keys.forEach(k => { const [a, b] = parseEdge(k); s.add(a); s.add(b); });
  return Array.from(s);
}
export function vertsOfFaces(m: MeshData, faces: number[]): number[] {
  const s = new Set<number>();
  faces.forEach(f => m.f[f]?.forEach(i => s.add(i)));
  return Array.from(s);
}
export function edgesOfFaces(m: MeshData, faces: number[]): string[] {
  const s = new Set<string>();
  faces.forEach(fi => {
    const face = m.f[fi];
    if (!face) return;
    for (let j = 0; j < face.length; j++) s.add(edgeKey(face[j], face[(j + 1) % face.length]));
  });
  return Array.from(s);
}
export function edgesOfVerts(m: MeshData, verts: number[]): string[] {
  const set = new Set(verts);
  return allEdgeKeys(m).filter(k => { const [a, b] = parseEdge(k); return set.has(a) && set.has(b); });
}
export function facesOfVerts(m: MeshData, verts: number[]): number[] {
  const set = new Set(verts);
  return allFaces(m).filter(fi => m.f[fi].every(i => set.has(i)));
}
export function facesOfEdges(m: MeshData, keys: string[]): number[] {
  const set = new Set(keys);
  return allFaces(m).filter(fi => edgesOfFaces(m, [fi]).every(k => set.has(k)));
}

export function centroid(m: MeshData, verts: number[]): Vec {
  if (verts.length === 0) return [0, 0, 0];
  let c: Vec = [0, 0, 0];
  verts.forEach(i => { c = add(c, getV(m, i)); });
  return mul(c, 1 / verts.length);
}

// ---------- transformari ----------
export function mapVerts(m: MeshData, verts: number[], fn: (p: Vec, i: number) => Vec): MeshData {
  const v = m.v.slice();
  verts.forEach(i => {
    if (i < 0 || i * 3 + 2 >= v.length) return;
    const p = fn(getV(m, i), i);
    v[i * 3] = r4(p[0]); v[i * 3 + 1] = r4(p[1]); v[i * 3 + 2] = r4(p[2]);
  });
  return { v, f: m.f };
}

export function translateVerts(m: MeshData, verts: number[], d: Vec): MeshData {
  return mapVerts(m, verts, p => add(p, d));
}

// ---------- unelte interne ----------
class Builder {
  v: number[];
  f: number[][];
  constructor(m: MeshData) { this.v = m.v.slice(); this.f = m.f.map(x => x.slice()); }
  addV(p: Vec): number { this.v.push(p[0], p[1], p[2]); return this.v.length / 3 - 1; }
  pos(i: number): Vec { return [this.v[i * 3], this.v[i * 3 + 1], this.v[i * 3 + 2]]; }
  setPos(i: number, p: Vec) { this.v[i * 3] = p[0]; this.v[i * 3 + 1] = p[1]; this.v[i * 3 + 2] = p[2]; }
  mesh(): MeshData { return { v: this.v, f: this.f }; }
}

// finalizeaza: curata, rotunjeste si recalculeaza selectia (indici vechi -> noi)
function finish(b: Builder, selV: number[], selF: number[]): OpResult {
  const keepFace = b.f.map(f => f.length >= 3 && new Set(f).size === f.length);
  const faces: number[][] = [];
  const faceMap = new Map<number, number>();
  b.f.forEach((f, i) => { if (keepFace[i]) { faceMap.set(i, faces.length); faces.push(f); } });
  const { mesh, map } = compact({ v: b.v, f: faces });
  return {
    mesh: roundMesh(mesh),
    selV: Array.from(new Set(selV.map(i => map[i]).filter(i => i >= 0))),
    selF: selF.map(i => faceMap.get(i)).filter((i): i is number => i !== undefined),
  };
}

// ---------- EXTRUDE ----------
export function extrudeFaces(m: MeshData, faces: number[], dist: number): OpResult {
  const sel = Array.from(new Set(faces.filter(f => f >= 0 && f < m.f.length)));
  if (sel.length === 0) return { mesh: m, selV: [], selF: [] };
  const b = new Builder(m);
  const selSet = new Set(sel);
  const dirCount = new Map<string, number>();
  const directed: [number, number][] = [];
  sel.forEach(fi => {
    const face = m.f[fi];
    for (let j = 0; j < face.length; j++) {
      const p = face[j], q = face[(j + 1) % face.length];
      directed.push([p, q]);
      const k = edgeKey(p, q);
      dirCount.set(k, (dirCount.get(k) ?? 0) + 1);
    }
  });
  const boundary = directed.filter(([p, q]) => dirCount.get(edgeKey(p, q)) === 1);

  // componente conectate (prin varfuri comune): fiecare se muta pe normala ei medie
  const parent = new Map<number, number>();
  const find = (x: number): number => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x)!)!); x = parent.get(x)!; } return x; };
  sel.forEach(fi => m.f[fi].forEach(i => { if (!parent.has(i)) parent.set(i, i); }));
  sel.forEach(fi => { const f = m.f[fi]; for (let j = 1; j < f.length; j++) parent.set(find(f[j]), find(f[0])); });
  const compNormal = new Map<number, Vec>();
  sel.forEach(fi => {
    const root = find(m.f[fi][0]);
    const n = mul(faceNormal(m, fi), faceArea(m, m.f[fi]) + 1e-9);
    compNormal.set(root, add(compNormal.get(root) ?? [0, 0, 0], n));
  });

  const newOf = new Map<number, number>();
  parent.forEach((_, i) => {
    const n = norm(compNormal.get(find(i)) ?? [0, 1, 0]);
    newOf.set(i, b.addV(add(b.pos(i), mul(n, dist))));
  });
  sel.forEach(fi => { b.f[fi] = m.f[fi].map(i => newOf.get(i)!); });
  const sideStart = b.f.length;
  boundary.forEach(([p, q]) => { b.f.push([p, q, newOf.get(q)!, newOf.get(p)!]); });
  void selSet; void sideStart;
  return finish(b, Array.from(newOf.values()), sel);
}

export function extrudeEdges(m: MeshData, keys: string[], dist: number): OpResult {
  const topo = buildTopology(m);
  const list = keys.filter(k => topo.has(k));
  if (list.length === 0) return { mesh: m, selV: [], selF: [] };
  const b = new Builder(m);
  const vdir = new Map<number, Vec>();
  const edgeDir = new Map<string, Vec>();
  list.forEach(k => {
    const e = topo.get(k)!;
    let n: Vec = [0, 0, 0];
    e.faces.forEach(x => { n = add(n, faceNormal(m, x.f)); });
    const d = norm(n);
    edgeDir.set(k, d);
    [e.a, e.b].forEach(i => vdir.set(i, add(vdir.get(i) ?? [0, 0, 0], d)));
  });
  const newOf = new Map<number, number>();
  vdir.forEach((d, i) => newOf.set(i, b.addV(add(b.pos(i), mul(norm(d), dist)))));
  const selF: number[] = [];
  list.forEach(k => {
    const e = topo.get(k)!;
    const first = e.faces[0];
    const a = e.a, c = e.b;
    // fata existenta parcurge a->c (fwd) => cadranul nou trebuie sa le parcurga invers
    const quad = first && first.fwd ? [c, a, newOf.get(a)!, newOf.get(c)!] : [a, c, newOf.get(c)!, newOf.get(a)!];
    selF.push(b.f.length);
    b.f.push(quad);
  });
  return finish(b, Array.from(newOf.values()), selF);
}

// ---------- INSET ----------
function insetRegion(m: MeshData, sel: number[], amount: number, depth: number): OpResult {
  const b = new Builder(m);
  const cnt = new Map<string, number>();
  const directed: [number, number, number][] = []; // p, q, fata
  sel.forEach(fi => {
    const face = m.f[fi];
    for (let j = 0; j < face.length; j++) {
      const p = face[j], q = face[(j + 1) % face.length];
      directed.push([p, q, fi]);
      cnt.set(edgeKey(p, q), (cnt.get(edgeKey(p, q)) ?? 0) + 1);
    }
  });
  const boundary = directed.filter(([p, q]) => cnt.get(edgeKey(p, q)) === 1);
  if (boundary.length === 0) return { mesh: m, selV: [], selF: [] };

  // limita: nu lasam inset-ul sa inverseze fata (aprox. raza cercului inscris)
  let maxAmt = Infinity;
  sel.forEach(fi => {
    const face = m.f[fi];
    let per = 0;
    for (let j = 0; j < face.length; j++) per += vlen(sub(getV(m, face[(j + 1) % face.length]), getV(m, face[j])));
    if (per > 1e-9) maxAmt = Math.min(maxAmt, (2 * faceArea(m, face)) / per);
  });
  const amt = Math.max(0, Math.min(amount, maxAmt * 0.98));

  const inEdges = new Map<number, [number, number][]>();
  const outEdges = new Map<number, [number, number][]>();
  const push = (mp: Map<number, [number, number][]>, k: number, v: [number, number]) => { if (!mp.has(k)) mp.set(k, []); mp.get(k)!.push(v); };
  boundary.forEach(([p, q, fi]) => { push(outEdges, p, [q, fi]); push(inEdges, q, [p, fi]); });

  const vn = new Map<number, Vec>();
  sel.forEach(fi => { const n = faceNormal(m, fi); m.f[fi].forEach(i => vn.set(i, add(vn.get(i) ?? [0, 0, 0], n))); });

  const bverts = new Set<number>();
  boundary.forEach(([p, q]) => { bverts.add(p); bverts.add(q); });
  const newOf = new Map<number, number>();
  bverts.forEach(v => {
    const pv = getV(m, v);
    const perps: Vec[] = [];
    (inEdges.get(v) ?? []).forEach(([x, fi]) => perps.push(norm(cross(faceNormal(m, fi), norm(sub(pv, getV(m, x)))))));
    (outEdges.get(v) ?? []).forEach(([y, fi]) => perps.push(norm(cross(faceNormal(m, fi), norm(sub(getV(m, y), pv))))));
    let off: Vec;
    if (perps.length === 2) {
      const d = 1 + dot(perps[0], perps[1]);
      off = mul(add(perps[0], perps[1]), amt / Math.max(0.25, d));
    } else {
      let s: Vec = [0, 0, 0];
      perps.forEach(p => { s = add(s, p); });
      off = mul(norm(s), amt);
    }
    const n = norm(vn.get(v) ?? [0, 1, 0]);
    newOf.set(v, b.addV(add(add(pv, off), mul(n, depth))));
  });
  sel.forEach(fi => { b.f[fi] = m.f[fi].map(i => newOf.get(i) ?? i); });
  boundary.forEach(([p, q]) => { b.f.push([p, q, newOf.get(q)!, newOf.get(p)!]); });
  return finish(b, Array.from(newOf.values()), sel);
}

export function insetFaces(m: MeshData, faces: number[], amount: number, depth = 0, individual = false): OpResult {
  const sel = Array.from(new Set(faces.filter(f => f >= 0 && f < m.f.length)));
  if (sel.length === 0) return { mesh: m, selV: [], selF: [] };
  if (!individual || sel.length === 1) return insetRegion(m, sel, amount, depth);
  // individual: fiecare fata separat (indicii fetelor raman stabili, pentru ca operatia le inlocuieste pe loc)
  let cur = m;
  const selV: number[] = [];
  let ok = true;
  for (const fi of sel) {
    const r = insetRegion(cur, [fi], amount, depth);
    if (r.selF.length === 0) { ok = false; continue; }
    cur = r.mesh;
  }
  void selV;
  if (!ok && cur === m) return { mesh: m, selV: [], selF: [] };
  return { mesh: cur, selV: [], selF: sel.map(fi => fi) }; // fetele selectate pastreaza indicii (compact pastreaza ordinea)
}

// ---------- BEVEL ----------
const PATCH_PULL = 0.5;

export function bevelEdges(m: MeshData, keys: string[], width: number, segments: number): OpResult | null {
  const seg = Math.max(1, Math.min(12, Math.round(segments)));
  const topo = buildTopology(m);
  type Sel = { a: number; b: number; F1: number; F2: number; key: string };
  const sels: Sel[] = [];
  Array.from(new Set(keys)).forEach(k => {
    const e = topo.get(k);
    if (!e || e.faces.length !== 2 || e.faces[0].fwd === e.faces[1].fwd) return; // doar muchii interioare, manifold
    const f1 = e.faces.find(x => x.fwd)!, f2 = e.faces.find(x => !x.fwd)!;
    if (f1.f === f2.f) return;
    sels.push({ a: e.a, b: e.b, F1: f1.f, F2: f2.f, key: k });
  });
  if (sels.length === 0 || width <= 1e-6) return null;
  const selSet = new Set(sels.map(s => s.key));
  const touched = new Set<number>();
  sels.forEach(s => { touched.add(s.a); touched.add(s.b); });

  // colturile fetelor din jurul varfurilor atinse
  type Corner = { f: number; j: number; v: number; prev: number; next: number; selIn: boolean; selOut: boolean };
  const cornersOf = new Map<number, Corner[]>();
  m.f.forEach((face, fi) => {
    for (let j = 0; j < face.length; j++) {
      const v = face[j];
      if (!touched.has(v)) continue;
      const prev = face[(j + face.length - 1) % face.length], next = face[(j + 1) % face.length];
      const c: Corner = { f: fi, j, v, prev, next, selIn: selSet.has(edgeKey(prev, v)), selOut: selSet.has(edgeKey(v, next)) };
      if (!cornersOf.has(v)) cornersOf.set(v, []);
      cornersOf.get(v)!.push(c);
    }
  });

  // latimea efectiva: sa nu depaseasca muchiile vecine
  let maxRatio = 0;
  sels.forEach(s => {
    const L = vlen(sub(getV(m, s.b), getV(m, s.a)));
    if (L > 1e-9) maxRatio = Math.max(maxRatio, 1 / (0.45 * L));
  });
  cornersOf.forEach(list => list.forEach(c => {
    if (c.selIn === c.selOut) return;
    const pv = getV(m, c.v), p = sub(getV(m, c.prev), pv), n = sub(getV(m, c.next), pv);
    const sinT = Math.max(0.1, vlen(cross(norm(p), norm(n))));
    const other = c.selIn ? n : p;
    const L = vlen(other);
    if (L > 1e-9) maxRatio = Math.max(maxRatio, 1 / (sinT * 0.49 * L));
  }));
  const w = maxRatio > 0 ? Math.min(width, 1 / maxRatio) : width;

  const b = new Builder(m);
  const keyed = new Map<string, number>();
  const fnorm = m.f.map((_, i) => faceNormal(m, i));

  // slide-urile cerute pe muchiile nebiselate, la varfurile atinse
  const demand = new Map<string, number[]>();
  const nKey = (v: number, ek: string) => `${v}|${ek}`;
  cornersOf.forEach(list => list.forEach(c => {
    if (c.selIn !== c.selOut) {
      const pv = getV(m, c.v), p = norm(sub(getV(m, c.prev), pv)), n = norm(sub(getV(m, c.next), pv));
      const sinT = Math.max(0.1, vlen(cross(p, n)));
      const ek = c.selIn ? edgeKey(c.v, c.next) : edgeKey(c.prev, c.v);
      const kk = nKey(c.v, ek);
      if (!demand.has(kk)) demand.set(kk, []);
      demand.get(kk)!.push(w / sinT);
    }
  }));
  const Npt = (v: number, other: number): number => {
    const ek = edgeKey(v, other);
    const kk = nKey(v, ek);
    const d = demand.get(kk);
    if (!d || d.length === 0) return v;
    const id = "N" + kk;
    if (keyed.has(id)) return keyed.get(id)!;
    const slide = d.reduce((x, y) => x + y, 0) / d.length;
    const pv = getV(m, v);
    const idx = b.addV(add(pv, mul(norm(sub(getV(m, other), pv)), slide)));
    keyed.set(id, idx);
    return idx;
  };
  const Mpt = (c: Corner): number => {
    const id = `M${c.f}|${c.v}`;
    if (keyed.has(id)) return keyed.get(id)!;
    const pv = getV(m, c.v), n = fnorm[c.f];
    const u1 = norm(cross(n, norm(sub(pv, getV(m, c.prev))))); // spre interiorul fetei, fata de muchia de intrare
    const u2 = norm(cross(n, norm(sub(getV(m, c.next), pv))));
    const dd = Math.max(0.25, 1 + dot(u1, u2));
    const idx = b.addV(add(pv, mul(add(u1, u2), w / dd)));
    keyed.set(id, idx);
    return idx;
  };
  const cornerAt = (f: number, v: number): Corner => cornersOf.get(v)!.find(c => c.f === f)!;
  // punctul fetei f langa muchia (v,other) la varful v
  const sidePoint = (f: number, v: number, other: number): number => {
    const c = cornerAt(f, v);
    const otherEdgeNeighbor = c.prev === other ? c.next : c.prev;
    const otherSel = selSet.has(edgeKey(v, otherEdgeNeighbor));
    return otherSel ? Mpt(c) : Npt(v, otherEdgeNeighbor);
  };

  // profilul fiecarei muchii selectate, la fiecare capat: P[0] pe F1, P[seg] pe F2
  const profile = new Map<string, number[]>(); // `${key}|${v}`
  sels.forEach(s => {
    const t = norm(sub(getV(m, s.b), getV(m, s.a)));
    [s.a, s.b].forEach(v => {
      const other = v === s.a ? s.b : s.a;
      const A = sidePoint(s.F1, v, other), B = sidePoint(s.F2, v, other);
      const idxs: number[] = new Array(seg + 1);
      idxs[0] = A; idxs[seg] = B;
      if (seg > 1) {
        const pv = getV(m, v);
        const p1 = norm(cross(fnorm[s.F1], t));
        const p2 = norm(cross(t, fnorm[s.F2]));
        const A0 = add(pv, mul(p1, w)), B0 = add(pv, mul(p2, w));
        const sA = dot(sub(b.pos(A), A0), t), sB = dot(sub(b.pos(B), B0), t);
        const cosT = dot(p1, p2);
        for (let k = 1; k < seg; k++) {
          const u = k / seg;
          let q: Vec;
          if (cosT < -0.9995) q = lerp(A0, B0, u);
          else {
            const bis = norm(add(p1, p2));
            const cosA = Math.sqrt((1 + cosT) / 2);
            const C = add(pv, mul(bis, w / cosA));
            const R = w * Math.sqrt((1 - cosT) / (1 + cosT));
            const ua = sub(A0, C), ub = sub(B0, C);
            const phi = Math.acos(Math.max(-1, Math.min(1, dot(ua, ub) / Math.max(1e-12, R * R))));
            const sp = Math.sin(phi);
            q = sp < 1e-6 ? lerp(A0, B0, u)
              : add(C, add(mul(ua, Math.sin((1 - u) * phi) / sp), mul(ub, Math.sin(u * phi) / sp)));
          }
          q = add(q, mul(t, sA + (sB - sA) * u));
          idxs[k] = b.addV(q);
        }
      }
      profile.set(`${s.key}|${v}`, idxs);
    });
  });

  // fetele existente: coltul fiecarui varf atins e inlocuit
  const newFaces: number[][] = m.f.map((face, fi) => {
    const out: number[] = [];
    for (let j = 0; j < face.length; j++) {
      const v = face[j];
      if (!touched.has(v)) { out.push(v); continue; }
      const c = cornerAt(fi, v);
      if (c.selIn && c.selOut) out.push(Mpt(c));
      else if (c.selIn) out.push(Npt(v, c.next));
      else if (c.selOut) out.push(Npt(v, c.prev));
      else { out.push(Npt(v, c.prev)); out.push(Npt(v, c.next)); }
    }
    return out.filter((x, i) => x !== out[(i + 1) % out.length] || out.length <= 2);
  });

  const selV = new Set<number>();
  const selF: number[] = [];
  // benzile de bevel
  const stripFaces: number[][] = [];
  sels.forEach(s => {
    const Pa = profile.get(`${s.key}|${s.a}`)!, Pb = profile.get(`${s.key}|${s.b}`)!;
    for (let k = 0; k < seg; k++) stripFaces.push([Pb[k], Pa[k], Pa[k + 1], Pb[k + 1]]);
    Pa.forEach(i => selV.add(i)); Pb.forEach(i => selV.add(i));
  });

  // peticele din colturi (inelul de puncte din jurul varfului original), doar daca evantaiul se inchide
  const patches: { ring: number[]; v: number }[] = [];
  touched.forEach(v => {
    const list = cornersOf.get(v)!;
    const byFace = new Map<number, Corner>();
    list.forEach(c => byFace.set(c.f, c));
    // toate fetele din jurul varfului trebuie sa fie in lista (altfel nu e interior) -> parcurgem evantaiul
    const start = list[0];
    const ring: number[] = [];
    let cur = start;
    let closed = false;
    for (let guard = 0; guard < 128; guard++) {
      const face = newFacesCornerPoints(cur);
      face.forEach(p => ring.push(p));
      const ek = edgeKey(v, cur.next);
      const info = topo.get(ek);
      if (!info || info.faces.length !== 2) break;
      if (selSet.has(ek)) {
        const s = sels.find(x => x.key === ek)!;
        const P = profile.get(`${ek}|${v}`)!;
        // de la partea fetei curente spre cealalta: la a (F1 iese din a) crescator, la b descrescator
        if (v === s.a) for (let k = 1; k < seg; k++) ring.push(P[k]);
        else for (let k = seg - 1; k >= 1; k--) ring.push(P[k]);
      }
      const nf = info.faces.find(x => x.f !== cur.f)!;
      const nc = byFace.get(nf.f) ?? cornerAt(nf.f, v);
      if (!nc) break;
      cur = nc;
      if (cur.f === start.f) { closed = true; break; }
    }
    if (!closed) return;
    const uniq: number[] = [];
    ring.forEach(p => { if (uniq[uniq.length - 1] !== p) uniq.push(p); });
    while (uniq.length > 1 && uniq[0] === uniq[uniq.length - 1]) uniq.pop();
    if (new Set(uniq).size !== uniq.length || uniq.length < 3) return;
    patches.push({ ring: uniq.slice().reverse(), v });
  });
  function newFacesCornerPoints(c: Corner): number[] {
    if (c.selIn && c.selOut) return [Mpt(c)];
    if (c.selIn) return [Npt(c.v, c.next)];
    if (c.selOut) return [Npt(c.v, c.prev)];
    const a = Npt(c.v, c.prev), bb = Npt(c.v, c.next);
    return a === bb ? [a] : [a, bb];
  }

  b.f = newFaces;
  stripFaces.forEach(f => { selF.push(b.f.length); b.f.push(f); });
  patches.forEach(({ ring, v }) => {
    if (ring.length > 4 && seg > 1) {
      let c: Vec = [0, 0, 0];
      ring.forEach(i => { c = add(c, b.pos(i)); });
      c = mul(c, 1 / ring.length);
      c = lerp(c, getV(m, v), PATCH_PULL); // trage centrul spre coltul original: aspect rotunjit
      const ci = b.addV(c);
      ring.forEach((p, i) => b.f.push([ci, p, ring[(i + 1) % ring.length]]));
    } else b.f.push(ring);
  });
  const res = finish(b, Array.from(selV), selF);
  return res;
}

// ---------- LOOP CUT ----------
// Inelul de muchii paralele (prin cadrane) care incepe de la muchia data.
export function edgeRing(m: MeshData, start: string): string[] {
  const topo = buildTopology(m);
  if (!topo.has(start)) return [];
  const ring = new Set<string>([start]);
  const walk = (k0: string, face0: number) => {
    let k = k0, fi = face0;
    for (let guard = 0; guard < 10000; guard++) {
      const face = m.f[fi];
      if (!face || face.length !== 4) return;
      const [a, b] = parseEdge(k);
      const j = face.findIndex((p, i) => edgeKey(p, face[(i + 1) % 4]) === k);
      if (j < 0) return;
      void a; void b;
      const opp = edgeKey(face[(j + 2) % 4], face[(j + 3) % 4]);
      if (ring.has(opp)) return;
      ring.add(opp);
      const info = topo.get(opp)!;
      if (info.faces.length !== 2) return;
      const nf = info.faces.find(x => x.f !== fi);
      if (!nf) return;
      k = opp; fi = nf.f;
    }
  };
  const e = topo.get(start)!;
  e.faces.forEach(x => walk(start, x.f));
  return Array.from(ring);
}

export function loopCut(m: MeshData, keys: string[], cuts: number, slide = 0): OpResult | null {
  const n = Math.max(1, Math.min(32, Math.round(cuts)));
  const topo = buildTopology(m);
  const ringSet = new Set<string>();
  keys.forEach(k => edgeRing(m, k).forEach(x => ringSet.add(x)));
  if (ringSet.size === 0) return null;
  const b = new Builder(m);
  const s = Math.max(-0.9, Math.min(0.9, slide));
  const tAt = (i: number) => {
    const base = (i + 1) / (n + 1);
    // slide doar cand e o singura taietura (ca in Blender)
    return n === 1 ? 0.5 + s * 0.5 : base;
  };
  // varfuri noi pe muchii: parametru t de la varful mic la cel mare
  const cutVerts = new Map<string, number[]>();
  ringSet.forEach(k => {
    const [a, c] = parseEdge(k);
    const pa = getV(m, a), pc = getV(m, c);
    cutVerts.set(k, Array.from({ length: n }, (_, i) => b.addV(lerp(pa, pc, tAt(i)))));
  });
  // punctele noi pe muchia p->q, in ordinea parcurgerii p->q
  const along = (p: number, q: number): number[] => {
    const arr = cutVerts.get(edgeKey(p, q))!;
    return p < q ? arr : arr.slice().reverse();
  };
  const newFaces: number[][] = [];
  const created: number[] = [];
  m.f.forEach(face => {
    const L = face.length;
    const cutEdges: number[] = [];
    for (let j = 0; j < L; j++) if (ringSet.has(edgeKey(face[j], face[(j + 1) % L]))) cutEdges.push(j);
    if (cutEdges.length === 0) { newFaces.push(face); return; }
    if (L === 4 && cutEdges.length === 2 && (cutEdges[1] - cutEdges[0]) === 2) {
      const j = cutEdges[0];
      const a = face[j], bb = face[(j + 1) % 4], c = face[(j + 2) % 4], d = face[(j + 3) % 4];
      const ab = along(a, bb);          // pe a->b
      const dc = along(d, c);           // pe d->c, aceeasi directie ca a->b
      let pa = a, pd = d;
      for (let i = 0; i < n; i++) {
        newFaces.push([pa, ab[i], dc[i], pd]);
        pa = ab[i]; pd = dc[i];
      }
      newFaces.push([pa, bb, c, pd]);
      return;
    }
    // altfel: doar insereaza punctele pe muchiile taiate (fara crapaturi)
    const out: number[] = [];
    for (let j = 0; j < L; j++) {
      const p = face[j], q = face[(j + 1) % L];
      out.push(p);
      if (ringSet.has(edgeKey(p, q))) along(p, q).forEach(x => out.push(x));
    }
    newFaces.push(out);
  });
  void topo; void created;
  b.f = newFaces;
  const all: number[] = [];
  cutVerts.forEach(arr => arr.forEach(x => all.push(x)));
  return finish(b, all, []);
}

// ---------- MERGE ----------
export type MergeMode = "center" | "first" | "last";
export function mergeVerts(m: MeshData, verts: number[], mode: MergeMode = "center"): OpResult {
  const set = Array.from(new Set(verts.filter(i => i >= 0 && i < vertCount(m))));
  if (set.length < 2) return { mesh: m, selV: set, selF: [] };
  const b = new Builder(m);
  const target = mode === "last" ? set[set.length - 1] : set[0];
  const pos = mode === "center" ? centroid(m, set) : getV(m, target);
  b.setPos(target, pos);
  const map = new Map<number, number>();
  set.forEach(i => map.set(i, target));
  b.f = m.f.map(face => {
    const mapped = face.map(i => map.get(i) ?? i);
    return mapped.filter((x, i) => x !== mapped[(i + 1) % mapped.length]); // scoate dublurile consecutive
  });
  return finish(b, [target], []);
}

// "Merge by distance": lipeste varfurile mai apropiate de eps (optional doar dintre cele date)
export function mergeByDistance(m: MeshData, eps: number, only?: number[]): OpResult {
  const n = vertCount(m);
  const cand = only ? Array.from(new Set(only)) : allVerts(m);
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x: number): number => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  const cell = Math.max(eps, 1e-6);
  const grid = new Map<string, number[]>();
  const keyOf = (p: Vec) => `${Math.floor(p[0] / cell)},${Math.floor(p[1] / cell)},${Math.floor(p[2] / cell)}`;
  cand.forEach(i => {
    const p = getV(m, i);
    const cx = Math.floor(p[0] / cell), cy = Math.floor(p[1] / cell), cz = Math.floor(p[2] / cell);
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      const lst = grid.get(`${cx + dx},${cy + dy},${cz + dz}`);
      if (!lst) continue;
      for (const j of lst) if (vlen(sub(getV(m, j), p)) <= eps) parent[find(i)] = find(j);
    }
    const k = keyOf(p);
    if (!grid.has(k)) grid.set(k, []);
    grid.get(k)!.push(i);
  });
  const b = new Builder(m);
  b.f = m.f.map(face => {
    const mapped = face.map(i => find(i));
    return mapped.filter((x, i) => x !== mapped[(i + 1) % mapped.length]);
  });
  return finish(b, cand.map(i => find(i)), []);
}

// ---------- DELETE ----------
export function deleteVerts(m: MeshData, verts: number[]): OpResult {
  const set = new Set(verts);
  const b = new Builder(m);
  b.f = m.f.filter(f => !f.some(i => set.has(i)));
  return finish(b, [], []);
}
export function deleteEdges(m: MeshData, keys: string[]): OpResult {
  const set = new Set(keys);
  const b = new Builder(m);
  b.f = m.f.filter(face => !face.some((p, j) => set.has(edgeKey(p, face[(j + 1) % face.length]))));
  return finish(b, [], []);
}
export function deleteFaces(m: MeshData, faces: number[]): OpResult {
  const set = new Set(faces);
  const b = new Builder(m);
  b.f = m.f.filter((_, i) => !set.has(i));
  return finish(b, [], []);
}

// ---------- SUBDIVIDE ----------
export function subdivideFaces(m: MeshData, faces: number[]): OpResult {
  const sel = new Set(faces.filter(f => f >= 0 && f < m.f.length));
  if (sel.size === 0) return { mesh: m, selV: [], selF: [] };
  const b = new Builder(m);
  const mids = new Map<string, number>();
  const mid = (p: number, q: number) => {
    const k = edgeKey(p, q);
    if (!mids.has(k)) mids.set(k, b.addV(lerp(b.pos(p), b.pos(q), 0.5)));
    return mids.get(k)!;
  };
  sel.forEach(fi => { const f = m.f[fi]; for (let j = 0; j < f.length; j++) mid(f[j], f[(j + 1) % f.length]); });
  const out: number[][] = [];
  const selV: number[] = [];
  const selF: number[] = [];
  m.f.forEach((face, fi) => {
    const L = face.length;
    if (sel.has(fi)) {
      const c = b.addV(faceCenter(m, fi));
      selV.push(c);
      for (let j = 0; j < L; j++) {
        const v = face[j];
        const mNext = mid(v, face[(j + 1) % L]), mPrev = mid(face[(j + L - 1) % L], v);
        selF.push(out.length);
        out.push([v, mNext, c, mPrev]);
      }
    } else {
      const f2: number[] = [];
      for (let j = 0; j < L; j++) {
        const p = face[j], q = face[(j + 1) % L];
        f2.push(p);
        const k = edgeKey(p, q);
        if (mids.has(k)) f2.push(mids.get(k)!);
      }
      out.push(f2);
    }
  });
  mids.forEach(i => selV.push(i));
  b.f = out;
  return finish(b, selV, selF);
}

// ---------- FILL / FLIP / MIRROR ----------
export function fillFace(m: MeshData, ordered: number[]): OpResult | null {
  const verts = ordered.filter((x, i) => ordered.indexOf(x) === i && x >= 0 && x < vertCount(m));
  if (verts.length < 3) return null;
  const b = new Builder(m);
  let face = verts;
  const tmp: MeshData = { v: m.v, f: [face] };
  const n = faceNormalV(tmp, face);
  const c = centroid(m, verts);
  const mc = centroid(m, allVerts(m));
  if (dot(n, sub(c, mc)) < 0) face = face.slice().reverse();
  b.f.push(face);
  return finish(b, verts, [m.f.length]);
}

export function flipFaces(m: MeshData, faces: number[]): OpResult {
  const set = new Set(faces);
  return { mesh: { v: m.v, f: m.f.map((f, i) => (set.has(i) ? f.slice().reverse() : f)) }, selV: [], selF: faces };
}

export function mirrorX(m: MeshData): OpResult {
  const keep = m.f.filter((_, i) => faceCenter(m, i)[0] > -1e-5);
  if (keep.length === 0) return { mesh: m, selV: [], selF: [] };
  const base = compact({ v: m.v, f: keep }).mesh;
  const n = vertCount(base);
  const v = base.v.slice();
  for (let i = 0; i < n; i++) v.push(-base.v[i * 3], base.v[i * 3 + 1], base.v[i * 3 + 2]);
  const f = base.f.map(x => x.slice());
  base.f.forEach(face => f.push(face.map(i => i + n).reverse()));
  const merged = mergeByDistance({ v, f }, 1e-4);
  // fetele lipite spate-in-spate pe planul de oglindire (acelasi set de varfuri) dispar
  const seen = new Map<string, number>();
  merged.mesh.f.forEach(face => { const k = face.slice().sort((a, b) => a - b).join(","); seen.set(k, (seen.get(k) ?? 0) + 1); });
  const keepF = merged.mesh.f.filter(face => seen.get(face.slice().sort((a, b) => a - b).join(",")) === 1);
  return finish(new Builder({ v: merged.mesh.v, f: keepF }), merged.selV, []);
}

// ---------- Fuziune: alipeste mai multe meshuri (deja in acelasi spatiu) ----------
export function joinMeshes(list: MeshData[]): MeshData {
  const v: number[] = [];
  const f: number[][] = [];
  list.forEach(m => {
    const off = v.length / 3;
    m.v.forEach(x => v.push(x));
    m.f.forEach(face => f.push(face.map(i => i + off)));
  });
  return { v, f };
}

// ---------- verificari (folosite si in teste) ----------
export function meshStats(m: MeshData) {
  const topo = buildTopology(m);
  let boundary = 0, nonManifold = 0, inconsistent = 0;
  topo.forEach(e => {
    if (e.faces.length === 1) boundary++;
    else if (e.faces.length > 2) nonManifold++;
    else if (e.faces[0].fwd === e.faces[1].fwd) inconsistent++;
  });
  const V = vertCount(m), E = topo.size, F = m.f.length;
  return { V, E, F, euler: V - E + F, boundary, nonManifold, inconsistent, volume: signedVolume(m) };
}