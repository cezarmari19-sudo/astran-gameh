// frontend/src/avatar/uvTemplate.ts
// Sistemul de UV mapping stil Roblox clasic pentru tricou/pantaloni: o singura imagine
// "desfasurata" (585x559) impartita in regiuni fixe (Piept, Spate, Maneca stanga/dreapta,
// Picior stanga/dreapta fata/spate). Userul picteaza sau incarca o imagine peste acest
// template, iar noi o aplicam ca `map` pe geometria corpului, remapand UV-urile primitivelor
// three.js sa corespunda exact regiunilor.
//
// Dimensiunile si regiunile sunt FIXE si identice intre acest fisier, templateGenerator.ts
// si TextureEditor.tsx.
import * as THREE from "three";

export const TEMPLATE_WIDTH = 585;
export const TEMPLATE_HEIGHT = 559;

export type RegionKey =
  | "chest" | "back" | "arm_left" | "arm_right"
  | "leg_left_front" | "leg_left_back" | "leg_right_front" | "leg_right_back";

export type Region = { key: RegionKey; label: string; x: number; y: number; w: number; h: number };

export const SHIRT_REGIONS: Region[] = [
  { key: "chest", label: "Piept", x: 128, y: 64, w: 128, h: 128 },
  { key: "back", label: "Spate", x: 288, y: 64, w: 128, h: 128 },
  { key: "arm_left", label: "Mânecă stângă", x: 16, y: 64, w: 64, h: 128 },
  { key: "arm_right", label: "Mânecă dreaptă", x: 448, y: 64, w: 64, h: 128 },
];

export const PANTS_REGIONS: Region[] = [
  { key: "leg_left_front", label: "Picior stâng (față)", x: 16, y: 224, w: 96, h: 192 },
  { key: "leg_left_back", label: "Picior stâng (spate)", x: 128, y: 224, w: 96, h: 192 },
  { key: "leg_right_front", label: "Picior drept (față)", x: 240, y: 224, w: 96, h: 192 },
  { key: "leg_right_back", label: "Picior drept (spate)", x: 352, y: 224, w: 96, h: 192 },
];

export function regionsFor(kind: "shirt" | "pants"): Region[] {
  return kind === "shirt" ? SHIRT_REGIONS : PANTS_REGIONS;
}

export function regionAt(kind: "shirt" | "pants", x: number, y: number): Region | null {
  const regions = regionsFor(kind);
  return regions.find(r => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h) ?? null;
}

function uv(x: number, y: number): [number, number] {
  return [x / TEMPLATE_WIDTH, 1 - y / TEMPLATE_HEIGHT];
}

function remapCylinderUV(geo: THREE.CylinderGeometry, front: Region, back: Region, radialSegments: number, heightSegments: number) {
  const uvAttr = geo.getAttribute("uv") as THREE.BufferAttribute;
  const [fu0, fv0] = uv(front.x, front.y + front.h);
  const [fu1, fv1] = uv(front.x + front.w, front.y);
  const [bu0, bv0] = uv(back.x, back.y + back.h);
  const [bu1, bv1] = uv(back.x + back.w, back.y);
  const vertsPerRow = radialSegments + 1;
  for (let ring = 0; ring <= heightSegments; ring++) {
    const v = ring / heightSegments;
    for (let seg = 0; seg <= radialSegments; seg++) {
      const idx = ring * vertsPerRow + seg;
      const angleFrac = seg / radialSegments;
      const isFront = angleFrac <= 0.5;
      const [u0, u1, v0, v1] = isFront ? [fu0, fu1, fv0, fv1] : [bu0, bu1, bv0, bv1];
      const localU = isFront ? angleFrac / 0.5 : (angleFrac - 0.5) / 0.5;
      uvAttr.setXY(idx, u0 + localU * (u1 - u0), v0 + (1 - v) * (v1 - v0));
    }
  }
  uvAttr.needsUpdate = true;
}

export function buildShirtGeometry() {
  const torso = new THREE.CylinderGeometry(0.26, 0.22, 0.6, 16, 4, true);
  remapCylinderUV(torso, SHIRT_REGIONS[0], SHIRT_REGIONS[1], 16, 4);

  const armL = new THREE.CylinderGeometry(0.085, 0.075, 0.55, 12, 3, true);
  remapCylinderUV(armL, SHIRT_REGIONS[2], SHIRT_REGIONS[2], 12, 3);

  const armR = new THREE.CylinderGeometry(0.085, 0.075, 0.55, 12, 3, true);
  remapCylinderUV(armR, SHIRT_REGIONS[3], SHIRT_REGIONS[3], 12, 3);

  return { torso, armL, armR };
}

export function buildPantsGeometry() {
  const legL = new THREE.CylinderGeometry(0.1, 0.09, 0.62, 12, 4, true);
  remapCylinderUV(legL, PANTS_REGIONS[0], PANTS_REGIONS[1], 12, 4);

  const legR = new THREE.CylinderGeometry(0.1, 0.09, 0.62, 12, 4, true);
  remapCylinderUV(legR, PANTS_REGIONS[2], PANTS_REGIONS[3], 12, 4);

  return { legL, legR };
}

export function textureFromDataUrl(dataUrl: string): Promise<THREE.Texture> {
  return new Promise((resolve, reject) => {
    const loader = new THREE.TextureLoader();
    loader.load(
      dataUrl,
      tex => {
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.flipY = true;
        resolve(tex);
      },
      undefined,
      reject
    );
  });
}