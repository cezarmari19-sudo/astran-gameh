import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, TextInput, Modal, Platform, Alert, ActivityIndicator, KeyboardAvoidingView } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { api } from "@/src/api/client";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

type SceneObj = { id: string; type: "cube" | "sphere" | "cylinder" | "cone" | "tree"; x: number; y: number; z: number; color: string; scale: number };
type Scene = { objects: SceneObj[]; sky: string; ground: string };

const PALETTE = ["#CCFF00", "#FF3366", "#00E5FF", "#FFD500", "#00FF66", "#FF9500", "#B266FF", "#FFFFFF", "#666666"];
const OBJ_TYPES: SceneObj["type"][] = ["cube", "sphere", "cylinder", "cone", "tree"];
const OBJ_ICON: Record<SceneObj["type"], string> = {
  cube: "cube-outline", sphere: "circle-outline", cylinder: "cylinder", cone: "triangle-outline", tree: "pine-tree",
};

function uid() { return Math.random().toString(36).slice(2, 10); }

export default function StudioEditor() {
  const router = useRouter();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ id?: string }>();
  const editingId = params.id && params.id !== "new" ? params.id : null;

  const [loading, setLoading] = useState(!!editingId);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("adventure");
  const [ageCategory, setAgeCategory] = useState<"under_18" | "adult_18">("under_18");
  const [isPublic, setIsPublic] = useState(true);
  const [thumbnail, setThumbnail] = useState<string | null>(null);
  const [scene, setScene] = useState<Scene>({ objects: [], sky: "#0F1012", ground: "#1A1D21" });
  const [selId, setSelId] = useState<string | null>(null);
  const [showMeta, setShowMeta] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const meshMap = useRef<Record<string, THREE.Object3D>>({});
  const sceneRef = useRef<THREE.Scene | null>(null);
  const groundRef = useRef<THREE.Mesh | null>(null);

  useEffect(() => {
    if (!editingId) return;
    (async () => {
      try {
        const r = await api(`/games/${editingId}`);
        const g = r.game;
        setTitle(g.title); setDescription(g.description || ""); setCategory(g.category || "adventure");
        setAgeCategory(g.age_category); setIsPublic(g.is_public);
        setThumbnail(g.thumbnail_url || null);
        setScene(g.scene && g.scene.objects ? g.scene : { objects: [], sky: "#0F1012", ground: "#1A1D21" });
      } catch (e: any) { setErr(e.message); }
      setLoading(false);
    })();
  }, [editingId]);

  function addObject(type: SceneObj["type"]) {
    const obj: SceneObj = { id: uid(), type, x: (Math.random() - 0.5) * 4, y: 0, z: (Math.random() - 0.5) * 4, color: PALETTE[Math.floor(Math.random() * PALETTE.length)], scale: 1 };
    setScene(s => ({ ...s, objects: [...s.objects, obj] }));
    setSelId(obj.id);
  }

  function updateSel(patch: Partial<SceneObj>) {
    if (!selId) return;
    setScene(s => ({ ...s, objects: s.objects.map(o => o.id === selId ? { ...o, ...patch } : o) }));
  }

  function removeSel() {
    if (!selId) return;
    setScene(s => ({ ...s, objects: s.objects.filter(o => o.id !== selId) }));
    setSelId(null);
  }

  async function pickThumb() {
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) { setErr("Photo permission denied"); return; }
      const res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 0.6, base64: true, allowsEditing: true, aspect: [16, 9],
      });
      if (res.canceled) return;
      const a = res.assets[0];
      if (a.base64) setThumbnail(`data:image/jpeg;base64,${a.base64}`);
      else if (a.uri) setThumbnail(a.uri);
    } catch (e: any) { setErr(e.message); }
  }

  async function save() {
    if (!title || title.length < 2) { setErr("Title too short"); setShowMeta(true); return; }
    setBusy(true); setErr(null);
    try {
      const body = { title, description, age_category: ageCategory, is_public: isPublic, category, thumbnail_url: thumbnail, scene };
      if (editingId) await api(`/games/${editingId}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api("/games", { method: "POST", body: JSON.stringify(body) });
      if (router.canGoBack()) router.back();
      else router.replace("/(tabs)/studio");
    } catch (e: any) { setErr(e.message); setShowMeta(true); }
    finally { setBusy(false); }
  }

  async function del() {
    if (!editingId) return;
    setBusy(true);
    try {
      await api(`/games/${editingId}`, { method: "DELETE" });
      if (router.canGoBack()) router.back();
      else router.replace("/(tabs)/studio");
    }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;
    Object.keys(meshMap.current).forEach(id => {
      if (!scene.objects.find(o => o.id === id)) {
        s.remove(meshMap.current[id]);
        delete meshMap.current[id];
      }
    });
    scene.objects.forEach(o => {
      let m = meshMap.current[o.id] as THREE.Mesh | undefined;
      if (!m) {
        let geo: THREE.BufferGeometry;
        if (o.type === "cube") geo = new THREE.BoxGeometry(1, 1, 1);
        else if (o.type === "sphere") geo = new THREE.SphereGeometry(0.6, 20, 16);
        else if (o.type === "cylinder") geo = new THREE.CylinderGeometry(0.5, 0.5, 1.2, 20);
        else if (o.type === "cone") geo = new THREE.ConeGeometry(0.6, 1.2, 20);
        else geo = new THREE.ConeGeometry(0.7, 1.6, 8);
        const mat = new THREE.MeshStandardMaterial({ color: new THREE.Color(o.color), roughness: 0.5, metalness: 0.1 });
        m = new THREE.Mesh(geo, mat);
        meshMap.current[o.id] = m;
        s.add(m);
      }
      m.position.set(o.x, o.y + 0.5 * o.scale, o.z);
      m.scale.setScalar(o.scale);
      (m.material as THREE.MeshStandardMaterial).color = new THREE.Color(o.color);
    });
    if (groundRef.current) (groundRef.current.material as THREE.MeshStandardMaterial).color = new THREE.Color(scene.ground);
  }, [scene]);

  const onContextCreate = async (gl: any) => {
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setSize(w, h);
    renderer.setClearColor(new THREE.Color(scene.sky), 1);
    const s = new THREE.Scene();
    sceneRef.current = s;
    const camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 100);
    camera.position.set(5, 5, 7);
    camera.lookAt(0, 0, 0);
    s.add(new THREE.AmbientLight(0xffffff, 0.5));
    const dir = new THREE.DirectionalLight(0xffffff, 1.1);
    dir.position.set(5, 8, 4);
    s.add(dir);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(20, 20, 20, 20),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(scene.ground), wireframe: true })
    );
    ground.rotation.x = -Math.PI / 2;
    s.add(ground);
    groundRef.current = ground;
    Object.keys(meshMap.current).forEach(k => delete meshMap.current[k]);
    let t0 = 0;
    const render = () => {
      requestAnimationFrame(render);
      t0 += 0.006;
      camera.position.x = Math.cos(t0) * 7;
      camera.position.z = Math.sin(t0) * 7;
      camera.position.y = 4;
      camera.lookAt(0, 0, 0);
      renderer.render(s, camera);
      gl.endFrameEXP();
    };
    render();
  };

  const sel = scene.objects.find(o => o.id === selId);

  if (loading) return <SafeAreaView style={styles.center}><ActivityIndicator color={colors.brand} /></SafeAreaView>;

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} testID="editor-back"><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title} numberOfLines={1}>{title || t("create_game")}</Text>
        <Pressable onPress={() => setShowMeta(true)} testID="editor-meta"><MaterialCommunityIcons name="cog" size={22} color={colors.onSurface} /></Pressable>
      </View>

      <View style={styles.canvas}>
        {Platform.OS === "web" ? (
          <View style={[StyleSheet.absoluteFillObject, { alignItems: "center", justifyContent: "center" }]}>
            <MaterialCommunityIcons name="cube-scan" size={64} color={colors.brand} />
            <Text style={{ color: colors.onSurface3, marginTop: 8 }}>3D preview available on Expo Go / device</Text>
            <Text style={{ color: colors.onSurface, marginTop: 4, fontWeight: "700" }}>{scene.objects.length} objects</Text>
          </View>
        ) : (
          <GLView style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
        )}
      </View>

      <View style={styles.toolbar}>
        <Text style={styles.toolLabel}>ADD</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12 }}>
          {OBJ_TYPES.map(k => (
            <Pressable key={k} testID={`editor-add-${k}`} onPress={() => addObject(k)} style={styles.toolBtn}>
              <MaterialCommunityIcons name={OBJ_ICON[k] as any} size={22} color={colors.brand} />
              <Text style={styles.toolBtnText}>{k}</Text>
            </Pressable>
          ))}
          <Pressable testID="editor-pick-thumb" onPress={pickThumb} style={[styles.toolBtn, { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="image-plus" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>{thumbnail ? "thumb ✓" : "thumbnail"}</Text>
          </Pressable>
        </ScrollView>
      </View>

      {sel ? (
        <View style={styles.inspector}>
          <View style={styles.inspHead}>
            <MaterialCommunityIcons name={OBJ_ICON[sel.type] as any} size={18} color={colors.brand} />
            <Text style={styles.inspTitle}>{sel.type}</Text>
            <View style={{ flex: 1 }} />
            <Pressable onPress={removeSel} testID="editor-delete-obj"><MaterialCommunityIcons name="trash-can-outline" size={20} color={colors.error} /></Pressable>
          </View>
          <View style={styles.axisRow}>
            {(["x", "y", "z"] as const).map(axis => (
              <View key={axis} style={styles.axisBox}>
                <Text style={styles.axisLabel}>{axis.toUpperCase()}</Text>
                <View style={{ flexDirection: "row", gap: 4 }}>
                  <Pressable onPress={() => updateSel({ [axis]: sel[axis] - 0.5 } as any)} style={styles.axisBtn}><Text style={styles.axisBtnText}>-</Text></Pressable>
                  <Text style={styles.axisVal}>{sel[axis].toFixed(1)}</Text>
                  <Pressable onPress={() => updateSel({ [axis]: sel[axis] + 0.5 } as any)} style={styles.axisBtn}><Text style={styles.axisBtnText}>+</Text></Pressable>
                </View>
              </View>
            ))}
            <View style={styles.axisBox}>
              <Text style={styles.axisLabel}>SCALE</Text>
              <View style={{ flexDirection: "row", gap: 4 }}>
                <Pressable onPress={() => updateSel({ scale: Math.max(0.2, sel.scale - 0.2) })} style={styles.axisBtn}><Text style={styles.axisBtnText}>-</Text></Pressable>
                <Text style={styles.axisVal}>{sel.scale.toFixed(1)}</Text>
                <Pressable onPress={() => updateSel({ scale: Math.min(4, sel.scale + 0.2) })} style={styles.axisBtn}><Text style={styles.axisBtnText}>+</Text></Pressable>
              </View>
            </View>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 8 }}>
            {PALETTE.map(c => (
              <Pressable key={c} testID={`editor-color-${c}`} onPress={() => updateSel({ color: c })} style={[styles.swatch, { backgroundColor: c }, sel.color === c && styles.swatchSel]} />
            ))}
          </ScrollView>
        </View>
      ) : (
        <View style={styles.objList}>
          <Text style={styles.objListTitle}>{scene.objects.length} OBJECTS · tap to select</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingHorizontal: 12, paddingBottom: 8 }}>
            {scene.objects.map(o => (
              <Pressable key={o.id} testID={`editor-obj-${o.id}`} onPress={() => setSelId(o.id)} style={[styles.objChip, { borderColor: o.color }]}>
                <MaterialCommunityIcons name={OBJ_ICON[o.type] as any} size={14} color={o.color} />
                <Text style={styles.objChipText}>{o.type}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>
      )}

      <View style={styles.footer}>
        {editingId ? (
          <Pressable testID="editor-delete-game" onPress={del} style={styles.delBtn}>
            <MaterialCommunityIcons name="trash-can-outline" size={20} color={colors.error} />
          </Pressable>
        ) : null}
        {editingId ? (
          <Pressable testID="editor-play" onPress={() => router.push({ pathname: "/play/[id]", params: { id: editingId } })} style={styles.playBtn}>
            <MaterialCommunityIcons name="play" size={20} color={colors.onSurface} />
          </Pressable>
        ) : null}
        <View style={{ flex: 1 }}>
          <PrimaryButton testID="editor-save" label={busy ? "..." : t("save")} icon="check" onPress={save} disabled={busy} />
        </View>
      </View>

      <Modal visible={showMeta} transparent animationType="slide" onRequestClose={() => setShowMeta(false)}>
        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1, justifyContent: "flex-end" }}>
          <Pressable style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.6)" }} onPress={() => setShowMeta(false)} />
          <ScrollView style={styles.sheet} contentContainerStyle={{ paddingBottom: 30 }} keyboardShouldPersistTaps="handled">
            <Text style={styles.sheetTitle}>Game Info</Text>
            {thumbnail ? (
              <Pressable testID="editor-change-thumb" onPress={pickThumb} style={styles.thumbBox}>
                <Image source={{ uri: thumbnail }} style={StyleSheet.absoluteFillObject} contentFit="cover" />
                <View style={styles.thumbOverlay}><MaterialCommunityIcons name="camera" size={20} color="#fff" /><Text style={styles.thumbText}>Change photo</Text></View>
              </Pressable>
            ) : (
              <Pressable testID="editor-pick-thumb-meta" onPress={pickThumb} style={[styles.thumbBox, { backgroundColor: colors.surface2, alignItems: "center", justifyContent: "center" }]}>
                <MaterialCommunityIcons name="image-plus" size={32} color={colors.brand} />
                <Text style={{ color: colors.onSurface2, marginTop: 4 }}>Pick from gallery</Text>
              </Pressable>
            )}
            <Text style={styles.lab}>{t("game_title")}</Text>
            <TextInput testID="editor-title" value={title} onChangeText={setTitle} style={styles.input} placeholder="Neon Runner" placeholderTextColor={colors.onSurface3} />
            <Text style={styles.lab}>{t("game_desc")}</Text>
            <TextInput testID="editor-desc" value={description} onChangeText={setDescription} multiline style={[styles.input, { height: 80 }]} placeholder="..." placeholderTextColor={colors.onSurface3} />
            <Text style={styles.lab}>Category</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 4 }}>
              {["adventure", "shooter", "simulation", "racing", "roleplay", "puzzle", "other"].map(c => (
                <Pressable key={c} onPress={() => setCategory(c)} style={[styles.pill, category === c && styles.pillActive]} testID={`editor-cat-${c}`}>
                  <Text style={[styles.pillText, category === c && { color: colors.brand }]}>{c}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <Text style={styles.lab}>{t("age_select_title")}</Text>
            <View style={{ flexDirection: "row", gap: 8 }}>
              <Pressable testID="editor-age-under" onPress={() => setAgeCategory("under_18")} style={[styles.ageBtn, ageCategory === "under_18" && styles.ageBtnActive]}><Text style={[styles.ageBtnText, ageCategory === "under_18" && { color: colors.brand }]}>{t("age_under_18")}</Text></Pressable>
              <Pressable testID="editor-age-adult" onPress={() => setAgeCategory("adult_18")} style={[styles.ageBtn, ageCategory === "adult_18" && styles.ageBtnActive]}><Text style={[styles.ageBtnText, ageCategory === "adult_18" && { color: colors.brand }]}>{t("age_18_plus")}</Text></Pressable>
            </View>
            <Pressable onPress={() => setIsPublic(v => !v)} style={styles.toggle} testID="editor-toggle-public">
              <MaterialCommunityIcons name={isPublic ? "eye" : "eye-off"} size={20} color={isPublic ? colors.brand : colors.onSurface3} />
              <Text style={{ color: colors.onSurface, flex: 1, fontWeight: "700" }}>{t("public_game")}</Text>
              <View style={[styles.switch, isPublic && styles.switchOn]}><View style={[styles.knob, isPublic && styles.knobOn]} /></View>
            </Pressable>
            {err ? <Text style={styles.err}>{err}</Text> : null}
            <View style={{ marginTop: 16 }}>
              <PrimaryButton testID="editor-meta-done" label="Done" onPress={() => setShowMeta(false)} />
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md, gap: 10 },
  title: { flex: 1, color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  canvas: { flex: 1, backgroundColor: colors.surface2, borderRadius: radius.md, margin: spacing.md, overflow: "hidden" },
  toolbar: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingVertical: 10 },
  toolLabel: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 2, paddingHorizontal: 14, marginBottom: 6 },
  toolBtn: { alignItems: "center", justifyContent: "center", width: 68, paddingVertical: 8, backgroundColor: colors.surface3, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, gap: 2 },
  toolBtnText: { color: colors.onSurface, fontSize: 10, fontWeight: "700" },
  inspector: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, padding: 12 },
  inspHead: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  inspTitle: { color: colors.onSurface, fontWeight: "800", fontSize: 14, textTransform: "capitalize" },
  axisRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  axisBox: { flexBasis: "48%", padding: 8, backgroundColor: colors.surface3, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border },
  axisLabel: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", marginBottom: 4 },
  axisBtn: { width: 28, height: 28, borderRadius: 14, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  axisBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 14 },
  axisVal: { flex: 1, color: colors.onSurface, fontWeight: "700", textAlign: "center", alignSelf: "center", fontSize: 13 },
  swatch: { width: 32, height: 32, borderRadius: 16, borderWidth: 2, borderColor: colors.border },
  swatchSel: { borderColor: colors.onSurface },
  objList: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingTop: 8 },
  objListTitle: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 1.5, paddingHorizontal: 14, marginBottom: 6 },
  objChip: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.surface3, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1 },
  objChipText: { color: colors.onSurface, fontSize: 11, fontWeight: "700", textTransform: "capitalize" },
  footer: { flexDirection: "row", alignItems: "center", gap: 8, padding: spacing.md, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  delBtn: { width: 44, height: 44, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.error, alignItems: "center", justifyContent: "center" },
  playBtn: { width: 44, height: 44, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  sheet: { maxHeight: "88%", backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border },
  sheetTitle: { color: colors.onSurface, fontSize: 20, fontWeight: "900", marginBottom: 12 },
  thumbBox: { height: 140, borderRadius: radius.md, overflow: "hidden", marginBottom: 4, borderWidth: 1, borderColor: colors.border },
  thumbOverlay: { position: "absolute", right: 8, bottom: 8, flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: "rgba(0,0,0,0.6)", paddingHorizontal: 10, paddingVertical: 6, borderRadius: radius.pill },
  thumbText: { color: "#fff", fontSize: 11, fontWeight: "700" },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  pill: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  pillActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  pillText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12, textTransform: "capitalize" },
  ageBtn: { flex: 1, padding: 12, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  ageBtnActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  ageBtnText: { color: colors.onSurface2, fontWeight: "700" },
  toggle: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  switch: { width: 44, height: 26, borderRadius: 13, backgroundColor: colors.surface3, padding: 3 },
  switchOn: { backgroundColor: colors.brand },
  knob: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.onSurface2 },
  knobOn: { backgroundColor: colors.onBrand, marginLeft: "auto" },
  err: { color: colors.error, marginTop: 8, fontSize: 12, fontWeight: "600" },
});