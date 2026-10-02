import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, Platform, Modal, ScrollView, useWindowDimensions } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import * as ScreenOrientation from "expo-screen-orientation";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { SceneObj, buildMesh, geometryFor, aabbFor, AABB, SPAWN_TYPE, spawnKindOf } from "@/src/studio/sceneShared";
import { PhysicsWorld, MaterialDef, DEFAULT_MATERIAL, PhysicsShapeType } from "@/src/play/physicsWorld";
import { AvatarBody, defaultBody, buildBodyMeshes, layoutBody, bodyHeightWorld } from "@/src/avatar/avatarTypes";
import type { Part } from "@/src/studio3d/modelTypes";
import { createObject, applyLocalTransform, applyMaterial } from "@/src/studio3d/modelTypes";
import { usePlayerSettings, GraphicsQuality } from "@/src/hooks/usePlayerSettings";

// ---------- operatii de script (redate silentios; erorile se logheaza, nu se afiseaza in UI) ----------
type ScriptOp = {
  t: number; op: "create" | "set" | "destroy" | "world" | "material"; id: string;
  type?: string; x?: number; y?: number; z?: number; color?: string; scale?: number; name?: string;
  gravity?: number; air?: number;
  density?: number; friction?: number; bounce?: number; liquid?: boolean;
  material?: string; anchored?: boolean; collide?: boolean;
  vx?: number; vy?: number; vz?: number;
};
type MeshState = { x: number; y: number; z: number; scale: number };

type SpawnRuntime = {
  id: string;
  kind: "spawn" | "checkpoint";
  initial: boolean;
  x: number; y: number; z: number;
  ry: number;
  radius: number;
};

function placeMesh(m: THREE.Mesh) {
  const s = m.userData as MeshState;
  m.position.set(s.x, s.y + 0.5 * s.scale, s.z);
  m.scale.setScalar(s.scale);
}
function disposeMesh(m: THREE.Mesh) {
  m.geometry.dispose();
  (m.material as THREE.Material).dispose();
}
function applyOp(scene: THREE.Scene, meshes: Map<string, THREE.Mesh>, physics: PhysicsWorld, op: ScriptOp) {
  if (op.op === "world") {
    if (op.gravity !== undefined) physics.setGravity(op.gravity);
    if (op.air !== undefined) physics.setAirDensity(op.air);
    return;
  }
  if (op.op === "material") {
    const def: MaterialDef = {
      name: op.name ?? op.id,
      density: op.density ?? DEFAULT_MATERIAL.density,
      friction: op.friction ?? DEFAULT_MATERIAL.friction,
      bounce: op.bounce ?? DEFAULT_MATERIAL.bounce,
      liquid: op.liquid ?? DEFAULT_MATERIAL.liquid,
      color: op.color,
    };
    physics.setMaterialDef(op.id, def);
    return;
  }

  if (op.op === "create") {
    const old = meshes.get(op.id);
    if (old) { scene.remove(old); disposeMesh(old); }
    const state: MeshState = { x: op.x ?? 0, y: op.y ?? 0, z: op.z ?? 0, scale: op.scale ?? 1 };
    const shapeType = (op.type || "cube") as PhysicsShapeType;
    const m = buildMesh({ id: op.id, type: op.type || "cube", x: state.x, y: state.y, z: state.z, color: op.color || "#A3A3A3", scale: state.scale });
    m.userData = state;
    scene.add(m);
    meshes.set(op.id, m);
    physics.upsert({
      id: op.id, type: shapeType, x: state.x, y: state.y, z: state.z, scale: state.scale,
      anchored: op.anchored ?? true, collide: op.collide, materialId: op.material,
      velocity: (op.vx !== undefined || op.vy !== undefined || op.vz !== undefined)
        ? { x: op.vx ?? 0, y: op.vy ?? 0, z: op.vz ?? 0 } : undefined,
    });
    return;
  }
  const m = meshes.get(op.id);
  if (!m) return;
  if (op.op === "destroy") { scene.remove(m); disposeMesh(m); meshes.delete(op.id); physics.remove(op.id); return; }

  const s = m.userData as MeshState;
  if (op.x !== undefined) s.x = op.x;
  if (op.y !== undefined) s.y = op.y;
  if (op.z !== undefined) s.z = op.z;
  if (op.scale !== undefined) s.scale = op.scale;
  if (op.color) (m.material as THREE.MeshStandardMaterial).color.set(op.color);
  if (op.type) { m.geometry.dispose(); m.geometry = geometryFor(op.type); }
  if (op.x !== undefined || op.y !== undefined || op.z !== undefined) physics.setPosition(op.id, op.x, op.y, op.z);
  if (op.scale !== undefined) physics.setScale(op.id, op.scale);
  if (op.type) physics.setShapeType(op.id, op.type as PhysicsShapeType);
  if (op.material !== undefined) physics.setMaterial(op.id, op.material);
  if (op.anchored !== undefined) physics.setAnchored(op.id, op.anchored);
  if (op.collide !== undefined) physics.setCollide(op.id, op.collide);
  if (op.vx !== undefined || op.vy !== undefined || op.vz !== undefined) physics.setVelocity(op.id, op.vx, op.vy, op.vz);
  if (physics.isAnchored(op.id)) placeMesh(m);
}

