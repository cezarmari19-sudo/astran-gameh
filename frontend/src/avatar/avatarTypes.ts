// frontend/src/avatar/avatarTypes.ts
// Tipuri + randare 3D pentru Avatar Editor. Corpul e construit din forme simple (three.js),
// iar item-urile echipate (haine/accesorii) sunt modele Studio (Part[]) atasate ca sub-grup.
import * as THREE from "three";
import type { Part } from "@/src/studio3d/modelTypes";
import { createObject, applyLocalTransform, applyMaterial, byIdMap } from "@/src/studio3d/modelTypes";

// Sloturile din Avatar Editor. Extensibil: pentru o categorie noua, adauga o linie aici
// SI in SLOTS din backend/astran_sandbox/avatar_routes.py (cheile trebuie sa fie identice).
export type Slot = "hair" | "shirt" | "pants" | "shoes" | "hat" | "accessory" | "face" | "back" | "effect";

export const SLOT_DEFS: { key: Slot; label: string; icon: string }[] = [
  { key: "hair", label: "Păr", icon: "hair-dryer" },
  { key: "shirt", label: "Haine", icon: "tshirt-crew-outline" },
  { key: "pants", label: "Pantaloni", icon: "human" },
  { key: "shoes", label: "Încălțăminte", icon: "shoe-sneaker" },
  { key: "hat", label: "Pălării", icon: "hat-fedora" },
  { key: "accessory", label: "Accesorii", icon: "sunglasses" },
  { key: "face", label: "Față", icon: "emoticon-outline" },
  { key: "back", label: "Spate", icon: "bag-personal-outline" },
  { key: "effect", label: "Efecte", icon: "shimmer" },
];

export const BODY_SHAPES: { key: string; label: string }[] = [
  { key: "standard", label: "Standard" },
  { key: "slim", label: "Slim" },
  { key: "broad", label: "Atletic" },
];

export type AvatarBody = {
  height: number;       // 0.7..1.4
  width: number;        // 0.7..1.4
  proportions: number;  // 0.7..1.3 (trunchi vs picioare)
  head_size: number;    // 0.7..1.4
  skin_color: string;   // #rrggbb
  body_shape: "standard" | "slim" | "broad";
};

export function defaultBody(): AvatarBody {
  return { height: 1, width: 1, proportions: 1, head_size: 1, skin_color: "#E8B48C", body_shape: "standard" };
}

export type Equipped = Partial<Record<Slot, string | null>>;

export type AvatarState = {
  body: AvatarBody;
  equipped: Equipped;
};

export type InventoryItem = {
  item_id: string;
  name: string;
  slot: Slot;
  thumbnail_url?: string | null;
  preview?: { type?: string; color?: string; scale?: number; part_count?: number };
  owner_username: string;
};

// ---------- construirea corpului 3D ----------
// Corpul e format din forme simple grupate ca un mini-model Studio: usor de intins pe
// inaltime/latime fara sa distorsionam capul, si usor de extins cu piese noi.

const BODY_COLOR_UNIFORM = "#3A4047"; // haine implicite (tricou/pantaloni de baza) daca slotul nu e echipat

type BodyMeshes = {
  group: THREE.Group;
  head: THREE.Mesh;
  torso: THREE.Mesh;
  hips: THREE.Mesh;
  armL: THREE.Mesh;
  armR: THREE.Mesh;
  legL: THREE.Mesh;
  legR: THREE.Mesh;
};

export function buildBodyMeshes(): BodyMeshes {
  const skinMat = () => new THREE.MeshStandardMaterial({ color: new THREE.Color("#E8B48C"), roughness: 0.7 });
  const clothMat = () => new THREE.MeshStandardMaterial({ color: new THREE.Color(BODY_COLOR_UNIFORM), roughness: 0.8 });

  const group = new THREE.Group();

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.28, 20, 16), skinMat());
  const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.26, 0.22, 0.6, 16), clothMat());
  const hips = new THREE.Mesh(new THREE.CylinderGeometry(0.23, 0.2, 0.22, 16), clothMat());
  const armL = new THREE.Mesh(new THREE.CapsuleGeometry(0.075, 0.55, 4, 8), skinMat());
  const armR = new THREE.Mesh(new THREE.CapsuleGeometry(0.075, 0.55, 4, 8), skinMat());
  const legL = new THREE.Mesh(new THREE.CapsuleGeometry(0.095, 0.62, 4, 8), clothMat());
  const legR = new THREE.Mesh(new THREE.CapsuleGeometry(0.095, 0.62, 4, 8), clothMat());

  group.add(head, torso, hips, armL, armR, legL, legR);
  return { group, head, torso, hips, armL, armR, legL, legR };
}

