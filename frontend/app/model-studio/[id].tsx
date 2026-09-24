import React, { useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, Modal, Platform, ActivityIndicator, Alert, BackHandler } from "react-native";
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
  Part, PartType, ROOT_ID, SHAPES, PART_ICON, PART_LABEL,
  newRootPart, newPart, uniqueName, normalizeParts, countShapes, byIdMap, descendantIds, topLevel, flatten,
  reparent, groupParts, ungroup, duplicateParts, deleteParts,
  createObject, disposeObject, applyLocalTransform, applyMaterial, isVisibleDeep, round3,
} from "@/src/studio3d/modelTypes";

type Dock = "add" | "tree" | "props" | null;

const DELTA_KEYS = ["x", "y", "z", "rx", "ry", "rz"];
const RATIO_KEYS = ["sx", "sy", "sz"];
const LOOK_KEYS = ["color", "material", "opacity"];

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

  const selParts = selIds.map(id => byId.get(id)).filter((p): p is Part => !!p);
  const primary = selParts[selParts.length - 1];
  const operable = topLevel(parts, selIds); // selectia fara radacina si fara copii ai unor selectati
  const rootName = byId.get(ROOT_ID)?.name ?? "Model";

  // referinte catre valorile curente, pentru butonul Back si dialoguri
  const dirtyRef = useRef(false);
  const saveRef = useRef<() => Promise<boolean>>(async () => false);
  const leaveRef = useRef<() => void>(() => {});
  dirtyRef.current = dirty;

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
  const objMap = useRef<Record<string, THREE.Object3D>>({});
  const helpers = useRef<THREE.BoxHelper[]>([]);
  const rafId = useRef<number | null>(null);
  const alive = useRef(true);
  const framed = useRef(false);
  const canvasSize = useRef({ w: 1, h: 1 });

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

    objMap.current = {};
    helpers.current = [];

    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);
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
      const mesh = o as THREE.Mesh;
      if (mesh.isMesh) applyMaterial(mesh.material as THREE.MeshStandardMaterial, p);
    }
    s.updateMatrixWorld(true);

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

  // ---------- gesturi: atingi = selectezi, tragi = rotesti, ciupesti = zoom ----------
  function pickAt(px: number, py: number) {
    const cam = cameraRef.current;
    if (!cam) return;
    const { w, h } = canvasSize.current;
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((px / w) * 2 - 1, -(py / h) * 2 + 1), cam);
    const meshes = Object.values(objMap.current).filter(o => (o as THREE.Mesh).isMesh && isVisibleDeep(o));
    const hits = ray.intersectObjects(meshes, false);
    if (hits.length === 0) {
      if (!multi) setSelIds([]);
      return;
    }
    selectPart(hits[0].object.userData.partId as string);
  }

  const onTapStateChange = (e: any) => {
    if (e.nativeEvent.state === State.ACTIVE) pickAt(e.nativeEvent.x, e.nativeEvent.y);
  };

  const onPanEvent = (e: any) => {
    const { translationX, translationY } = e.nativeEvent;
    camAngle.current = lastAngle.current - translationX * 0.008;
    camPolar.current = Math.max(0.15, Math.min(Math.PI - 0.15, lastPolar.current - translationY * 0.008));
    updateCamera();
  };
  const onPanState = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) {
      lastAngle.current = camAngle.current;
      lastPolar.current = camPolar.current;
    }
  };
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
            <PinchGestureHandler onGestureEvent={onPinchEvent} onHandlerStateChange={onPinchState}>
              <PanGestureHandler onGestureEvent={onPanEvent} onHandlerStateChange={onPanState} minPointers={1} maxPointers={1}>
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
                      <Text style={styles.hintText}>Tap select · Drag orbit · Pinch zoom</Text>
                    </View>
                  </View>
                </TapGestureHandler>
              </PanGestureHandler>
            </PinchGestureHandler>
            <Pressable testID="ms-frame" onPress={frameAll} style={styles.viewBtn}>
              <MaterialCommunityIcons name="fit-to-screen-outline" size={20} color={colors.onSurface} />
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
        <View style={[styles.dock, { height: dock === "add" ? 168 : 250 }]}>
          {dock === "add" ? (
            <ScrollView contentContainerStyle={styles.addGrid}>
              {SHAPES.map(t => (
                <Pressable key={t} testID={`ms-add-${t}`} onPress={() => addPart(t)} style={styles.addBtn}>
                  <MaterialCommunityIcons name={PART_ICON[t] as any} size={24} color={colors.brand} />
                  <Text style={styles.addBtnText}>{PART_LABEL[t]}</Text>
                </Pressable>
              ))}
              <Pressable testID="ms-add-group" onPress={() => addPart("group")} style={[styles.addBtn, { borderColor: colors.brand }]}>
                <MaterialCommunityIcons name="folder-outline" size={24} color={colors.brand} />
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
  hBtn: { width: 34, height: 34, alignItems: "center", justifyContent: "center" },
  canvas: { flex: 1, backgroundColor: colors.surface2, borderRadius: radius.md, marginHorizontal: spacing.md, marginBottom: 8, overflow: "hidden" },
  hintPill: { position: "absolute", bottom: 8, alignSelf: "center", backgroundColor: "rgba(0,0,0,0.55)", paddingHorizontal: 12, paddingVertical: 5, borderRadius: radius.pill },
  hintText: { color: colors.onSurface3, fontSize: 10, fontWeight: "600" },
  emptyHint: { position: "absolute", top: 0, bottom: 0, left: 0, right: 0, alignItems: "center", justifyContent: "center" },
  emptyHintText: { color: colors.onSurface3, fontSize: 13, fontWeight: "700" },
  viewBtn: { position: "absolute", top: 10, right: 10, width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(0,0,0,0.55)", alignItems: "center", justifyContent: "center" },
  actions: { flexGrow: 0, marginBottom: 8 },
  action: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  actionActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  actionText: { color: colors.onSurface, fontSize: 12, fontWeight: "700" },
  dockTabs: { flexDirection: "row", borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface2 },
  dockTab: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingVertical: 11, borderBottomWidth: 2, borderColor: "transparent" },
  dockTabActive: { borderColor: colors.brand },
  dockTabText: { color: colors.onSurface2, fontSize: 12, fontWeight: "800" },
  dock: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border },
  addGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, padding: 12 },
  addBtn: { width: 78, alignItems: "center", gap: 4, paddingVertical: 10, borderRadius: radius.md, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  addBtnText: { color: colors.onSurface, fontSize: 11, fontWeight: "700" },
  addHint: { width: "100%", color: colors.onSurface3, fontSize: 11 },
  noSel: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg, gap: 8 },
  noSelText: { color: colors.onSurface3, fontSize: 13, textAlign: "center" },
  errText: { color: colors.error, fontSize: 12, textAlign: "center" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  modalBox: { width: "100%", maxWidth: 380, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  modalTitle: { color: colors.onSurface, fontSize: 16, fontWeight: "900", marginBottom: 10 },
  targetRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 11, paddingRight: 10 },
  targetText: { color: colors.onSurface, fontSize: 14, fontWeight: "600" },
});