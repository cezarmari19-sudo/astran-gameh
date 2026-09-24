// frontend/src/studio/sceneShared.ts
// Tipuri si functii folosite si de editor (studio/create.tsx), si de jocul propriu-zis (play/[id].tsx).
import * as THREE from "three";

export type ObjType = "cube" | "sphere" | "cylinder" | "cone" | "tree";

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
};

export type Scene = { objects: SceneObj[]; sky: string; ground: string };

export const PALETTE = ["#CCFF00", "#FF3366", "#00E5FF", "#FFD500", "#00FF66", "#FF9500", "#B266FF", "#FFFFFF", "#666666"];

export const OBJ_TYPES: ObjType[] = ["cube", "sphere", "cylinder", "cone", "tree"];

const OBJ_ICON: Record<string, string> = {
  cube: "cube-outline",
  sphere: "circle-outline",
  cylinder: "cylinder",
  cone: "triangle-outline",
  tree: "pine-tree",
};

export function iconFor(type: string): string {
  return OBJ_ICON[type] ?? "cube-outline";
}

export function geometryFor(type: string): THREE.BufferGeometry {
  if (type === "cube") return new THREE.BoxGeometry(1, 1, 1);
  if (type === "sphere") return new THREE.SphereGeometry(0.6, 20, 16);
  if (type === "cylinder") return new THREE.CylinderGeometry(0.5, 0.5, 1.2, 20);
  if (type === "cone") return new THREE.ConeGeometry(0.6, 1.2, 20);
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
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(o.color), roughness: 0.5, metalness: 0.1 });
  const m = new THREE.Mesh(geometryFor(o.type), mat);
  applyTransform(m, o);
  return m;
}