// Reaplica pozitiile/scalele pe baza parametrilor de corp curenti. Chemat de fiecare data
// cand utilizatorul misca un slider, ca modificarile sa fie instant vizibile.
export function layoutBody(m: BodyMeshes, body: AvatarBody) {
  const { height, width, proportions, head_size, skin_color } = body;
  const legLen = 0.62 * height * (2 - proportions) * 0.6 + 0.62 * height * 0.4;
  const torsoLen = 0.6 * height * proportions;

  (m.head.material as THREE.MeshStandardMaterial).color.set(skin_color);
  (m.armL.material as THREE.MeshStandardMaterial).color.set(skin_color);
  (m.armR.material as THREE.MeshStandardMaterial).color.set(skin_color);

  m.head.scale.setScalar(head_size);
  const headY = legLen + torsoLen + 0.22 + 0.28 * head_size;
  m.head.position.set(0, headY, 0);

  m.torso.scale.set(width, torsoLen / 0.6, width);
  m.torso.position.y = legLen + 0.22 + torsoLen / 2;

  m.hips.scale.set(width, 1, width);
  m.hips.position.set(0, legLen + 0.11, 0);

  const armY = legLen + 0.22 + torsoLen * 0.75;
  const armX = 0.26 * width + 0.09;
  m.armL.position.set(-armX, armY, 0);
  m.armR.position.set(armX, armY, 0);
  m.armL.scale.set(1, (0.55 * height) / 0.55, 1);
  m.armR.scale.set(1, (0.55 * height) / 0.55, 1);

  const legX = 0.1 * width;
  m.legL.position.set(-legX, legLen / 2, 0);
  m.legR.position.set(legX, legLen / 2, 0);
  m.legL.scale.set(1, legLen / 0.62, 1);
  m.legR.scale.set(1, legLen / 0.62, 1);
}

export function bodyHeightWorld(body: AvatarBody): number {
  const legLen = 0.62 * body.height * (2 - body.proportions) * 0.6 + 0.62 * body.height * 0.4;
  const torsoLen = 0.6 * body.height * body.proportions;
  return legLen + 0.22 + torsoLen + 0.56 * body.head_size;
}

// ---------- randarea unui item echipat (model din Studio) ----------
// Un item echipat e un mic model pe piese (Part[]); il randam ca un THREE.Group atasat
// avatarului, la fel cum Studio randeaza un model intreg, dar scalat/pozitionat pe slot.

export function buildEquippedGroup(parts: Part[]): THREE.Group {
  const group = new THREE.Group();
  const objects = new Map<string, THREE.Object3D>();
  const by = byIdMap(parts);

  parts.forEach(p => {
    const obj = createObject(p);
    applyLocalTransform(obj, p);
    if ((obj as THREE.Mesh).isMesh) applyMaterial((obj as THREE.Mesh).material as THREE.MeshStandardMaterial, p);
    obj.visible = p.visible;
    objects.set(p.id, obj);
  });

  parts.forEach(p => {
    const obj = objects.get(p.id)!;
    const parent = p.parent ? objects.get(p.parent) : null;
    (parent ?? group).add(obj);
  });

  return group;
}

export function disposeGroup(g: THREE.Group) {
  g.traverse(o => {
    const m = o as THREE.Mesh;
    if (m.isMesh) {
      m.geometry?.dispose();
      const mat = m.material as THREE.Material | THREE.Material[];
      if (Array.isArray(mat)) mat.forEach(x => x.dispose());
      else mat?.dispose();
    }
  });
}

// Ancora aproximativa (inaltime relativa la corp, 0=picioare .. 1=varful capului) pentru fiecare slot,
// folosita cand pozitionam grupul unui item echipat pe corp.
export const SLOT_ANCHOR_Y: Record<Slot, number> = {
  shoes: 0.02,
  pants: 0.28,
  shirt: 0.55,
  back: 0.55,
  accessory: 0.6,
  hair: 0.97,
  hat: 1.0,
  face: 0.88,
  effect: 0.5,
};