// frontend/src/studio3d/modelTypes.ts
// Structura unui model din Studio (lista plata de piese cu ierarhie) + functii pentru arbore si three.js.
import * as THREE from "three";

export type PartType = "group" | "cube" | "sphere" | "cylinder" | "cone" | "plane" | "torus";
export type MaterialKind = "matte" | "glossy" | "metal" | "glass" | "neon";

export type Part = {
  id: string;
  name: string;
  type: PartType;
  parent: string | null; // id-ul grupului parinte; null doar pentru radacina
  x: number; y: number; z: number;     // pozitie locala (fata de parinte)
  rx: number; ry: number; rz: number;  // rotatie in grade
  sx: number; sy: number; sz: number;  // scala
  color: string;
  material: MaterialKind;
  opacity: number; // 0..1
  visible: boolean;
};

export const ROOT_ID = "root";
export const SHAPES: PartType[] = ["cube", "sphere", "cylinder", "cone", "plane", "torus"];
export const MATERIALS: MaterialKind[] = ["matte", "glossy", "metal", "glass", "neon"];
export const PALETTE = [
  "#CCFF00", "#FF3366", "#00E5FF", "#FFD500", "#00FF66", "#FF9500",
  "#B266FF", "#FFFFFF", "#9E9E9E", "#5D4037", "#2E7D32", "#212121",
];

export const PART_LABEL: Record<PartType, string> = {
  group: "Group", cube: "Cube", sphere: "Sphere", cylinder: "Cylinder", cone: "Cone", plane: "Plane", torus: "Torus",
};

export const PART_ICON: Record<PartType, string> = {
  group: "folder-outline", cube: "cube-outline", sphere: "circle-outline", cylinder: "cylinder",
  cone: "triangle-outline", plane: "square-outline", torus: "circle-double",
};

// Dimensiunea reala (latime, inaltime, adancime) a formei la scala 1. Size = scala * aceste valori.
export const BASE_SIZE: Record<PartType, [number, number, number]> = {
  group: [1, 1, 1],
  cube: [1, 1, 1],
  sphere: [1, 1, 1],
  cylinder: [1, 1, 1],
  cone: [1, 1, 1],
  plane: [1, 0, 1],
  torus: [1, 1, 0.2],
};

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;
export const round3 = (v: number) => Math.round(v * 1000) / 1000;
const num = (v: any, d: number) => (typeof v === "number" && isFinite(v) ? v : d);

export function uid(): string {
  return "p" + Math.random().toString(36).slice(2, 10);
}

// ---------- creare piese ----------

export function newRootPart(name = "Model"): Part {
  return {
    id: ROOT_ID, name, type: "group", parent: null,
    x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0, sx: 1, sy: 1, sz: 1,
    color: "#cccccc", material: "matte", opacity: 1, visible: true,
  };
}

const DEFAULT_Y: Record<PartType, number> = { group: 0, cube: 0.5, sphere: 0.5, cylinder: 0.5, cone: 0.5, plane: 0.01, torus: 0.1 };

export function newPart(type: PartType, parent: string, name: string): Part {
  return {
    id: uid(), name, type, parent,
    x: 0, y: DEFAULT_Y[type], z: 0,
    rx: type === "torus" ? 90 : 0, ry: 0, rz: 0,
    sx: 1, sy: 1, sz: 1,
    color: "#CCFF00", material: "matte", opacity: 1, visible: true,
  };
}

// "Ramura" -> "Ramura", "Ramura" (a doua oara) -> "Ramura_2", ...
export function uniqueName(parts: Part[], base: string): string {
  const stem = base.replace(/_\d+$/, "");
  const used = new Set(parts.map(p => p.name));
  if (!used.has(stem)) return stem;
  let n = 2;
  while (used.has(`${stem}_${n}`)) n++;
  return `${stem}_${n}`;
}

export function countShapes(parts: Part[]): number {
  return parts.filter(p => p.type !== "group").length;
}

