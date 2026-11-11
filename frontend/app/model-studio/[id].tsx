import React, { useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, Modal, Platform, ActivityIndicator, Alert, BackHandler, useWindowDimensions } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { PanGestureHandler, PinchGestureHandler, TapGestureHandler, State } from "react-native-gesture-handler";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { useHistory } from "@/src/studio3d/useHistory";
import Outliner from "@/src/studio3d/Outliner";
import PropertiesPanel from "@/src/studio3d/PropertiesPanel";
import {
  Gizmo, GizmoMode, HandleInfo, Axis, UP,
  createGizmo, updateGizmo, pickHandle, axisVector, closestParamOnAxis, planeHit, planeAngle, wrapPi, eulerNear,
} from "@/src/studio3d/gizmo";
import {
  Part, PartType, ROOT_ID, SHAPES, PART_ICON, PART_LABEL,
  newRootPart, newPart, uniqueName, normalizeParts, countShapes, byIdMap, descendantIds, topLevel, outermost, flatten,
  reparent, groupParts, ungroup, duplicateParts, deleteParts,
  createObject, disposeObject, applyLocalTransform, applyMaterial, isVisibleDeep, round3,
  convertToMesh, joinParts, syncMeshGeometry,
} from "@/src/studio3d/modelTypes";
import { MeshData, centroid, mapVerts, vertCount, buildTopology } from "@/src/studio3d/meshData";
import {
  EditSel, SelMode, emptySel, selCount, selVerts, convertMode, toggleElement, selectAll, invertSel, sanitizeSel,
} from "@/src/studio3d/editSelection";
import { ToolId, toolDef, defaultParams, runTool } from "@/src/studio3d/editOps";
import { createEditOverlay, EditOverlay, pickVertex, pickEdge, pickFace } from "@/src/studio3d/editOverlay";
import EditPanel from "@/src/studio3d/EditPanel";

type Dock = "add" | "tree" | "props" | null;

const DELTA_KEYS = ["x", "y", "z", "rx", "ry", "rz"];
const RATIO_KEYS = ["sx", "sy", "sz"];
const LOOK_KEYS = ["color", "material", "opacity"];
const RAD = 180 / Math.PI;

const MOVE_SNAP = 0.25;
const ROT_SNAP = Math.PI / 12; // 15 grade

const MODES: { key: GizmoMode; icon: string }[] = [
  { key: "move", icon: "cursor-move" },
  { key: "rotate", icon: "rotate-3d-variant" },
  { key: "scale", icon: "resize" },
];

// ---------- tipuri pentru gesturile de transformare ----------
type DragItem = {
  id: string;
  part: Part; // starea de la inceputul gestului
  parentInv: THREE.Matrix4;
  parentQInv: THREE.Quaternion;
  worldPos: THREE.Vector3;
  worldQ: THREE.Quaternion;
  rot: { rx: number; ry: number; rz: number }; // ultima rotatie scrisa (pentru continuitate)
};
type ManipBase = { items: DragItem[]; pivot: THREE.Vector3 };
type Manip =
  | (ManipBase & { kind: "axis-move"; dir: THREE.Vector3; t0: number })
  | (ManipBase & { kind: "plane-move"; p0: THREE.Vector3 })
  | (ManipBase & { kind: "rotate"; dir: THREE.Vector3; angPrev: number; angAcc: number })
  | (ManipBase & { kind: "axis-scale"; axis: Axis; dir: THREE.Vector3; t0: number })
  | (ManipBase & { kind: "uniform-scale"; y0: number });
// Edit Mode: gizmo-ul actioneaza pe varfurile selectate ale mesh-ului piesei editate
type EditManip = {
  kind: "edit";
  sub: "axis-move" | "plane-move" | "rotate" | "axis-scale" | "uniform-scale";
  id: string; mesh: MeshData; verts: number[];
  W: THREE.Matrix4; Winv: THREE.Matrix4;
  pivot: THREE.Vector3;
  dir: THREE.Vector3; t0: number; p0: THREE.Vector3; angPrev: number; angAcc: number; y0: number;
};
type ActiveOp = { id: ToolId; params: Record<string, number>; baseMesh: MeshData; baseSel: EditSel; partId: string };
type Drag = { kind: "camera" } | Manip | EditManip;
type Pending = { type: "handle"; handle: HandleInfo } | { type: "ground" } | { type: "camera" };

