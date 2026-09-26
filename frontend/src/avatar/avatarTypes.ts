// frontend/src/avatar/avatarTypes.ts
// Tipuri + randare 3D pentru corpul avatarului. Doar personalizarea corpului (inaltime,
// latime, proportii, marime cap, culoare piele, forma) - fara haine/accesorii echipabile
// deocamdata; acelea raman pregatite in backend (clothes_routes.py, avatar_routes.py) pentru
// cand se reia partea de Shop pe frontend.
import * as THREE from "three";

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

const BODY_COLOR_UNIFORM = "#3A4047"; // haine implicite (tricou/pantaloni de baza) - inca fara sistem de echipare

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