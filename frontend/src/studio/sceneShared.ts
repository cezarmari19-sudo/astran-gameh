// frontend/src/studio/sceneShared.ts
// Tipuri si functii folosite si de editor (studio/create.tsx), si de jocul propriu-zis (play/[id].tsx).
import * as THREE from "three";

export type ObjType = "cube" | "sphere" | "cylinder" | "cone" | "tree" | "spawn";

export type SceneObj = {
  id: string;
  type: string;
  x: number;
  y: number;
  z: number;
  color: string;
  scale: number; // scala uniforma (compatibila cu jocurile vechi)
  rx?: number; // rotatie in grade
  ry?: number;
  rz?: number;
  sx?: number; // intindere pe axa (se inmulteste cu scale); implicit 1
  sy?: number;
  sz?: number;
  visible?: boolean; // implicit true; daca false, nu se randeaza in Play (dar tot exista in Studio, semi-transparent)
  solid?: boolean;   // implicit true pentru obiecte normale, false pentru spawn; controleaza collision in Play
};

export type Scene = { objects: SceneObj[]; sky: string; ground: string };

export const PALETTE = ["#CCFF00", "#FF3366", "#00E5FF", "#FFD500", "#00FF66", "#FF9500", "#B266FF", "#FFFFFF", "#666666"];

// "spawn" e un marker special, nu un obiect de gameplay obisnuit: nu apare in lista ADD alaturi
// de restul formelor cu acelasi tratament - vezi SPAWN_TYPE mai jos si tratarea lui separata in Studio.
export const OBJ_TYPES: ObjType[] = ["cube", "sphere", "cylinder", "cone", "tree"];
export const SPAWN_TYPE: ObjType = "spawn";

const OBJ_ICON: Record<string, string> = {
  cube: "cube-outline",
  sphere: "circle-outline",
  cylinder: "cylinder",
  cone: "triangle-outline",
  tree: "pine-tree",
  spawn: "map-marker-radius-outline",
};

export function iconFor(type: string): string {
  return OBJ_ICON[type] ?? "cube-outline";
}

export function isSolidDefault(type: string): boolean {
  return type !== "spawn";
}

export function geometryFor(type: string): THREE.BufferGeometry {
  if (type === "cube") return new THREE.BoxGeometry(1, 1, 1);
  if (type === "sphere") return new THREE.SphereGeometry(0.6, 20, 16);
  if (type === "cylinder") return new THREE.CylinderGeometry(0.5, 0.5, 1.2, 20);
  if (type === "cone") return new THREE.ConeGeometry(0.6, 1.2, 20);
  if (type === "spawn") return new THREE.CylinderGeometry(0.6, 0.6, 0.05, 24); // disc plat, marcheaza locul
  return new THREE.ConeGeometry(0.7, 1.6, 8);
}

type TransformLike = Pick<SceneObj, "x" | "y" | "z" | "scale" | "rx" | "ry" | "rz" | "sx" | "sy" | "sz">;

// Pune pozitia, rotatia si scala unui obiect 3D dupa datele din scena.
export function applyTransform(m: THREE.Object3D, o: TransformLike) {
  const sx = o.scale * (o.sx ?? 1);
  const sy = o.scale * (o.sy ?? 1);
  const sz = o.scale * (o.sz ?? 1);
  const d = Math.PI / 180;
  m.scale.set(sx, sy, sz);
  m.position.set(o.x, o.y + 0.5 * sy, o.z);
  m.rotation.set((o.rx ?? 0) * d, (o.ry ?? 0) * d, (o.rz ?? 0) * d);
}

export function buildMesh(o: SceneObj): THREE.Mesh {
  const isSpawn = o.type === "spawn";
  const mat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(isSpawn ? "#CCFF00" : o.color),
    roughness: 0.5,
    metalness: 0.1,
    transparent: isSpawn,
    opacity: isSpawn ? 0.55 : 1,
  });
  const m = new THREE.Mesh(geometryFor(o.type), mat);
  applyTransform(m, o);
  return m;
}

// Cutia de coliziune (AABB, in coordonate lume) a unui obiect solid - folosita in Play pentru
// a opri jucatorul sa treaca prin el. Aproximare simpla (nu tine cont de rotatie), suficienta
// pentru obiectele de baza din Studio.
export type AABB = { minX: number; maxX: number; minY: number; maxY: number; minZ: number; maxZ: number };

export function aabbFor(o: SceneObj): AABB {
  const sx = o.scale * (o.sx ?? 1);
  const sy = o.scale * (o.sy ?? 1);
  const sz = o.scale * (o.sz ?? 1);
  // dimensiunea de baza a formei (inainte de scalare) - aproximam toate formele ca o cutie
  // egala cu diametrul lor, e suficient de precis pentru collision de gameplay simplu
  const baseHalf = o.type === "sphere" ? 0.6 : o.type === "cylinder" ? 0.5 : o.type === "cone" ? 0.6 : o.type === "tree" ? 0.7 : 0.5;
  const hx = baseHalf * sx;
  const hy = 0.5 * sy;
  const hz = baseHalf * sz;
  const cy = o.y + hy;
  return { minX: o.x - hx, maxX: o.x + hx, minY: o.y, maxY: cy + hy, minZ: o.z - hz, maxZ: o.z + hz };
}