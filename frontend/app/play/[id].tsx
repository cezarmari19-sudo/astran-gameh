import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, Platform, Modal, ScrollView, useWindowDimensions } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import * as ScreenOrientation from "expo-screen-orientation";
import { PanGestureHandler, PinchGestureHandler, State } from "react-native-gesture-handler";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { SceneObj, buildMesh, geometryFor, aabbFor, AABB, SPAWN_TYPE } from "@/src/studio/sceneShared";
import { AvatarBody, defaultBody, buildBodyMeshes, layoutBody, bodyHeightWorld } from "@/src/avatar/avatarTypes";
import type { Part } from "@/src/studio3d/modelTypes";
import { createObject, applyLocalTransform, applyMaterial } from "@/src/studio3d/modelTypes";

// ---------- operatii de script (redate silentios; erorile se logheaza, nu se afiseaza in UI) ----------
type ScriptOp = {
  t: number; op: "create" | "set" | "destroy"; id: string;
  type?: string; x?: number; y?: number; z?: number; color?: string; scale?: number; name?: string;
};
type MeshState = { x: number; y: number; z: number; scale: number };

function placeMesh(m: THREE.Mesh) {
  const s = m.userData as MeshState;
  m.position.set(s.x, s.y + 0.5 * s.scale, s.z);
  m.scale.setScalar(s.scale);
}
function disposeMesh(m: THREE.Mesh) {
  m.geometry.dispose();
  (m.material as THREE.Material).dispose();
}
function applyOp(scene: THREE.Scene, meshes: Map<string, THREE.Mesh>, op: ScriptOp) {
  if (op.op === "create") {
    const old = meshes.get(op.id);
    if (old) { scene.remove(old); disposeMesh(old); }
    const state: MeshState = { x: op.x ?? 0, y: op.y ?? 0, z: op.z ?? 0, scale: op.scale ?? 1 };
    const m = buildMesh({ id: op.id, type: op.type || "cube", x: state.x, y: state.y, z: state.z, color: op.color || "#A3A3A3", scale: state.scale });
    m.userData = state;
    scene.add(m);
    meshes.set(op.id, m);
    return;
  }
  const m = meshes.get(op.id);
  if (!m) return;
  if (op.op === "destroy") { scene.remove(m); disposeMesh(m); meshes.delete(op.id); return; }
  const s = m.userData as MeshState;
  if (op.x !== undefined) s.x = op.x;
  if (op.y !== undefined) s.y = op.y;
  if (op.z !== undefined) s.z = op.z;
  if (op.scale !== undefined) s.scale = op.scale;
  if (op.color) (m.material as THREE.MeshStandardMaterial).color.set(op.color);
  if (op.type) { m.geometry.dispose(); m.geometry = geometryFor(op.type); }
  placeMesh(m);
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

type GraphicsQuality = "low" | "medium" | "high";

// Numele afisate in Settings. Valorile interne (low/medium/high) raman
// neschimbate si controleaza in continuare pixel ratio-ul; se schimba doar textul.
const QUALITY_LABELS: Record<GraphicsQuality, string> = {
  low: "Viziunea 1",
  medium: "Viziunea 2",
  high: "Viziunea 3",
};

// ---------- Render: 10 niveluri de distanta de randare (metri) ----------
// Nivelul 4 = 60m, adica valoarea de dinainte. Vechile 30/60/100/150 sunt nivelurile 2/4/6/8.
const RENDER_DISTANCES = [20, 30, 45, 60, 80, 100, 125, 150, 200, 250];

// ---------- Grafica: 10 niveluri de calitate a iluminarii ----------
// Nivelul 5 = exact aspectul de dinainte (ambient 0.55 + directionala 1.1).
// Sub 5: iluminare mai plata. Peste 5: mai bogata (lumina de cer/sol + lumina de umplere).
// Nu foloseste pixel ratio: acela ramane la "Viziune" (vezi nota din useEffect-ul [quality]).
type Lights = {
  ambient: THREE.AmbientLight;
  dir: THREE.DirectionalLight;
  hemi: THREE.HemisphereLight;
  fill: THREE.DirectionalLight;
};
const GRAPHICS_LEVELS: { ambient: number; dir: number; hemi: number; fill: number }[] = [
  { ambient: 1.0,  dir: 0.0,  hemi: 0.0,  fill: 0.0 },  // 1
  { ambient: 0.85, dir: 0.45, hemi: 0.0,  fill: 0.0 },  // 2
  { ambient: 0.7,  dir: 0.8,  hemi: 0.0,  fill: 0.0 },  // 3
  { ambient: 0.6,  dir: 1.0,  hemi: 0.0,  fill: 0.0 },  // 4
  { ambient: 0.55, dir: 1.1,  hemi: 0.0,  fill: 0.0 },  // 5 (ca inainte)
  { ambient: 0.45, dir: 1.1,  hemi: 0.25, fill: 0.0 },  // 6
  { ambient: 0.4,  dir: 1.15, hemi: 0.3,  fill: 0.25 }, // 7
  { ambient: 0.35, dir: 1.2,  hemi: 0.35, fill: 0.35 }, // 8
  { ambient: 0.3,  dir: 1.25, hemi: 0.4,  fill: 0.45 }, // 9
  { ambient: 0.25, dir: 1.3,  hemi: 0.45, fill: 0.55 }, // 10
];
function applyGraphicsLevel(lights: Lights, level: number) {
  const cfg = GRAPHICS_LEVELS[Math.max(1, Math.min(10, level)) - 1];
  lights.ambient.intensity = cfg.ambient;
  lights.dir.intensity = cfg.dir;
  lights.hemi.intensity = cfg.hemi;
  lights.fill.intensity = cfg.fill;
}

// Selector cu 10 puncte: casutele 1..valoare sunt aprinse, cea aleasa e plina.
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

export default function PlayScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { width: screenW, height: screenH } = useWindowDimensions();

  const [game, setGame] = useState<any>(null);
  const [ready, setReady] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  // "quality" = Viziunea 1/2/3 (pixel ratio 1 / 1.4 / 2) - vezi nota din useEffect-ul [quality].
  const [quality, setQuality] = useState<GraphicsQuality>("medium");
  const [renderLevel, setRenderLevel] = useState(4); // 4 = 60m, ca inainte
  const renderDistance = RENDER_DISTANCES[renderLevel - 1];
  const [graphicsLevel, setGraphicsLevel] = useState(5); // 5 = aspectul de dinainte
  const graphicsLevelRef = useRef(graphicsLevel);
  graphicsLevelRef.current = graphicsLevel;
  // Cheia de remontare a GLView-ului: creste la fiecare schimbare REALA de
  // orientare a device-ului (nu la orice mica variatie de screenW/screenH,
  // gen bare de sistem). Vezi useEffect-ul de mai jos, bazat pe evenimentul
  // nativ de orientare, nu pe screenW/screenH.
  const [glMountKey, setGlMountKey] = useState(0);

  const instanceRef = useRef<{ instance_id: string; game_id: string } | null>(null);
  const scriptOpsRef = useRef<ScriptOp[]>([]);
  const avatarBodyRef = useRef<AvatarBody>(defaultBody());
  const characterPartsRef = useRef<Part[] | null>(null);
  const spawnPointRef = useRef<{ x: number; y: number; z: number }>({ x: 0, y: 0, z: 0 });
  const solidBoxesRef = useRef<AABB[]>([]);

  // Three.js
  const rendererRef = useRef<Renderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const lightsRef = useRef<Lights | null>(null);
  // Referinta catre contextul GL, ca sa putem citi dimensiunile REALE ale
  // drawing buffer-ului (in pixeli fizici) de fiecare data cand se schimba
  // orientarea/dimensiunea ecranului - nu doar o data, la creare.
  const glRef = useRef<any>(null);
  const playerGroupRef = useRef<THREE.Group | null>(null);
  const playerHalfHeight = useRef(PLAYER_HALF_HEIGHT_DEFAULT);
  const alive = useRef(true);
  const rafId = useRef<number | null>(null);

  const pos = useRef(new THREE.Vector3(0, 0, 0));
  const velY = useRef(0);
  const facingAngle = useRef(0);

  // Camera: DOAR swipe + pinch. Fara giroscop/inclinare.
  const camAngle = useRef(0.0);
  const camPolar = useRef(1.15);
  const camDist = useRef(4.5);
  const lastCamAngle = useRef(0);
  const lastCamPolar = useRef(1.15);
  const lastCamDist = useRef(4.5);
  const firstPerson = useRef(false);

  // La remontarea GLView-ului (schimbare de orientare) contextul GL, scena,
  // camera si renderer-ul se recreeaza de la zero. Ca sa nu se vada un reset
  // (jucatorul sarind la spawn, camera revenind la unghiul implicit), pastram
  // aici pozitia curenta si unghiul camerei INAINTE de remontare, si le
  // restauram in noul onContextCreate. Ref, nu state: nu trebuie sa declanseze
  // re-render, doar sa supravietuiasca intre demontare/montare.
  const preservedStateRef = useRef<{
    pos: THREE.Vector3; velY: number; facingAngle: number;
    camAngle: number; camPolar: number; camDist: number;
  } | null>(null);

  // Joystick
  const joyActive = useRef(false);
  const joyVec = useRef({ x: 0, y: 0 });
  const [joyKnob, setJoyKnob] = useState({ x: 0, y: 0 });
  const [joyVisible, setJoyVisible] = useState(false);
  const [joyOrigin, setJoyOrigin] = useState({ x: 80, y: 80 });

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; if (rafId.current !== null) cancelAnimationFrame(rafId.current); };
  }, []);

  // ---------- rotatia ecranului: Play Mode elibereaza orientarea, si o reblocheaza pe portrait la iesire ----------
  useEffect(() => {
    if (Platform.OS === "web") return;
    ScreenOrientation.unlockAsync().catch(() => {});
    return () => {
      ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP).catch(() => {});
    };
  }, []);

  // ---------- remontare GLView la FIECARE schimbare reala de orientare ----------
  // Pe Android, expo-gl leaga suprafata EGL de dimensiunile View-ului la
  // momentul montarii. Redimensionarea view-ului FARA remontare nu garanteaza
  // realocarea completa a acelei suprafete pe toate device-urile, iar randarea
  // ajunge sa foloseasca un buffer cu dimensiunea/orientarea veche.
  //
  // IMPORTANT: nu putem detecta asta comparand doar screenW > screenH (boolean
  // "e landscape?"), pentru ca useWindowDimensions() intoarce ACEEASI pereche
  // de valori atat pentru LANDSCAPE_LEFT cat si pentru LANDSCAPE_RIGHT (e doar
  // telefonul intors 180 fata de axa lunga, dimensiunile logice raman identice).
  // Asta inseamna ca o rotire directa stanga<->dreapta (fara sa treci prin
  // portret) nu schimba deloc acel boolean, deci nu declansa remontarea -
  // exact cauza bug-ului: suprafata EGL ramanea legata de orientarea veche,
  // iar randarea acoperea doar partea din ecran care se suprapune cu vechea
  // orientare, restul ramanand nedesenat (negru).
  //
  // Solutia tehnica corecta e sa ascultam evenimentul NATIV de orientare
  // (expo-screen-orientation), care distinge toate cele 4 stari posibile
  // (PORTRAIT_UP, PORTRAIT_DOWN, LANDSCAPE_LEFT, LANDSCAPE_RIGHT) si sa
  // remontam GLView-ul de fiecare data cand aceasta valoare se schimba,
  // indiferent daca e o schimbare portret<->landscape sau landscape<->landscape.
  const currentOrientationRef = useRef<ScreenOrientation.Orientation | null>(null);
  useEffect(() => {
    if (Platform.OS === "web") return;
    let subscription: ScreenOrientation.Subscription | null = null;
    let cancelled = false;

    const remountFor = (orientation: ScreenOrientation.Orientation) => {
      if (currentOrientationRef.current === orientation) return; // acelasi unghi, nimic de facut
      currentOrientationRef.current = orientation;
      // Salvam starea curenta INAINTE de remontare, ca sa o restauram in noul
      // context GL (vezi inceputul lui onContextCreate). La primul apel
      // (montarea initiala a ecranului) inca nu exista o scena activa - nu
      // salvam nimic, jucatorul porneste normal din spawn.
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
      setReady(false);
      setGlMountKey(k => k + 1);
    };

    (async () => {
      // Citim orientarea curenta o data, la montare, ca sa avem o valoare de
      // start (fara sa declansam remontare - e chiar prima montare a GLView).
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

  // ---------- incarcare joc + instanta de server ----------
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

    // Lumea salvata in Studio: obiectele vizibile se randeaza EXACT cum au fost create/pozitionate.
    const boxes: AABB[] = [];
    let spawn = { x: 0, y: 0, z: 0 };
    (game.scene?.objects || []).forEach((o: SceneObj) => {
      if (o.type === SPAWN_TYPE) { spawn = { x: o.x, y: o.y, z: o.z }; return; }
      const isVisible = o.visible !== false;
      if (isVisible) scene.add(buildMesh(o));
      const isSolid = o.solid !== false;
      if (isSolid) boxes.push(aabbFor(o));
    });
    solidBoxesRef.current = boxes;
    spawnPointRef.current = spawn;

    // Daca venim dintr-o remontare (schimbare de orientare), restauram starea
    // jocului din instanta veche in loc sa trimitem jucatorul inapoi la spawn.
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
      lastCamDist.current = preserved.camDist;
      preservedStateRef.current = null;
    } else {
      pos.current.set(spawn.x, spawn.y, spawn.z);
      velY.current = 0;
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

      // NOTA: aspectul camerei se seteaza la creare (onContextCreate). Schimbarile
      // de orientare remonteaza GLView-ul (vezi glMountKey si listener-ul de
      // orientare de mai sus), deci camera se recreeaza cu aspectul corect.

      const now = Date.now();
      const dt = Math.min(0.05, (now - lastFrame) / 1000);
      lastFrame = now;

      const elapsed = (now - startedAt) / 1000;
      while (nextOp < scriptOps.length && scriptOps[nextOp].t <= elapsed) {
        applyOp(scene, scriptMeshes, scriptOps[nextOp]);
        nextOp += 1;
      }

      // Miscare jucator, relativa la directia camerei (doar swipe/pan controleaza camera).
      // jv.y > 0 inseamna ca joystick-ul a fost tras in JOS (coordonate ecran).
      // Vrem: tras in JOS => inapoi, impins in SUS => inainte.
      // camForward e vectorul "inainte" al camerei; adaugam +jv.y * camForward (nu -jv.y)
      // ca "in jos pe joystick" (jv.y pozitiv) sa impinga in directia opusa lui camForward
      // prin semnul din spatele calcului de mai jos - vezi nota linia urmatoare.
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

  const onJoyStart = (e: any) => {
    const { x, y } = e.nativeEvent;
    setJoyOrigin({ x, y });
    setJoyVisible(true);
    joyActive.current = true;
  };
  const onJoyMove = (e: any) => {
    if (!joyActive.current) return;
    const { x, y } = e.nativeEvent;
    let dx = x - joyOrigin.x, dy = y - joyOrigin.y;
    const dist = Math.min(JOYSTICK_RADIUS, Math.hypot(dx, dy));
    const ang = Math.atan2(dy, dx);
    const kx = Math.cos(ang) * dist, ky = Math.sin(ang) * dist;
    setJoyKnob({ x: kx, y: ky });
    // ky pozitiv = deget tras in jos fata de centrul joystick-ului (coordonate ecran standard)
    joyVec.current = { x: kx / JOYSTICK_RADIUS, y: ky / JOYSTICK_RADIUS };
  };
  const onJoyEnd = () => {
    joyActive.current = false;
    joyVec.current = { x: 0, y: 0 };
    setJoyKnob({ x: 0, y: 0 });
    setJoyVisible(false);
  };
  const onJoyStateChange = (e: any) => {
    const st = e.nativeEvent.state;
    if (st === State.BEGAN) onJoyStart(e);
    else if (st === State.END || st === State.CANCELLED || st === State.FAILED) onJoyEnd();
  };

  const onCamPan = (e: any) => {
    const { translationX, translationY } = e.nativeEvent;
    camAngle.current = lastCamAngle.current - translationX * 0.008;
    camPolar.current = Math.max(0.4, Math.min(Math.PI - 0.15, lastCamPolar.current - translationY * 0.006));
  };
  const onCamPanState = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) { lastCamAngle.current = camAngle.current; lastCamPolar.current = camPolar.current; }
  };
  const onCamPinch = (e: any) => {
    camDist.current = Math.max(CAM_MIN_DIST, Math.min(CAM_MAX_DIST, lastCamDist.current / e.nativeEvent.scale));
  };
  const onCamPinchState = (e: any) => { if (e.nativeEvent.oldState === State.ACTIVE) lastCamDist.current = camDist.current; };

  function doRespawn() {
    const sp = spawnPointRef.current;
    pos.current.set(sp.x, sp.y, sp.z);
    velY.current = 0;
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
    // NOTA (Viziune): three.js seteaza viewport-ul GL la size * pixelRatio, dar
    // bufferul expo-gl are dimensiune fixa. La 1.4 / 2 viewport-ul depaseste
    // bufferul, deci se vede doar o parte din scena, marita (personajul se muta
    // spre coltul din dreapta-sus). Comportamentul e pastrat intentionat ca
    // "Viziunea 1/2/3", la cererea utilizatorului. NU folosi pixelRatio pentru
    // calitate grafica - pentru asta exista setarea Grafica (iluminare).
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
      {Platform.OS === "web" || !game ? (
        <View style={[StyleSheet.absoluteFillObject, styles.webFallback]}>
          <MaterialCommunityIcons name="cube-outline" size={80} color={colors.brand} />
          <Text style={styles.webText}>{game?.title || ""}</Text>
        </View>
      ) : (
        // key={glMountKey}: GLView se remonteaza la fiecare schimbare REALA de
        // orientare a device-ului (inclusiv landscape-stanga <-> landscape-dreapta),
        // detectata prin evenimentul nativ, nu prin screenW/screenH. Remontarea
        // creeaza un context GL nou, cu suprafata EGL alocata corect la
        // orientarea curenta. Starea jocului (pozitie, unghi camera) e pastrata
        // si restaurata in onContextCreate, ca userul sa nu simta un reset vizual.
        <GLView key={glMountKey} style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
      )}

      {ready && Platform.OS !== "web" ? (
        <PinchGestureHandler onGestureEvent={onCamPinch} onHandlerStateChange={onCamPinchState}>
          <PanGestureHandler onGestureEvent={onCamPan} onHandlerStateChange={onCamPanState} minPointers={1} maxPointers={1}>
            <View style={[styles.cameraZone, { width: isLandscape ? "40%" : "55%" }]} />
          </PanGestureHandler>
        </PinchGestureHandler>
      ) : null}

      {ready && Platform.OS !== "web" ? (
        <PanGestureHandler onGestureEvent={onJoyMove} onHandlerStateChange={onJoyStateChange} minPointers={1} maxPointers={1}>
          <View style={styles.joystickZone}>
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
        </PanGestureHandler>
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
          <Pressable style={[styles.menuBox, isLandscape && styles.menuBoxLandscape]} onPress={e => e.stopPropagation?.()}>
            <ScrollView showsVerticalScrollIndicator={false}>
              <Text style={styles.menuTitle}>Settings</Text>

              <Text style={styles.settingLabel}>Viziune</Text>
              <View style={styles.qualityRow}>
                {(["low", "medium", "high"] as GraphicsQuality[]).map(q => (
                  <Pressable key={q} testID={`play-quality-${q}`} onPress={() => setQuality(q)} style={[styles.qualityChip, quality === q && styles.qualityChipActive]}>
                    <Text style={[styles.qualityChipText, quality === q && { color: colors.brand }]}>{QUALITY_LABELS[q]}</Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.settingLabel}>Grafică: {graphicsLevel}/10</Text>
              <LevelPicker value={graphicsLevel} onChange={setGraphicsLevel} testIDPrefix="play-graphics" />

              <Text style={styles.settingLabel}>Render: {renderLevel}/10 · {renderDistance}m</Text>
              <LevelPicker value={renderLevel} onChange={setRenderLevel} testIDPrefix="play-render" />

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