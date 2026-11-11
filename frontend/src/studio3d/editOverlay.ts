// frontend/src/studio3d/editOverlay.ts
// Edit Mode in three.js: overlay-ul (varfuri, muchii, fete selectate) si selectarea prin atingere (picking).
// Overlay-ul e copil al obiectului editat, deci urmeaza automat pozitia/rotatia/scala lui.
import * as THREE from "three";
import { MeshData, getV, buildTopology, parseEdge, buildRenderBuffers, vertCount } from "./meshData";
import { EditSel, selVerts, selEdges } from "./editSelection";

const HILITE = 0xccff00;
const WIRE = 0x8a9099;

export type EditOverlay = {
  group: THREE.Group;
  update: (mesh: MeshData, sel: EditSel, xray: boolean) => void;
  dispose: () => void;
};

function disposeChildren(g: THREE.Group) {
  for (const c of [...g.children]) {
    g.remove(c);
    const o = c as THREE.Mesh;
    o.geometry?.dispose();
    (o.material as THREE.Material)?.dispose();
  }
}

export function createEditOverlay(): EditOverlay {
  const group = new THREE.Group();
  group.name = "edit-overlay";

  const update = (m: MeshData, sel: EditSel, xray: boolean) => {
    disposeChildren(group);
    const topo = buildTopology(m);

    // toate muchiile (sters/estompat)
    const wire: number[] = [];
    topo.forEach(e => { const a = getV(m, e.a), b = getV(m, e.b); wire.push(a[0], a[1], a[2], b[0], b[1], b[2]); });
    const wg = new THREE.BufferGeometry();
    wg.setAttribute("position", new THREE.Float32BufferAttribute(wire, 3));
    const wl = new THREE.LineSegments(wg, new THREE.LineBasicMaterial({ color: WIRE, transparent: true, opacity: 0.95, depthTest: !xray }));
    wl.renderOrder = 2;
    group.add(wl);

    // fete selectate (umplute translucid)
    if (sel.mode === "face" && sel.f.length > 0) {
      const sub = buildRenderBuffers({ v: m.v, f: sel.f.map(i => m.f[i]).filter(Boolean) });
      const fg = new THREE.BufferGeometry();
      fg.setAttribute("position", new THREE.BufferAttribute(sub.positions, 3));
      const fm = new THREE.Mesh(fg, new THREE.MeshBasicMaterial({
        color: HILITE, transparent: true, opacity: 0.38, side: THREE.DoubleSide, depthWrite: false, depthTest: !xray,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
      }));
      fm.renderOrder = 1;
      fm.raycast = () => {}; // overlay-ul nu intercepteaza atingerile
      group.add(fm);
    }

    // muchii selectate
    const se = selEdges(m, sel);
    if (se.length > 0 && sel.mode !== "vertex") {
      const pos: number[] = [];
      se.forEach(k => { const [a, b] = parseEdge(k); const A = getV(m, a), B = getV(m, b); pos.push(A[0], A[1], A[2], B[0], B[1], B[2]); });
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      const l = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: HILITE, depthTest: !xray }));
      l.renderOrder = 3;
      group.add(l);
    }

    // varfuri (doar in Vertex Select; in celelalte moduri doar cele atinse, ca sa nu aglomeram ecranul)
    const n = vertCount(m);
    const selSet = new Set(selVerts(m, sel));
    if (sel.mode === "vertex") {
      const pos: number[] = [];
      for (let i = 0; i < n; i++) if (!selSet.has(i)) pos.push(m.v[i * 3], m.v[i * 3 + 1], m.v[i * 3 + 2]);
      if (pos.length) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
        const p = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 9, sizeAttenuation: false, depthTest: !xray }));
        p.renderOrder = 4;
        group.add(p);
      }
    }
    if (selSet.size > 0) {
      const pos: number[] = [];
      selSet.forEach(i => { if (i < n) pos.push(m.v[i * 3], m.v[i * 3 + 1], m.v[i * 3 + 2]); });
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      const p = new THREE.Points(g, new THREE.PointsMaterial({ color: HILITE, size: 13, sizeAttenuation: false, depthTest: !xray }));
      p.renderOrder = 5;
      group.add(p);
    }
    // Points/Lines nu trebuie sa fie tinta de raycast pentru selectarea obiectelor
    group.traverse(o => { if ((o as any).isPoints || (o as any).isLine) o.raycast = () => {}; });
  };

  return { group, update, dispose: () => disposeChildren(group) };
}