// Curata ce vine de la server (sau dintr-o versiune mai veche): valori lipsa, radacina, parinti disparuti.
export function normalizeParts(raw: any): Part[] {
  const list: any[] = Array.isArray(raw) ? raw : [];
  const parts: Part[] = list
    .filter(p => p && typeof p.id === "string")
    .map(p => ({
      id: p.id,
      name: typeof p.name === "string" && p.name ? p.name : "Part",
      type: (p.type === "group" || SHAPES.includes(p.type) ? p.type : "cube") as PartType,
      parent: typeof p.parent === "string" ? p.parent : null,
      x: num(p.x, 0), y: num(p.y, 0), z: num(p.z, 0),
      rx: num(p.rx, 0), ry: num(p.ry, 0), rz: num(p.rz, 0),
      sx: num(p.sx, 1) || 1, sy: num(p.sy, 1) || 1, sz: num(p.sz, 1) || 1,
      color: typeof p.color === "string" && /^#[0-9a-fA-F]{6}$/.test(p.color) ? p.color : "#cccccc",
      material: (MATERIALS.includes(p.material) ? p.material : "matte") as MaterialKind,
      opacity: Math.max(0, Math.min(1, num(p.opacity, 1))),
      visible: p.visible !== false,
    }));
  if (!parts.some(p => p.id === ROOT_ID)) parts.unshift(newRootPart("Model"));
  const ids = new Set(parts.map(p => p.id));
  return parts.map(p => {
    if (p.id === ROOT_ID) return { ...p, parent: null, type: "group" as PartType };
    if (!p.parent || !ids.has(p.parent)) return { ...p, parent: ROOT_ID };
    return p;
  });
}

// ---------- arbore ----------

export function byIdMap(parts: Part[]): Map<string, Part> {
  return new Map(parts.map(p => [p.id, p]));
}

export function descendantIds(parts: Part[], id: string): string[] {
  const out: string[] = [];
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    for (const p of parts) {
      if (p.parent === cur) {
        out.push(p.id);
        stack.push(p.id);
      }
    }
  }
  return out; // parintii apar inaintea copiilor
}

// Doar elementele care nu au un stramos in selectie (radacina nu se poate muta/sterge/copia)
export function topLevel(parts: Part[], ids: string[]): string[] {
  const set = new Set(ids);
  const by = byIdMap(parts);
  return ids.filter(id => {
    if (id === ROOT_ID) return false;
    let cur = by.get(id)?.parent ?? null;
    while (cur) {
      if (set.has(cur)) return false;
      cur = by.get(cur)?.parent ?? null;
    }
    return true;
  });
}

// Ca topLevel, dar si radacina poate fi transformata (mutata / rotita / scalata) cu gizmo-ul
export function outermost(parts: Part[], ids: string[]): string[] {
  const set = new Set(ids);
  const by = byIdMap(parts);
  return ids.filter(id => {
    let cur = by.get(id)?.parent ?? null;
    while (cur) {
      if (set.has(cur)) return false;
      cur = by.get(cur)?.parent ?? null;
    }
    return true;
  });
}

