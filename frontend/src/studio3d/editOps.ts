// frontend/src/studio3d/editOps.ts
// Unelte din Edit Mode: descriere (butoane + parametri pentru slidere) si executie pe geometria reala.
// Fisier pur: tot ce face un buton modifica efectiv MeshData-ul piesei.
import {
  MeshData, OpResult, meshWithinLimits, MESH_LIMITS, extrudeFaces, extrudeEdges, insetFaces, bevelEdges, loopCut, subdivideFaces,
  mergeVerts, mergeByDistance, deleteVerts, deleteEdges, deleteFaces, fillFace, flipFaces, mirrorX,
} from "./meshData";
import { EditSel, selVerts, selEdges, selFaces, selFromResult, emptySel, orderLoop, selectAll } from "./editSelection";

export type ToolId = "extrude" | "inset" | "bevel" | "loopcut" | "subdivide" | "merge" | "weld" | "delete" | "fill" | "flip" | "mirror";
export type ToolParam = { key: string; label: string; min: number; max: number; step: number; def: number; int?: boolean; toggle?: boolean };
export type ToolDef = { id: ToolId; label: string; icon: string; live: boolean; params: ToolParam[]; hint: string };

export const TOOLS: ToolDef[] = [
  { id: "extrude", label: "Extrude", icon: "arrow-expand-up", live: true, hint: "Face or edge",
    params: [{ key: "dist", label: "Distance", min: -3, max: 3, step: 0.05, def: 0.3 }] },
  { id: "inset", label: "Inset", icon: "vector-square-minus", live: true, hint: "Faces",
    params: [
      { key: "amount", label: "Amount", min: 0, max: 1, step: 0.01, def: 0.1 },
      { key: "depth", label: "Depth", min: -1, max: 1, step: 0.02, def: 0 },
      { key: "individual", label: "Individual", min: 0, max: 1, step: 1, def: 0, toggle: true },
    ] },
  { id: "bevel", label: "Bevel", icon: "vector-polyline-edit", live: true, hint: "Edges (shared by 2 faces)",
    params: [
      { key: "width", label: "Width", min: 0.005, max: 1, step: 0.005, def: 0.1 },
      { key: "segments", label: "Segments", min: 1, max: 8, step: 1, def: 2, int: true },
    ] },
  { id: "loopcut", label: "Loop Cut", icon: "vector-line", live: true, hint: "Select an edge",
    params: [
      { key: "cuts", label: "Cuts", min: 1, max: 10, step: 1, def: 1, int: true },
      { key: "slide", label: "Slide", min: -0.9, max: 0.9, step: 0.02, def: 0 },
    ] },
  { id: "subdivide", label: "Subdivide", icon: "grid", live: false, hint: "Faces", params: [] },
  { id: "merge", label: "Merge", icon: "set-merge", live: false, hint: "2+ vertices → center", params: [] },
  { id: "weld", label: "Weld", icon: "vector-combine", live: true, hint: "Merge by distance",
    params: [{ key: "dist", label: "Distance", min: 0.001, max: 0.5, step: 0.001, def: 0.02 }] },
  { id: "delete", label: "Delete", icon: "trash-can-outline", live: false, hint: "Selection", params: [] },
  { id: "fill", label: "Fill", icon: "shape-polygon-plus", live: false, hint: "3+ vertices (in tap order)", params: [] },
  { id: "flip", label: "Flip", icon: "swap-vertical-bold", live: false, hint: "Faces", params: [] },
  { id: "mirror", label: "Mirror X", icon: "flip-horizontal", live: false, hint: "Keeps the +X half", params: [] },
];
export const toolDef = (id: ToolId) => TOOLS.find(t => t.id === id)!;
export const defaultParams = (id: ToolId): Record<string, number> => Object.fromEntries(toolDef(id).params.map(p => [p.key, p.def]));

export type ToolOutcome = { ok: true; mesh: MeshData; sel: EditSel } | { ok: false; error: string };
const fail = (error: string): ToolOutcome => ({ ok: false, error });

