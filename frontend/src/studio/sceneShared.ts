// frontend/src/studio/sceneShared.ts
// Tipuri si functii folosite si de editor (studio/create.tsx), si de jocul propriu-zis (play/[id].tsx).
import * as THREE from "three";
import { getModelAsset } from "@/src/studio/modelAssets";

export type ObjType = "cube" | "sphere" | "cylinder" | "cone" | "tree" | "spawn";

// "spawn" e un tip generic de marker (nu de gameplay obisnuit): acopera atat Spawn
// Point-urile cat si Checkpoint-urile, diferentiate prin campul spawnKind de mai jos.
// Le tratam la fel din punct de vedere al tipului (SPAWN_TYPE) ca sa nu stricam nimic
// din ce depinde deja de el (fizica, runner-ul Luau, filtrarea din Studio) - doar
// comportamentul lor in Play difera dupa spawnKind.
export type SpawnKind = "spawn" | "checkpoint";

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
  name?: string;
  // ---------- Modele din Shop (doar cand type === "model") ----------
  // assetId = item_id-ul REAL din Shop (acelasi id ca la Assets.load("id")); geometria se incarca de la server
  // prin acelasi resolver ca Assets.load - vezi studio/modelAssets.ts. ex/ey/ez = dimensiunea naturala a
  // modelului (la scale 1), salvata ca sa putem calcula coliziunea in Play fara sa asteptam geometria.
  assetId?: string;
  ex?: number;
  ey?: number;
  ez?: number;
  visible?: boolean; // implicit true; daca false, nu se randeaza in Play (dar tot exista in Studio, semi-transparent)
  solid?: boolean;   // implicit true pentru obiecte normale, false pentru spawn; controleaza collision in Play

  // ---------- Spawn Points & Checkpoints (doar cand type === "spawn") ----------
  // Vezi si play/[id].tsx pentru logica de spawn initial / checkpoint activ / respawn,
  // si studio/create.tsx + studio/Inspector.tsx pentru UI-ul din Game Studio.
  spawnKind?: SpawnKind; // implicit "spawn" - pastreaza compatibilitatea cu Spawn Point-urile vechi (dinainte de checkpoint-uri si spawn-uri multiple)
  initial?: boolean;     // doar cand spawnKind === "spawn": acesta e spawn-ul initial al jocului (primul loc unde apare playerul)? Un singur obiect ar trebui sa aiba true la un moment dat - vezi setInitialSpawn() din create.tsx, care garanteaza asta
  enabled?: boolean;     // implicit true; daca false, punctul nu functioneaza deloc in Play (nu e ales ca spawn initial, nu se activeaza ca checkpoint), indiferent de Visible/Solid
  teamId?: string;       // rezervat pentru reguli viitoare de spawn pe echipe/grupuri (spawn doar pentru echipa X) - neimplementat inca, dar campul exista ca arhitectura sa nu presupuna un singur spawn "universal"
};

// BASEPLATE: groundWidth/groundDepth sunt proprietati REALE ale scenei (ca sky/ground),
// NU un obiect din `objects[]` - intentionat, ca sa nu poata fi niciodata prins accidental
// in selectie multipla, duplicare, rotatie sau scalare uniforma ca un cub oarecare. Se
// editeaza DOAR printr-un panou dedicat (vezi BaseplateInspector din studio/create.tsx).
// Lipsa lor (jocuri vechi) cade pe DEFAULT_GROUND_WIDTH/DEPTH de mai jos - comportament
// identic cu gridul fix de dinainte de aceasta functionalitate.
export type Scene = {
  objects: SceneObj[];
  sky: string;
  ground: string;
  groundWidth?: number;
  groundDepth?: number;
};

export const GROUND_MIN = 4;
export const GROUND_MAX = 2000;
export const DEFAULT_GROUND_WIDTH = 100;
export const DEFAULT_GROUND_DEPTH = 100;

export function groundWidthOf(s: Pick<Scene, "groundWidth">): number {
  const v = s.groundWidth;
  return typeof v === "number" && v > 0 ? v : DEFAULT_GROUND_WIDTH;
}

export function groundDepthOf(s: Pick<Scene, "groundDepth">): number {
  const v = s.groundDepth;
  return typeof v === "number" && v > 0 ? v : DEFAULT_GROUND_DEPTH;
}

export function clampGroundSize(v: number): number {
  if (!Number.isFinite(v)) return DEFAULT_GROUND_WIDTH;
  return Math.max(GROUND_MIN, Math.min(GROUND_MAX, Math.round(v)));
}

// Geometria reala a Baseplate-ului - un segment pe unitate (pastreaza densitatea gridului
// de dinainte: 20x20 unitati = 20x20 segmente), plafonat ca sa nu generam zeci de mii de
// triunghiuri pentru un Baseplate foarte mare.
const MAX_GRID_SEGMENTS = 200;

export function buildGroundGeometry(width: number, depth: number): THREE.PlaneGeometry {
  const segX = Math.max(1, Math.min(MAX_GRID_SEGMENTS, Math.round(width)));
  const segZ = Math.max(1, Math.min(MAX_GRID_SEGMENTS, Math.round(depth)));
  return new THREE.PlaneGeometry(width, depth, segX, segZ);
}

export const PALETTE = ["#CCFF00", "#FF3366", "#00E5FF", "#FFD500", "#00FF66", "#FF9500", "#B266FF", "#FFFFFF", "#666666"];

