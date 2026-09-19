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

function buildMesh(o: SceneObj): THREE.Mesh {
  let geo: THREE.BufferGeometry;
  if (o.type === "cube") geo = new THREE.BoxGeometry(1, 1, 1);
  else if (o.type === "sphere") geo = new THREE.SphereGeometry(0.6, 20, 16);
  else if (o.type === "cylinder") geo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 20);
  else if (o.type === "cone") geo = new THREE.ConeGeometry(0.6, 1.2, 20);
  else geo = new THREE.ConeGeometry(0.7, 1.6, 8);
  const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(o.color), roughness: 0.5, metalness: 0.1 });
  const m = new THREE.Mesh(geo, mat);
  m.position.set(o.x, o.y + 0.5 * o.scale, o.z);
  m.scale.setScalar(o.scale);
  return m;
}

export default function PlayScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useI18n();
  const [game, setGame] = useState<any>(null);
  const playerRef = useRef<THREE.Mesh | null>(null);
  const initialPos = useRef({ x: 0, y: 0.5, z: 0 });
  const [resetKey, setResetKey] = useState(0);

  useEffect(() => {
    (async () => {
      try {
        const [g] = await Promise.all([
          api(`/games/${id}`),
          api(`/games/${id}/play`, { method: "POST" }),
        ]);
        setGame(g.game);
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

    let t0 = 0;
    const render = () => {
      requestAnimationFrame(render);
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
        <View style={{ width: 40 }} />
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