// ---------- constante de gameplay ----------
const PLAYER_RADIUS = 0.35;
const PLAYER_HALF_HEIGHT_DEFAULT = 0.9;
const MOVE_SPEED = 3.2;
const GRAVITY = -18;
const JOYSTICK_RADIUS = 52;
const CAM_MIN_DIST = 0.15;
const CAM_MAX_DIST = 7;
const CAM_FIRST_PERSON_THRESHOLD = 0.6;
const CHECKPOINT_RADIUS_FACTOR = 1.4;
const CHECKPOINT_MIN_RADIUS = 0.6;

const QUALITY_LABELS: Record<GraphicsQuality, string> = {
  low: "Viziunea 1",
  medium: "Viziunea 2",
  high: "Viziunea 3",
};

const RENDER_DISTANCES = [20, 30, 45, 60, 80, 100, 125, 150, 200, 250];

type Lights = {
  ambient: THREE.AmbientLight;
  dir: THREE.DirectionalLight;
  hemi: THREE.HemisphereLight;
  fill: THREE.DirectionalLight;
};
const GRAPHICS_LEVELS: { ambient: number; dir: number; hemi: number; fill: number }[] = [
  { ambient: 1.0,  dir: 0.0,  hemi: 0.0,  fill: 0.0 },
  { ambient: 0.85, dir: 0.45, hemi: 0.0,  fill: 0.0 },
  { ambient: 0.7,  dir: 0.8,  hemi: 0.0,  fill: 0.0 },
  { ambient: 0.6,  dir: 1.0,  hemi: 0.0,  fill: 0.0 },
  { ambient: 0.55, dir: 1.1,  hemi: 0.0,  fill: 0.0 },
  { ambient: 0.45, dir: 1.1,  hemi: 0.25, fill: 0.0 },
  { ambient: 0.4,  dir: 1.15, hemi: 0.3,  fill: 0.25 },
  { ambient: 0.35, dir: 1.2,  hemi: 0.35, fill: 0.35 },
  { ambient: 0.3,  dir: 1.25, hemi: 0.4,  fill: 0.45 },
  { ambient: 0.25, dir: 1.3,  hemi: 0.45, fill: 0.55 },
];
function applyGraphicsLevel(lights: Lights, level: number) {
  const cfg = GRAPHICS_LEVELS[Math.max(1, Math.min(10, level)) - 1];
  lights.ambient.intensity = cfg.ambient;
  lights.dir.intensity = cfg.dir;
  lights.hemi.intensity = cfg.hemi;
  lights.fill.intensity = cfg.fill;
}

function LevelPicker({ value, onChange, testIDPrefix }: { value: number; onChange: (n: number) => void; testIDPrefix: string }) {
  return (
    <View style={styles.levelRow}>
      {Array.from({ length: 10 }, (_, i) => i + 1).map(n => (
        <Pressable
          key={n}
          testID={`${testIDPrefix}-${n}`}
          onPress={() => onChange(n)}
          style={[styles.levelChip, n <= value && styles.levelChipActive, n === value && styles.levelChipCurrent]}
        >
          <Text style={[styles.levelChipText, n < value && { color: colors.brand }, n === value && { color: colors.onBrand }]}>{n}</Text>
        </Pressable>
      ))}
    </View>
  );
}

type TouchXY = { x: number; y: number };

