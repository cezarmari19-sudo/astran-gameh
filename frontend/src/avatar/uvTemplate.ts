// frontend/src/avatar/uvTemplate.ts
// Sistemul de UV mapping stil Roblox clasic pentru tricou/pantaloni: o singura imagine
// "desfasurata" (585x559) impartita in regiuni fixe (Piept, Spate, Maneca stanga/dreapta,
// Picior stanga/dreapta fata/spate). Userul picteaza sau incarca o imagine peste acest
// template, iar noi o aplicam ca `map` pe geometria corpului, remapand UV-urile primitivelor
// three.js (care nu se aliniaza implicit cu regiunile Roblox) sa corespunda exact.
//
// Dimensiunile canvasului si regiunile sunt FIXE si trebuie sa ramana identice intre:
// - acest fisier (remapare UV pe geometrie)
// - templateGenerator.ts (desenarea liniilor/etichetelor pe care userul le vede)
// - editorul de textura (unde userul picteaza/plaseaza imaginea)
import * as THREE from "three";

export const TEMPLATE_WIDTH = 585;
export const TEMPLATE_HEIGHT = 559;

export type RegionKey =
  | "chest" | "back" | "arm_left" | "arm_right"           // tricou
  | "leg_left_front" | "leg_left_back" | "leg_right_front" | "leg_right_back"; // pantaloni

export type Region = { key: RegionKey; label: string; x: number; y: number; w: number; h: number };

// Coordonate in pixeli pe canvasul de 585x559 (origine stanga-sus), pastrand proportiile
// clasice ale template-ului Roblox (torso in centru, brate lateral, picioare jos).
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

function uv(x: number, y: number): [number, number] {
  // three.js: V=0 e jos; imaginea noastra are Y=0 sus, deci inversam V
  return [x / TEMPLATE_WIDTH, 1 - y / TEMPLATE_HEIGHT];
}

// Remapeaza UV-urile unei geometrii BoxGeometry/CylinderGeometry ca fiecare fata majora
// sa arate exact spre regiunea corecta din template, in loc de UV-ul implicit 0..1 per fata.
// `faces` mapeaza fiecare grup de fete (dupa index de material three.js) la o regiune.
function remapBoxUV(geo: THREE.BoxGeometry, faceRegions: Partial<Record<"px" | "nx" | "py" | "ny" | "pz" | "nz", Region>>) {
  const uvAttr = geo.getAttribute("uv") as THREE.BufferAttribute;
  // BoxGeometry: grupurile de fete sunt in ordinea +x,-x,+y,-y,+z,-z, cate 4 vertecsi fiecare
  const order: ("px" | "nx" | "py" | "ny" | "pz" | "nz")[] = ["px", "nx", "py", "ny", "pz", "nz"];
  order.forEach((face, i) => {
    const region = faceRegions[face];
    if (!region) return;
    const [u0, v0] = uv(region.x, region.y + region.h);
    const [u1, v1] = uv(region.x + region.w, region.y);
    const base = i * 4;
    // BoxGeometry UV implicit per fata: (0,1),(1,1),(0,0),(1,0) -> remapam la dreptunghiul regiunii
    uvAttr.setXY(base + 0, u0, v0);
    uvAttr.setXY(base + 1, u1, v0);
    uvAttr.setXY(base + 2, u0, v1);
    uvAttr.setXY(base + 3, u1, v1);
  });
  uvAttr.needsUpdate = true;
}

// Remapeaza UV-urile unui CylinderGeometry (fara capace) ca fata "din fata" (jumatatea
// vazuta de camera implicit) sa arate spre `front`, iar restul cilindrului spre `back`.
// E o aproximare (Roblox foloseste acelasi truc pe capsule) suficienta pentru un tricou/pantalon.
function remapCylinderUV(geo: THREE.CylinderGeometry, front: Region, back: Region, radialSegments: number, heightSegments: number) {
  const uvAttr = geo.getAttribute("uv") as THREE.BufferAttribute;
  const [fu0, fv0] = uv(front.x, front.y + front.h);
  const [fu1, fv1] = uv(front.x + front.w, front.y);
  const [bu0, bv0] = uv(back.x, back.y + back.h);
  const [bu1, bv1] = uv(back.x + back.w, back.y);
  const vertsPerRow = radialSegments + 1;
  for (let ring = 0; ring <= heightSegments; ring++) {
    const v = ring / heightSegments; // 0 = top, 1 = bottom in geometria originala
    for (let seg = 0; seg <= radialSegments; seg++) {
      const idx = ring * vertsPerRow + seg;
      const angleFrac = seg / radialSegments; // 0..1 in jurul cilindrului
      const isFront = angleFrac <= 0.5;
      const [u0, u1, v0, v1] = isFront ? [fu0, fu1, fv0, fv1] : [bu0, bu1, bv0, bv1];
      const localU = isFront ? angleFrac / 0.5 : (angleFrac - 0.5) / 0.5;
      uvAttr.setXY(idx, u0 + localU * (u1 - u0), v0 + (1 - v) * (v1 - v0));
    }
  }
  uvAttr.needsUpdate = true;
}

// ---------- geometrii UV-mapate pentru tricou (torso + 2 brate) ----------

export function buildShirtGeometry() {
  const torso = new THREE.CylinderGeometry(0.26, 0.22, 0.6, 16, 4, true);
  remapCylinderUV(torso, SHIRT_REGIONS[0], SHIRT_REGIONS[1], 16, 4); // chest / back

  const armL = new THREE.CylinderGeometry(0.085, 0.075, 0.55, 12, 3, true);
  remapCylinderUV(armL, SHIRT_REGIONS[2], SHIRT_REGIONS[2], 12, 3); // arm_left pe tot cilindrul

  const armR = new THREE.CylinderGeometry(0.085, 0.075, 0.55, 12, 3, true);
  remapCylinderUV(armR, SHIRT_REGIONS[3], SHIRT_REGIONS[3], 12, 3); // arm_right

  return { torso, armL, armR };
}

// ---------- geometrii UV-mapate pentru pantaloni (2 picioare) ----------

export function buildPantsGeometry() {
  const legL = new THREE.CylinderGeometry(0.1, 0.09, 0.62, 12, 4, true);
  remapCylinderUV(legL, PANTS_REGIONS[0], PANTS_REGIONS[1], 12, 4); // leg_left front/back

  const legR = new THREE.CylinderGeometry(0.1, 0.09, 0.62, 12, 4, true);
  remapCylinderUV(legR, PANTS_REGIONS[2], PANTS_REGIONS[3], 12, 4); // leg_right front/back

  return { legL, legR };
}

// ---------- material comun ----------

export function textureFromDataUrl(dataUrl: string): Promise<THREE.Texture> {
  return new Promise((resolve, reject) => {
    const loader = new THREE.TextureLoader();
    loader.load(
      dataUrl,
      tex => {
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.flipY = true; // consistent cu maparea de mai sus (V=0 jos)
        resolve(tex);
      },
      undefined,
      reject
    );
  });
}