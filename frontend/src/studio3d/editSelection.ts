// frontend/src/studio3d/editSelection.ts
// Selectia din Edit Mode (varfuri / muchii / fete) + conversii intre moduri. Fisier pur, testabil.
import {
  MeshData, OpResult, vertCount, allVerts, allFaces, allEdgeKeys, edgesOfVerts, facesOfVerts, facesOfEdges, edgesOfFaces,
  vertsOfEdges, vertsOfFaces, edgeKey, parseEdge,
} from "./meshData";

export type SelMode = "vertex" | "edge" | "face";
// Doar lista modului activ conteaza (v = in ordinea in care au fost atinse, util pentru Fill)
export type EditSel = { mode: SelMode; v: number[]; e: string[]; f: number[] };

export const emptySel = (mode: SelMode = "vertex"): EditSel => ({ mode, v: [], e: [], f: [] });
export const selCount = (s: EditSel) => (s.mode === "vertex" ? s.v.length : s.mode === "edge" ? s.e.length : s.f.length);

export function selVerts(m: MeshData, s: EditSel): number[] {
  return s.mode === "vertex" ? s.v : s.mode === "edge" ? vertsOfEdges(s.e) : vertsOfFaces(m, s.f);
}
export function selEdges(m: MeshData, s: EditSel): string[] {
  return s.mode === "edge" ? s.e : s.mode === "face" ? edgesOfFaces(m, s.f) : edgesOfVerts(m, s.v);
}
export function selFaces(m: MeshData, s: EditSel): number[] {
  return s.mode === "face" ? s.f : s.mode === "edge" ? facesOfEdges(m, s.e) : facesOfVerts(m, s.v);
}

export function convertMode(m: MeshData, s: EditSel, mode: SelMode): EditSel {
  if (s.mode === mode) return s;
  if (mode === "vertex") return { mode, v: selVerts(m, s), e: [], f: [] };
  if (mode === "edge") return { mode, v: [], e: selEdges(m, s), f: [] };
  return { mode, v: [], e: [], f: selFaces(m, s) };
}

export function toggleElement(s: EditSel, id: number | string, additive: boolean): EditSel {
  if (s.mode === "vertex") {
    const v = additive ? (s.v.includes(id as number) ? s.v.filter(x => x !== id) : [...s.v, id as number]) : [id as number];
    return { ...s, v };
  }
  if (s.mode === "edge") {
    const e = additive ? (s.e.includes(id as string) ? s.e.filter(x => x !== id) : [...s.e, id as string]) : [id as string];
    return { ...s, e };
  }
  const f = additive ? (s.f.includes(id as number) ? s.f.filter(x => x !== id) : [...s.f, id as number]) : [id as number];
  return { ...s, f };
}

export function selectAll(m: MeshData, mode: SelMode): EditSel {
  return mode === "vertex" ? { mode, v: allVerts(m), e: [], f: [] } : mode === "edge" ? { mode, v: [], e: allEdgeKeys(m), f: [] } : { mode, v: [], e: [], f: allFaces(m) };
}

export function invertSel(m: MeshData, s: EditSel): EditSel {
  if (s.mode === "vertex") { const set = new Set(s.v); return { ...s, v: allVerts(m).filter(i => !set.has(i)) }; }
  if (s.mode === "edge") { const set = new Set(s.e); return { ...s, e: allEdgeKeys(m).filter(k => !set.has(k)) }; }
  const set = new Set(s.f);
  return { ...s, f: allFaces(m).filter(i => !set.has(i)) };
}

// Selectia dupa o operatie: operatiile intorc varfurile/fetele noi; le aratam in modul curent
export function selFromResult(m: MeshData, mode: SelMode, res: OpResult): EditSel {
  if (mode === "vertex") return { mode, v: res.selV, e: [], f: [] };
  if (mode === "edge") return { mode, v: [], e: edgesOfVerts(m, res.selV), f: [] };
  const f = res.selF.length > 0 ? res.selF : facesOfVerts(m, res.selV);
  return { mode, v: [], e: [], f };
}

// Dupa Undo/Redo indicii pot sa nu mai fie valizi: pastram doar ce exista. Intoarce acelasi obiect daca nu s-a schimbat nimic.
export function sanitizeSel(m: MeshData, s: EditSel): EditSel {
  const nv = vertCount(m), nf = m.f.length;
  if (s.mode === "vertex") {
    const v = s.v.filter(i => i >= 0 && i < nv);
    return v.length === s.v.length ? s : { ...s, v };
  }
  if (s.mode === "face") {
    const f = s.f.filter(i => i >= 0 && i < nf);
    return f.length === s.f.length ? s : { ...s, f };
  }
  const existing = new Set(allEdgeKeys(m));
  const e = s.e.filter(k => existing.has(k));
  return e.length === s.e.length ? s : { ...s, e };
}

// Ordoneaza muchiile selectate intr-un lant/ciclu de varfuri (pentru Fill). null daca nu formeaza un singur lant simplu.
export function orderLoop(keys: string[]): number[] | null {
  if (keys.length < 2) return null;
  const adj = new Map<number, number[]>();
  keys.forEach(k => {
    const [a, b] = parseEdge(k);
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a)!.push(b); adj.get(b)!.push(a);
  });
  for (const l of adj.values()) if (l.length > 2) return null;
  const start = Array.from(adj.keys()).find(k => adj.get(k)!.length === 1) ?? Array.from(adj.keys())[0];
  const order = [start];
  const used = new Set<string>();
  let cur = start;
  for (;;) {
    const nxt = adj.get(cur)!.find(n => !used.has(edgeKey(cur, n)));
    if (nxt === undefined) break;
    used.add(edgeKey(cur, nxt));
    if (nxt === start) break;
    order.push(nxt);
    cur = nxt;
  }
  return used.size === keys.length ? order : null;
}