// ---------- picking (atingere -> element) ----------
export type PickView = { camera: THREE.PerspectiveCamera; w: number; h: number };

const tmp = new THREE.Vector3();
function toScreen(p: THREE.Vector3, v: PickView): { x: number; y: number; ok: boolean } {
  tmp.copy(p).project(v.camera); // p e in spatiul lumii
  return { x: (tmp.x * 0.5 + 0.5) * v.w, y: (-tmp.y * 0.5 + 0.5) * v.h, ok: tmp.z > -1 && tmp.z < 1 };
}

// un punct de pe obiect e ascuns daca o alta suprafata il acopera din pozitia camerei
export function isOccluded(obj: THREE.Mesh, worldPoint: THREE.Vector3, camera: THREE.Camera): boolean {
  const o = camera.position;
  const dir = worldPoint.clone().sub(o);
  const dist = dir.length();
  if (dist < 1e-6) return false;
  const rc = new THREE.Raycaster(o.clone(), dir.normalize(), 0, dist);
  const hits = rc.intersectObject(obj, false);
  if (hits.length === 0) return false;
  return hits[0].distance < dist - (0.015 + dist * 0.004);
}

export function pickVertex(obj: THREE.Mesh, m: MeshData, view: PickView, x: number, y: number, radius: number, xray: boolean): number | null {
  obj.updateWorldMatrix(true, false);
  view.camera.updateMatrixWorld();
  const cands: { i: number; d: number; wp: THREE.Vector3 }[] = [];
  const n = vertCount(m);
  for (let i = 0; i < n; i++) {
    const wp = new THREE.Vector3(m.v[i * 3], m.v[i * 3 + 1], m.v[i * 3 + 2]).applyMatrix4(obj.matrixWorld);
    const s = toScreen(wp, view);
    if (!s.ok) continue;
    const d = Math.hypot(s.x - x, s.y - y);
    if (d <= radius) cands.push({ i, d, wp });
  }
  cands.sort((a, b) => a.d - b.d);
  for (const c of cands.slice(0, 8)) if (xray || !isOccluded(obj, c.wp, view.camera)) return c.i;
  return null;
}

export function pickEdge(obj: THREE.Mesh, m: MeshData, view: PickView, x: number, y: number, radius: number, xray: boolean): string | null {
  obj.updateWorldMatrix(true, false);
  view.camera.updateMatrixWorld();
  const topo = buildTopology(m);
  const cands: { k: string; d: number; mid: THREE.Vector3 }[] = [];
  topo.forEach((e, k) => {
    const A = new THREE.Vector3(...getV(m, e.a)).applyMatrix4(obj.matrixWorld);
    const B = new THREE.Vector3(...getV(m, e.b)).applyMatrix4(obj.matrixWorld);
    const sa = toScreen(A, view), sb = toScreen(B, view);
    if (!sa.ok && !sb.ok) return;
    const dx = sb.x - sa.x, dy = sb.y - sa.y;
    const L2 = dx * dx + dy * dy;
    const t = L2 < 1e-9 ? 0 : Math.max(0, Math.min(1, ((x - sa.x) * dx + (y - sa.y) * dy) / L2));
    const d = Math.hypot(sa.x + dx * t - x, sa.y + dy * t - y);
    if (d <= radius) cands.push({ k, d, mid: A.clone().lerp(B, Math.max(0.05, Math.min(0.95, t))) });
  });
  cands.sort((a, b) => a.d - b.d);
  for (const c of cands.slice(0, 8)) if (xray || !isOccluded(obj, c.mid, view.camera)) return c.k;
  return null;
}

// fata atinsa (cea mai apropiata de camera)
export function pickFace(obj: THREE.Mesh, rc: THREE.Raycaster): number | null {
  const hits = rc.intersectObject(obj, false);
  if (hits.length === 0) return null;
  const map = obj.geometry.userData.faceOfTri as Int32Array | undefined;
  const ti = hits[0].faceIndex;
  if (!map || ti === undefined || ti === null || ti >= map.length) return null;
  return map[ti];
}