export default function PlayScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { width: screenW, height: screenH } = useWindowDimensions();

  const [game, setGame] = useState<any>(null);
  const [ready, setReady] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const { settings: playerSettings, loaded: settingsLoaded, update: updateSetting } = usePlayerSettings();
  const quality = playerSettings.graphics_quality;
  const renderLevel = playerSettings.render_level;
  const graphicsLevel = playerSettings.graphics_level;
  const renderDistance = RENDER_DISTANCES[renderLevel - 1];
  const graphicsLevelRef = useRef(graphicsLevel);
  graphicsLevelRef.current = graphicsLevel;
  const [glMountKey, setGlMountKey] = useState(0);

  const instanceRef = useRef<{ instance_id: string; game_id: string } | null>(null);
  const scriptOpsRef = useRef<ScriptOp[]>([]);
  const avatarBodyRef = useRef<AvatarBody>(defaultBody());
  const characterPartsRef = useRef<Part[] | null>(null);
  const spawnPointRef = useRef<{ x: number; y: number; z: number; ry: number }>({ x: 0, y: 0, z: 0, ry: 0 });
  const checkpointsRef = useRef<SpawnRuntime[]>([]);
  const activeCheckpointRef = useRef<SpawnRuntime | null>(null);
  const solidBoxesRef = useRef<AABB[]>([]);

  const canStartGL = settingsLoaded && !!game;

  const rendererRef = useRef<Renderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const lightsRef = useRef<Lights | null>(null);
  const physicsRef = useRef<PhysicsWorld | null>(null);
  const glRef = useRef<any>(null);
  const playerGroupRef = useRef<THREE.Group | null>(null);
  const playerHalfHeight = useRef(PLAYER_HALF_HEIGHT_DEFAULT);
  const alive = useRef(true);
  const rafId = useRef<number | null>(null);

  const pos = useRef(new THREE.Vector3(0, 0, 0));
  const velY = useRef(0);
  const facingAngle = useRef(0);

  const camAngle = useRef(0.0);
  const camPolar = useRef(1.15);
  const camDist = useRef(4.5);
  const lastCamAngle = useRef(0);
  const lastCamPolar = useRef(1.15);
  const firstPerson = useRef(false);

  const preservedStateRef = useRef<{
    pos: THREE.Vector3; velY: number; facingAngle: number;
    camAngle: number; camPolar: number; camDist: number;
  } | null>(null);

  // ============================================================================================
  // MULTI-TOUCH REAL - acelasi fix ca in frontend/app/play/[id].tsx.
  //
  // Istoricul problemei (pastrat ca sa nu se repete greseala):
  // 1) PanGestureHandler separat pe fiecare zona -> cele doua recognizers s-au exclus reciproc.
  // 2) onTouchStart/onTouchMove brute, dar cu DOUA View-uri responder separate (unul pentru
  //    joystick, unul pentru camera), fiecare cu onResponderTerminationRequest: () => false ->
  //    tot gresit: React Native are UN SINGUR responder activ in toata aplicatia. Primul deget
  //    care atinge o zona devine responder; cand al doilea deget atinge CEALALTA zona, RN
  //    intreaba responder-ul curent daca cedeaza (onResponderTerminationRequest), iar raspunsul
  //    "false" refuza cererea - zona noua nu primeste niciodata acel deget. Asta bloca reciproc
  //    joystick-ul si camera, exact bug-ul raportat.
  // 3) SOLUTIA CORECTA: UN SINGUR View responder, care acopera tot ecranul si citeste el insusi
  //    toate touch-urile active (nativeEvent.touches), asignand fiecare touch nou (dupa pozitie)
  //    fie joystick-ului, fie camerei. Nu mai exista niciun alt View cu care sa se negocieze
  //    "cine e responder-ul", deci nu mai exista cine sa refuze pe cine.
  // ============================================================================================

  const joyZoneLayout = useRef({ x: 0, y: 0, w: 180, h: 180 });
  const camZoneLayout = useRef({ x: 0, y: 0, w: 0, h: 0 });

  // --- Joystick: un singur deget, propriul identifier ---
  const joyTouchId = useRef<number | null>(null);
  const joyActive = useRef(false);
  const joyVec = useRef({ x: 0, y: 0 });
  const [joyKnob, setJoyKnob] = useState({ x: 0, y: 0 });
  const [joyVisible, setJoyVisible] = useState(false);
  const [joyOrigin, setJoyOrigin] = useState({ x: 80, y: 80 });

  // --- Camera: 1 deget = rotatie, al 2-lea deget (tot in zona camerei) = zoom (pinch) ---
  const camTouchIds = useRef<number[]>([]);
  const camTouchPos = useRef<Map<number, TouchXY>>(new Map());
  const camTouchStartPos = useRef<Map<number, TouchXY>>(new Map());
  const camPinchStartDist = useRef<number | null>(null);
  const camPinchStartCamDist = useRef(4.5);

  function touchesOf(evt: any): Array<{ identifier: number; pageX: number; pageY: number }> {
    return (evt?.nativeEvent?.touches as any[]) ?? [];
  }

  function startJoystick(t: { identifier: number; pageX: number; pageY: number }) {
    if (joyTouchId.current !== null) return;
    joyTouchId.current = t.identifier;
    const ox = t.pageX - joyZoneLayout.current.x;
    const oy = t.pageY - joyZoneLayout.current.y;
    setJoyOrigin({ x: ox, y: oy });
    setJoyKnob({ x: 0, y: 0 });
    setJoyVisible(true);
    joyActive.current = true;
  }
  function updateJoystick(t: { pageX: number; pageY: number }) {
    const zx = t.pageX - joyZoneLayout.current.x;
    const zy = t.pageY - joyZoneLayout.current.y;
    const dx0 = zx - joyOrigin.x, dy0 = zy - joyOrigin.y;
    const dist = Math.min(JOYSTICK_RADIUS, Math.hypot(dx0, dy0));
    const ang = Math.atan2(dy0, dx0);
    const kx = Math.cos(ang) * dist, ky = Math.sin(ang) * dist;
    setJoyKnob({ x: kx, y: ky });
    joyVec.current = { x: kx / JOYSTICK_RADIUS, y: ky / JOYSTICK_RADIUS };
  }
  function endJoystick() {
    joyTouchId.current = null;
    joyActive.current = false;
    joyVec.current = { x: 0, y: 0 };
    setJoyKnob({ x: 0, y: 0 });
    setJoyVisible(false);
  }

  function startCameraTouch(t: { identifier: number; pageX: number; pageY: number }) {
    if (camTouchIds.current.length >= 2) return;
    camTouchIds.current.push(t.identifier);
    camTouchPos.current.set(t.identifier, { x: t.pageX, y: t.pageY });
    camTouchStartPos.current.set(t.identifier, { x: t.pageX, y: t.pageY });

    if (camTouchIds.current.length === 1) {
      lastCamAngle.current = camAngle.current;
      lastCamPolar.current = camPolar.current;
    } else if (camTouchIds.current.length === 2) {
      const [idA, idB] = camTouchIds.current;
      const a = camTouchPos.current.get(idA)!, b = camTouchPos.current.get(idB)!;
      camPinchStartDist.current = Math.hypot(a.x - b.x, a.y - b.y);
      camPinchStartCamDist.current = camDist.current;
    }
  }

  function assignTouch(t: { identifier: number; pageX: number; pageY: number }) {
    const jz = joyZoneLayout.current;
    const inJoyZone = t.pageX >= jz.x && t.pageX <= jz.x + jz.w && t.pageY >= jz.y && t.pageY <= jz.y + jz.h;
    if (inJoyZone && joyTouchId.current === null) { startJoystick(t); return; }
    const cz = camZoneLayout.current;
    const inCamZone = t.pageX >= cz.x && t.pageX <= cz.x + cz.w && t.pageY >= cz.y && t.pageY <= cz.y + cz.h;
    if (inCamZone && camTouchIds.current.length < 2) startCameraTouch(t);
  }

  const onRootResponderGrant = (evt: any) => {
    const t = evt.nativeEvent.changedTouches?.[0] ?? evt.nativeEvent;
    assignTouch(t);
  };

  const onRootResponderMove = (evt: any) => {
    const touches = touchesOf(evt);

    for (const t of touches) {
      const known = joyTouchId.current === t.identifier || camTouchIds.current.includes(t.identifier);
      if (!known) assignTouch(t);
    }

    if (joyTouchId.current !== null) {
      const t = touches.find(x => x.identifier === joyTouchId.current);
      if (t) updateJoystick(t);
    }

    for (const tid of camTouchIds.current) {
      const t = touches.find(x => x.identifier === tid);
      if (t) camTouchPos.current.set(tid, { x: t.pageX, y: t.pageY });
    }
    if (camTouchIds.current.length === 1) {
      const tid = camTouchIds.current[0];
      const cur = camTouchPos.current.get(tid);
      const start = camTouchStartPos.current.get(tid);
      if (cur && start) {
        const translationX = cur.x - start.x;
        const translationY = cur.y - start.y;
        camAngle.current = lastCamAngle.current - translationX * 0.008;
        camPolar.current = Math.max(0.4, Math.min(Math.PI - 0.15, lastCamPolar.current - translationY * 0.006));
      }
    } else if (camTouchIds.current.length === 2 && camPinchStartDist.current !== null) {
      const [idA, idB] = camTouchIds.current;
      const a = camTouchPos.current.get(idA), b = camTouchPos.current.get(idB);
      if (a && b) {
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        const scale = dist / camPinchStartDist.current;
        camDist.current = Math.max(CAM_MIN_DIST, Math.min(CAM_MAX_DIST, camPinchStartCamDist.current / scale));
      }
    }
  };

  const onRootResponderEnd = (evt: any) => {
    const changed = (evt?.nativeEvent?.changedTouches as any[]) ?? [];
    let camChanged = false;
    for (const c of changed) {
      if (joyTouchId.current === c.identifier) { endJoystick(); continue; }
      const idx = camTouchIds.current.indexOf(c.identifier);
      if (idx !== -1) {
        camTouchIds.current.splice(idx, 1);
        camTouchPos.current.delete(c.identifier);
        camTouchStartPos.current.delete(c.identifier);
        camChanged = true;
      }
    }
    if (camChanged) {
      camPinchStartDist.current = null;
      if (camTouchIds.current.length === 1) {
        const tid = camTouchIds.current[0];
        const pos2 = camTouchPos.current.get(tid);
        if (pos2) camTouchStartPos.current.set(tid, pos2);
        lastCamAngle.current = camAngle.current;
        lastCamPolar.current = camPolar.current;
      }
    }
  };

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
      physicsRef.current?.dispose();
      physicsRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (Platform.OS === "web") return;
    ScreenOrientation.unlockAsync().catch(() => {});
    return () => {
      ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP).catch(() => {});
    };
  }, []);

  const currentOrientationRef = useRef<ScreenOrientation.Orientation | null>(null);
  useEffect(() => {
    if (Platform.OS === "web") return;
    let subscription: ScreenOrientation.Subscription | null = null;
    let cancelled = false;

    const remountFor = (orientation: ScreenOrientation.Orientation) => {
      if (currentOrientationRef.current === orientation) return;
      currentOrientationRef.current = orientation;
      if (sceneRef.current) {
        preservedStateRef.current = {
          pos: pos.current.clone(),
          velY: velY.current,
          facingAngle: facingAngle.current,
          camAngle: camAngle.current,
          camPolar: camPolar.current,
          camDist: camDist.current,
        };
      }
      physicsRef.current?.dispose();
      physicsRef.current = null;
      setReady(false);
      setGlMountKey(k => k + 1);
    };

    (async () => {
      try {
        const initial = await ScreenOrientation.getOrientationAsync();
        if (!cancelled) currentOrientationRef.current = initial;
      } catch {}

      subscription = ScreenOrientation.addOrientationChangeListener(event => {
        remountFor(event.orientationInfo.orientation);
      });
    })();

    return () => {
      cancelled = true;
      if (subscription) ScreenOrientation.removeOrientationChangeListener(subscription);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [g, me] = await Promise.all([api(`/games/${id}`), api("/auth/me")]);
        if (cancelled) return;
        setGame(g.game);

        try {
          const av = await api("/avatar/me");
          if (av?.avatar?.body) avatarBodyRef.current = { ...defaultBody(), ...av.avatar.body };
        } catch (e) { console.log("[play] avatar load failed", e); }

        const charId = g.game?.player_character_model_id;
        if (charId && g.game?.player_character_source === "shop_model") {
          try {
            const item = await api(`/shop/items/${charId}`);
            if (Array.isArray(item?.item?.parts)) characterPartsRef.current = item.item.parts;
          } catch (e) { console.log("[play] character load failed", e); }
        }
      } catch (e: any) {
        console.log("[play] game load failed", e);
        return;
      }

      try {
        const r = await api(`/games/${id}/play`, { method: "POST" });
        instanceRef.current = r?.session ?? null;
      } catch (e) { console.log("[play] instance join failed", e); }

      try {
        const run = await api(`/sandbox/games/${id}/run`, { method: "POST" });
        if (cancelled) return;
        scriptOpsRef.current = Array.isArray(run?.ops) ? run.ops : [];
        if (Array.isArray(run?.errors) && run.errors.length > 0) {
          console.log("[play] script errors", run.errors);
        }
      } catch (e) { console.log("[play] script run failed", e); }
    })();
    return () => { cancelled = true; };
  }, [id]);

  useEffect(() => {
    const iv = setInterval(() => {
      const inst = instanceRef.current;
      if (inst) api(`/games/${id}/instance/heartbeat`, { method: "POST", body: JSON.stringify({ instance_id: inst.instance_id }) }).catch(() => {});
    }, 20000);
    return () => {
      clearInterval(iv);
      const inst = instanceRef.current;
      if (inst) api(`/games/${id}/instance/leave`, { method: "POST", body: JSON.stringify({ instance_id: inst.instance_id }) }).catch(() => {});
    };
  }, [id]);

  function buildPlayerVisual(): THREE.Group {
    const group = new THREE.Group();
    if (characterPartsRef.current && characterPartsRef.current.length > 0) {
      const objects = new Map<string, THREE.Object3D>();
      characterPartsRef.current.forEach(p => {
        const obj = createObject(p);
        applyLocalTransform(obj, p);
        const mesh = obj as THREE.Mesh;
        if (mesh.isMesh) applyMaterial(mesh.material as THREE.MeshStandardMaterial, p);
        obj.visible = p.visible;
        objects.set(p.id, obj);
      });
      characterPartsRef.current.forEach(p => {
        const obj = objects.get(p.id)!;
        const parent = p.parent ? objects.get(p.parent) : null;
        (parent ?? group).add(obj);
      });
      playerHalfHeight.current = 0.6;
    } else {
      const bm = buildBodyMeshes();
      layoutBody(bm, avatarBodyRef.current);
      group.add(bm.group);
      playerHalfHeight.current = bodyHeightWorld(avatarBodyRef.current) / 2;
    }
    return group;
  }

  function resolveCollisions(next: THREE.Vector3, prev: THREE.Vector3): THREE.Vector3 {
    const r = PLAYER_RADIUS;
    const result = next.clone();
    for (const box of solidBoxesRef.current) {
      const withinY = result.y < box.maxY && result.y + playerHalfHeight.current * 2 > box.minY;
      if (!withinY) continue;
      if (result.x + r > box.minX && result.x - r < box.maxX && result.z + r > box.minZ && result.z - r < box.maxZ) {
        const prevOutsideX = prev.x + r <= box.minX || prev.x - r >= box.maxX;
        const prevOutsideZ = prev.z + r <= box.minZ || prev.z - r >= box.maxZ;
        if (prevOutsideX) result.x = prev.x;
        if (prevOutsideZ) result.z = prev.z;
        if (!prevOutsideX && !prevOutsideZ) { result.x = prev.x; result.z = prev.z; }
      }
    }
    return result;
  }

  function groundHeightAt(x: number, z: number): number {
    let maxTop = 0;
    for (const box of solidBoxesRef.current) {
      if (x >= box.minX && x <= box.maxX && z >= box.minZ && z <= box.maxZ) {
        if (box.maxY > maxTop) maxTop = box.maxY;
      }
    }
    return maxTop;
  }

  const onContextCreate = async (gl: any) => {
    if (!game) return;
    glRef.current = gl;
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setPixelRatio(quality === "low" ? 1 : quality === "medium" ? 1.4 : 2);
    renderer.setSize(w, h);
    rendererRef.current = renderer as any;
    const sky = game.scene?.sky || "#0F1012";
    renderer.setClearColor(new THREE.Color(sky), 1);

    const scene = new THREE.Scene();
    sceneRef.current = scene;
    const camera = new THREE.PerspectiveCamera(70, w / h, 0.05, renderDistance);
    cameraRef.current = camera;

    const ambient = new THREE.AmbientLight(0xffffff, 0.55);
    const dir = new THREE.DirectionalLight(0xffffff, 1.1);
    dir.position.set(5, 10, 4);
    const hemi = new THREE.HemisphereLight(0xbfd4ff, 0x1a1d21, 0);
    const fill = new THREE.DirectionalLight(0xffffff, 0);
    fill.position.set(-5, 4, -4);
    scene.add(ambient);
    scene.add(dir);
    scene.add(hemi);
    scene.add(fill);
    lightsRef.current = { ambient, dir, hemi, fill };
    applyGraphicsLevel(lightsRef.current, graphicsLevelRef.current);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(80, 80, 1, 1),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(game.scene?.ground || "#1A1D21") })
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);

    const physics = new PhysicsWorld();
    physicsRef.current = physics;
    physics.addGroundPlane();

    const boxes: AABB[] = [];
    const spawns: SpawnRuntime[] = [];
    (game.scene?.objects || []).forEach((o: SceneObj) => {
      if (o.type === SPAWN_TYPE) {
        if (o.enabled !== false) {
          spawns.push({
            id: o.id,
            kind: spawnKindOf(o),
            initial: o.initial === true,
            x: o.x, y: o.y, z: o.z,
            ry: o.ry ?? 0,
            radius: Math.max(CHECKPOINT_MIN_RADIUS, 0.6 * (o.scale ?? 1) * CHECKPOINT_RADIUS_FACTOR),
          });
        }
        if (o.visible === true) scene.add(buildMesh(o));
        if (o.solid === true) boxes.push(aabbFor(o));
        return;
      }
      const isVisible = o.visible !== false;
      if (isVisible) scene.add(buildMesh(o));
      const isSolid = o.solid !== false;
      if (isSolid) boxes.push(aabbFor(o));
      physics.addStudioObject(o);
    });
    solidBoxesRef.current = boxes;

    const spawnKindPoints = spawns.filter(p => p.kind === "spawn");
    const resolvedInitial = spawnKindPoints.find(p => p.initial) ?? spawnKindPoints[0] ?? null;
    const initial = resolvedInitial
      ? { x: resolvedInitial.x, y: resolvedInitial.y, z: resolvedInitial.z, ry: resolvedInitial.ry }
      : { x: 0, y: 0, z: 0, ry: 0 };
    spawnPointRef.current = initial;
    checkpointsRef.current = spawns.filter(p => p.kind === "checkpoint");
    activeCheckpointRef.current = null;

    const preserved = preservedStateRef.current;
    if (preserved) {
      pos.current.copy(preserved.pos);
      velY.current = preserved.velY;
      facingAngle.current = preserved.facingAngle;
      camAngle.current = preserved.camAngle;
      lastCamAngle.current = preserved.camAngle;
      camPolar.current = preserved.camPolar;
      lastCamPolar.current = preserved.camPolar;
      camDist.current = preserved.camDist;
      preservedStateRef.current = null;
    } else {
      pos.current.set(initial.x, initial.y, initial.z);
      velY.current = 0;
      facingAngle.current = (initial.ry * Math.PI) / 180;
    }

    const playerGroup = buildPlayerVisual();
    playerGroup.position.copy(pos.current);
    scene.add(playerGroup);
    playerGroupRef.current = playerGroup;

    const scriptOps = scriptOpsRef.current;
    const scriptMeshes = new Map<string, THREE.Mesh>();
    let nextOp = 0;
    const startedAt = Date.now();
    let lastFrame = Date.now();

    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);

      const now = Date.now();
      const dt = Math.min(0.05, (now - lastFrame) / 1000);
      lastFrame = now;

      const elapsed = (now - startedAt) / 1000;
      while (nextOp < scriptOps.length && scriptOps[nextOp].t <= elapsed) {
        applyOp(scene, scriptMeshes, physics, scriptOps[nextOp]);
        nextOp += 1;
      }

      physics.step(dt);
      for (const [opId, mesh] of scriptMeshes) {
        if (physics.isAnchored(opId)) continue;
        const t = physics.getTransform(opId);
        if (!t) continue;
        mesh.position.copy(t.position);
        mesh.quaternion.copy(t.quaternion);
        const s = mesh.userData as MeshState;
        s.x = t.position.x; s.y = t.position.y; s.z = t.position.z;
      }

      const jv = joyVec.current;
      const moveMag = Math.min(1, Math.hypot(jv.x, jv.y));
      if (moveMag > 0.05) {
        const camForward = new THREE.Vector3(Math.sin(camAngle.current), 0, Math.cos(camAngle.current));
        const camRight = new THREE.Vector3(camForward.z, 0, -camForward.x);
        const moveDir = new THREE.Vector3()
          .addScaledVector(camForward, jv.y)
          .addScaledVector(camRight, jv.x);
        if (moveDir.lengthSq() > 0.0001) {
          moveDir.normalize();
          facingAngle.current = Math.atan2(moveDir.x, moveDir.z);
          const prev = pos.current.clone();
          const next = prev.clone().addScaledVector(moveDir, MOVE_SPEED * moveMag * dt);
          const resolved = resolveCollisions(next, prev);
          pos.current.set(resolved.x, pos.current.y, resolved.z);
        }
      }

      const groundY = groundHeightAt(pos.current.x, pos.current.z);
      velY.current += GRAVITY * dt;
      let nextY = pos.current.y + velY.current * dt;
      if (nextY <= groundY) { nextY = groundY; velY.current = 0; }
      pos.current.y = nextY;

      for (const cp of checkpointsRef.current) {
        const dx = pos.current.x - cp.x, dz = pos.current.z - cp.z;
        if (dx * dx + dz * dz <= cp.radius * cp.radius) {
          if (activeCheckpointRef.current?.id !== cp.id) activeCheckpointRef.current = cp;
          break;
        }
      }

      if (playerGroupRef.current) {
        playerGroupRef.current.position.copy(pos.current);
        playerGroupRef.current.rotation.y = facingAngle.current;
        playerGroupRef.current.visible = !firstPerson.current;
      }

      firstPerson.current = camDist.current <= CAM_FIRST_PERSON_THRESHOLD;
      const eyeY = pos.current.y + playerHalfHeight.current * 1.8;
      if (firstPerson.current) {
        camera.position.set(pos.current.x, eyeY, pos.current.z);
        const lookDir = new THREE.Vector3(Math.sin(camAngle.current), 0, Math.cos(camAngle.current));
        camera.lookAt(camera.position.clone().add(lookDir));
      } else {
        const target = new THREE.Vector3(pos.current.x, eyeY, pos.current.z);
        const r = camDist.current, th = camAngle.current, ph = camPolar.current;
        camera.position.set(
          target.x + r * Math.sin(ph) * Math.sin(th),
          target.y + r * Math.cos(ph),
          target.z + r * Math.sin(ph) * Math.cos(th)
        );
        camera.lookAt(target);
      }

      renderer.render(scene, camera);
      gl.endFrameEXP();
    };
    render();
    setReady(true);
  };

  function doRespawn() {
    const target = activeCheckpointRef.current ?? spawnPointRef.current;
    pos.current.set(target.x, target.y, target.z);
    velY.current = 0;
    facingAngle.current = (target.ry * Math.PI) / 180;
    setShowMenu(false);
  }
  async function doLeave() {
    const inst = instanceRef.current;
    if (inst) api(`/games/${id}/instance/leave`, { method: "POST", body: JSON.stringify({ instance_id: inst.instance_id }) }).catch(() => {});
    router.replace({ pathname: "/game/[id]", params: { id } } as any);
  }

  useEffect(() => {
    const r = rendererRef.current as any;
    const cam = cameraRef.current;
    if (!r) return;
    r.setPixelRatio(quality === "low" ? 1 : quality === "medium" ? 1.4 : 2);
    if (cam) {
      const size = new THREE.Vector2();
      r.getSize(size);
      if (size.x > 0 && size.y > 0) {
        cam.aspect = size.x / size.y;
        cam.updateProjectionMatrix();
      }
    }
  }, [quality]);
  useEffect(() => {
    if (lightsRef.current) applyGraphicsLevel(lightsRef.current, graphicsLevel);
  }, [graphicsLevel]);
  useEffect(() => {
    const cam = cameraRef.current;
    if (cam) { cam.far = renderDistance; cam.updateProjectionMatrix(); }
  }, [renderDistance]);

  const isLandscape = screenW > screenH;

  return (
    <View style={styles.root}>
      {Platform.OS === "web" || !canStartGL ? (
        <View style={[StyleSheet.absoluteFillObject, styles.webFallback]}>
          <MaterialCommunityIcons name="cube-outline" size={80} color={colors.brand} />
          <Text style={styles.webText}>{game?.title || ""}</Text>
        </View>
      ) : (
        <GLView key={glMountKey} style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
      )}

      {/* UN SINGUR View responder peste tot ecranul - vezi comentariul mare de mai sus. */}
      {ready && Platform.OS !== "web" ? (
        <View
          style={StyleSheet.absoluteFillObject}
          onStartShouldSetResponder={() => true}
          onMoveShouldSetResponder={() => true}
          onResponderGrant={onRootResponderGrant}
          onResponderMove={onRootResponderMove}
          onResponderRelease={onRootResponderEnd}
          onResponderTerminate={onRootResponderEnd}
          onResponderTerminationRequest={() => false}
        >
          <View
            style={[styles.cameraZone, { width: isLandscape ? "40%" : "55%" }]}
            pointerEvents="none"
            onLayout={e => {
              const { x, y, width, height } = e.nativeEvent.layout;
              camZoneLayout.current = { x, y, w: width, h: height };
            }}
          />

          <View
            style={styles.joystickZone}
            pointerEvents="none"
            onLayout={e => {
              const { x, y, width, height } = e.nativeEvent.layout;
              joyZoneLayout.current = { x, y, w: width, h: height };
            }}
          >
            {joyVisible ? (
              <View style={[styles.joyBase, { left: joyOrigin.x - 52, top: joyOrigin.y - 52 }]} pointerEvents="none">
                <View style={[styles.joyKnob, { transform: [{ translateX: joyKnob.x }, { translateY: joyKnob.y }] }]} />
              </View>
            ) : (
              <View style={styles.joyHint} pointerEvents="none">
                <MaterialCommunityIcons name="gesture-tap" size={14} color="rgba(255,255,255,0.5)" />
              </View>
            )}
          </View>
        </View>
      ) : null}

      <SafeAreaView edges={["top", "left"]} style={styles.aBtnWrap} pointerEvents="box-none">
        <Pressable testID="play-menu-btn" onPress={() => setShowMenu(true)} style={styles.aBtn}>
          <Text style={styles.aBtnText}>A</Text>
        </Pressable>
      </SafeAreaView>

      <Modal visible={showMenu} transparent animationType="fade" onRequestClose={() => setShowMenu(false)}>
        <Pressable style={styles.menuBackdrop} onPress={() => setShowMenu(false)}>
          <View style={[styles.menuBox, isLandscape && styles.menuBoxLandscape]}>
            <Text style={styles.menuTitle}>{game?.title}</Text>
            <Pressable testID="play-menu-respawn" onPress={doRespawn} style={styles.menuRow}>
              <MaterialCommunityIcons name="restart" size={20} color={colors.brand} />
              <Text style={styles.menuRowText}>Respawn</Text>
            </Pressable>
            <Pressable testID="play-menu-settings" onPress={() => { setShowMenu(false); setShowSettings(true); }} style={styles.menuRow}>
              <MaterialCommunityIcons name="cog-outline" size={20} color={colors.brand} />
              <Text style={styles.menuRowText}>Settings</Text>
            </Pressable>
            <Pressable testID="play-menu-leave" onPress={doLeave} style={styles.menuRow}>
              <MaterialCommunityIcons name="exit-run" size={20} color={colors.error} />
              <Text style={[styles.menuRowText, { color: colors.error }]}>Leave</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>

      <Modal visible={showSettings} transparent animationType="fade" onRequestClose={() => setShowSettings(false)}>
        <Pressable style={styles.menuBackdrop} onPress={() => setShowSettings(false)}>
          <Pressable style={[styles.menuBox, isLandscape && styles.menuBoxLandscape]} onPress={(e: any) => e.stopPropagation?.()}>
            <ScrollView showsVerticalScrollIndicator={false}>
              <Text style={styles.menuTitle}>Settings</Text>

              <Text style={styles.settingLabel}>Viziune</Text>
              <View style={styles.qualityRow}>
                {(["low", "medium", "high"] as GraphicsQuality[]).map(q => (
                  <Pressable key={q} testID={`play-quality-${q}`} onPress={() => updateSetting("graphics_quality", q)} style={[styles.qualityChip, quality === q && styles.qualityChipActive]}>
                    <Text style={[styles.qualityChipText, quality === q && { color: colors.brand }]}>{QUALITY_LABELS[q]}</Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.settingLabel}>Grafică: {graphicsLevel}/10</Text>
              <LevelPicker value={graphicsLevel} onChange={v => updateSetting("graphics_level", v)} testIDPrefix="play-graphics" />

              <Text style={styles.settingLabel}>Render: {renderLevel}/10 · {renderDistance}m</Text>
              <LevelPicker value={renderLevel} onChange={v => updateSetting("render_level", v)} testIDPrefix="play-render" />

              <Pressable testID="play-settings-close" onPress={() => setShowSettings(false)} style={styles.menuCloseBtn}>
                <Text style={styles.menuCloseBtnText}>Close</Text>
              </Pressable>
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  webFallback: { alignItems: "center", justifyContent: "center", gap: 12 },
  webText: { color: colors.onSurface, fontWeight: "800", fontSize: 18 },
  cameraZone: { position: "absolute", top: 0, bottom: 0, right: 0 },
  joystickZone: { position: "absolute", left: 0, bottom: 0, width: 180, height: 180 },
  joyBase: { position: "absolute", width: 104, height: 104, borderRadius: 52, backgroundColor: "rgba(255,255,255,0.12)", borderWidth: 2, borderColor: "rgba(255,255,255,0.35)", alignItems: "center", justifyContent: "center" },
  joyKnob: { width: 46, height: 46, borderRadius: 23, backgroundColor: "rgba(204,255,0,0.85)" },
  joyHint: { position: "absolute", left: 24, bottom: 24, width: 44, height: 44, borderRadius: 22, borderWidth: 2, borderColor: "rgba(255,255,255,0.25)", alignItems: "center", justifyContent: "center" },
  aBtnWrap: { position: "absolute", top: 0, left: 0, padding: spacing.md },
  aBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(0,0,0,0.6)", borderWidth: 1, borderColor: "rgba(255,255,255,0.2)", alignItems: "center", justifyContent: "center" },
  aBtnText: { color: colors.brand, fontWeight: "900", fontSize: 16 },
  menuBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  menuBox: { width: "100%", maxWidth: 340, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  menuBoxLandscape: { maxWidth: 420, maxHeight: "85%" },
  menuTitle: { color: colors.onSurface, fontSize: 17, fontWeight: "900", marginBottom: 14 },
  menuRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12 },
  menuRowText: { color: colors.onSurface, fontSize: 14, fontWeight: "700" },
  settingLabel: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14, marginBottom: 8 },
  qualityRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  levelRow: { flexDirection: "row", gap: 5 },
  levelChip: { flex: 1, height: 34, borderRadius: 8, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  levelChipActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  levelChipCurrent: { backgroundColor: colors.brand },
  levelChipText: { color: colors.onSurface2, fontWeight: "800", fontSize: 11 },
  qualityChip: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  qualityChipActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  qualityChipText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12, textTransform: "capitalize" },
  menuCloseBtn: { marginTop: 18, backgroundColor: colors.brand, borderRadius: radius.pill, paddingVertical: 12, alignItems: "center" },
  menuCloseBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
});