function Action({ icon, label, onPress, disabled, active, danger }: {
  icon: string; label: string; onPress: () => void; disabled?: boolean; active?: boolean; danger?: boolean;
}) {
  return (
    <Pressable onPress={onPress} disabled={disabled} style={[styles.action, active && styles.actionActive, disabled && { opacity: 0.35 }]}>
      <MaterialCommunityIcons name={icon as any} size={18} color={danger ? colors.error : active ? colors.brand : colors.onSurface} />
      <Text style={[styles.actionText, danger && { color: colors.error }, active && { color: colors.brand }]}>{label}</Text>
    </Pressable>
  );
}

export default function ModelStudio() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id?: string }>();
  const routeId = params.id && params.id !== "new" ? params.id : null;
  const { height: winH } = useWindowDimensions();

  // ---------- modelul (cu Undo/Redo) ----------
  const initialParts = useRef<Part[]>([newRootPart("Model")]).current;
  const hist = useHistory<Part[]>(initialParts);
  const parts = hist.present;
  const [savedParts, setSavedParts] = useState<Part[]>(initialParts);
  const dirty = parts !== savedParts;
  const byId = useMemo(() => byIdMap(parts), [parts]);
  const modelId = useRef<string | null>(routeId);

  const [selIds, setSelIds] = useState<string[]>([]);
  const [multi, setMulti] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dock, setDock] = useState<Dock>("add");
  const [loading, setLoading] = useState(!!routeId);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [glReady, setGlReady] = useState(false);
  const [showMove, setShowMove] = useState(false);
  const [mode, setMode] = useState<GizmoMode>("move");
  const [snap, setSnap] = useState(false);

  // Edit Mode (modelare manuala a geometriei) - Object Mode ramane neschimbat
  const [editing, setEditing] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [editSel, setEditSel] = useState<EditSel>(emptySel("vertex"));
  const [xray, setXray] = useState(false);
  const [op, setOp] = useState<ActiveOp | null>(null);
  const [editMsg, setEditMsg] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);

  const selParts = selIds.map(id => byId.get(id)).filter((p): p is Part => !!p);
  const primary = selParts[selParts.length - 1];
  const operable = topLevel(parts, selIds); // selectia fara radacina si fara copii ai unor selectati
  const rootName = byId.get(ROOT_ID)?.name ?? "Model";
  const dockHeight = dock === "add" ? 168 : Math.max(240, Math.min(380, Math.round(winH * 0.4)));

  // referinte catre valorile curente (folosite de bucla de desenare, de gesturi si de dialoguri)
  const dirtyRef = useRef(false);
  const saveRef = useRef<() => Promise<boolean>>(async () => false);
  const leaveRef = useRef<() => void>(() => {});
  const partsRef = useRef<Part[]>(parts);
  const selIdsRef = useRef<string[]>([]);
  const modeRef = useRef<GizmoMode>("move");
  const snapRef = useRef(false);
  dirtyRef.current = dirty;
  partsRef.current = parts;
  selIdsRef.current = selIds;
  modeRef.current = mode;
  snapRef.current = snap;
  const editingRef = useRef(false);
  const editIdRef = useRef<string | null>(null);
  const editSelRef = useRef<EditSel>(editSel);
  const xrayRef = useRef(false);
  const opRef = useRef<ActiveOp | null>(null);
  const multiRef = useRef(false);
  const shiftRef = useRef(false);
  const overlayRef = useRef<EditOverlay | null>(null);
  const editPivotRef = useRef<THREE.Vector3 | null>(null);
  const exitEditRef = useRef<() => void>(() => {});
  editingRef.current = editing;
  editIdRef.current = editId;
  editSelRef.current = editSel;
  xrayRef.current = xray;
  opRef.current = op;
  multiRef.current = multi;
  const editPart = editId ? byId.get(editId) : undefined;
  const editMesh = editPart?.type === "mesh" ? editPart.mesh : undefined;

  // ---------- incarcare ----------
  useEffect(() => {
    if (!routeId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api(`/studio/models/${routeId}`);
        if (cancelled) return;
        const loaded = normalizeParts(r?.model?.parts);
        hist.reset(loaded);
        setSavedParts(loaded);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || "Could not load the model");
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [routeId]);

  // scoate din selectie ce nu mai exista (dupa stergere / undo)
  useEffect(() => {
    setSelIds(cur => {
      const next = cur.filter(id => byId.has(id));
      return next.length === cur.length ? cur : next;
    });
  }, [byId]);

  // ---------- salvare / iesire / publicare ----------
  async function save(): Promise<boolean> {
    setSaving(true);
    setErr(null);
    try {
      if (modelId.current) {
        await api(`/studio/models/${modelId.current}`, { method: "PUT", body: JSON.stringify({ parts }) });
      } else {
        const r = await api("/studio/models", { method: "POST", body: JSON.stringify({ parts }) });
        modelId.current = r?.model?.model_id || null;
      }
      setSavedParts(parts);
      return true;
    } catch (e: any) {
      setErr(e?.message || "Save failed");
      Alert.alert("Nu s-a putut salva", e?.message || "Încearcă din nou.");
      return false;
    } finally {
      setSaving(false);
    }
  }
  saveRef.current = save;

  function leave() {
    if (!dirtyRef.current) { router.back(); return; }
    Alert.alert("Modificări nesalvate", "Vrei să salvezi modelul înainte să ieși?", [
      { text: "Rămâi", style: "cancel" },
      { text: "Ieși fără salvare", style: "destructive", onPress: () => router.back() },
      { text: "Salvează", onPress: async () => { if (await saveRef.current()) router.back(); } },
    ]);
  }
  leaveRef.current = leave;

  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (editingRef.current) { exitEditRef.current(); return true; } // intai iesim din Edit Mode
      if (dirtyRef.current) { leaveRef.current(); return true; }
      return false;
    });
    return () => sub.remove();
  }, []);

  async function publish() {
    if (countShapes(parts) === 0) {
      Alert.alert("Model gol", "Adaugă cel puțin un obiect în Studio înainte să publici.");
      return;
    }
    const ok = dirty || !modelId.current ? await save() : true;
    if (ok && modelId.current) router.push({ pathname: "/shop", params: { publish: modelId.current } } as any);
  }

  // ---------- editare ----------
  function selectPart(id: string) {
    setSelIds(cur => (multi ? (cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id]) : [id]));
  }

  function defaultParent(): string {
    if (!primary) return ROOT_ID;
    return primary.type === "group" ? primary.id : primary.parent ?? ROOT_ID;
  }

  function addPart(type: PartType) {
    const p = newPart(type, defaultParent(), uniqueName(parts, PART_LABEL[type]));
    hist.set(cur => [...cur, p]);
    setSelIds([p.id]);
  }

  // Modificarea unei proprietati se aplica tuturor celor selectate:
  // pozitie/rotatie = se aduna diferenta, scala = se inmulteste raportul, restul = valoare noua.
  function patchSelected(patch: Partial<Part>) {
    if (!primary) return;
    const ids = new Set(selParts.map(p => p.id));
    const base = primary;
    const single = selParts.length === 1;
    hist.set(cur => cur.map(p => {
      if (!ids.has(p.id)) return p;
      const next: any = { ...p };
      for (const key of Object.keys(patch) as (keyof Part)[]) {
        const v: any = patch[key];
        if (DELTA_KEYS.includes(key)) next[key] = round3((p[key] as number) + (v - (base[key] as number)));
        else if (RATIO_KEYS.includes(key)) {
          const b = base[key] as number;
          next[key] = b === 0 ? v : Math.max(0.01, round3((p[key] as number) * (v / b)));
        } else if (LOOK_KEYS.includes(key)) {
          if (p.type !== "group") next[key] = v;
        } else if (key === "name") {
          if (single) next.name = v;
        } else next[key] = v;
      }
      return next as Part;
    }));
  }

  function duplicateSel() {
    if (operable.length === 0) return;
    const r = duplicateParts(parts, selIds);
    hist.set(r.parts);
    setSelIds(r.newIds);
  }

  function deleteSel() {
    if (operable.length === 0) return;
    hist.set(deleteParts(parts, selIds));
    setSelIds([]);
  }

  function groupSel() {
    const r = groupParts(parts, selIds);
    if (!r) return;
    hist.set(r.parts);
    setSelIds([r.groupId]);
    setDock("tree");
  }

  function ungroupSel() {
    if (!primary || primary.type !== "group" || primary.id === ROOT_ID) return;
    hist.set(ungroup(parts, primary.id));
    setSelIds([]);
  }

  function moveSelTo(groupId: string) {
    hist.set(reparent(parts, selIds, groupId));
    setShowMove(false);
  }

  const moveTargets = useMemo(() => {
    const blocked = new Set<string>();
    topLevel(parts, selIds).forEach(id => {
      blocked.add(id);
      descendantIds(parts, id).forEach(d => blocked.add(d));
    });
    return flatten(parts, new Set()).filter(r => r.part.type === "group" && !blocked.has(r.part.id));
  }, [parts, selIds, showMove]);

  // ---------- Object Mode <-> Edit Mode ----------
  function flash(msg: string) {
    setEditMsg(msg);
    setTimeout(() => setEditMsg(cur => (cur === msg ? null : cur)), 2600);
  }

  function enterEdit() {
    if (editingRef.current) return;
    const target = primary && primary.type !== "group" ? primary : null;
    if (!target) {
      Alert.alert("Edit Mode", "Select a shape first (tap it in the viewport), then switch to Edit.");
      return;
    }
    // primitiva devine mesh editabil (aceeasi forma si aceeasi pozitie) - un pas normal de Undo
    if (target.type !== "mesh") hist.set(cur => cur.map(q => (q.id === target.id ? convertToMesh(q) : q)));
    setSelIds([target.id]);
    setEditId(target.id);
    setEditSel(emptySel("vertex"));
    setOp(null);
    setEditMsg(null);
    setEditing(true);
  }

  function exitEdit() {
    if (opRef.current) { hist.end(); setOp(null); } // operatia in curs se aplica, nu se pierde
    setEditing(false);
    setEditId(null);
  }
  exitEditRef.current = exitEdit;

  function switchMode(next: "object" | "edit") {
    if (next === "edit") enterEdit();
    else exitEdit();
  }

  // iesim din Edit Mode daca piesa dispare / nu mai e mesh (ex: Undo peste conversie)
  useEffect(() => {
    if (!editing) return;
    if (!editMesh) { setEditing(false); setEditId(null); setOp(null); }
  }, [editing, editMesh]);

  // dupa Undo/Redo indicii selectati pot sa nu mai existe
  useEffect(() => {
    if (!editing || !editMesh) return;
    setEditSel(cur => sanitizeSel(editMesh, cur));
  }, [editMesh, editing]);

  function setPartMesh(id: string, mesh: MeshData) {
    hist.set(cur => cur.map(q => (q.id === id ? { ...q, mesh } : q)));
  }

  function setSelMode(m: SelMode) {
    if (!editMesh || opRef.current) return;
    setEditSel(cur => convertMode(editMesh, cur, m));
  }

  function startTool(id: ToolId) {
    if (!editMesh || !editId || op) return;
    const def = toolDef(id);
    const params = defaultParams(id);
    const out = runTool(id, editMesh, editSel, params);
    if (!out.ok) { flash(out.error); return; }
    if (!def.live) { // unealta instantanee: un singur pas de Undo
      setPartMesh(editId, out.mesh);
      setEditSel(out.sel);
      return;
    }
    hist.begin(); // previzualizare live: slider-ul recalculeaza din mesh-ul de baza; Apply = un singur pas de Undo
    setPartMesh(editId, out.mesh);
    setEditSel(out.sel);
    setOp({ id, params, baseMesh: editMesh, baseSel: editSel, partId: editId });
  }

  function changeParam(key: string, value: number) {
    if (!op) return;
    const params = { ...op.params, [key]: value };
    setOp({ ...op, params });
    const out = runTool(op.id, op.baseMesh, op.baseSel, params);
    if (out.ok) { setPartMesh(op.partId, out.mesh); setEditSel(out.sel); }
    else flash(out.error);
  }

  function applyOp() {
    if (!op) return;
    hist.end();
    setOp(null);
  }

  function cancelOp() {
    if (!op) return;
    hist.cancel();
    setEditSel(op.baseSel);
    setOp(null);
  }

  function editSelectAll() { if (editMesh && !op) setEditSel(selectAll(editMesh, editSel.mode)); }
  function editSelectNone() { if (!op) setEditSel(emptySel(editSel.mode)); }
  function editInvert() { if (editMesh && !op) setEditSel(invertSel(editMesh, editSel)); }

  function joinSel() {
    const r = joinParts(parts, selIds);
    if (!r) { Alert.alert("Join", "Select two or more shapes to join them into one editable mesh."); return; }
    hist.set(r.parts);
    setSelIds([r.targetId]);
  }

  const editStats = useMemo(() => {
    if (!editMesh) return "";
    const E = buildTopology(editMesh).size;
    return `V ${vertCount(editMesh)} · E ${E} · F ${editMesh.f.length} · sel ${selCount(editSel)}`;
  }, [editMesh, editSel]);

  function toggleCollapse(id: string) {
    setCollapsed(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }

  // ---------- scena 3D ----------
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const gizmoRef = useRef<Gizmo | null>(null);
  const objMap = useRef<Record<string, THREE.Object3D>>({});
  const helpers = useRef<THREE.BoxHelper[]>([]);
  const rafId = useRef<number | null>(null);
  const alive = useRef(true);
  const framed = useRef(false);
  const canvasSize = useRef({ w: 1, h: 1 });
  const draggingRef = useRef(false);
  const drag = useRef<Drag | null>(null);
  const pending = useRef<Pending | null>(null);
  const pinchRef = useRef<any>(null);
  const pan2Ref = useRef<any>(null);

  const camTarget = useRef(new THREE.Vector3(0, 0.5, 0));
  const camAngle = useRef(0.7);
  const camPolar = useRef(1.0);
  const camDist = useRef(9);
  const lastAngle = useRef(0.7);
  const lastPolar = useRef(1.0);
  const lastDist = useRef(9);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
      gizmoRef.current?.dispose();
    };
  }, []);

  function updateCamera() {
    const cam = cameraRef.current;
    if (!cam) return;
    const r = camDist.current, th = camAngle.current, ph = camPolar.current, tg = camTarget.current;
    cam.position.set(
      tg.x + r * Math.sin(ph) * Math.cos(th),
      tg.y + r * Math.cos(ph),
      tg.z + r * Math.sin(ph) * Math.sin(th)
    );
    cam.lookAt(tg);
  }

  function frameObjects(objs: THREE.Object3D[]) {
    const s = sceneRef.current;
    if (!s) return;
    s.updateMatrixWorld(true);
    const box = new THREE.Box3();
    objs.forEach(o => box.expandByObject(o));
    if (box.isEmpty()) {
      camTarget.current.set(0, 0.5, 0);
      camDist.current = 9;
    } else {
      const size = box.getSize(new THREE.Vector3());
      camTarget.current.copy(box.getCenter(new THREE.Vector3()));
      camDist.current = Math.max(3, Math.min(50, Math.max(size.x, size.y, size.z) * 2.2));
    }
    lastDist.current = camDist.current;
    updateCamera();
  }

  const frameAll = () => frameObjects(
    editingRef.current && editIdRef.current ? [objMap.current[editIdRef.current]].filter(Boolean) : [objMap.current[ROOT_ID]].filter(Boolean)
  );
  const focusSel = () => frameObjects(selIds.map(id => objMap.current[id]).filter(Boolean));

  // Edit Mode: incadreaza varfurile selectate
  function focusEditSel() {
    const obj = editId ? objMap.current[editId] : undefined;
    if (!obj || !editMesh) return;
    const verts = selVerts(editMesh, editSel);
    if (verts.length === 0) { frameAll(); return; }
    obj.updateWorldMatrix(true, false);
    const box = new THREE.Box3();
    verts.forEach(i => box.expandByPoint(new THREE.Vector3(editMesh.v[i * 3], editMesh.v[i * 3 + 1], editMesh.v[i * 3 + 2]).applyMatrix4(obj.matrixWorld)));
    camTarget.current.copy(box.getCenter(new THREE.Vector3()));
    const size = box.getSize(new THREE.Vector3());
    camDist.current = Math.max(2.5, Math.min(50, Math.max(size.x, size.y, size.z, 0.5) * 3));
    lastDist.current = camDist.current;
    updateCamera();
  }

  const onContextCreate = async (gl: any) => {
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setSize(w, h);
    renderer.setClearColor(new THREE.Color(0x15171a), 1);
    const s = new THREE.Scene();
    sceneRef.current = s;
    const camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 200);
    cameraRef.current = camera;
    updateCamera();

    s.add(new THREE.AmbientLight(0xffffff, 0.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.0);
    key.position.set(5, 8, 4);
    s.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.4);
    fill.position.set(-5, 3, -4);
    s.add(fill);
    s.add(new THREE.GridHelper(20, 20, 0x555555, 0x2c2c2c));

    gizmoRef.current?.dispose();
    const gizmo = createGizmo();
    s.add(gizmo.root);
    gizmoRef.current = gizmo;

    objMap.current = {};
    helpers.current = [];

    const tmpPos = new THREE.Vector3();
    const tmpQuat = new THREE.Quaternion();

    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);

      // gizmo-ul urmareste obiectul selectat (ultimul din selectie); in Edit Mode, centrul varfurilor selectate
      const ids = selIdsRef.current;
      const o = ids.length ? objMap.current[ids[ids.length - 1]] : undefined;
      if (editingRef.current) {
        const eo = editIdRef.current ? objMap.current[editIdRef.current] : undefined;
        const pl = editPivotRef.current;
        if (eo && pl) {
          eo.updateWorldMatrix(true, false);
          tmpPos.copy(pl).applyMatrix4(eo.matrixWorld);
          eo.getWorldQuaternion(tmpQuat);
          updateGizmo(gizmo, modeRef.current, tmpPos, tmpQuat, camera);
        } else updateGizmo(gizmo, modeRef.current, null, tmpQuat, camera);
      } else if (o) {
        o.updateWorldMatrix(true, false);
        o.getWorldPosition(tmpPos);
        o.getWorldQuaternion(tmpQuat);
        updateGizmo(gizmo, modeRef.current, tmpPos, tmpQuat, camera);
      } else {
        updateGizmo(gizmo, modeRef.current, null, tmpQuat, camera);
      }

      renderer.render(s, camera);
      gl.endFrameEXP();
    };
    render();
    setGlReady(true); // scena exista: sincronizam piesele deja incarcate
  };

  // Sincronizeaza scena three.js cu lista de piese (si conturul celor selectate)
  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    const map = objMap.current;
    const fast = draggingRef.current; // in timpul unui gest actualizam doar transformarile

    for (const id of Object.keys(map)) {
      if (!byId.has(id)) {
        const o = map[id];
        o.parent?.remove(o);
        disposeObject(o);
        delete map[id];
      }
    }
    // o piesa si-a schimbat tipul (ex: primitiva -> mesh, sau Undo peste conversie): refacem obiectul
    for (const p of parts) {
      const cur = map[p.id];
      if (cur && cur.userData.kind !== undefined && cur.userData.kind !== p.type) {
        cur.parent?.remove(cur);
        disposeObject(cur);
        delete map[p.id];
      }
    }
    for (const p of parts) {
      if (!map[p.id]) {
        const o = createObject(p);
        o.userData.partId = p.id;
        o.userData.kind = p.type;
        map[p.id] = o;
      }
    }
    for (const p of parts) {
      const o = map[p.id];
      const target: THREE.Object3D = (p.parent ? map[p.parent] : s) ?? s;
      if (o.parent !== target) target.add(o);
      applyLocalTransform(o, p);
      o.visible = p.visible;
      syncMeshGeometry(o, p); // geometria editata se vede imediat (si in timpul tragerii)
      if (!fast) {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh) applyMaterial(mesh.material as THREE.MeshStandardMaterial, p);
      }
    }
    s.updateMatrixWorld(true);

    if (fast && helpers.current.length === selIds.length) {
      helpers.current.forEach(h => h.update());
      return;
    }

    helpers.current.forEach(h => {
      s.remove(h);
      h.geometry.dispose();
      (h.material as THREE.Material).dispose();
    });
    helpers.current = [];
    selIds.forEach((id, i) => {
      const o = map[id];
      if (!o || editing) return; // in Edit Mode conturul cutiei dispare: se vad varfurile/muchiile/fetele
      if (!o) return;
      const h = new THREE.BoxHelper(o, i === selIds.length - 1 ? 0xccff00 : 0x00e5ff);
      s.add(h);
      helpers.current.push(h);
    });
  }, [parts, selIds, glReady, editing]);

  // Overlay-ul Edit Mode (varfuri / muchii / fete selectate) pe obiectul editat
  useEffect(() => {
    const detach = () => {
      const ov = overlayRef.current;
      if (!ov) return;
      ov.group.parent?.remove(ov.group);
      ov.dispose();
      overlayRef.current = null;
    };
    const obj = editId ? (objMap.current[editId] as THREE.Mesh | undefined) : undefined;
    if (!editing || !obj || !(obj as THREE.Mesh).isMesh || !editMesh) { detach(); return; }
    if (!overlayRef.current) overlayRef.current = createEditOverlay();
    const ov = overlayRef.current;
    if (ov.group.parent !== obj) obj.add(ov.group);
    ov.update(editMesh, editSel, xray);
    const mat = obj.material as THREE.MeshStandardMaterial;
    mat.polygonOffset = true; // suprafata un pic mai in spate: muchiile nu "licaresc" pe ea
    mat.polygonOffsetFactor = 1;
    mat.polygonOffsetUnits = 1;
    return () => {};
  }, [editMesh, editSel, editing, editId, xray, glReady, parts]);

  useEffect(() => {
    if (editing) return;
    const ov = overlayRef.current;
    if (ov) { ov.group.parent?.remove(ov.group); ov.dispose(); overlayRef.current = null; }
    Object.values(objMap.current).forEach(o => {
      const m = o as THREE.Mesh;
      if (m.isMesh) { (m.material as THREE.MeshStandardMaterial).polygonOffset = false; }
    });
  }, [editing]);

  // pivotul gizmo-ului in Edit Mode: centrul varfurilor selectate (in spatiul local al piesei)
  useEffect(() => {
    if (!editing || !editMesh) { editPivotRef.current = null; return; }
    const verts = selVerts(editMesh, editSel);
    if (verts.length === 0) { editPivotRef.current = null; return; }
    const c = centroid(editMesh, verts);
    editPivotRef.current = new THREE.Vector3(c[0], c[1], c[2]);
  }, [editMesh, editSel, editing]);

  // prima incadrare a modelului (dupa ce scena si piesele exista)
  useEffect(() => {
    if (!glReady || loading || framed.current) return;
    framed.current = true;
    frameAll();
  }, [glReady, loading]);

  // ---------- gesturi ----------
  function rcAt(x: number, y: number): THREE.Raycaster {
    const cam = cameraRef.current!;
    cam.updateMatrixWorld();
    const { w, h } = canvasSize.current;
    const rc = new THREE.Raycaster();
    rc.setFromCamera(new THREE.Vector2((x / w) * 2 - 1, -(y / h) * 2 + 1), cam);
    return rc;
  }

  function firstMeshHit(rc: THREE.Raycaster): string | null {
    const meshes = Object.values(objMap.current).filter(o => (o as THREE.Mesh).isMesh && isVisibleDeep(o));
    const hits = rc.intersectObjects(meshes, false);
    return hits.length > 0 ? (hits[0].object.userData.partId as string) : null;
  }

  // atingerea (tap): selecteaza; atingerea unui mâner de gizmo nu schimba selectia
  // Edit Mode: atingerea selecteaza varful / muchia / fata cea mai apropiata (raza de atingere mare, pentru degete)
  function pickEditAt(x: number, y: number) {
    const cam = cameraRef.current;
    const id = editIdRef.current;
    const obj = id ? (objMap.current[id] as THREE.Mesh | undefined) : undefined;
    const mesh = partsRef.current.find(q => q.id === id)?.mesh;
    if (!cam || !obj || !mesh || opRef.current) return;
    const rc = rcAt(x, y);
    const g = gizmoRef.current;
    const sel = editSelRef.current;
    if (g && selCount(sel) > 0 && pickHandle(g, modeRef.current, rc)) return;
    const view = { camera: cam, w: canvasSize.current.w, h: canvasSize.current.h };
    const additive = multiRef.current || shiftRef.current;
    let hit: number | string | null;
    if (sel.mode === "vertex") hit = pickVertex(obj, mesh, view, x, y, 30, xrayRef.current);
    else if (sel.mode === "edge") hit = pickEdge(obj, mesh, view, x, y, 26, xrayRef.current);
    else hit = pickFace(obj, rc);
    if (hit === null) {
      if (!additive) setEditSel(emptySel(sel.mode));
      return;
    }
    setEditSel(toggleElement(sel, hit, additive));
  }