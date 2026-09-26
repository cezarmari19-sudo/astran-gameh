import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, Platform, Modal } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { PanGestureHandler, PinchGestureHandler, State } from "react-native-gesture-handler";
import { DeviceMotion } from "expo-sensors";
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

// Giroscop: cat de mult influenteaza inclinarea telefonului rotirea camerei, pe langa swipe.
// Valorile mici insumate cu swipe-ul dau o senzatie fluida, nu brusca - swipe-ul ramane
// controlul principal, giroscopul adauga o senzatie de "priveste in jur inclinand telefonul".
const GYRO_YAW_SENSITIVITY = 1.4;   // rotatia stanga-dreapta a telefonului (beta pe Android/iOS, in jurul axei verticale)
const GYRO_PITCH_SENSITIVITY = 1.1; // inclinarea in sus/jos a telefonului
const GYRO_SMOOTHING = 0.12;        // 0..1, cat de repede urmeaza camera unghiul brut al giroscopului (mai mic = mai fluid)

type GraphicsQuality = "low" | "medium" | "high";

export default function PlayScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();

  const [game, setGame] = useState<any>(null);
  const [isOwner, setIsOwner] = useState(false);
  const [ready, setReady] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [quality, setQuality] = useState<GraphicsQuality>("medium");
  const [renderDistance, setRenderDistance] = useState(60);
  const [gyroEnabled, setGyroEnabled] = useState(true);

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
  const playerGroupRef = useRef<THREE.Group | null>(null);
  const playerHalfHeight = useRef(PLAYER_HALF_HEIGHT_DEFAULT);
  const alive = useRef(true);
  const rafId = useRef<number | null>(null);

  const pos = useRef(new THREE.Vector3(0, 0, 0));
  const velY = useRef(0);
  const facingAngle = useRef(0);

  // Camera: unghiul final = swipe (baza manuala) + contributie giroscop (relativa, netezita)
  const camAngle = useRef(0.0);
  const camPolar = useRef(1.15);
  const camDist = useRef(4.5);
  const lastCamAngle = useRef(0);
  const lastCamPolar = useRef(1.15);
  const lastCamDist = useRef(4.5);
  const firstPerson = useRef(false);

  // Giroscop: unghiuri brute citite din senzor si contributia lor netezita, separate de swipe
  const gyroYawRaw = useRef(0);
  const gyroPitchRaw = useRef(0);
  const gyroYawSmoothed = useRef(0);
  const gyroPitchSmoothed = useRef(0);
  const gyroBaseYaw = useRef<number | null>(null);   // unghiul initial al telefonului, folosit ca "zero" relativ
  const gyroBasePitch = useRef<number | null>(null);

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

  // ---------- giroscop: citim orientarea telefonului, calculam o contributie RELATIVA (nu absoluta) ----------
  // Relativ, ca playerul sa poata tine telefonul in orice pozitie de start confortabila - primul
  // cadru citit devine "centrul", iar miscarea ulterioara a telefonului roteste camera fata de acel centru.
  useEffect(() => {
    if (Platform.OS === "web" || !gyroEnabled) return;
    let sub: any;
    (async () => {
      const available = await DeviceMotion.isAvailableAsync().catch(() => false);
      if (!available) return;
      DeviceMotion.setUpdateInterval(33); // ~30fps, suficient pentru camera, fara sa consume prea multa baterie
      sub = DeviceMotion.addListener(evt => {
        const rotation = evt.rotation; // { alpha, beta, gamma } in radiani
        if (!rotation) return;
        const yaw = rotation.alpha ?? 0;
        const pitch = rotation.beta ?? 0;
        if (gyroBaseYaw.current === null) { gyroBaseYaw.current = yaw; gyroBasePitch.current = pitch; }
        gyroYawRaw.current = (yaw - gyroBaseYaw.current) * GYRO_YAW_SENSITIVITY;
        gyroPitchRaw.current = (pitch - (gyroBasePitch.current ?? 0)) * GYRO_PITCH_SENSITIVITY;
      });
    })();
    return () => { sub?.remove(); };
  }, [gyroEnabled]);

  // ---------- incarcare joc + instanta de server ----------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [g, me] = await Promise.all([api(`/games/${id}`), api("/auth/me")]);
        if (cancelled) return;
        setGame(g.game);
        setIsOwner(!!me?.user?.user_id && me.user.user_id === g.game?.owner_id);

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
          // erorile de script sunt de interes pentru dezvoltator, niciodata afisate playerului
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

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const dir = new THREE.DirectionalLight(0xffffff, 1.1);
    dir.position.set(5, 10, 4);
    scene.add(dir);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(80, 80, 1, 1),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(game.scene?.ground || "#1A1D21") })
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);

    // Lumea salvata in Studio: obiectele vizibile se randeaza EXACT cum au fost create/pozitionate,
    // fara nicio modificare de aspect. Doar cele solide (indiferent daca vizibile sau nu) devin collision.
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
    pos.current.set(spawn.x, spawn.y, spawn.z);
    velY.current = 0;

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
        applyOp(scene, scriptMeshes, scriptOps[nextOp]);
        nextOp += 1;
      }

      // netezirea contributiei giroscopului (independent de swipe, care ramane instant/direct)
      gyroYawSmoothed.current += (gyroYawRaw.current - gyroYawSmoothed.current) * GYRO_SMOOTHING;
      gyroPitchSmoothed.current += (gyroPitchRaw.current - gyroPitchSmoothed.current) * GYRO_SMOOTHING;
      const effectiveAngle = camAngle.current + (gyroEnabled ? gyroYawSmoothed.current : 0);
      const effectivePolar = Math.max(0.4, Math.min(Math.PI - 0.15, camPolar.current + (gyroEnabled ? gyroPitchSmoothed.current : 0)));

      // miscare jucator - directia se calculeaza fata de directia CAMEREI (swipe + giroscop combinate),
      // avatarul insusi nu se roteste singur, doar cand jucatorul se misca activ cu joystick-ul
      const jv = joyVec.current;
      const moveMag = Math.min(1, Math.hypot(jv.x, jv.y));
      if (moveMag > 0.05) {
        const camForward = new THREE.Vector3(Math.sin(effectiveAngle), 0, Math.cos(effectiveAngle));
        const camRight = new THREE.Vector3(camForward.z, 0, -camForward.x);
        const moveDir = new THREE.Vector3()
          .addScaledVector(camForward, -jv.y)
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
        const lookDir = new THREE.Vector3(Math.sin(effectiveAngle), 0, Math.cos(effectiveAngle));
        camera.lookAt(camera.position.clone().add(lookDir));
      } else {
        const target = new THREE.Vector3(pos.current.x, eyeY, pos.current.z);
        const r = camDist.current, th = effectiveAngle, ph = effectivePolar;
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

  // swipe: ramane controlul manual principal, se aduna liber peste contributia giroscopului
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
    if (r) r.setPixelRatio(quality === "low" ? 1 : quality === "medium" ? 1.4 : 2);
  }, [quality]);
  useEffect(() => {
    const cam = cameraRef.current;
    if (cam) { cam.far = renderDistance; cam.updateProjectionMatrix(); }
  }, [renderDistance]);

  return (
    <View style={styles.root}>
      {Platform.OS === "web" || !game ? (
        <View style={[StyleSheet.absoluteFillObject, styles.webFallback]}>
          <MaterialCommunityIcons name="cube-outline" size={80} color={colors.brand} />
          <Text style={styles.webText}>{game?.title || ""}</Text>
        </View>
      ) : (
        <GLView style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
      )}

      {ready && Platform.OS !== "web" ? (
        <PinchGestureHandler onGestureEvent={onCamPinch} onHandlerStateChange={onCamPinchState}>
          <PanGestureHandler onGestureEvent={onCamPan} onHandlerStateChange={onCamPanState} minPointers={1} maxPointers={1}>
            <View style={styles.cameraZone} />
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

      {/* Singurul element de UI permanent: butonul de meniu "A", stanga sus */}
      <SafeAreaView edges={["top"]} style={styles.aBtnWrap} pointerEvents="box-none">
        <Pressable testID="play-menu-btn" onPress={() => setShowMenu(true)} style={styles.aBtn}>
          <Text style={styles.aBtnText}>A</Text>
        </Pressable>
      </SafeAreaView>

      <Modal visible={showMenu} transparent animationType="fade" onRequestClose={() => setShowMenu(false)}>
        <Pressable style={styles.menuBackdrop} onPress={() => setShowMenu(false)}>
          <View style={styles.menuBox}>
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
          <Pressable style={styles.menuBox} onPress={e => e.stopPropagation?.()}>
            <Text style={styles.menuTitle}>Settings</Text>

            <Text style={styles.settingLabel}>Camera: Gyroscope</Text>
            <Pressable testID="play-toggle-gyro" onPress={() => { setGyroEnabled(v => !v); gyroBaseYaw.current = null; gyroBasePitch.current = null; }} style={styles.gyroToggle}>
              <MaterialCommunityIcons name={gyroEnabled ? "toggle-switch" : "toggle-switch-off-outline"} size={26} color={gyroEnabled ? colors.brand : colors.onSurface3} />
              <Text style={styles.gyroToggleText}>{gyroEnabled ? "On - tilt your phone to look around" : "Off"}</Text>
            </Pressable>

            <Text style={styles.settingLabel}>Graphics Quality</Text>
            <View style={styles.qualityRow}>
              {(["low", "medium", "high"] as GraphicsQuality[]).map(q => (
                <Pressable key={q} testID={`play-quality-${q}`} onPress={() => setQuality(q)} style={[styles.qualityChip, quality === q && styles.qualityChipActive]}>
                  <Text style={[styles.qualityChipText, quality === q && { color: colors.brand }]}>{q}</Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.settingLabel}>Render Distance: {renderDistance}m</Text>
            <View style={styles.distRow}>
              {[30, 60, 100, 150].map(d => (
                <Pressable key={d} testID={`play-distance-${d}`} onPress={() => setRenderDistance(d)} style={[styles.qualityChip, renderDistance === d && styles.qualityChipActive]}>
                  <Text style={[styles.qualityChipText, renderDistance === d && { color: colors.brand }]}>{d}m</Text>
                </Pressable>
              ))}
            </View>

            <Pressable testID="play-settings-close" onPress={() => setShowSettings(false)} style={styles.menuCloseBtn}>
              <Text style={styles.menuCloseBtnText}>Close</Text>
            </Pressable>
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
  cameraZone: { position: "absolute", top: 0, bottom: 0, right: 0, width: "55%" },
  joystickZone: { position: "absolute", left: 0, bottom: 0, width: 180, height: 180 },
  joyBase: { position: "absolute", width: 104, height: 104, borderRadius: 52, backgroundColor: "rgba(255,255,255,0.12)", borderWidth: 2, borderColor: "rgba(255,255,255,0.35)", alignItems: "center", justifyContent: "center" },
  joyKnob: { width: 46, height: 46, borderRadius: 23, backgroundColor: "rgba(204,255,0,0.85)" },
  joyHint: { position: "absolute", left: 24, bottom: 24, width: 44, height: 44, borderRadius: 22, borderWidth: 2, borderColor: "rgba(255,255,255,0.25)", alignItems: "center", justifyContent: "center" },
  aBtnWrap: { position: "absolute", top: 0, left: 0, padding: spacing.md },
  aBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(0,0,0,0.6)", borderWidth: 1, borderColor: "rgba(255,255,255,0.2)", alignItems: "center", justifyContent: "center" },
  aBtnText: { color: colors.brand, fontWeight: "900", fontSize: 16 },
  menuBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  menuBox: { width: "100%", maxWidth: 340, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  menuTitle: { color: colors.onSurface, fontSize: 17, fontWeight: "900", marginBottom: 14 },
  menuRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12 },
  menuRowText: { color: colors.onSurface, fontSize: 14, fontWeight: "700" },
  settingLabel: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14, marginBottom: 8 },
  gyroToggle: { flexDirection: "row", alignItems: "center", gap: 10 },
  gyroToggleText: { color: colors.onSurface2, fontSize: 12, fontWeight: "600", flex: 1 },
  qualityRow: { flexDirection: "row", gap: 8 },
  distRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  qualityChip: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  qualityChipActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  qualityChipText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12, textTransform: "capitalize" },
  menuCloseBtn: { marginTop: 18, backgroundColor: colors.brand, borderRadius: radius.pill, paddingVertical: 12, alignItems: "center" },
  menuCloseBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
});