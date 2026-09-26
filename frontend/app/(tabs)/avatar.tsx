import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, Platform, ActivityIndicator, Dimensions } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { PanGestureHandler, PinchGestureHandler, State } from "react-native-gesture-handler";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";
import { BODY_SHAPES, AvatarBody, defaultBody, buildBodyMeshes, layoutBody, bodyHeightWorld } from "@/src/avatar/avatarTypes";

const SKIN_TONES = ["#F5D0B0", "#E8B48C", "#C68863", "#9C6642", "#6B4426", "#3D2817"];

export default function AvatarEditor() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [body, setBody] = useState<AvatarBody>(defaultBody());

  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const bodyRef = useRef<ReturnType<typeof buildBodyMeshes> | null>(null);
  const rafId = useRef<number | null>(null);
  const alive = useRef(true);
  const glReady = useRef(false);

  const cameraTarget = useRef(new THREE.Vector3(0, 0.9, 0));
  const cameraAngle = useRef(0.3);
  const cameraPolar = useRef(1.35);
  const cameraDistance = useRef(3.2);
  const lastAngle = useRef(0.3);
  const lastPolar = useRef(1.35);
  const lastDistance = useRef(3.2);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
    };
  }, []);

  // ---------- incarcare initiala: doar parametrii de corp ----------
  useEffect(() => {
    (async () => {
      try {
        const r = await api("/avatar/me");
        const a = r?.avatar;
        if (a?.body) setBody({ ...defaultBody(), ...a.body });
      } catch (e: any) {
        setErr(e?.message || "Nu am putut încărca avatarul");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // ---------- sincronizare corp: la orice schimbare, reasezam mesh-urile ----------
  useEffect(() => {
    if (!glReady.current || !bodyRef.current) return;
    layoutBody(bodyRef.current, body);
    cameraTarget.current.y = bodyHeightWorld(body) * 0.55;
  }, [body]);

  // ---------- camera ----------
  function updateCameraPosition() {
    const cam = cameraRef.current;
    if (!cam) return;
    const r = cameraDistance.current, theta = cameraAngle.current, phi = cameraPolar.current, tg = cameraTarget.current;
    cam.position.x = tg.x + r * Math.sin(phi) * Math.cos(theta);
    cam.position.z = tg.z + r * Math.sin(phi) * Math.sin(theta);
    cam.position.y = tg.y + r * Math.cos(phi);
    cam.lookAt(tg);
  }

  const onContextCreate = async (gl: any) => {
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setSize(w, h);
    renderer.setClearColor(new THREE.Color(colors.surface2), 1);

    const s = new THREE.Scene();
    sceneRef.current = s;
    const camera = new THREE.PerspectiveCamera(45, w / h, 0.05, 50);
    cameraRef.current = camera;
    cameraTarget.current.y = bodyHeightWorld(body) * 0.55;
    updateCameraPosition();

    s.add(new THREE.AmbientLight(0xffffff, 0.65));
    const dir = new THREE.DirectionalLight(0xffffff, 1.0);
    dir.position.set(2, 4, 3);
    s.add(dir);
    const rim = new THREE.DirectionalLight(0xccff00, 0.25);
    rim.position.set(-2, 2, -3);
    s.add(rim);

    const grid = new THREE.GridHelper(3, 12, 0x3a4047, 0x262a2f);
    s.add(grid);

    const bm = buildBodyMeshes();
    bodyRef.current = bm;
    layoutBody(bm, body);
    s.add(bm.group);

    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);
      renderer.render(s, camera);
      gl.endFrameEXP();
    };
    render();
    glReady.current = true;
  };

  const onPanGestureEvent = (e: any) => {
    const { translationX, translationY } = e.nativeEvent;
    cameraAngle.current = lastAngle.current - translationX * 0.008;
    cameraPolar.current = Math.max(0.5, Math.min(2.4, lastPolar.current - translationY * 0.006));
    updateCameraPosition();
  };
  const onPanHandlerStateChange = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) { lastAngle.current = cameraAngle.current; lastPolar.current = cameraPolar.current; }
  };
  const onPinchGestureEvent = (e: any) => {
    cameraDistance.current = Math.max(1.2, Math.min(7, lastDistance.current / e.nativeEvent.scale));
    updateCameraPosition();
  };
  const onPinchHandlerStateChange = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) lastDistance.current = cameraDistance.current;
  };

  // ---------- actiuni ----------
  function patchBody(p: Partial<AvatarBody>) {
    setBody(b => ({ ...b, ...p }));
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      const r = await api("/avatar/me", { method: "PUT", body: JSON.stringify({ body, equipped: {} }) });
      if (r?.avatar?.body) setBody({ ...defaultBody(), ...r.avatar.body });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setErr(e?.message || "Salvarea a eșuat");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <SafeAreaView style={styles.center}><ActivityIndicator color={colors.brand} /></SafeAreaView>;
  }

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} testID="avatar-back">
          <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>{saved ? "Salvat ✓" : "Avatar"}</Text>
        <View style={{ width: 26 }} />
      </View>

      {/* Viewport 3D */}
      <View style={styles.canvas}>
        {Platform.OS === "web" ? (
          <View style={[StyleSheet.absoluteFillObject, { alignItems: "center", justifyContent: "center" }]}>
            <MaterialCommunityIcons name="account-outline" size={64} color={colors.brand} />
            <Text style={{ color: colors.onSurface3, marginTop: 8 }}>Avatarul 3D se vede pe telefon / Expo Go</Text>
          </View>
        ) : (
          <PinchGestureHandler onGestureEvent={onPinchGestureEvent} onHandlerStateChange={onPinchHandlerStateChange}>
            <PanGestureHandler onGestureEvent={onPanGestureEvent} onHandlerStateChange={onPanHandlerStateChange} minPointers={1} maxPointers={1}>
              <View style={StyleSheet.absoluteFillObject}>
                <GLView style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
                <View style={styles.hintPill} pointerEvents="none">
                  <MaterialCommunityIcons name="gesture-swipe" size={14} color={colors.onSurface3} />
                  <Text style={styles.hintText}>Drag pentru rotire · Pinch pentru zoom</Text>
                </View>
              </View>
            </PanGestureHandler>
          </PinchGestureHandler>
        )}
      </View>

      {/* Sliderele de corp - singurul continut al Avatar Editor deocamdata */}
      <ScrollView style={styles.content} contentContainerStyle={{ padding: spacing.md, gap: 14 }}>
        <BodySlider label="Înălțime" value={body.height} min={0.7} max={1.4} onChange={v => patchBody({ height: v })} testID="avatar-slider-height" />
        <BodySlider label="Lățime" value={body.width} min={0.7} max={1.4} onChange={v => patchBody({ width: v })} testID="avatar-slider-width" />
        <BodySlider label="Proporții" value={body.proportions} min={0.7} max={1.3} onChange={v => patchBody({ proportions: v })} testID="avatar-slider-proportions" />
        <BodySlider label="Mărime cap" value={body.head_size} min={0.7} max={1.4} onChange={v => patchBody({ head_size: v })} testID="avatar-slider-head" />

        <Text style={styles.lab}>Formă corp</Text>
        <View style={{ flexDirection: "row", gap: 8 }}>
          {BODY_SHAPES.map(s => (
            <Pressable
              key={s.key}
              testID={`avatar-shape-${s.key}`}
              onPress={() => patchBody({ body_shape: s.key as AvatarBody["body_shape"] })}
              style={[styles.shapeBtn, body.body_shape === s.key && styles.shapeBtnActive]}
            >
              <Text style={[styles.shapeText, body.body_shape === s.key && { color: colors.brand }]}>{s.label}</Text>
            </Pressable>
          ))}
        </View>

        <Text style={styles.lab}>Culoare piele</Text>
        <View style={{ flexDirection: "row", gap: 10, flexWrap: "wrap" }}>
          {SKIN_TONES.map(c => (
            <Pressable
              key={c}
              testID={`avatar-skin-${c}`}
              onPress={() => patchBody({ skin_color: c })}
              style={[styles.swatch, { backgroundColor: c }, body.skin_color.toLowerCase() === c.toLowerCase() && styles.swatchActive]}
            />
          ))}
        </View>
      </ScrollView>

      {err ? <Text style={styles.err}>{err}</Text> : null}

      <View style={styles.footer}>
        <PrimaryButton testID="avatar-save" label={busy ? "..." : "Save Avatar"} icon="check" onPress={save} disabled={busy} />
      </View>
    </SafeAreaView>
  );
}

