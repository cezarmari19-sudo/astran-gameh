// frontend/src/studio/modelAssets.ts
// Geometria REALA a modelelor din Shop puse in scena (obiecte type === "model", vezi sceneShared.ts).
//
// Datele vin de la server din acelasi resolver ca Assets.load(id) (backend: resolve_assets), cu piesele
// modelului (`parts`). Aici le transformam intr-un singur BufferGeometry cu culori pe varfuri, ca un
// obiect de model sa se comporte ca ORICE alt obiect din scena (un singur Mesh in meshMap): se poate
// selecta, muta, roti, scala, sterge si salva cu aceleasi unelte ca un cub.
//
// Conventie (identica cu applyTransform din sceneShared.ts): la scale 1 baza modelului sta pe y = obj.y,
// centrat pe x/z => geometria are centrul bazei in origine si e translatata cu -0.5 pe y fata de centrul
// ei pe inaltime 1 (applyTransform urca pozitia cu 0.5 * sy).
import * as THREE from "three";
import { normalizeParts, worldMatrix, createObject, Part } from "@/src/studio3d/modelTypes";

export type ModelGeometry = {
  geometry: THREE.BufferGeometry;
  size: [number, number, number]; // latime, inaltime, adancime la scale 1
  name?: string;
};

const cache = new Map<string, ModelGeometry>();

function visibleDeep(parts: Part[], p: Part): boolean {
  const by = new Map(parts.map(x => [x.id, x]));
  let cur: Part | undefined = p;
  while (cur) {
    if (!cur.visible) return false;
    cur = cur.parent ? by.get(cur.parent) : undefined;
  }
  return true;
}

/** Combina toate piesele vizibile ale unui model intr-un singur geometry. null daca nu exista nicio forma. */
export function buildModelGeometry(rawParts: any): ModelGeometry | null {
  const parts = normalizeParts(rawParts);
  const pos: number[] = [];
  const nor: number[] = [];
  const col: number[] = [];

  for (const p of parts) {
    if (p.type === "group" || !visibleDeep(parts, p)) continue;
    const obj = createObject(p) as THREE.Mesh;
    if (!obj.isMesh) continue;
    let g = obj.geometry as THREE.BufferGeometry;
    g = g.index ? g.toNonIndexed() : g.clone();
    g.applyMatrix4(worldMatrix(parts, p.id));
    if (!g.getAttribute("normal")) g.computeVertexNormals();
    const P = g.getAttribute("position");
    const N = g.getAttribute("normal");
    const c = new THREE.Color(p.color);
    for (let i = 0; i < P.count; i++) {
      pos.push(P.getX(i), P.getY(i), P.getZ(i));
      nor.push(N.getX(i), N.getY(i), N.getZ(i));
      col.push(c.r, c.g, c.b);
    }
    g.dispose();
    obj.geometry.dispose();
    (obj.material as THREE.Material).dispose();
  }
  if (pos.length === 0) return null;

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  geometry.computeBoundingBox();
  const bb = geometry.boundingBox!;
  const size: [number, number, number] = [
    Math.max(0.01, bb.max.x - bb.min.x),
    Math.max(0.01, bb.max.y - bb.min.y),
    Math.max(0.01, bb.max.z - bb.min.z),
  ];
  // centru pe x/z, baza la -0.5 (vezi conventia de sus)
  geometry.translate(-(bb.min.x + bb.max.x) / 2, -bb.min.y - 0.5, -(bb.min.z + bb.max.z) / 2);
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  return { geometry, size };
}

/** Inregistreaza (sau reinnoieste) geometria unui model dupa id-ul lui real din Shop. */
export function registerModelAsset(assetId: string, rawParts: any, name?: string): ModelGeometry | null {
  const built = buildModelGeometry(rawParts);
  if (!built) return null;
  built.name = name;
  const old = cache.get(assetId);
  if (old) old.geometry.dispose();
  cache.set(assetId, built);
  return built;
}

export function getModelAsset(assetId: string | undefined): ModelGeometry | undefined {
  return assetId ? cache.get(assetId) : undefined;
}

export function hasModelAsset(assetId: string | undefined): boolean {
  return !!assetId && cache.has(assetId);
}

export function clearModelAssets() {
  cache.forEach(m => m.geometry.dispose());
  cache.clear();
}

/**
 * Incarca de la server geometria modelelor din Shop referite de o scena (obiecte type === "model"),
 * prin /sandbox/games/{id}/scene-models - acelasi resolver ca Assets.load. Intoarce numele/id-urile
 * modelelor care NU s-au putut incarca (lista goala = totul ok). Nu arunca niciodata.
 */
export async function loadSceneModelAssets(
  fetchJson: (path: string) => Promise<any>,
  gameId: string,
  scene: { objects?: any[] } | null | undefined,
): Promise<string[]> {
  const wanted = (scene?.objects || []).filter(o => o && o.type === "model" && typeof o.assetId === "string");
  if (wanted.length === 0) return [];
  const failed: string[] = [];
  try {
    const r = await fetchJson(`/sandbox/games/${gameId}/scene-models`);
    const byId = new Map<string, any>((Array.isArray(r?.models) ? r.models : []).map((m: any) => [m.id, m]));
    for (const o of wanted) {
      const m = byId.get(o.assetId);
      if (!m || !registerModelAsset(m.id, m.parts, m.name)) failed.push(o.name || o.assetId);
    }
  } catch {
    return wanted.map(o => o.name || o.assetId);
  }
  return Array.from(new Set(failed));
}