// Lista pentru Outliner: [{part, depth}] in ordinea arborelui, sarind peste grupurile inchise
export function flatten(parts: Part[], collapsed: Set<string>): { part: Part; depth: number }[] {
  const kids = new Map<string | null, Part[]>();
  parts.forEach(p => {
    if (!kids.has(p.parent)) kids.set(p.parent, []);
    kids.get(p.parent)!.push(p);
  });
  const out: { part: Part; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const p of kids.get(parent) ?? []) {
      out.push({ part: p, depth });
      if (p.type === "group" && !collapsed.has(p.id)) walk(p.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

// ---------- matrici (pastreaza pozitia in lume la mutarea intre grupuri) ----------

export function localMatrix(p: Part): THREE.Matrix4 {
  const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(p.rx * DEG, p.ry * DEG, p.rz * DEG, "XYZ"));
  return new THREE.Matrix4().compose(new THREE.Vector3(p.x, p.y, p.z), q, new THREE.Vector3(p.sx, p.sy, p.sz));
}

export function worldMatrix(parts: Part[], id: string): THREE.Matrix4 {
  const by = byIdMap(parts);
  const chain: Part[] = [];
  let cur = by.get(id);
  while (cur) {
    chain.push(cur);
    cur = cur.parent ? by.get(cur.parent) : undefined;
  }
  const m = new THREE.Matrix4();
  for (let i = chain.length - 1; i >= 0; i--) m.multiply(localMatrix(chain[i]));
  return m;
}

function patchFromMatrix(m: THREE.Matrix4): Pick<Part, "x" | "y" | "z" | "rx" | "ry" | "rz" | "sx" | "sy" | "sz"> {
  const pos = new THREE.Vector3();
  const q = new THREE.Quaternion();
  const s = new THREE.Vector3();
  m.decompose(pos, q, s);
  const e = new THREE.Euler().setFromQuaternion(q, "XYZ");
  return {
    x: round3(pos.x), y: round3(pos.y), z: round3(pos.z),
    rx: round3(e.x * RAD), ry: round3(e.y * RAD), rz: round3(e.z * RAD),
    sx: round3(Math.abs(s.x)) || 1, sy: round3(Math.abs(s.y)) || 1, sz: round3(Math.abs(s.z)) || 1,
  };
}

// ---------- operatii pe model (toate intorc o lista noua de piese) ----------

// Muta piesele sub alt grup, fara sa se miste in scena
export function reparent(parts: Part[], ids: string[], newParent: string): Part[] {
  const moving = new Set(topLevel(parts, ids));
  if (moving.size === 0) return parts;
  const forbidden = new Set<string>();
  moving.forEach(id => {
    forbidden.add(id);
    descendantIds(parts, id).forEach(d => forbidden.add(d));
  });
  if (forbidden.has(newParent)) return parts; // nu poti muta un grup in el insusi
  const inv = worldMatrix(parts, newParent).invert();
  const worlds = new Map<string, THREE.Matrix4>();
  moving.forEach(id => worlds.set(id, worldMatrix(parts, id)));
  return parts.map(p => (moving.has(p.id) ? { ...p, parent: newParent, ...patchFromMatrix(inv.clone().multiply(worlds.get(p.id)!)) } : p));
}

export function groupParts(parts: Part[], ids: string[]): { parts: Part[]; groupId: string } | null {
  const top = topLevel(parts, ids);
  if (top.length === 0) return null;
  const by = byIdMap(parts);
  const parentId = by.get(top[0])?.parent ?? ROOT_ID;
  // grupul se pune in centrul pieselor, ca sa se roteasca/scaleze in jurul lor
  const inv = worldMatrix(parts, parentId).invert();
  const c = new THREE.Vector3();
  top.forEach(id => c.add(new THREE.Vector3().setFromMatrixPosition(inv.clone().multiply(worldMatrix(parts, id)))));
  c.divideScalar(top.length);
  const g: Part = { ...newPart("group", parentId, uniqueName(parts, "Group")), x: round3(c.x), y: round3(c.y), z: round3(c.z) };
  return { parts: reparent([...parts, g], top, g.id), groupId: g.id };
}

export function ungroup(parts: Part[], groupId: string): Part[] {
  const g = byIdMap(parts).get(groupId);
  if (!g || g.id === ROOT_ID || g.type !== "group" || !g.parent) return parts;
  const kids = parts.filter(p => p.parent === groupId).map(p => p.id);
  return reparent(parts, kids, g.parent).filter(p => p.id !== groupId);
}

export function duplicateParts(parts: Part[], ids: string[]): { parts: Part[]; newIds: string[] } {
  const top = topLevel(parts, ids);
  const by = byIdMap(parts);
  const added: Part[] = [];
  const newIds: string[] = [];
  for (const id of top) {
    const subtree = [id, ...descendantIds(parts, id)];
    const map = new Map<string, string>();
    subtree.forEach(s => map.set(s, uid()));
    subtree.forEach(s => {
      const p = by.get(s)!;
      const isTop = s === id;
      added.push({
        ...p,
        id: map.get(s)!,
        parent: isTop ? p.parent : map.get(p.parent as string)!,
        name: isTop ? uniqueName([...parts, ...added], p.name) : p.name,
        x: isTop ? round3(p.x + 0.5) : p.x,
      });
    });
    newIds.push(map.get(id)!);
  }
  return { parts: [...parts, ...added], newIds };
}

export function deleteParts(parts: Part[], ids: string[]): Part[] {
  const gone = new Set<string>();
  topLevel(parts, ids).forEach(id => {
    gone.add(id);
    descendantIds(parts, id).forEach(d => gone.add(d));
  });
  return gone.size === 0 ? parts : parts.filter(p => !gone.has(p.id));
}

// ---------- three.js ----------

function geometryForShape(type: PartType): THREE.BufferGeometry {
  switch (type) {
    case "sphere": return new THREE.SphereGeometry(0.5, 24, 16);
    case "cylinder": return new THREE.CylinderGeometry(0.5, 0.5, 1, 24);
    case "cone": return new THREE.ConeGeometry(0.5, 1, 24);
    case "plane": {
      const g = new THREE.PlaneGeometry(1, 1);
      g.rotateX(-Math.PI / 2); // culcat pe pamant
      return g;
    }
    case "torus": return new THREE.TorusGeometry(0.4, 0.1, 12, 32);
    default: return new THREE.BoxGeometry(1, 1, 1);
  }
}

export function createObject(p: Part): THREE.Object3D {
  if (p.type === "group") return new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(p.color), side: p.type === "plane" ? THREE.DoubleSide : THREE.FrontSide });
  return new THREE.Mesh(geometryForShape(p.type), mat);
}