function BodySlider({ label, value, min, max, onChange, testID }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void; testID?: string }) {
  const pct = (value - min) / (max - min);
  const width = Dimensions.get("window").width - spacing.md * 2 - 24;

  const onGesture = (e: any) => {
    const x = Math.max(0, Math.min(width, e.nativeEvent.x));
    const v = min + (x / width) * (max - min);
    onChange(Math.round(v * 100) / 100);
  };

  return (
    <View>
      <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
        <Text style={styles.lab}>{label}</Text>
        <Text style={styles.sliderVal}>{value.toFixed(2)}</Text>
      </View>
      <PanGestureHandler onGestureEvent={onGesture} onHandlerStateChange={(e: any) => { if (e.nativeEvent.state === State.ACTIVE) onGesture(e); }}>
        <View style={styles.sliderTrack} testID={testID}>
          <View style={[styles.sliderFill, { width: `${pct * 100}%` }]} />
          <View style={[styles.sliderKnob, { left: `${pct * 100}%` }]} />
        </View>
      </PanGestureHandler>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md },
  title: { flex: 1, textAlign: "center", color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  canvas: { height: "45%", backgroundColor: colors.surface2, borderRadius: radius.md, marginHorizontal: spacing.md, overflow: "hidden" },
  hintPill: { position: "absolute", bottom: 10, alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "rgba(0,0,0,0.55)", paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill },
  hintText: { color: colors.onSurface3, fontSize: 11, fontWeight: "600" },
  content: { flex: 1, marginTop: spacing.md },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase" },
  sliderVal: { color: colors.onSurface, fontSize: 11, fontWeight: "700" },
  sliderTrack: { height: 30, justifyContent: "center", marginTop: 6 },
  sliderFill: { position: "absolute", left: 0, height: 4, backgroundColor: colors.brand, borderRadius: 2 },
  sliderKnob: { position: "absolute", width: 18, height: 18, borderRadius: 9, backgroundColor: colors.brand, marginLeft: -9, top: 6 },
  shapeBtn: { flex: 1, paddingVertical: 10, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  shapeBtnActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  shapeText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12 },
  swatch: { width: 34, height: 34, borderRadius: 17, borderWidth: 2, borderColor: "transparent" },
  swatchActive: { borderColor: colors.brand },
  err: { color: colors.error, fontSize: 12, fontWeight: "600", textAlign: "center", marginTop: 6 },
  footer: { padding: spacing.md, borderTopWidth: 1, borderColor: colors.border },
});