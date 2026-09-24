// frontend/src/studio3d/gizmo.ts
// Gizmo-ul 3D (Move / Rotate / Scale) si matematica gesturilor de tras pe telefon.
import * as THREE from "three";
import { round3 } from "./modelTypes";

export type GizmoMode = "move" | "rotate" | "scale";
export type Axis = "x" | "y" | "z";
export type HandleInfo = { kind: "axis" | "center" | "ring"; axis?: Axis };

export type Gizmo = {
  root: THREE.Group;
  groups: Record<GizmoMode, THREE.Group>;
  hits: Record<GizmoMode, THREE.Mesh[]>; // zone invizibile, groase, pentru degete
  dispose: () => void;
};

export const UP = new THREE.Vector3(0, 1, 0);
const GIZMO_SCREEN_SCALE = 0.19; // marimea gizmo-ului pe ecran (proportionala cu distanta camerei)
const AXIS_COLOR: Record<Axis, number> = { x: 0xff5555, y: 0x55ee77, z: 0x5599ff };
const RAD = 180 / Math.PI;

export function axisVector(axis: Axis): THREE.Vector3 {
  return axis === "x" ? new THREE.Vector3(1, 0, 0) : axis === "y" ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(0, 0, 1);
}

// ---------- constructie ----------

function visibleMaterial(color: number) {
  return new THREE.MeshBasicMaterial({ color, depthTest: false, depthWrite: false, transparent: true, opacity: 0.95 });
}

// geometria se construieste pe +Y si se roteste spre axa dorita
function orientToAxis(axis: Axis, obj: THREE.Object3D) {
  if (axis === "x") obj.rotation.z = -Math.PI / 2;
  else if (axis === "z") obj.rotation.x = Math.PI / 2;
}

export function createGizmo(): Gizmo {
  const root = new THREE.Group();
  const groups: Record<GizmoMode, THREE.Group> = { move: new THREE.Group(), rotate: new THREE.Group(), scale: new THREE.Group() };
  const hits: Record<GizmoMode, THREE.Mesh[]> = { move: [], rotate: [], scale: [] };
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];

  const geo = <T extends THREE.BufferGeometry>(g: T): T => { geometries.push(g); return g; };
  const vis = (color: number) => { const m = visibleMaterial(color); materials.push(m); return m; };
  const hitMesh = (mode: GizmoMode, parent: THREE.Object3D, g: THREE.BufferGeometry, pos: THREE.Vector3, info: HandleInfo) => {
    const m = new THREE.Mesh(geo(g), (() => { const mm = new THREE.MeshBasicMaterial({ visible: false }); materials.push(mm); return mm; })());
    m.position.copy(pos);
    m.userData.handle = info;
    parent.add(m);
    hits[mode].push(m);
  };

  (["x", "y", "z"] as Axis[]).forEach(axis => {
    const color = AXIS_COLOR[axis];

    // MOVE: sageata
    const mv = new THREE.Group();
    orientToAxis(axis, mv);
    const shaft = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.035, 0.035, 0.7, 8)), vis(color));
    shaft.position.y = 0.5;
    const head = new THREE.Mesh(geo(new THREE.ConeGeometry(0.11, 0.3, 12)), vis(color));
    head.position.y = 1.0;
    mv.add(shaft, head);
    hitMesh("move", mv, new THREE.CylinderGeometry(0.34, 0.34, 0.95, 6), new THREE.Vector3(0, 0.775, 0), { kind: "axis", axis });
    groups.move.add(mv);

    // ROTATE: inel
    const rg = new THREE.Group();
    if (axis === "x") rg.rotation.y = Math.PI / 2;
    else if (axis === "y") rg.rotation.x = Math.PI / 2;
    rg.add(new THREE.Mesh(geo(new THREE.TorusGeometry(1, 0.03, 8, 64)), vis(color)));
    hitMesh("rotate", rg, new THREE.TorusGeometry(1, 0.3, 6, 40), new THREE.Vector3(0, 0, 0), { kind: "ring", axis });
    groups.rotate.add(rg);

    // SCALE: linie + cub
    const sg = new THREE.Group();
    orientToAxis(axis, sg);
    const line = new THREE.Mesh(geo(new THREE.CylinderGeometry(0.03, 0.03, 0.75, 8)), vis(color));
    line.position.y = 0.525;
    const cube = new THREE.Mesh(geo(new THREE.BoxGeometry(0.2, 0.2, 0.2)), vis(color));
    cube.position.y = 1.0;
    sg.add(line, cube);
    hitMesh("scale", sg, new THREE.CylinderGeometry(0.34, 0.34, 0.95, 6), new THREE.Vector3(0, 0.775, 0), { kind: "axis", axis });
    groups.scale.add(sg);
  });

  // centrul: la Move = mutare pe podea, la Scale = scalare uniforma
  const moveCenter = new THREE.Mesh(geo(new THREE.SphereGeometry(0.13, 12, 10)), vis(0xffffff));
  groups.move.add(moveCenter);
  hitMesh("move", groups.move, new THREE.SphereGeometry(0.3, 8, 6), new THREE.Vector3(0, 0, 0), { kind: "center" });

  const scaleCenter = new THREE.Mesh(geo(new THREE.BoxGeometry(0.22, 0.22, 0.22)), vis(0xffffff));
  groups.scale.add(scaleCenter);
  hitMesh("scale", groups.scale, new THREE.SphereGeometry(0.3, 8, 6), new THREE.Vector3(0, 0, 0), { kind: "center" });

  root.add(groups.move, groups.rotate, groups.scale);
  root.traverse(o => { o.renderOrder = 1000; }); // desenat mereu deasupra obiectelor
  root.visible = false;

  return {
    root, groups, hits,
    dispose: () => {
      geometries.forEach(g => g.dispose());
      materials.forEach(m => m.dispose());
    },
  };
}

