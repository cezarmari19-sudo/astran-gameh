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
import { createObject, applyLocalTransform, applyMaterial, normalizeParts } from "@/src/studio3d/modelTypes";
import { usePlayerSettings, GraphicsQuality } from "@/src/hooks/usePlayerSettings";

// ---------- operatii de script (redate silentios; erorile se logheaza, nu se afiseaza in UI) ----------
type ScriptOp = {
  t: number; op: "create" | "set" | "destroy" | "world" | "material"; id: string;
  type?: string; x?: number; y?: number; z?: number; color?: string; scale?: number; name?: string;
  // fizica (vezi backend/astran_sandbox/prelude.luau si runner.sanitize_ops)
  gravity?: number; air?: number;                          // op: "world"
  density?: number; friction?: number; bounce?: number; liquid?: boolean; // op: "material"
  material?: string; anchored?: boolean; collide?: boolean; // pe "create"/"set"
  vx?: number; vy?: number; vz?: number;
};
type MeshState = { x: number; y: number; z: number; scale: number };

// ---------- Spawn Points & Checkpoints (vezi si sceneShared.ts pentru campurile de pe SceneObj) ----------
// Reprezentarea "de runtime" a unui Spawn Point/Checkpoint activ (enabled !== false), extrasa
// o singura data la incarcarea scenei. Nu depinde de niciun numar fix de puncte - poate fi orice
// numar de Spawn Points/Checkpoints, exact cerinta din Game Studio.
type SpawnRuntime = {
  id: string;
  kind: "spawn" | "checkpoint";
  initial: boolean;
  x: number; y: number; z: number;
  ry: number;    // orientarea (grade) cu care playerul trebuie sa apara la acest punct
  radius: number; // raza zonei de activare (doar pt checkpoint) / nefolosita pt spawn simplu
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
  // "world" si "material" nu au un mesh asociat: schimba starea globala a lumii fizice.
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
    // Implicit piesele create de script sunt FIXE (Anchored = true), la fel ca inainte
    // de fizica reala - devin mobile doar daca scriptul seteaza explicit Anchored = false.
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
  // Corpul mobil isi ia pozitia din fizica in fiecare cadru (vezi bucla de render);
  // aici doar aplicam pe corpul fizic schimbarile explicite venite din script.
  if (op.x !== undefined || op.y !== undefined || op.z !== undefined) physics.setPosition(op.id, op.x, op.y, op.z);
  if (op.scale !== undefined) physics.setScale(op.id, op.scale);
  if (op.type) physics.setShapeType(op.id, op.type as PhysicsShapeType);
  if (op.material !== undefined) physics.setMaterial(op.id, op.material);
  if (op.anchored !== undefined) physics.setAnchored(op.id, op.anchored);
  if (op.collide !== undefined) physics.setCollide(op.id, op.collide);
  if (op.vx !== undefined || op.vy !== undefined || op.vz !== undefined) physics.setVelocity(op.id, op.vx, op.vy, op.vz);
  // Daca e mobil, pozitia vizuala reala vine din fizica la urmatorul cadru - nu suprascriem
  // aici cu placeMesh() valorile explicite de mai sus, ca sa nu "sara" inainte de primul step.
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
// Raza zonei de activare a unui Checkpoint, relativa la scala discului sau (vezi geometryFor
// "spawn" in sceneShared.ts: disc de raza 0.6 * scale). Facuta putin mai mare decat discul
// vizual, ca activarea sa se simta naturala (nu trebuie calcat exact pe centrul discului).
const CHECKPOINT_RADIUS_FACTOR = 1.4;
const CHECKPOINT_MIN_RADIUS = 0.6;

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

type TouchXY = { x: number; y: number };

export default function PlayScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { width: screenW, height: screenH } = useWindowDimensions();

  const [game, setGame] = useState<any>(null);
  const [ready, setReady] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  // Orice eroare care scapa din onContextCreate sau din bucla de randare ajunge aici -
  // ecranul afiseaza mesajul REAL in loc sa ramana un ecran gri/inghetat fara nicio
  // explicatie. Vezi runCrashGuard/render() mai jos.
  const [crashError, setCrashError] = useState<string | null>(null);

  // Setarile PLAYERULUI (Graphics Quality, Render Distance, etc) - persistente, aceleasi in
  // orice joc, salvate automat la fiecare schimbare (fara buton de Save). Vezi
  // src/hooks/usePlayerSettings.ts si backend/astran_sandbox/user_settings_routes.py.
  const { settings: playerSettings, loaded: settingsLoaded, update: updateSetting } = usePlayerSettings();
  const quality = playerSettings.graphics_quality;
  const renderLevel = playerSettings.render_level;
  const graphicsLevel = playerSettings.graphics_level;
  const renderDistance = RENDER_DISTANCES[renderLevel - 1];
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
  // Spawn-ul initial al jocului (rezolvat o data la incarcarea scenei - vezi onContextCreate).
  // Are mereu o valoare valida (fallback (0,0,0) daca jocul nu are niciun Spawn Point configurat -
  // compatibil cu jocurile foarte vechi, dinainte de aceasta functionalitate).
  const spawnPointRef = useRef<{ x: number; y: number; z: number; ry: number }>({ x: 0, y: 0, z: 0, ry: 0 });
  // Toate Checkpoint-urile active (enabled !== false) din scena - oricate, nu doar unul.
  const checkpointsRef = useRef<SpawnRuntime[]>([]);
  // Checkpoint-ul activ AL ACESTUI PLAYER in sesiunea curenta - independent de alti jucatori,
  // pentru ca fiecare client isi tine propria stare locala (nu exista sincronizare de server
  // pentru progresul de checkpoint-uri). null = niciun checkpoint activat inca.
  const activeCheckpointRef = useRef<SpawnRuntime | null>(null);
  const solidBoxesRef = useRef<AABB[]>([]);

  // Asteptam setarile playerului ca sa pornim Play Mode cu ele deja aplicate (cerinta punctul
  // 4: "incarca setarile -> aplica -> apoi porneste jocul", nu default-uri urmate de un "sarit"
  // vizual cand ajunge raspunsul). O mica intarziere, o singura data, la intrarea in orice joc.
  const canStartGL = settingsLoaded && !!game;

  // Three.js
  const rendererRef = useRef<Renderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const lightsRef = useRef<Lights | null>(null);
  const physicsRef = useRef<PhysicsWorld | null>(null);
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

  // Elibereaza TOATE resursele GPU ale scenei curente (geometrii, materiale, renderer) -
  // THREE.js NU le elibereaza singur la demontare, trebuie .dispose() explicit pe fiecare,
  // altfel fiecare remontare a GLView-ului (schimbare de orientare, sau iesirea din ecran)
  // lasa in urma memorie GPU nefolosita dar nealocata inapoi - exact tipul de "leak" care, cu
  // destule remontari, umple memoria telefonului si duce in cele din urma la crash/ecran gri.
  function disposeSceneResources() {
    const scene = sceneRef.current;
    if (scene) {
      scene.traverse((obj: any) => {
        if (obj.isMesh) {
          obj.geometry?.dispose?.();
          const mat = obj.material;
          if (Array.isArray(mat)) mat.forEach((m: THREE.Material) => m.dispose());
          else mat?.dispose?.();
        }
      });
    }
    try { (rendererRef.current as any)?.dispose?.(); } catch {}
    rendererRef.current = null;
    sceneRef.current = null;
    cameraRef.current = null;
    lightsRef.current = null;
    playerGroupRef.current = null;
  }

  // ============================================================================================
  // MULTI-TOUCH REAL - DE CE E UN SINGUR RESPONDER, NU DOUA:
  //
  // Incercarea veche (pastrata ca istoric in alte fisiere ale proiectului) avea doua View-uri
  // SEPARATE, fiecare cu propriul onStartShouldSetResponder + onResponderTerminationRequest.
  // Asta era bug-ul: React Native are UN SINGUR "responder" activ pentru intreaga aplicatie.
  // Cand primul deget atinge, de ex., zona joystick-ului, acel View devine responder-ul curent.
  // Cand al doilea deget atinge zona camerei, RN intreaba responder-ul curent (joystick-ul)
  // daca accepta sa cedeze (onResponderTerminationRequest) - raspunsul era mereu "false", deci
  // cererea e refuzata si zona camerei NU primeste niciodata evenimentele acelui deget (si
  // invers, daca ordinea era inversa). De-aia joystick-ul si camera se blocau reciproc.
  //
  // SOLUTIA CORECTA: UN SINGUR View responder, care acopera tot ecranul si citeste el insusi
  // toate touch-urile active (nativeEvent.touches). Fiecare touch nou e asignat, dupa pozitia
  // lui pe ecran, fie joystick-ului fie camerei (vezi assignTouch mai jos); apoi, la fiecare
  // miscare, se actualizeaza independent starea joystick-ului si starea camerei pentru touch-
  // urile care le apartin. Nu mai exista NICIUN alt View cu care sa se negocieze "cine e
  // responder-ul" - deci nu mai exista cine sa refuze pe cine, si cele doua zone chiar
  // functioneaza simultan.
  // ============================================================================================

  // Pozitia absoluta pe ecran a fiecarei zone (masurata prin onLayout) - necesara ca sa
  // convertim coordonatele absolute (pageX/pageY) ale unui touch in coordonate relative la
  // zona lui, si ca sa stim in ce zona a "cazut" un touch nou.
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
    if (joyTouchId.current !== null) return; // deja avem un deget pe joystick
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
    // ky pozitiv = deget tras in jos fata de centrul joystick-ului (coordonate ecran standard)
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
    if (camTouchIds.current.length >= 2) return; // doar 1 (rotire) sau 2 degete (pinch)
    camTouchIds.current.push(t.identifier);
    camTouchPos.current.set(t.identifier, { x: t.pageX, y: t.pageY });
    camTouchStartPos.current.set(t.identifier, { x: t.pageX, y: t.pageY });

    if (camTouchIds.current.length === 1) {
      // primul deget: incepe o rotatie noua, fata de pozitia curenta a camerei
      lastCamAngle.current = camAngle.current;
      lastCamPolar.current = camPolar.current;
    } else if (camTouchIds.current.length === 2) {
      // al doilea deget a intrat: trecem pe pinch-to-zoom; rotatia se opreste cat timp
      // sunt 2 degete
      const [idA, idB] = camTouchIds.current;
      const a = camTouchPos.current.get(idA)!, b = camTouchPos.current.get(idB)!;
      camPinchStartDist.current = Math.hypot(a.x - b.x, a.y - b.y);
      camPinchStartCamDist.current = camDist.current;
    }
  }

  // Asigneaza un touch NOU (identificator inca necunoscut) zonei lui, dupa pozitia absoluta
  // pe ecran. Apelata atat la primul touch (onResponderGrant), cat si pentru orice deget care
  // mai intra ulterior, cat timp alte degete sunt deja active (detectat in onRootResponderMove
  // prin comparatie cu id-urile deja cunoscute).
  function assignTouch(t: { identifier: number; pageX: number; pageY: number }) {
    const jz = joyZoneLayout.current;
    const inJoyZone = t.pageX >= jz.x && t.pageX <= jz.x + jz.w && t.pageY >= jz.y && t.pageY <= jz.y + jz.h;
    if (inJoyZone && joyTouchId.current === null) { startJoystick(t); return; }
    const cz = camZoneLayout.current;
    const inCamZone = t.pageX >= cz.x && t.pageX <= cz.x + cz.w && t.pageY >= cz.y && t.pageY <= cz.y + cz.h;
    if (inCamZone && camTouchIds.current.length < 2) startCameraTouch(t);
  }

  // Orice exceptie care scapa dintr-un handler de touch (sau dintr-un useEffect care ruleaza
  // DUPA montare, in afara buclei de randare din onContextCreate) nu trecea prin NICIUN
  // try/catch - spre deosebire de onContextCreate si de bucla render() de mai jos, care isi
  // afiseaza eroarea pe crashError. O exceptie neprinsa intr-un handler de event (de ex. un
  // undefined neasteptat in camTouchPos, provocat de o secventa rara de atingere/ridicare a
  // degetelor) omora firul JS: din acel moment React Native nu mai poate rula NICIUN cod -
  // inclusiv setCrashError -, deci ecranul de eroare nu mai apare deloc. Ramane "inghetat" pe
  // ultimul cadru deja randat de GL (ecranul negru descris de utilizator, cu doar butonul "A"
  // vizibil, ultimul strat nativ compus), fara niciun mesaj. Asta explica de ce bug-ul parea
  // "aleatoriu, dupa cateva secunde sau minute de joc": depinde de o secventa particulara de
  // atingeri pe ecran, nu de timpul scurs in sine. Solutia tehnic corecta NU e sa evitam acel
  // caz (ar reduce multi-touch-ul), ci sa prindem orice exceptie chiar la sursa, ca sa ajunga
  // mereu pe crashError - acelasi mecanism deja construit, acum aplicat peste tot unde poate
  // rula cod dupa montare, nu doar in bucla de randare.
  function safeHandler<T extends (...args: any[]) => void>(fn: T): T {
    return ((...args: any[]) => {
      try {
        fn(...args);
      } catch (e: any) {
        console.log("[play] touch handler crashed", e);
        if (alive.current) setCrashError(e?.message ? String(e.message) : String(e));
      }
    }) as T;
  }

  const onRootResponderGrant = safeHandler((evt: any) => {
    const t = evt.nativeEvent.changedTouches?.[0] ?? evt.nativeEvent;
    assignTouch(t);
  });

  const onRootResponderMove = safeHandler((evt: any) => {
    const touches = touchesOf(evt);

    // Deget nou aparut (al doilea/al treilea, intrat cat timp altele sunt deja active) - nu
    // trece prin onResponderGrant (acela se declanseaza o singura data, la primul deget care
    // revendica responder-ul); il detectam aici si il asignam zonei lui.
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
  });

  const onRootResponderEnd = safeHandler((evt: any) => {
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
        // a ramas un singur deget (celalalt a fost ridicat in timpul unui pinch) - re-pornim
        // rotatia de la pozitia curenta a acestui deget, ca sa nu sara camera brusc
        const tid = camTouchIds.current[0];
        const pos2 = camTouchPos.current.get(tid);
        if (pos2) camTouchStartPos.current.set(tid, pos2);
        lastCamAngle.current = camAngle.current;
        lastCamPolar.current = camPolar.current;
      }
    }
  });

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
      physicsRef.current?.dispose();
      physicsRef.current = null;
      disposeSceneResources();
    };
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
      physicsRef.current?.dispose();
      physicsRef.current = null;
      // Fara asta, fiecare schimbare de orientare lasa in urma scena/renderer-ul VECHI
      // nealocate (geometrii, materiale, bufferele GPU ale renderer-ului) - cu destule
      // rotiri intr-o singura sesiune de joc, memoria consumata creste neintrerupt pana
      // la crash. Vezi disposeSceneResources() de mai sus.
      disposeSceneResources();
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
            // IMPORTANT: datele vin direct de la server (pot fi un model vechi, salvat
            // inainte de o validare, sau pur si simplu corupt) - exact ca piesele incarcate
            // in editorul de modele (model-studio/[id].tsx), care NICIODATA nu le foloseste
            // brute, ci mereu prin normalizeParts() (vezi modelTypes.ts: "Curata ce vine de
            // la server ... valori lipsa, radacina, parinti disparuti"). Inainte, Play Mode
            // ocolea exact aceasta curatare si trimitea piesele brute direct in
            // createObject()/applyLocalTransform() - daca lista avea o intrare nula/malformata
            // sau un parent catre un id care nu mai exista, asta arunca direct "Cannot read
            // property 'x'/'name' of undefined" in buildPlayerVisual(), cu ecranul deja
            // negru/pe un cadru vechi. normalizeParts() garanteaza, exact ca in Studio, ca
            // fiecare piesa are id/name/type valide si un lant de parinti care se termina
            // mereu in ROOT_ID - fara sa reduca sau sa ascunda nimic, doar sa repare fluxul
            // de date, acelasi mecanism folosit deja de editor pentru exact acest caz.
            if (Array.isArray(item?.item?.parts)) characterPartsRef.current = normalizeParts(item.item.parts);
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
    // Orice exceptie neasteptata aici (date de scena/script malformate, un geometry/
    // material invalid, etc) iesea inainte NECAPTATA: setReady(true) nu se mai apela
    // niciodata, iar ecranul ramanea blocat pe gri la nesfarsit, fara niciun indiciu.
    // Acum eroarea REALA ajunge pe ecran (vezi crashError in JSX-ul de mai jos).
    try {
    glRef.current = gl;
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    // Setarile playerului (quality/renderDistance/graphicsLevel) sunt deja incarcate la acest
    // punct (vezi canStartGL - GLView nu se monteaza decat dupa ce settingsLoaded e true),
    // deci jocul porneste direct cu ele aplicate, fara un "jump" vizual ulterior.
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

    // Lumea fizica: gravitatie/densitatea aerului si materialele pornesc de la valorile
    // implicite (identice cu backend/astran_sandbox/prelude.luau) si sunt schimbate de
    // operatiile "world"/"material" primite de la server, redate mai jos in bucla de render.
    const physics = new PhysicsWorld();
    physicsRef.current = physics;
    physics.addGroundPlane();

    // Lumea salvata in Studio: obiectele vizibile se randeaza EXACT cum au fost create/pozitionate.
    // Intra si in fizica, implicit FIXE (Anchored = true) - opresc jucatorul si orice obiect
    // mobil creat de script, exact ca inainte de fizica reala; devin mobile doar daca scriptul le
    // schimba explicit Anchored-ul prin workspace.NumeObiect (vezi runner.scene_file / prelude.luau).
    //
    // Spawn Points si Checkpoint-urile (o.type === SPAWN_TYPE) sunt marker-e speciale: nu intra
    // niciodata in fizica de obiecte mobile (physics.addStudioObject le ignora oricum), dar de-
    // acum RESPECTA Visible si Solid ca orice alt obiect din scena (inainte erau mereu invizibile
    // si fara collision, indiferent de proprietati) - vezi punctul 9 din cerinta.
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
        // Implicit Spawn Point/Checkpoint e invizibil si fara collision (comportamentul de
        // dinainte); devine vizibil/solid DOAR daca creatorul seteaza explicit Visible/Solid = true.
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

    // Rezolvarea spawnului initial: primul Spawn Point (nu Checkpoint) marcat initial=true.
    // Daca niciunul nu e marcat asa - joc vechi salvat inainte de aceasta functionalitate, sau
    // configurare incompleta - cade pe primul Spawn Point activ gasit, iar daca jocul nu are
    // niciun Spawn Point deloc, foloseste (0,0,0) ca inainte de aceasta functionalitate.
    const spawnKindPoints = spawns.filter(p => p.kind === "spawn");
    const resolvedInitial = spawnKindPoints.find(p => p.initial) ?? spawnKindPoints[0] ?? null;
    const initial = resolvedInitial
      ? { x: resolvedInitial.x, y: resolvedInitial.y, z: resolvedInitial.z, ry: resolvedInitial.ry }
      : { x: 0, y: 0, z: 0, ry: 0 };
    spawnPointRef.current = initial;
    checkpointsRef.current = spawns.filter(p => p.kind === "checkpoint");
    // Fiecare intrare noua in joc (fiecare montare a acestui ecran) porneste fara niciun
    // checkpoint activ - playerul trebuie sa il re-activeze parcurgand zona lui din nou.
    activeCheckpointRef.current = null;

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
      preservedStateRef.current = null;
    } else {
      // Playerul apare EXACT la pozitia si orientarea Spawn Point-ului initial (punctul 9 din cerinta).
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
      // Orice exceptie intr-un cadru (script malformat, fizica instabila, etc) oprea
      // inainte bucla SILENTIOS - requestAnimationFrame nu mai era reprogramat, dar
      // niciun semnal nu ajungea pe ecran: ultimul cadru randat ramanea inghetat (sau,
      // daca exceptia venea foarte devreme, ramanea doar culoarea gri implicita a
      // suprafetei GL, inainte de primul desen). Acum eroarea REALA ajunge pe ecran.
      try {
        const now = Date.now();
        const dt = Math.min(0.05, (now - lastFrame) / 1000);
        lastFrame = now;

        const elapsed = (now - startedAt) / 1000;
        while (nextOp < scriptOps.length && scriptOps[nextOp].t <= elapsed) {
          applyOp(scene, scriptMeshes, physics, scriptOps[nextOp]);
          nextOp += 1;
        }

        // Avansam simularea fizica (gravitatie, ciocniri, densitate/frecare/elasticitate pe
        // materialele setate de script) si aducem pozitia/rotatia FIECARUI corp mobil creat
        // de script inapoi pe mesh-ul lui 3D. Corpurile fixe (Anchored) nu se misca niciodata,
        // deci nu au nevoie sa fie citite aici.
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

        // Miscare jucator, relativa la directia camerei (doar swipe pe zona camerei o roteste).
        // jv.y > 0 inseamna ca joystick-ul a fost tras in JOS (coordonate ecran).
        // Vrem: tras in JOS => inapoi, impins in SUS => inainte.
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

        // Activarea checkpoint-urilor: cand playerul intra in zona unui checkpoint, acesta devine
        // noul punct de respawn AL ACESTUI PLAYER (vezi activeCheckpointRef mai sus - independent
        // de alti jucatori). Nu conteaza ordinea in care sunt parcurse - oricare checkpoint activ
        // a carui zona o calci devine cel curent, exact ca cerinta punctelor 4-5.
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
      } catch (e: any) {
        console.log("[play] render loop crashed", e);
        if (alive.current) setCrashError(e?.message ? String(e.message) : String(e));
        return; // nu mai reprogramam cadrul urmator - bucla se opreste curat aici
      }
      rafId.current = requestAnimationFrame(render);
    };
    render();
    setReady(true);
    } catch (e: any) {
      console.log("[play] onContextCreate crashed", e);
      setCrashError(e?.message ? String(e.message) : String(e));
    }
  };

  // Respawn: foloseste checkpoint-ul activ AL ACESTUI PLAYER daca exista unul (punctul 6 din
  // cerinta); altfel cade pe spawn-ul initial al jocului. Reface si orientarea (ry), nu doar
  // pozitia, la fel ca la intrarea initiala in joc.
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

  // Reincearca dupa un crash: remonteaza GLView-ul de la zero (acelasi mecanism ca la
  // schimbarea de orientare), fara sa iasa din ecran.
  function doRetryAfterCrash() {
    setCrashError(null);
    physicsRef.current?.dispose();
    physicsRef.current = null;
    disposeSceneResources();
    setReady(false);
    setGlMountKey(k => k + 1);
  }

  useEffect(() => {
    try {
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
    } catch (e: any) {
      console.log("[play] quality effect crashed", e);
      if (alive.current) setCrashError(e?.message ? String(e.message) : String(e));
    }
  }, [quality]);
  useEffect(() => {
    try {
      if (lightsRef.current) applyGraphicsLevel(lightsRef.current, graphicsLevel);
    } catch (e: any) {
      console.log("[play] graphicsLevel effect crashed", e);
      if (alive.current) setCrashError(e?.message ? String(e.message) : String(e));
    }
  }, [graphicsLevel]);
  useEffect(() => {
    try {
      const cam = cameraRef.current;
      if (cam) { cam.far = renderDistance; cam.updateProjectionMatrix(); }
    } catch (e: any) {
      console.log("[play] renderDistance effect crashed", e);
      if (alive.current) setCrashError(e?.message ? String(e.message) : String(e));
    }
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
        // key={glMountKey}: GLView se remonteaza la fiecare schimbare REALA de
        // orientare a device-ului (inclusiv landscape-stanga <-> landscape-dreapta),
        // detectata prin evenimentul nativ, nu prin screenW/screenH. Remontarea
        // creeaza un context GL nou, cu suprafata EGL alocata corect la
        // orientarea curenta. Starea jocului (pozitie, unghi camera) e pastrata
        // si restaurata in onContextCreate, ca userul sa nu simta un reset vizual.
        //
        // canStartGL tine GLView-ul nemontat pana cand setarile playerului s-au
        // incarcat (settingsLoaded) - punctul 4 din cerinta: incarca -> aplica ->
        // abia apoi porneste jocul, nu invers.
        <GLView key={glMountKey} style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
      )}

      {/* UN SINGUR View responder peste tot ecranul: joystick-ul si camera sunt citite din
          acelasi loc (vezi onRootResponder*), ca sa nu mai existe doua View-uri care se
          negociaza reciproc responder-ul - vezi comentariul mare de mai sus din componenta. */}
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
          {/* Zona camerei (dreapta): doar layout + randare vizuala; input-ul vine din View-ul
              parinte de mai sus, deci pointerEvents="none" aici. */}
          <View
            style={[styles.cameraZone, { width: isLandscape ? "40%" : "55%" }]}
            pointerEvents="none"
            onLayout={e => {
              const { x, y, width, height } = e.nativeEvent.layout;
              camZoneLayout.current = { x, y, w: width, h: height };
            }}
          />

          {/* Zona joystick-ului (stanga-jos): doar layout + randare vizuala, acelasi motiv. */}
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

      {/* Ecranul de eroare: inlocuieste ecranul gri "mort" cu mesajul REAL al crash-ului
          si doua actiuni clare - Reincearca (remonteaza jocul fara sa iasa din el) sau
          Leave. Se afiseaza peste tot (deasupra GLView-ului, daca mai e ceva randat). */}
      <Modal visible={!!crashError} transparent animationType="fade" onRequestClose={() => {}}>
        <View style={styles.menuBackdrop}>
          <View style={[styles.menuBox, isLandscape && styles.menuBoxLandscape]}>
            <MaterialCommunityIcons name="alert-circle-outline" size={28} color={colors.error} style={{ marginBottom: 10 }} />
            <Text style={styles.menuTitle}>Jocul s-a oprit cu o eroare</Text>
            <ScrollView style={{ maxHeight: 160 }}>
              <Text style={styles.crashMessage}>{crashError}</Text>
            </ScrollView>
            <Pressable testID="play-crash-retry" onPress={doRetryAfterCrash} style={styles.menuCloseBtn}>
              <Text style={styles.menuCloseBtnText}>Retry</Text>
            </Pressable>
            <Pressable testID="play-crash-leave" onPress={doLeave} style={[styles.menuRow, { justifyContent: "center", marginTop: 4 }]}>
              <MaterialCommunityIcons name="exit-run" size={18} color={colors.error} />
              <Text style={[styles.menuRowText, { color: colors.error }]}>Leave</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

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
  crashMessage: { color: colors.onSurface2, fontSize: 12, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", marginBottom: 6 },
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