function done(mesh: MeshData, sel: EditSel, res: OpResult | null, empty = false): ToolOutcome {
  if (!res) return fail("Nothing changed");
  if (res.mesh.f.length === 0) return fail("That would delete the whole mesh");
  if (!meshWithinLimits(res.mesh)) return fail(`Mesh too detailed (max ${MESH_LIMITS.maxFaces} faces)`);
  return { ok: true, mesh: res.mesh, sel: empty ? emptySel(sel.mode) : selFromResult(res.mesh, sel.mode, res) };
}

export function runTool(id: ToolId, m: MeshData, sel: EditSel, p: Record<string, number>): ToolOutcome {
  switch (id) {
    case "extrude": {
      const faces = selFaces(m, sel);
      if (faces.length > 0) return done(m, sel, extrudeFaces(m, faces, p.dist ?? 0.3));
      const edges = selEdges(m, sel);
      if (edges.length > 0) return done(m, sel, extrudeEdges(m, edges, p.dist ?? 0.3));
      return fail("Select a face or an edge to extrude");
    }
    case "inset": {
      const faces = selFaces(m, sel);
      if (faces.length === 0) return fail("Select one or more faces to inset");
      const r = insetFaces(m, faces, p.amount ?? 0.1, p.depth ?? 0, (p.individual ?? 0) >= 0.5);
      return r.selF.length === 0 ? fail("Nothing changed") : done(m, sel, r);
    }
    case "bevel": {
      const edges = selEdges(m, sel);
      if (edges.length === 0) return fail("Select edges to bevel");
      const r = bevelEdges(m, edges, p.width ?? 0.1, p.segments ?? 2);
      return r ? done(m, sel, r) : fail("Bevel needs edges shared by two faces");
    }
    case "loopcut": {
      const edges = selEdges(m, sel);
      if (edges.length === 0) return fail("Select an edge to cut a loop through");
      const r = loopCut(m, edges.slice(0, 8), p.cuts ?? 1, p.slide ?? 0);
      return r ? done(m, { ...sel, mode: sel.mode === "face" ? "edge" : sel.mode }, r) : fail("Loop cut failed");
    }
    case "subdivide": {
      const faces = selFaces(m, sel);
      if (faces.length === 0) return fail("Select faces to subdivide");
      return done(m, sel, subdivideFaces(m, faces));
    }
    case "merge": {
      const verts = selVerts(m, sel);
      if (verts.length < 2) return fail("Select 2 or more vertices to merge");
      return done(m, sel, mergeVerts(m, verts, "center"));
    }
    case "weld": {
      const verts = selVerts(m, sel);
      return done(m, sel, mergeByDistance(m, p.dist ?? 0.02, verts.length > 1 ? verts : undefined));
    }
    case "delete": {
      if (sel.mode === "vertex") return sel.v.length ? done(m, sel, deleteVerts(m, sel.v), true) : fail("Select vertices to delete");
      if (sel.mode === "edge") return sel.e.length ? done(m, sel, deleteEdges(m, sel.e), true) : fail("Select edges to delete");
      return sel.f.length ? done(m, sel, deleteFaces(m, sel.f), true) : fail("Select faces to delete");
    }
    case "fill": {
      const order = sel.mode === "vertex" ? sel.v : sel.mode === "edge" ? orderLoop(sel.e) : null;
      if (!order || order.length < 3) return fail("Select 3+ vertices (or a closed edge loop) to fill");
      const r = fillFace(m, order);
      return r ? done(m, { ...sel, mode: sel.mode === "vertex" ? "face" : sel.mode }, r) : fail("Could not create the face");
    }
    case "flip": {
      const faces = selFaces(m, sel);
      if (faces.length === 0) return fail("Select faces to flip");
      return { ok: true, mesh: flipFaces(m, faces).mesh, sel };
    }
    case "mirror": return done(m, sel, mirrorX(m), true);
  }
}

export { selectAll };