// Pozitioneaza gizmo-ul la obiectul selectat si il tine de aceeasi marime pe ecran
export function updateGizmo(g: Gizmo, mode: GizmoMode, pivot: THREE.Vector3 | null, quat: THREE.Quaternion, camera: THREE.Camera) {
  if (!pivot) {
    g.root.visible = false;
    return;
  }
  g.root.visible = true;
  g.root.position.copy(pivot);
  const dist = Math.max(1.5, camera.position.distanceTo(pivot));
  g.root.scale.setScalar(dist * GIZMO_SCREEN_SCALE);
  if (mode === "scale") g.root.quaternion.copy(quat); // la Scale axele urmeaza obiectul
  else g.root.quaternion.identity();
  (Object.keys(g.groups) as GizmoMode[]).forEach(m => { g.groups[m].visible = m === mode; });
  g.root.updateMatrixWorld(true);
}

export function pickHandle(g: Gizmo, mode: GizmoMode, rc: THREE.Raycaster): HandleInfo | null {
  if (!g.root.visible) return null;
  const hits = rc.intersectObjects(g.hits[mode], false);
  return hits.length > 0 ? (hits[0].object.userData.handle as HandleInfo) : null;
}

// ---------- matematica gesturilor ----------

// Punctul (parametrul t) de pe axa pivot + dir*t cel mai apropiat de raza degetului
export function closestParamOnAxis(pivot: THREE.Vector3, dir: THREE.Vector3, ray: THREE.Ray): number | null {
  const w = pivot.clone().sub(ray.origin);
  const b = dir.dot(ray.direction);
  const d = dir.dot(w);
  const e = ray.direction.dot(w);
  const denom = 1 - b * b;
  if (denom < 1e-4) return null; // raza e paralela cu axa
  return (b * e - d) / denom;
}

export function planeHit(pivot: THREE.Vector3, normal: THREE.Vector3, ray: THREE.Ray): THREE.Vector3 | null {
  if (Math.abs(normal.dot(ray.direction)) < 0.05) return null; // privim planul "din lateral"
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, pivot);
  return ray.intersectPlane(plane, new THREE.Vector3());
}

// Unghiul (radiani) al punctului atins, in planul inelului, in jurul axei `normal`
export function planeAngle(pivot: THREE.Vector3, normal: THREE.Vector3, ray: THREE.Ray): number | null {
  const hit = planeHit(pivot, normal, ray);
  if (!hit) return null;
  const helper = Math.abs(normal.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0);
  const u = new THREE.Vector3().crossVectors(helper, normal).normalize();
  const v = new THREE.Vector3().crossVectors(normal, u);
  const w = hit.sub(pivot);
  return Math.atan2(w.dot(v), w.dot(u));
}

export function wrapPi(a: number): number {
  return ((((a + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)) - Math.PI;
}

// Din orientare (quaternion) in grade Euler XYZ, alegand varianta cea mai apropiata de valorile anterioare
export function eulerNear(q: THREE.Quaternion, ref: { rx: number; ry: number; rz: number }): { rx: number; ry: number; rz: number } {
  const e = new THREE.Euler().setFromQuaternion(q, "XYZ");
  const a = [e.x * RAD, e.y * RAD, e.z * RAD];
  const b = [a[0] + 180, 180 - a[1], a[2] + 180]; // aceeasi orientare, scrisa altfel
  const near = (v: number, r: number) => v + 360 * Math.round((r - v) / 360);
  const fit = (c: number[]) => [near(c[0], ref.rx), near(c[1], ref.ry), near(c[2], ref.rz)];
  const dist = (c: number[]) => Math.abs(c[0] - ref.rx) + Math.abs(c[1] - ref.ry) + Math.abs(c[2] - ref.rz);
  const ca = fit(a);
  const cb = fit(b);
  const best = dist(ca) <= dist(cb) ? ca : cb;
  return { rx: round3(best[0]), ry: round3(best[1]), rz: round3(best[2]) };
}