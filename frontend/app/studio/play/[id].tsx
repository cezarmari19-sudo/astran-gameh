import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { api } from "@/src/api/client";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";

type SceneObj = { id: string; type: string; x: number; y: number; z: number; color: string; scale: number };

// Operatie produsa de scriptul Luau al jocului (vezi backend/astran_sandbox)
type ScriptOp = {
  t: number; // secunde de la startul jocului
  op: "create" | "set" | "destroy";
  id: string;
  type?: string;
  x?: number;
  y?: number;
  z?: number;
  color?: string;
  scale?: number;
  name?: string;
};

type MeshState = { x: number; y: number; z: number; scale: number };

function geometryFor(type: string): THREE.BufferGeometry {
  if (type === "cube") return new THREE.BoxGeometry(1, 1, 1);
  if (type === "sphere") return new THREE.SphereGeometry(0.6, 20, 16);
  if (type === "cylinder") return new THREE.CylinderGeometry(0.5, 0.5, 1.2, 20);
  if (type === "cone") return new THREE.ConeGeometry(0.6, 1.2, 20);
  return new THREE.ConeGeometry(0.7, 1.6, 8);
}

function buildMesh(o: SceneObj): THREE.Mesh {
  const geo = geometryFor(o.type);
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(o.color), roughness: 0.5, metalness: 0.1 });
  const m = new THREE.Mesh(geo, mat);
  m.position.set(o.x, o.y + 0.5 * o.scale, o.z);
  m.scale.setScalar(o.scale);
  return m;
}

function placeMesh(m: THREE.Mesh) {
  const s = m.userData as MeshState;
  m.position.set(s.x, s.y + 0.5 * s.scale, s.z);
  m.scale.setScalar(s.scale);
}

function disposeMesh(m: THREE.Mesh) {
  m.geometry.dispose();
  (m.material as THREE.Material).dispose();
}

// Aplica in scena o operatie venita din script
function applyOp(scene: THREE.Scene, meshes: Map<string, THREE.Mesh>, op: ScriptOp) {
  if (op.op === "create") {
    const old = meshes.get(op.id);
    if (old) {
      scene.remove(old);
      disposeMesh(old);
    }
    const state: MeshState = { x: op.x ?? 0, y: op.y ?? 0, z: op.z ?? 0, scale: op.scale ?? 1 };
    const m = buildMesh({ id: op.id, type: op.type || "cube", x: state.x, y: state.y, z: state.z, color: op.color || "#A3A3A3", scale: state.scale });
    m.userData = state;
    scene.add(m);
    meshes.set(op.id, m);
    return;
  }

  const m = meshes.get(op.id);
  if (!m) return;

  if (op.op === "destroy") {
    scene.remove(m);
    disposeMesh(m);
    meshes.delete(op.id);
    return;
  }

  // op.op === "set"
  const s = m.userData as MeshState;
  if (op.x !== undefined) s.x = op.x;
  if (op.y !== undefined) s.y = op.y;
  if (op.z !== undefined) s.z = op.z;
  if (op.scale !== undefined) s.scale = op.scale;
  if (op.color) (m.material as THREE.MeshStandardMaterial).color.set(op.color);
  if (op.type) {
    m.geometry.dispose();
    m.geometry = geometryFor(op.type);
  }
  placeMesh(m);
}