export function disposeObject(o: THREE.Object3D) {
  const m = o as THREE.Mesh;
  if (m.isMesh) {
    m.geometry.dispose();
    (m.material as THREE.Material).dispose();
  }
}

export function applyLocalTransform(o: THREE.Object3D, p: Part) {
  o.position.set(p.x, p.y, p.z);
  o.rotation.set(p.rx * DEG, p.ry * DEG, p.rz * DEG, "XYZ");
  o.scale.set(p.sx, p.sy, p.sz);
}

export function applyMaterial(mat: THREE.MeshStandardMaterial, p: Part) {
  mat.color.set(p.color);
  if (p.material === "glossy") { mat.roughness = 0.2; mat.metalness = 0.1; }
  else if (p.material === "metal") { mat.roughness = 0.3; mat.metalness = 0.9; }
  else if (p.material === "glass") { mat.roughness = 0.05; mat.metalness = 0; }
  else if (p.material === "neon") { mat.roughness = 0.5; mat.metalness = 0; }
  else { mat.roughness = 0.9; mat.metalness = 0; }
  if (p.material === "neon") {
    mat.emissive.set(p.color);
    mat.emissiveIntensity = 1.2;
  } else {
    mat.emissive.set(0x000000);
    mat.emissiveIntensity = 1;
  }
  const transparent = p.opacity < 1;
  mat.opacity = p.opacity;
  mat.depthWrite = !transparent;
  if (mat.transparent !== transparent) {
    mat.transparent = transparent;
    mat.needsUpdate = true;
  }
}

// three.js nu ignora obiectele ascunse la raycast, deci verificam noi tot lantul de parinti
export function isVisibleDeep(o: THREE.Object3D): boolean {
  let cur: THREE.Object3D | null = o;
  while (cur) {
    if (!cur.visible) return false;
    cur = cur.parent;
  }
  return true;
}