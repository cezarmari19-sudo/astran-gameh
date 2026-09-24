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
import { SceneObj, buildMesh, geometryFor } from "@/src/studio/sceneShared";

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

type ScriptStatus =
  | { kind: "none" }              // jocul nu are script
  | { kind: "loading" }           // se ruleaza acum
  | { kind: "ok"; count: number } // a rulat, cate operatii a produs
  | { kind: "error"; message: string }; // sandbox-ul a raspuns cu o eroare

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
  const [scriptStatus, setScriptStatus] = useState<ScriptStatus>({ kind: "none" });
  const playerRef = useRef<THREE.Mesh | null>(null);
  const scriptOpsRef = useRef<ScriptOp[]>([]);
  const initialPos = useRef({ x: 0, y: 0.5, z: 0 });
  const [resetKey, setResetKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      // 1) cine sunt eu si care e jocul — inainte de orice altceva, ca proprietarul sa vada mereu </>
      let ownerNow = false;
      try {
        const [g, me] = await Promise.all([api(`/games/${id}`), api("/auth/me")]);
        if (cancelled) return;
        setGame(g.game);
        ownerNow = !!me?.user?.user_id && me.user.user_id === g.game?.owner_id;
        setIsOwner(ownerNow);
      } catch (e: any) {
        if (!cancelled) setScriptStatus({ kind: "error", message: e?.message || "Could not load the game" });
        return;
      }

      // 2) marchez ca "s-a jucat" (nu blocheaza restul daca da eroare)
      api(`/games/${id}/play`, { method: "POST" }).catch(() => {});

      // 3) scriptul jocului, separat, ca o eroare aici sa nu ascunda restul scenei
      setScriptStatus({ kind: "loading" });
      try {
        const run = await api(`/sandbox/games/${id}/run`, { method: "POST" });
        if (cancelled) return;
        const ops = Array.isArray(run?.ops) ? run.ops : [];
        scriptOpsRef.current = ops;
        if (Array.isArray(run?.errors) && run.errors.length > 0) {
          setScriptStatus({ kind: "error", message: run.errors[0] });
        } else if (ops.length === 0) {
          setScriptStatus({ kind: "none" });
        } else {
          setScriptStatus({ kind: "ok", count: ops.length });
        }
      } catch (e: any) {
        if (!cancelled) setScriptStatus({ kind: "error", message: e?.message || "Script did not run (sandbox unavailable?)" });
      }
    })();

    return () => { cancelled = true; };
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

    // obiectele facute in editor (cu pozitie, rotatie si scala pe axe)
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
            onPress={() => router.push(`/studio/edit/${id}` as any)}
            style={styles.iconBtn}
          >
            <MaterialCommunityIcons name="code-braces" size={22} color={colors.onSurface} />
          </Pressable>
        ) : (
          <View style={{ width: 40 }} />
        )}
      </SafeAreaView>

      {isOwner ? (
        <View style={styles.statusPill} pointerEvents="none">
          {scriptStatus.kind === "loading" ? (
            <Text style={styles.statusText}>Script: running…</Text>
          ) : scriptStatus.kind === "ok" ? (
            <Text style={[styles.statusText, { color: colors.brand }]}>Script: {scriptStatus.count} changes</Text>
          ) : scriptStatus.kind === "error" ? (
            <Text style={[styles.statusText, { color: colors.error }]} numberOfLines={2}>Script error: {scriptStatus.message}</Text>
          ) : (
            <Text style={[styles.statusText, { color: colors.onSurface3 }]}>Script: none (workspace stays empty)</Text>
          )}
        </View>
      ) : null}

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
  statusPill: { position: "absolute", top: 68, left: spacing.md, right: spacing.md, backgroundColor: "rgba(0,0,0,0.65)", paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.md },
  statusText: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  leftControls: { position: "absolute", left: 0, bottom: 0, padding: spacing.lg },
  resetBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.brand, paddingHorizontal: 14, paddingVertical: 10, borderRadius: radius.pill },
  resetText: { color: colors.onBrand, fontWeight: "900", fontSize: 13, letterSpacing: 1 },
});