export default function PlayScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useI18n();
  const [game, setGame] = useState<any>(null);
  const [isOwner, setIsOwner] = useState(false);
  const playerRef = useRef<THREE.Mesh | null>(null);
  const scriptOpsRef = useRef<ScriptOp[]>([]);
  const initialPos = useRef({ x: 0, y: 0.5, z: 0 });
  const [resetKey, setResetKey] = useState(0);

  useEffect(() => {
    (async () => {
      try {
        const [g] = await Promise.all([
          api(`/games/${id}`),
          api(`/games/${id}/play`, { method: "POST" }),
        ]);

        // Scriptul Luau ruleaza in sandbox pe server; aici primim doar efectele lui.
        // Daca sandbox-ul nu e disponibil, jocul porneste normal, fara script.
        let ops: ScriptOp[] = [];
        try {
          const run = await api(`/sandbox/games/${id}/run`, { method: "POST" });
          ops = Array.isArray(run?.ops) ? run.ops : [];
        } catch {}
        scriptOpsRef.current = ops;

        setGame(g.game);

        try {
          const me = await api("/auth/me");
          setIsOwner(!!me?.user?.user_id && me.user.user_id === g.game?.owner_id);
        } catch {}
      } catch {}
    })();
  }, [id]);

  const onContextCreate = async (gl: any) => {
    if (!game) return;
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setSize(w, h);
    const sky = game.scene?.sky || "#0F1012";
    renderer.setClearColor(new THREE.Color(sky), 1);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(70, w / h, 0.1, 200);
    camera.position.set(6, 5, 8);
    camera.lookAt(0, 0, 0);

    scene.add(new THREE.AmbientLight(0xffffff, 0.5));
    const dir = new THREE.DirectionalLight(0xffffff, 1.1);
    dir.position.set(5, 10, 4);
    scene.add(dir);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(40, 40, 30, 30),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(game.scene?.ground || "#1A1D21"), wireframe: true })
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);

    (game.scene?.objects || []).forEach((o: SceneObj) => scene.add(buildMesh(o)));

    const player = new THREE.Mesh(
      new THREE.BoxGeometry(0.8, 0.8, 0.8),
      new THREE.MeshStandardMaterial({ color: 0xCCFF00, emissive: 0x2A3300, roughness: 0.3 })
    );
    player.position.set(0, 0.5, 0);
    scene.add(player);
    playerRef.current = player;
    initialPos.current = { x: 0, y: 0.5, z: 0 };

    // Redarea operatiilor din script, in ordinea si la momentul la care au aparut
    const scriptOps = scriptOpsRef.current;
    const scriptMeshes = new Map<string, THREE.Mesh>();
    let nextOp = 0;
    const startedAt = Date.now();

    let t0 = 0;
    const render = () => {
      requestAnimationFrame(render);

      const elapsed = (Date.now() - startedAt) / 1000;
      while (nextOp < scriptOps.length && scriptOps[nextOp].t <= elapsed) {
        applyOp(scene, scriptMeshes, scriptOps[nextOp]);
        nextOp += 1;
      }

      t0 += 0.008;
      if (playerRef.current) playerRef.current.rotation.y += 0.02;
      camera.position.x = Math.cos(t0 * 0.4) * 9;
      camera.position.z = Math.sin(t0 * 0.4) * 9;
      camera.position.y = 5 + Math.sin(t0) * 0.5;
      camera.lookAt(0, 0.5, 0);
      renderer.render(scene, camera);
      gl.endFrameEXP();
    };
    render();
  };

  const doReset = () => {
    if (playerRef.current) {
      playerRef.current.position.set(initialPos.current.x, initialPos.current.y, initialPos.current.z);
      playerRef.current.rotation.set(0, 0, 0);
    }
    setResetKey(k => k + 1);
  };

  return (
    <View style={styles.root}>
      {Platform.OS === "web" || !game ? (
        <View style={[StyleSheet.absoluteFillObject, styles.webFallback]}>
          <MaterialCommunityIcons name="cube-outline" size={80} color={colors.brand} />
          <Text style={styles.webText}>{game?.title || "Loading..."}</Text>
          <Text style={styles.webSub}>{game ? `${game.scene?.objects?.length || 0} objects · 3D on Expo Go` : ""}</Text>
        </View>
      ) : (
        <GLView key={resetKey} style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
      )}

      <SafeAreaView edges={["top"]} style={styles.topBar} pointerEvents="box-none">
        <Pressable testID="play-back-btn" onPress={() => router.back()} style={styles.iconBtn}>
          <MaterialCommunityIcons name="chevron-left" size={24} color={colors.onSurface} />
        </Pressable>
        <View style={styles.titlePill}>
          <Text style={styles.titleText} numberOfLines={1}>{game?.title || "..."}</Text>
        </View>
        {isOwner ? (
          <Pressable
            testID="play-script-btn"
            onPress={() => router.push(`/studio/script/${id}` as any)}
            style={styles.iconBtn}
          >
            <MaterialCommunityIcons name="code-braces" size={22} color={colors.onSurface} />
          </Pressable>
        ) : (
          <View style={{ width: 40 }} />
        )}
      </SafeAreaView>

      <SafeAreaView edges={["bottom"]} style={styles.leftControls} pointerEvents="box-none">
        <Pressable testID="play-reset-btn" onPress={doReset} style={styles.resetBtn}>
          <MaterialCommunityIcons name="restart" size={22} color={colors.onBrand} />
          <Text style={styles.resetText}>{t("reset")}</Text>
        </Pressable>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  webFallback: { alignItems: "center", justifyContent: "center", gap: 12 },
  webText: { color: colors.onSurface, fontWeight: "800", fontSize: 18 },
  webSub: { color: colors.onSurface3, fontSize: 12 },
  topBar: { position: "absolute", top: 0, left: 0, right: 0, flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md },
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center" },
  titlePill: { flex: 1, marginHorizontal: 10, backgroundColor: "rgba(0,0,0,0.6)", paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, alignItems: "center" },
  titleText: { color: colors.onSurface, fontWeight: "800", fontSize: 13 },
  leftControls: { position: "absolute", left: 0, bottom: 0, padding: spacing.lg },
  resetBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.brand, paddingHorizontal: 14, paddingVertical: 10, borderRadius: radius.pill },
  resetText: { color: colors.onBrand, fontWeight: "900", fontSize: 13, letterSpacing: 1 },
});