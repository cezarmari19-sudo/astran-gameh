import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, Platform, ActivityIndicator, FlatList, Dimensions } from "react-native";
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
import {
  Slot, SLOT_DEFS, BODY_SHAPES, AvatarBody, Equipped, InventoryItem,
  defaultBody, buildBodyMeshes, layoutBody, bodyHeightWorld,
  buildEquippedGroup, disposeGroup, SLOT_ANCHOR_Y,
} from "@/src/avatar/avatarTypes";
import type { Part } from "@/src/studio3d/modelTypes";
import { PALETTE } from "@/src/studio3d/modelTypes";

const SKIN_TONES = ["#F5D0B0", "#E8B48C", "#C68863", "#9C6642", "#6B4426", "#3D2817"];

export default function AvatarEditor() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const [body, setBody] = useState<AvatarBody>(defaultBody());
  const [equipped, setEquipped] = useState<Equipped>({});
  const [inventory, setInventory] = useState<InventoryItem[]>([]);
  const [activeSlot, setActiveSlot] = useState<Slot>("shirt");
  const [activeTab, setActiveTab] = useState<"body" | Slot>("body");

  // cache local al pieselor (Part[]) pentru item-urile echipate, ca sa nu cerem serverul de fiecare randare
  const partsCache = useRef<Record<string, Part[]>>({});

  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const bodyRef = useRef<ReturnType<typeof buildBodyMeshes> | null>(null);
  const equippedGroups = useRef<Partial<Record<Slot, THREE.Group>>>({});
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

  // ---------- incarcare initiala: avatarul salvat + inventarul ----------
  useEffect(() => {
    (async () => {
      try {
        const [avatarRes, invRes] = await Promise.all([
          api("/avatar/me"),
          api("/avatar/inventory"),
        ]);
        const a = avatarRes?.avatar;
        if (a?.body) setBody({ ...defaultBody(), ...a.body });
        if (a?.equipped) setEquipped(a.equipped);
        setInventory(Array.isArray(invRes?.items) ? invRes.items : []);
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
    repositionEquipped();
    cameraTarget.current.y = bodyHeightWorld(body) * 0.55;
  }, [body]);

  // ---------- sincronizare iteme echipate: incarca piesele lipsa si (re)construieste grupurile ----------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const s = sceneRef.current;
      if (!s) return;

      for (const def of SLOT_DEFS) {
        const slot = def.key;
        const itemId = equipped[slot];
        const existing = equippedGroups.current[slot];

        if (!itemId) {
          if (existing) {
            s.remove(existing);
            disposeGroup(existing);
            delete equippedGroups.current[slot];
          }
          continue;
        }

        if (existing && (existing.userData as any).itemId === itemId) continue; // deja corect

        if (existing) {
          s.remove(existing);
          disposeGroup(existing);
          delete equippedGroups.current[slot];
        }

        let parts = partsCache.current[itemId];
        if (!parts) {
          try {
            const r = await api(`/shop/items/${itemId}`);
            parts = Array.isArray(r?.item?.parts) ? r.item.parts : [];
            partsCache.current[itemId] = parts;
          } catch {
            parts = [];
          }
        }
        if (cancelled) return;
        if (parts.length === 0) continue;

        const g = buildEquippedGroup(parts);
        (g.userData as any).itemId = itemId;
        s.add(g);
        equippedGroups.current[slot] = g;
      }
      repositionEquipped();
    })();
    return () => { cancelled = true; };
  }, [equipped]);

  function repositionEquipped() {
    const h = bodyHeightWorld(body);
    (Object.keys(equippedGroups.current) as Slot[]).forEach(slot => {
      const g = equippedGroups.current[slot];
      if (!g) return;
      const anchor = SLOT_ANCHOR_Y[slot] ?? 0.5;
      g.position.set(0, h * anchor, 0);
      g.scale.setScalar(body.width);
    });
  }

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
    repositionEquipped();
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

  function toggleEquip(item: InventoryItem) {
    setEquipped(eq => {
      const cur = eq[item.slot];
      return { ...eq, [item.slot]: cur === item.item_id ? null : item.item_id };
    });
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      const r = await api("/avatar/me", { method: "PUT", body: JSON.stringify({ body, equipped }) });
      if (r?.avatar?.equipped) setEquipped(r.avatar.equipped);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      setErr(e?.message || "Salvarea a eșuat");
    } finally {
      setBusy(false);
    }
  }

  const itemsForActiveSlot = inventory.filter(i => i.slot === activeSlot);

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

      {/* Categorii (extensibile: body + fiecare slot din SLOT_DEFS) */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.catBar} contentContainerStyle={{ gap: 8, paddingHorizontal: 12 }}>
        <Pressable testID="avatar-cat-body" onPress={() => setActiveTab("body")} style={[styles.catBtn, activeTab === "body" && styles.catBtnActive]}>
          <MaterialCommunityIcons name="human-handsup" size={20} color={activeTab === "body" ? colors.brand : colors.onSurface3} />
          <Text style={[styles.catText, activeTab === "body" && { color: colors.brand }]}>Corp</Text>
        </Pressable>
        {SLOT_DEFS.map(def => (
          <Pressable
            key={def.key}
            testID={`avatar-cat-${def.key}`}
            onPress={() => { setActiveTab(def.key); setActiveSlot(def.key); }}
            style={[styles.catBtn, activeTab === def.key && styles.catBtnActive]}
          >
            <MaterialCommunityIcons name={def.icon as any} size={20} color={activeTab === def.key ? colors.brand : colors.onSurface3} />
            <Text style={[styles.catText, activeTab === def.key && { color: colors.brand }]}>{def.label}</Text>
            {equipped[def.key] ? <View style={styles.dot} /> : null}
          </Pressable>
        ))}
      </ScrollView>

      {/* Continut: sliders de corp, sau grid de iteme pentru slotul activ */}
      <View style={styles.content}>
        {activeTab === "body" ? (
          <ScrollView contentContainerStyle={{ padding: spacing.md, gap: 14 }}>
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
        ) : (
          <FlatList
            key={activeSlot}
            data={itemsForActiveSlot}
            keyExtractor={i => i.item_id}
            numColumns={3}
            contentContainerStyle={{ padding: spacing.md, gap: 10 }}
            columnWrapperStyle={{ gap: 10 }}
            ListEmptyComponent={
              <View style={styles.emptyBox}>
                <MaterialCommunityIcons name="package-variant-closed" size={32} color={colors.onSurface3} />
                <Text style={styles.emptyText}>Nu ai încă niciun item pentru {SLOT_DEFS.find(d => d.key === activeSlot)?.label.toLowerCase()}</Text>
                <Pressable onPress={() => router.push("/shop" as any)} style={styles.shopBtn} testID="avatar-go-shop">
                  <Text style={styles.shopBtnText}>Vezi Magazinul</Text>
                </Pressable>
              </View>
            }
            renderItem={({ item }) => {
              const isEquipped = equipped[item.slot] === item.item_id;
              return (
                <Pressable
                  testID={`avatar-item-${item.item_id}`}
                  onPress={() => toggleEquip(item)}
                  style={[styles.itemCard, isEquipped && styles.itemCardActive]}
                >
                  <View style={[styles.itemSwatch, { backgroundColor: item.preview?.color || colors.surface3 }]} />
                  <Text numberOfLines={1} style={styles.itemName}>{item.name}</Text>
                  {isEquipped ? (
                    <View style={styles.equippedPill}><MaterialCommunityIcons name="check" size={12} color={colors.onBrand} /></View>
                  ) : null}
                </Pressable>
              );
            }}
          />
        )}
      </View>

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
  canvas: { height: "42%", backgroundColor: colors.surface2, borderRadius: radius.md, marginHorizontal: spacing.md, overflow: "hidden" },
  hintPill: { position: "absolute", bottom: 10, alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "rgba(0,0,0,0.55)", paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill },
  hintText: { color: colors.onSurface3, fontSize: 11, fontWeight: "600" },
  catBar: { marginTop: spacing.md, flexGrow: 0, flexShrink: 0 },
  catBtn: { alignItems: "center", justifyContent: "center", width: 72, paddingVertical: 8, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, gap: 2 },
  catBtnActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  catText: { color: colors.onSurface3, fontSize: 10, fontWeight: "700" },
  dot: { position: "absolute", top: 6, right: 8, width: 6, height: 6, borderRadius: 3, backgroundColor: colors.brand },
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
  emptyBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8, paddingVertical: 40, width: "100%" },
  emptyText: { color: colors.onSurface3, fontSize: 12, textAlign: "center", paddingHorizontal: 30 },
  shopBtn: { marginTop: 8, paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, backgroundColor: colors.brand },
  shopBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 12 },
  itemCard: { flex: 1, aspectRatio: 1, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center", padding: 8, gap: 6 },
  itemCardActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  itemSwatch: { width: "60%", aspectRatio: 1, borderRadius: radius.sm },
  itemName: { color: colors.onSurface, fontSize: 11, fontWeight: "700", textAlign: "center" },
  equippedPill: { position: "absolute", top: 6, right: 6, width: 18, height: 18, borderRadius: 9, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  err: { color: colors.error, fontSize: 12, fontWeight: "600", textAlign: "center", marginTop: 6 },
  footer: { padding: spacing.md, borderTopWidth: 1, borderColor: colors.border },
});