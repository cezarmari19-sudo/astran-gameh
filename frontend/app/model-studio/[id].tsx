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
} from "@/src/studio3d/modelTypes";

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
type Drag = { kind: "camera" } | Manip;
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

  const frameAll = () => frameObjects([objMap.current[ROOT_ID]].filter(Boolean));
  const focusSel = () => frameObjects(selIds.map(id => objMap.current[id]).filter(Boolean));

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

      // gizmo-ul urmareste obiectul selectat (ultimul din selectie)
      const ids = selIdsRef.current;
      const o = ids.length ? objMap.current[ids[ids.length - 1]] : undefined;
      if (o) {
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
    for (const p of parts) {
      if (!map[p.id]) {
        const o = createObject(p);
        o.userData.partId = p.id;
        map[p.id] = o;
      }
    }
    for (const p of parts) {
      const o = map[p.id];
      const target: THREE.Object3D = (p.parent ? map[p.parent] : s) ?? s;
      if (o.parent !== target) target.add(o);
      applyLocalTransform(o, p);
      o.visible = p.visible;
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
      if (!o) return;
      const h = new THREE.BoxHelper(o, i === selIds.length - 1 ? 0xccff00 : 0x00e5ff);
      s.add(h);
      helpers.current.push(h);
    });
  }, [parts, selIds, glReady]);

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
  function pickAt(x: number, y: number) {
    if (!cameraRef.current) return;
    const rc = rcAt(x, y);
    const g = gizmoRef.current;
    if (g && selIdsRef.current.length > 0 && pickHandle(g, modeRef.current, rc)) return;
    const id = firstMeshHit(rc);
    if (!id) {
      if (!multi) setSelIds([]);
      return;
    }
    selectPart(id);
  }

  const onTapStateChange = (e: any) => {
    if (e.nativeEvent.state === State.ACTIVE) pickAt(e.nativeEvent.x, e.nativeEvent.y);
  };

  // e obiectul (sau un descendent al unui grup) din selectie?
  function isInSelection(partId: string): boolean {
    const by = byIdMap(partsRef.current);
    const sel = new Set(selIdsRef.current);
    let cur: string | null = partId;
    while (cur) {
      if (sel.has(cur)) return true;
      cur = by.get(cur)?.parent ?? null;
    }
    return false;
  }

  // Ce face degetul care a atins ecranul: trage un mâner, muta obiectul selectat, sau invarte camera
  function beginTouch(x: number, y: number) {
    pending.current = { type: "camera" };
    if (!cameraRef.current) return;
    const rc = rcAt(x, y);
    const hasSel = selIdsRef.current.length > 0;
    const g = gizmoRef.current;
    if (g && hasSel) {
      const h = pickHandle(g, modeRef.current, rc);
      if (h) { pending.current = { type: "handle", handle: h }; return; }
    }
    if (hasSel && modeRef.current === "move") {
      const id = firstMeshHit(rc);
      if (id && isInSelection(id)) pending.current = { type: "ground" };
    }
  }

  function startManip(p: Pending, x: number, y: number): Manip | null {
    if (p.type === "camera") return null;
    const ids = selIdsRef.current;
    const ps = partsRef.current;
    const primaryObj = ids.length ? objMap.current[ids[ids.length - 1]] : undefined;
    if (!primaryObj) return null;
    primaryObj.updateWorldMatrix(true, false);
    const pivot = primaryObj.getWorldPosition(new THREE.Vector3());
    const primaryQ = primaryObj.getWorldQuaternion(new THREE.Quaternion());

    const by = byIdMap(ps);
    const items: DragItem[] = [];
    for (const id of outermost(ps, ids)) {
      const obj = objMap.current[id];
      const part = by.get(id);
      if (!obj || !part || !obj.parent) continue;
      obj.parent.updateWorldMatrix(true, false);
      items.push({
        id, part,
        parentInv: obj.parent.matrixWorld.clone().invert(),
        parentQInv: obj.parent.getWorldQuaternion(new THREE.Quaternion()).invert(),
        worldPos: obj.getWorldPosition(new THREE.Vector3()),
        worldQ: obj.getWorldQuaternion(new THREE.Quaternion()),
        rot: { rx: part.rx, ry: part.ry, rz: part.rz },
      });
    }
    if (items.length === 0) return null;

    const rc = rcAt(x, y);
    const base: ManipBase = { items, pivot };

    if (p.type === "ground" || (modeRef.current === "move" && p.handle.kind === "center")) {
      const p0 = planeHit(pivot, UP, rc.ray);
      return p0 ? { ...base, kind: "plane-move", p0 } : null;
    }
    const h = p.handle;
    const gscale = gizmoRef.current ? gizmoRef.current.root.scale.x : 1;

    if (modeRef.current === "move" && h.kind === "axis" && h.axis) {
      const dir = axisVector(h.axis);
      const t0 = closestParamOnAxis(pivot, dir, rc.ray);
      return t0 === null ? null : { ...base, kind: "axis-move", dir, t0 };
    }
    if (modeRef.current === "rotate" && h.kind === "ring" && h.axis) {
      const dir = axisVector(h.axis);
      const a0 = planeAngle(pivot, dir, rc.ray);
      return a0 === null ? null : { ...base, kind: "rotate", dir, angPrev: a0, angAcc: 0 };
    }
    if (modeRef.current === "scale") {
      if (h.kind === "center") return { ...base, kind: "uniform-scale", y0: y };
      if (h.kind === "axis" && h.axis) {
        const dir = axisVector(h.axis).applyQuaternion(primaryQ); // axa locala a obiectului, in lume
        let t0 = closestParamOnAxis(pivot, dir, rc.ray);
        if (t0 === null) return null;
        if (Math.abs(t0) < 0.15 * gscale) t0 = gscale; // mânerul sta la ~1 unitate de gizmo
        return { ...base, kind: "axis-scale", axis: h.axis, dir, t0 };
      }
    }
    return null;
  }

  function commitPatches(patches: Map<string, Partial<Part>>) {
    hist.set(cur => cur.map(p => {
      const pt = patches.get(p.id);
      return pt ? { ...p, ...pt } : p;
    }));
  }

  const clampNum = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

  function updateManip(d: Manip, x: number, y: number) {
    const rc = rcAt(x, y);
    const patches = new Map<string, Partial<Part>>();
    const worldToLocal = (it: DragItem, wp: THREE.Vector3) => {
      const l = wp.clone().applyMatrix4(it.parentInv);
      return { x: round3(l.x), y: round3(l.y), z: round3(l.z) };
    };

    if (d.kind === "axis-move") {
      const t = closestParamOnAxis(d.pivot, d.dir, rc.ray);
      if (t === null) return;
      let dt = t - d.t0;
      if (snapRef.current) dt = Math.round(dt / MOVE_SNAP) * MOVE_SNAP;
      dt = clampNum(dt, -100, 100);
      const delta = d.dir.clone().multiplyScalar(dt);
      d.items.forEach(it => patches.set(it.id, worldToLocal(it, it.worldPos.clone().add(delta))));
    } else if (d.kind === "plane-move") {
      const hit = planeHit(d.pivot, UP, rc.ray);
      if (!hit) return;
      const delta = hit.sub(d.p0);
      delta.y = 0;
      if (snapRef.current) {
        delta.x = Math.round(delta.x / MOVE_SNAP) * MOVE_SNAP;
        delta.z = Math.round(delta.z / MOVE_SNAP) * MOVE_SNAP;
      }
      delta.x = clampNum(delta.x, -100, 100);
      delta.z = clampNum(delta.z, -100, 100);
      d.items.forEach(it => patches.set(it.id, worldToLocal(it, it.worldPos.clone().add(delta))));
    } else if (d.kind === "rotate") {
      const a = planeAngle(d.pivot, d.dir, rc.ray);
      if (a === null) return;
      d.angAcc += wrapPi(a - d.angPrev);
      d.angPrev = a;
      let theta = d.angAcc;
      if (snapRef.current) theta = Math.round(theta / ROT_SNAP) * ROT_SNAP;
      const qd = new THREE.Quaternion().setFromAxisAngle(d.dir, theta);
      d.items.forEach(it => {
        const newWorld = qd.clone().multiply(it.worldQ);
        const local = it.parentQInv.clone().multiply(newWorld);
        const r = eulerNear(local, it.rot);
        it.rot = r;
        patches.set(it.id, r);
      });
    } else if (d.kind === "axis-scale") {
      const t = closestParamOnAxis(d.pivot, d.dir, rc.ray);
      if (t === null) return;
      let f = clampNum(t / d.t0, 0.02, 50);
      if (snapRef.current) f = Math.max(0.1, Math.round(f * 10) / 10);
      const key = (d.axis === "x" ? "sx" : d.axis === "y" ? "sy" : "sz") as "sx" | "sy" | "sz";
      d.items.forEach(it => patches.set(it.id, { [key]: Math.max(0.01, round3(it.part[key] * f)) } as Partial<Part>));
    } else if (d.kind === "uniform-scale") {
      let f = clampNum(Math.exp((d.y0 - y) * 0.008), 0.02, 50); // in sus = mai mare
      if (snapRef.current) f = Math.max(0.1, Math.round(f * 10) / 10);
      d.items.forEach(it => patches.set(it.id, {
        sx: Math.max(0.01, round3(it.part.sx * f)),
        sy: Math.max(0.01, round3(it.part.sy * f)),
        sz: Math.max(0.01, round3(it.part.sz * f)),
      }));
    }
    if (patches.size > 0) commitPatches(patches);
  }

  // un deget: mâner de gizmo / mutare obiect / orbita (pe fundal)
  const onDragState = (e: any) => {
    const { state, oldState, x, y } = e.nativeEvent;
    if (state === State.BEGAN) {
      beginTouch(x, y);
      return;
    }
    if (state === State.ACTIVE && !drag.current) {
      const p = pending.current;
      if (p && p.type !== "camera") {
        const m = startManip(p, x, y);
        if (m) {
          drag.current = m;
          draggingRef.current = true;
          hist.begin();
          return;
        }
      }
      drag.current = { kind: "camera" };
      return;
    }
    if (oldState === State.ACTIVE || state === State.CANCELLED || state === State.FAILED || state === State.END) {
      const d = drag.current;
      if (d) {
        if (d.kind === "camera") {
          lastAngle.current = camAngle.current;
          lastPolar.current = camPolar.current;
        } else {
          draggingRef.current = false;
          hist.end();
        }
      }
      drag.current = null;
      pending.current = null;
    }
  };

  const onDragEvent = (e: any) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "camera") {
      const { translationX, translationY } = e.nativeEvent;
      camAngle.current = lastAngle.current - translationX * 0.008;
      camPolar.current = Math.max(0.15, Math.min(Math.PI - 0.15, lastPolar.current - translationY * 0.008));
      updateCamera();
      return;
    }
    updateManip(d, e.nativeEvent.x, e.nativeEvent.y);
  };

  // doua degete: orbita camerei
  const onPan2Event = (e: any) => {
    const { translationX, translationY } = e.nativeEvent;
    camAngle.current = lastAngle.current - translationX * 0.008;
    camPolar.current = Math.max(0.15, Math.min(Math.PI - 0.15, lastPolar.current - translationY * 0.008));
    updateCamera();
  };
  const onPan2State = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) {
      lastAngle.current = camAngle.current;
      lastPolar.current = camPolar.current;
    }
  };

  // ciupit: zoom
  const onPinchEvent = (e: any) => {
    camDist.current = Math.max(1.5, Math.min(60, lastDist.current / e.nativeEvent.scale));
    updateCamera();
  };
  const onPinchState = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) lastDist.current = camDist.current;
  };

  // ---------- interfata ----------
  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator color={colors.brand} />
      </SafeAreaView>
    );
  }

  const hasSel = selIds.length > 0;

  return (
    <SafeAreaView style={styles.root} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Pressable testID="ms-back" onPress={leave} hitSlop={8}>
          <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1}>{dirty ? "• " : ""}{rootName}</Text>
        <Pressable testID="ms-undo" onPress={hist.undo} disabled={!hist.canUndo} hitSlop={6} style={[styles.hBtn, !hist.canUndo && { opacity: 0.3 }]}>
          <MaterialCommunityIcons name="undo" size={22} color={colors.onSurface} />
        </Pressable>
        <Pressable testID="ms-redo" onPress={hist.redo} disabled={!hist.canRedo} hitSlop={6} style={[styles.hBtn, !hist.canRedo && { opacity: 0.3 }]}>
          <MaterialCommunityIcons name="redo" size={22} color={colors.onSurface} />
        </Pressable>
        <Pressable testID="ms-save" onPress={save} disabled={saving} hitSlop={6} style={styles.hBtn}>
          {saving ? <ActivityIndicator size="small" color={colors.brand} /> : (
            <MaterialCommunityIcons name="content-save-outline" size={22} color={dirty ? colors.brand : colors.onSurface2} />
          )}
        </Pressable>
        <Pressable testID="ms-publish" onPress={publish} hitSlop={6} style={styles.hBtn}>
          <MaterialCommunityIcons name="storefront-outline" size={22} color={colors.brand} />
        </Pressable>
      </View>

      <View style={styles.canvas}>
        {Platform.OS === "web" ? (
          <View style={[StyleSheet.absoluteFillObject, { alignItems: "center", justifyContent: "center" }]}>
            <MaterialCommunityIcons name="cube-scan" size={64} color={colors.brand} />
            <Text style={{ color: colors.onSurface3, marginTop: 8 }}>3D viewport available on Expo Go / device</Text>
            <Text style={{ color: colors.onSurface, marginTop: 4, fontWeight: "700" }}>{countShapes(parts)} objects</Text>
          </View>
        ) : (
          <>
            <PinchGestureHandler ref={pinchRef} simultaneousHandlers={pan2Ref} onGestureEvent={onPinchEvent} onHandlerStateChange={onPinchState}>
              <PanGestureHandler ref={pan2Ref} simultaneousHandlers={pinchRef} minPointers={2} maxPointers={2} onGestureEvent={onPan2Event} onHandlerStateChange={onPan2State}>
                <PanGestureHandler minPointers={1} maxPointers={1} onGestureEvent={onDragEvent} onHandlerStateChange={onDragState}>
                  <TapGestureHandler maxDist={10} onHandlerStateChange={onTapStateChange}>
                    <View
                      style={StyleSheet.absoluteFillObject}
                      onLayout={e => { canvasSize.current = { w: e.nativeEvent.layout.width || 1, h: e.nativeEvent.layout.height || 1 }; }}
                    >
                      <GLView style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
                      {countShapes(parts) === 0 ? (
                        <View style={styles.emptyHint} pointerEvents="none">
                          <Text style={styles.emptyHintText}>Add objects from the ADD tab</Text>
                        </View>
                      ) : null}
                      <View style={styles.hintPill} pointerEvents="none">
                        <Text style={styles.hintText}>Tap select · Drag handles · 2 fingers orbit · Pinch zoom</Text>
                      </View>
                    </View>
                  </TapGestureHandler>
                </PanGestureHandler>
              </PanGestureHandler>
            </PinchGestureHandler>

            <View style={styles.modeCol} pointerEvents="box-none">
              {MODES.map(m => (
                <Pressable key={m.key} testID={`ms-mode-${m.key}`} onPress={() => setMode(m.key)} style={[styles.modeBtn, mode === m.key && styles.modeBtnActive]}>
                  <MaterialCommunityIcons name={m.icon as any} size={24} color={mode === m.key ? colors.brand : colors.onSurface} />
                </Pressable>
              ))}
              <Pressable testID="ms-snap" onPress={() => setSnap(v => !v)} style={[styles.modeBtn, snap && styles.modeBtnActive]}>
                <MaterialCommunityIcons name="magnet" size={22} color={snap ? colors.brand : colors.onSurface} />
              </Pressable>
            </View>

            <Pressable testID="ms-frame" onPress={frameAll} style={styles.viewBtn}>
              <MaterialCommunityIcons name="fit-to-screen-outline" size={22} color={colors.onSurface} />
            </Pressable>
          </>
        )}
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.actions} contentContainerStyle={{ gap: 8, paddingHorizontal: 12 }}>
        <Action icon="checkbox-multiple-marked-outline" label="Multi" active={multi} onPress={() => setMulti(m => !m)} />
        <Action icon="content-copy" label="Duplicate" disabled={operable.length === 0} onPress={duplicateSel} />
        <Action icon="group" label="Group" disabled={operable.length === 0} onPress={groupSel} />
        <Action icon="ungroup" label="Ungroup" disabled={!(primary && primary.type === "group" && primary.id !== ROOT_ID)} onPress={ungroupSel} />
        <Action icon="folder-move-outline" label="Move to" disabled={operable.length === 0} onPress={() => setShowMove(true)} />
        <Action icon="arrow-up" label="Parent" disabled={!primary?.parent} onPress={() => primary?.parent && setSelIds([primary.parent])} />
        <Action icon="crosshairs-gps" label="Focus" disabled={!hasSel} onPress={focusSel} />
        <Action icon="trash-can-outline" label="Delete" danger disabled={operable.length === 0} onPress={deleteSel} />
      </ScrollView>

      <View style={styles.dockTabs}>
        {([["add", "plus-box-outline", "Add"], ["tree", "file-tree", "Hierarchy"], ["props", "tune", "Properties"]] as const).map(([k, icon, label]) => (
          <Pressable key={k} testID={`ms-tab-${k}`} onPress={() => setDock(d => (d === k ? null : k))} style={[styles.dockTab, dock === k && styles.dockTabActive]}>
            <MaterialCommunityIcons name={icon as any} size={16} color={dock === k ? colors.brand : colors.onSurface2} />
            <Text style={[styles.dockTabText, dock === k && { color: colors.brand }]}>{label}</Text>
          </Pressable>
        ))}
      </View>

      {dock ? (
        <View style={[styles.dock, { height: dockHeight }]}>
          {dock === "add" ? (
            <ScrollView contentContainerStyle={styles.addGrid}>
              {SHAPES.map(t => (
                <Pressable key={t} testID={`ms-add-${t}`} onPress={() => addPart(t)} style={styles.addBtn}>
                  <MaterialCommunityIcons name={PART_ICON[t] as any} size={26} color={colors.brand} />
                  <Text style={styles.addBtnText}>{PART_LABEL[t]}</Text>
                </Pressable>
              ))}
              <Pressable testID="ms-add-group" onPress={() => addPart("group")} style={[styles.addBtn, { borderColor: colors.brand }]}>
                <MaterialCommunityIcons name="folder-outline" size={26} color={colors.brand} />
                <Text style={styles.addBtnText}>Group</Text>
              </Pressable>
              <Text style={styles.addHint}>New objects go inside the selected group (or the model root).</Text>
            </ScrollView>
          ) : dock === "tree" ? (
            <ScrollView contentContainerStyle={{ padding: 8 }}>
              <Outliner
                parts={parts}
                selIds={selIds}
                collapsed={collapsed}
                onSelect={selectPart}
                onToggleCollapse={toggleCollapse}
                onToggleVisible={id => hist.set(cur => cur.map(p => (p.id === id ? { ...p, visible: !p.visible } : p)))}
              />
            </ScrollView>
          ) : selParts.length > 0 ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              <PropertiesPanel selected={selParts} onPatch={patchSelected} />
            </ScrollView>
          ) : (
            <View style={styles.noSel}>
              <Text style={styles.noSelText}>Select an object (tap it, or use Hierarchy) to edit its properties.</Text>
              {err ? <Text style={styles.errText}>{err}</Text> : null}
            </View>
          )}
        </View>
      ) : null}

      <Modal visible={showMove} transparent animationType="fade" onRequestClose={() => setShowMove(false)}>
        <View style={styles.modalBackdrop}>
          <Pressable style={StyleSheet.absoluteFillObject} onPress={() => setShowMove(false)} />
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>Move to group</Text>
            <ScrollView style={{ maxHeight: 320 }}>
              {moveTargets.map(({ part, depth }) => (
                <Pressable key={part.id} onPress={() => moveSelTo(part.id)} style={[styles.targetRow, { paddingLeft: 10 + depth * 16 }]}>
                  <MaterialCommunityIcons name={(part.id === ROOT_ID ? "cube-scan" : "folder-outline") as any} size={18} color={colors.brand} />
                  <Text style={styles.targetText}>{part.name}</Text>
                </Pressable>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: 8, gap: 6 },
  title: { flex: 1, color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  hBtn: { width: 36, height: 36, alignItems: "center", justifyContent: "center" },
  canvas: { flex: 1, backgroundColor: colors.surface2, borderRadius: radius.md, marginHorizontal: spacing.md, marginBottom: 8, overflow: "hidden" },
  hintPill: { position: "absolute", bottom: 8, alignSelf: "center", backgroundColor: "rgba(0,0,0,0.55)", paddingHorizontal: 12, paddingVertical: 5, borderRadius: radius.pill },
  hintText: { color: colors.onSurface3, fontSize: 10, fontWeight: "600" },
  emptyHint: { position: "absolute", top: 0, bottom: 0, left: 0, right: 0, alignItems: "center", justifyContent: "center" },
  emptyHintText: { color: colors.onSurface3, fontSize: 13, fontWeight: "700" },
  modeCol: { position: "absolute", left: 10, top: 10, gap: 8 },
  modeBtn: { width: 46, height: 46, borderRadius: 23, backgroundColor: "rgba(0,0,0,0.6)", borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center" },
  modeBtnActive: { borderColor: colors.brand, backgroundColor: "rgba(204,255,0,0.16)" },
  viewBtn: { position: "absolute", top: 10, right: 10, width: 46, height: 46, borderRadius: 23, backgroundColor: "rgba(0,0,0,0.6)", borderWidth: 1, borderColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center" },
  actions: { flexGrow: 0, marginBottom: 8 },
  action: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, paddingVertical: 10, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  actionActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  actionText: { color: colors.onSurface, fontSize: 12, fontWeight: "700" },
  dockTabs: { flexDirection: "row", borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface2 },
  dockTab: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 13, borderBottomWidth: 2, borderColor: "transparent" },
  dockTabActive: { borderColor: colors.brand },
  dockTabText: { color: colors.onSurface2, fontSize: 12, fontWeight: "800" },
  dock: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border },
  addGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, padding: 12 },
  addBtn: { width: 78, alignItems: "center", gap: 4, paddingVertical: 12, borderRadius: radius.md, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  addBtnText: { color: colors.onSurface, fontSize: 11, fontWeight: "700" },
  addHint: { width: "100%", color: colors.onSurface3, fontSize: 11 },
  noSel: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg, gap: 8 },
  noSelText: { color: colors.onSurface3, fontSize: 13, textAlign: "center" },
  errText: { color: colors.error, fontSize: 12, textAlign: "center" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  modalBox: { width: "100%", maxWidth: 380, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  modalTitle: { color: colors.onSurface, fontSize: 16, fontWeight: "900", marginBottom: 10 },
  targetRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 13, paddingRight: 10 },
  targetText: { color: colors.onSurface, fontSize: 14, fontWeight: "600" },
});