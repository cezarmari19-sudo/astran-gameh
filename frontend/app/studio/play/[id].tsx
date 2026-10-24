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
import { SceneObj, buildMesh, geometryFor, aabbFor, AABB, SPAWN_TYPE, spawnKindOf, groundWidthOf, groundDepthOf } from "@/src/studio/sceneShared";
import { PhysicsWorld, MaterialDef, DEFAULT_MATERIAL, PhysicsShapeType } from "@/src/play/physicsWorld";
import { AvatarBody, defaultBody, buildBodyMeshes, layoutBody, bodyHeightWorld } from "@/src/avatar/avatarTypes";
import type { Part } from "@/src/studio3d/modelTypes";
import { createObject, applyLocalTransform, applyMaterial } from "@/src/studio3d/modelTypes";
import { usePlayerSettings, GraphicsQuality } from "@/src/hooks/usePlayerSettings";

type ScriptOp = {
  t: number; op: "create" | "set" | "destroy" | "world" | "material"; id: string;
  type?: string; x?: number; y?: number; z?: number; color?: string; scale?: number; name?: string;
  gravity?: number; air?: number; jump_speed?: number; move_speed?: number;
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

const PLAYER_RADIUS = 0.35;
const PLAYER_HALF_HEIGHT_DEFAULT = 0.9;
// Valori implicite - pot fi suprascrise din scriptul jocului (main.lua) prin
// workspace.MoveSpeed / workspace.Gravity / workspace.JumpSpeed (vezi applyOp -> "world"
// op mai jos si backend/astran_sandbox/prelude.luau). Refs, nu const, tocmai ca sa poata
// fi schimbate in timpul rularii cand scriptul seteaza aceste proprietati pe workspace.
const DEFAULT_MOVE_SPEED = 3.2;
const DEFAULT_GRAVITY = -18;
const DEFAULT_JUMP_SPEED = 7.5; // viteza verticala initiala la saritura (m/s); cu GRAVITY=-18 da o saritura de ~1.56m
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
  const shiftLock = playerSettings.shift_lock;
  const shiftLockRef = useRef(shiftLock);
  shiftLockRef.current = shiftLock;
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
  const groundedRef = useRef(true); // true cand personajul sta pe sol (vezi bucla de gravitatie mai jos)
  // Suprascrise de scriptul jocului via workspace.MoveSpeed/.Gravity/.JumpSpeed (op "world") -
  // vezi bucla de aplicare a ops mai jos. Pornesc cu valorile implicite ale platformei.
  const moveSpeedRef = useRef(DEFAULT_MOVE_SPEED);
  const gravityRef = useRef(DEFAULT_GRAVITY);
  const jumpSpeedRef = useRef(DEFAULT_JUMP_SPEED);
  const jumpRequestedRef = useRef(false); // setat de butonul Jump; consumat o singura data per apasare, nu in fiecare frame cat e tinut
  const facingAngle = useRef(0);

  const scratchCamForward = useRef(new THREE.Vector3());
  const scratchCamRight = useRef(new THREE.Vector3());
  const scratchMoveDir = useRef(new THREE.Vector3());
  const scratchPrevPos = useRef(new THREE.Vector3());
  const scratchNextPos = useRef(new THREE.Vector3());
  const scratchResolvedPos = useRef(new THREE.Vector3());
  const scratchTarget = useRef(new THREE.Vector3());
  const scratchLookDir = useRef(new THREE.Vector3());
  const scratchLookAt = useRef(new THREE.Vector3());

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

  // Starea "live" a scriptului (ce operatii s-au rulat deja, cat timp a trecut, si
  // unde a ajuns fizic fiecare obiect mobil creat de script) - vezi remountFor() mai
  // jos: la schimbarea orientarii, GLView se remonteaza (contextul GL se poate pierde
  // la rotire pe unele telefoane), iar fara asta scriptul repornea mereu de la t=0,
  // adica exact bug-ul "se duce la cea de la inceput cand rotesc telefonul".
  type ScriptRuntimeSnapshot = {
    nextOp: number;
    elapsed: number;
    bodies: Map<string, { x: number; y: number; z: number; qx: number; qy: number; qz: number; qw: number; vx: number; vy: number; vz: number }>;
  };
  const preservedScriptRuntimeRef = useRef<ScriptRuntimeSnapshot | null>(null);
  const scriptMeshesRef = useRef<Map<string, THREE.Mesh>>(new Map());
  const nextOpRef = useRef(0);
  const scriptElapsedRef = useRef(0);

  const joyZoneLayout = useRef({ x: 0, y: 0, w: 180, h: 180 });
  const camZoneLayout = useRef({ x: 0, y: 0, w: 0, h: 0 });

  const joyTouchId = useRef<number | null>(null);
  const joyActive = useRef(false);
  const joyVec = useRef({ x: 0, y: 0 });
  const [joyKnob, setJoyKnob] = useState({ x: 0, y: 0 });
  const [joyVisible, setJoyVisible] = useState(false);
  const [joyOrigin, setJoyOrigin] = useState({ x: 80, y: 80 });

  const camTouchIds = useRef<number[]>([]);
  const camTouchPos = useRef<Map<number, TouchXY>>(new Map());
  const camTouchStartPos = useRef<Map<number, TouchXY>>(new Map());
  const camPinchStartDist = useRef<number | null>(null);
  const camPinchStartCamDist = useRef(4.5);
  const touchDownPos = useRef<Map<number, TouchXY>>(new Map());

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
    const down = touchDownPos.current.get(t.identifier) ?? { x: t.pageX, y: t.pageY };
    camTouchStartPos.current.set(t.identifier, down);

    if (camTouchIds.current.length === 1) {
      lastCamAngle.current = camAngle.current;
      lastCamPolar.current = camPolar.current;
    } else if (camTouchIds.current.length === 2) {
      const [idA, idB] = camTouchIds.current;
      const a = camTouchPos.current.get(idA), b = camTouchPos.current.get(idB);
      if (!a || !b) { camPinchStartDist.current = null; return; }
      camPinchStartDist.current = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      camPinchStartCamDist.current = camDist.current;
    }
  }

  const JOY_SAFETY_MARGIN = 28;
  function assignTouch(t: { identifier: number; pageX: number; pageY: number }) {
    const jz = joyZoneLayout.current;
    const inJoyZone = t.pageX >= jz.x && t.pageX <= jz.x + jz.w && t.pageY >= jz.y && t.pageY <= jz.y + jz.h;
    if (inJoyZone && joyTouchId.current === null) { startJoystick(t); return; }
    const nearJoyZone =
      t.pageX >= jz.x - JOY_SAFETY_MARGIN && t.pageX <= jz.x + jz.w + JOY_SAFETY_MARGIN &&
      t.pageY >= jz.y - JOY_SAFETY_MARGIN && t.pageY <= jz.y + jz.h + JOY_SAFETY_MARGIN;
    if (!nearJoyZone && camTouchIds.current.length < 2) startCameraTouch(t);
  }

  function removeCamTouchId(tid: number): boolean {
    const idx = camTouchIds.current.indexOf(tid);
    if (idx === -1) return false;
    camTouchIds.current.splice(idx, 1);
    camTouchPos.current.delete(tid);
    camTouchStartPos.current.delete(tid);
    return true;
  }

  function finalizeCamTouchChange() {
    camPinchStartDist.current = null;
    if (camTouchIds.current.length === 1) {
      const tid = camTouchIds.current[0];
      const pos2 = camTouchPos.current.get(tid);
      if (pos2) camTouchStartPos.current.set(tid, pos2);
      lastCamAngle.current = camAngle.current;
      lastCamPolar.current = camPolar.current;
    }
  }

  const onRootResponderGrant = (evt: any) => {
    const t = evt.nativeEvent.changedTouches?.[0] ?? evt.nativeEvent;
    if (!touchDownPos.current.has(t.identifier)) {
      touchDownPos.current.set(t.identifier, { x: t.pageX, y: t.pageY });
    }
    assignTouch(t);
  };

  const onRootResponderMove = (evt: any) => {
    const touches = touchesOf(evt);

    const activeIds = new Set(touches.map(t => t.identifier));
    if (joyTouchId.current !== null && !activeIds.has(joyTouchId.current)) {
      endJoystick();
    }
    let camChanged = false;
    for (const tid of [...camTouchIds.current]) {
      if (!activeIds.has(tid) && removeCamTouchId(tid)) camChanged = true;
    }
    for (const key of Array.from(touchDownPos.current.keys())) {
      if (!activeIds.has(key)) touchDownPos.current.delete(key);
    }
    if (camChanged) finalizeCamTouchChange();

    for (const t of touches) {
      const known = joyTouchId.current === t.identifier || camTouchIds.current.includes(t.identifier);
      if (!known) {
        if (!touchDownPos.current.has(t.identifier)) {
          touchDownPos.current.set(t.identifier, { x: t.pageX, y: t.pageY });
        }
        assignTouch(t);
      }
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
      touchDownPos.current.delete(c.identifier);
      if (joyTouchId.current === c.identifier) { endJoystick(); continue; }
      if (removeCamTouchId(c.identifier)) camChanged = true;
    }
    if (camChanged) finalizeCamTouchChange();
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
      if (physicsRef.current) {
        const bodies: ScriptRuntimeSnapshot["bodies"] = new Map();
        for (const opId of scriptMeshesRef.current.keys()) {
          if (physicsRef.current.isAnchored(opId)) continue; // cele fixe se recreeaza identic din scriptOps, nu au nevoie de snapshot
          const t = physicsRef.current.getTransform(opId);
          const v = physicsRef.current.getVelocity(opId);
          if (!t || !v) continue;
          bodies.set(opId, {
            x: t.position.x, y: t.position.y, z: t.position.z,
            qx: t.quaternion.x, qy: t.quaternion.y, qz: t.quaternion.z, qw: t.quaternion.w,
            vx: v.x, vy: v.y, vz: v.z,
          });
        }
        preservedScriptRuntimeRef.current = { nextOp: nextOpRef.current, elapsed: scriptElapsedRef.current, bodies };
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