// "spawn" e un marker special, nu un obiect de gameplay obisnuit: nu apare in lista ADD alaturi
// de restul formelor cu acelasi tratament - vezi SPAWN_TYPE mai jos si tratarea lui separata in Studio.
export const OBJ_TYPES: ObjType[] = ["cube", "sphere", "cylinder", "cone", "tree"];
export const SPAWN_TYPE: ObjType = "spawn";

// Culorile disctinctive pentru cele doua "specii" de marker spawn - folosite in Studio (disc-ul
// semi-transparent) si in Play (daca creatorul alege sa le faca Visible).
export const SPAWN_COLOR = "#CCFF00";
export const CHECKPOINT_COLOR = "#00E5FF";

const OBJ_ICON: Record<string, string> = {
  cube: "cube-outline",
  sphere: "circle-outline",
  cylinder: "cylinder",
  cone: "triangle-outline",
  tree: "pine-tree",
  model: "package-variant-closed",
  spawn: "map-marker-radius-outline",
};

const CHECKPOINT_ICON = "flag-checkered";

export function iconFor(type: string): string {
  return OBJ_ICON[type] ?? "cube-outline";
}

// Normalizeaza spawnKind (implicit "spawn" daca lipseste - Spawn Point-urile vechi, salvate
// inainte de aceasta functionalitate, nu au campul si trebuie sa se comporte ca inainte).
export function spawnKindOf(o: Pick<SceneObj, "spawnKind">): SpawnKind {
  return o.spawnKind === "checkpoint" ? "checkpoint" : "spawn";
}

export function isCheckpoint(o: Pick<SceneObj, "type" | "spawnKind">): boolean {
  return o.type === SPAWN_TYPE && spawnKindOf(o) === "checkpoint";
}

export function isSpawnPoint(o: Pick<SceneObj, "type" | "spawnKind">): boolean {
  return o.type === SPAWN_TYPE && spawnKindOf(o) === "spawn";
}

// Icon-ul corect pentru un obiect din scena: Spawn Point si Checkpoint au acelasi `type`
// ("spawn"), dar icoane diferite dupa spawnKind - de-aia foloseste obiectul intreg, nu doar type.
export function iconForObj(o: Pick<SceneObj, "type" | "spawnKind">): string {
  if (o.type === SPAWN_TYPE) return isCheckpoint(o) ? CHECKPOINT_ICON : OBJ_ICON.spawn;
  return iconFor(o.type);
}

// Culoarea "de marker" a unui Spawn Point / Checkpoint (ignora obj.color - la fel ca inainte,
// cand orice spawn era mereu galben-verde; acum verde = spawn, cyan = checkpoint).
export function spawnMarkerColor(o: Pick<SceneObj, "type" | "spawnKind">): string {
  return isCheckpoint(o) ? CHECKPOINT_COLOR : SPAWN_COLOR;
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

export const MODEL_TYPE = "model";

// Obiect de model din Shop a carui geometrie nu s-a incarcat (inca / deloc): in Studio ramane o cutie rosie
// semi-transparenta, selectabila si stergabila, ca eroarea sa fie vizibila - niciodata un model "fals" care
// pare ca a mers.
function buildModelMesh(o: SceneObj): THREE.Mesh {
  const asset = getModelAsset(o.assetId);
  if (asset) {
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, color: new THREE.Color("#ffffff"), roughness: 0.6, metalness: 0.05 });
    const m = new THREE.Mesh(asset.geometry.clone(), mat);
    m.userData.modelReady = true;
    applyTransform(m, o);
    return m;
  }
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color("#ff3355"), transparent: true, opacity: 0.35, roughness: 0.8 });
  const box = new THREE.BoxGeometry(o.ex ?? 1, o.ey ?? 1, o.ez ?? 1);
  box.translate(0, ((o.ey ?? 1) - 1) / 2, 0); // baza pe y = obj.y, ca la modelul real (vezi applyTransform)
  const m = new THREE.Mesh(box, mat);
  m.userData.modelReady = false;
  applyTransform(m, o);
  return m;
}

export function buildMesh(o: SceneObj): THREE.Mesh {
  if (o.type === MODEL_TYPE) return buildModelMesh(o);
  const isSpawn = o.type === "spawn";
  const mat = new THREE.MeshStandardMaterial({
    // culoare invalida (ex. un string arbitrar dintr-o scena veche) cade pe gri, in loc sa arunce / sa umple consola
    color: new THREE.Color(isSpawn ? spawnMarkerColor(o) : (/^#[0-9a-fA-F]{3,8}$/.test(String(o.color)) ? o.color : "#a3a3a3")),
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
  if (o.type === MODEL_TYPE) {
    const hx = 0.5 * (o.ex ?? 1) * sx;
    const hy = 0.5 * (o.ey ?? 1) * sy;
    const hz = 0.5 * (o.ez ?? 1) * sz;
    return { minX: o.x - hx, maxX: o.x + hx, minY: o.y, maxY: o.y + 2 * hy, minZ: o.z - hz, maxZ: o.z + hz };
  }
  const baseHalf = o.type === "sphere" ? 0.6 : o.type === "cylinder" ? 0.5 : o.type === "cone" ? 0.6 : o.type === "tree" ? 0.7 : 0.5;
  const hx = baseHalf * sx;
  const hy = 0.5 * sy;
  const hz = baseHalf * sz;
  const cy = o.y + hy;
  return { minX: o.x - hx, maxX: o.x + hx, minY: o.y, maxY: cy + hy, minZ: o.z - hz, maxZ: o.z + hz };
}