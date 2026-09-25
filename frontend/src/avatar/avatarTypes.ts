// frontend/src/avatar/avatarTypes.ts
// Tipuri + randare 3D pentru Avatar Editor. Corpul e construit din forme simple (three.js),
// iar item-urile de geometrie echipate (par, palarii, accesorii) sunt piese (Part[]) create in
// Avatar Item Studio - randate cu exact aceleasi functii pure ca in Game Studio
// (createObject/applyLocalTransform/applyMaterial din modelTypes.ts), dar NICIODATA citite
// din colectia studio_models: sursa lor e clothes_items (vezi backend/astran_sandbox/clothes_routes.py).
import * as THREE from "three";
import type { Part } from "@/src/studio3d/modelTypes";
import { createObject, applyLocalTransform, applyMaterial, disposeObject, byIdMap, isVisibleDeep } from "@/src/studio3d/modelTypes";

// Sloturile din Avatar Editor. Extensibil: pentru o categorie noua, adauga o linie aici
// SI in SLOTS din backend/astran_sandbox/clothes_routes.py (cheile trebuie identice).
export type Slot = "hair" | "shirt" | "pants" | "shoes" | "hat" | "accessory" | "face" | "back" | "effect";

export const SLOT_DEFS: { key: Slot; label: string; icon: string; renderKind: "geometry" | "texture" }[] = [
  { key: "hair", label: "Păr", icon: "hair-dryer", renderKind: "geometry" },
  { key: "shirt", label: "Tricouri", icon: "tshirt-crew-outline", renderKind: "texture" },
  { key: "pants", label: "Pantaloni", icon: "human", renderKind: "texture" },
  { key: "shoes", label: "Încălțăminte", icon: "shoe-sneaker", renderKind: "geometry" },
  { key: "hat", label: "Pălării", icon: "hat-fedora", renderKind: "geometry" },
  { key: "accessory", label: "Accesorii", icon: "sunglasses", renderKind: "geometry" },
  { key: "face", label: "Față", icon: "emoticon-outline", renderKind: "geometry" },
  { key: "back", label: "Accesorii spate", icon: "bag-personal-outline", renderKind: "geometry" },
  { key: "effect", label: "Efecte", icon: "shimmer", renderKind: "geometry" },
];

export const BODY_SHAPES: { key: string; label: string }[] = [
  { key: "standard", label: "Standard" },
  { key: "slim", label: "Slim" },
  { key: "broad", label: "Atletic" },
];

export type AvatarBody = {
  height: number;
  width: number;
  proportions: number;
  head_size: number;
  skin_color: string;
  body_shape: "standard" | "slim" | "broad";
};

export function defaultBody(): AvatarBody {
  return { height: 1, width: 1, proportions: 1, head_size: 1, skin_color: "#E8B48C", body_shape: "standard" };
}

export type Equipped = Partial<Record<Slot, string | null>>;

export type InventoryItem = {
  item_id: string;
  name: string;
  slot: Slot;
  render_kind: "geometry" | "texture";
  thumbnail_url?: string | null;
  preview?: { color?: string; part_count?: number; has_texture?: boolean };
  owner_username: string;
};

// ---------- corp ----------

const BODY_COLOR_UNIFORM = "#3A4047"; // haine implicite daca slotul shirt/pants nu e echipat

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

export function layoutBody(m: BodyMeshes, body: AvatarBody) {
  const { height, width, proportions, head_size, skin_color } = body;
  const legLen = 0.62 * height * (2 - proportions) * 0.6 + 0.62 * height * 0.4;
  const torsoLen = 0.6 * height * proportions;

  (m.head.material as THREE.MeshStandardMaterial).color.set(skin_color);
  (m.armL.material as THREE.MeshStandardMaterial).color.set(skin_color);
  (m.armR.material as THREE.MeshStandardMaterial).color.set(skin_color);

  m.head.scale.setScalar(head_size);
  m.head.position.set(0, legLen + torsoLen + 0.22 + 0.28 * head_size, 0);

  m.torso.scale.set(width, torsoLen / 0.6, width);
  m.torso.position.set(0, legLen + 0.22 + torsoLen / 2, 0);

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

// ---------- randarea unui item de geometrie echipat (piese din Avatar Item Studio) ----------
// Foloseste exact functiile pure din modelTypes.ts (aceleasi ca in Game Studio), aplicate
// unei liste de piese ce provin din clothes_items, nu din studio_models.

export function buildEquippedGroup(parts: Part[]): THREE.Group {
  const group = new THREE.Group();
  const objects = new Map<string, THREE.Object3D>();

  parts.forEach(p => {
    const obj = createObject(p);
    applyLocalTransform(obj, p);
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh) applyMaterial(mesh.material as THREE.MeshStandardMaterial, p);
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

export function disposeEquippedGroup(g: THREE.Group) {
  g.traverse(o => disposeObject(o));
}

// Ancora aproximativa (inaltime relativa la corp, 0=picioare .. 1=varful capului) pentru fiecare
// slot de geometrie, folosita cand pozitionam grupul unui item echipat pe corp.
export const SLOT_ANCHOR_Y: Partial<Record<Slot, number>> = {
  shoes: 0.02,
  hair: 0.97,
  hat: 1.0,
  accessory: 0.6,
  face: 0.88,
  back: 0.55,
  effect: 0.5,
};