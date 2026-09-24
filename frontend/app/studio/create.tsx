import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, TextInput, Modal, Platform, ActivityIndicator, KeyboardAvoidingView, Alert, BackHandler } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { PanGestureHandler, PinchGestureHandler, TapGestureHandler, State } from "react-native-gesture-handler";
import { api } from "@/src/api/client";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";
import ScriptEditor, { ScriptFile } from "@/src/components/ScriptEditor";
import AssetPicker from "@/src/components/AssetPicker";
import Inspector from "@/src/studio/Inspector";
import { ObjType, Scene, SceneObj, PALETTE, OBJ_TYPES, iconFor, buildMesh, applyTransform } from "@/src/studio/sceneShared";

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
  const [glReady, setGlReady] = useState(false); // devine true cand scena 3D e creata (ca sa sincronizam obiectele)

  // Scripturi Luau (mai multe fisiere), rulate in sandbox pe server
  const [scriptFiles, setScriptFiles] = useState<ScriptFile[]>([]);
  const scriptsDirty = useRef(false); // se trimit la salvare doar daca au fost modificate
  const [showScript, setShowScript] = useState(false);
  const createdId = useRef<string | null>(null); // id-ul jocului nou creat, ca sa nu se creeze de doua ori
  const [saved, setSaved] = useState(false); // arata "Salvat" o clipa dupa salvare

  // Modele din Shop atasate jocului (folosite din script cu Assets.load("id"))
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const assetsDirty = useRef(false);
  const [showAssets, setShowAssets] = useState(false);

  const meshMap = useRef<Record<string, THREE.Mesh>>({});
  const sceneRef = useRef<THREE.Scene | null>(null);
  const groundRef = useRef<THREE.Mesh | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const selBoxRef = useRef<THREE.BoxHelper | null>(null); // conturul obiectului selectat
  const rafId = useRef<number | null>(null);
  const alive = useRef(true);
  const canvasSize = useRef({ w: 1, h: 1 });

  // Camera orbit control state (manual, gesture-driven — no auto animation)
  const cameraTarget = useRef(new THREE.Vector3(0, 0, 0)); // punctul in jurul caruia se roteste camera
  const cameraAngle = useRef(0.6);       // horizontal angle (radians)
  const cameraPolar = useRef(0.85);      // vertical angle (radians), clamped
  const cameraDistance = useRef(9);      // distance from target
  const lastAngle = useRef(0.6);
  const lastPolar = useRef(0.85);
  const lastDistance = useRef(9);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
    };
  }, []);

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
        try {
          const sr = await api(`/sandbox/games/${editingId}/files`);
          setScriptFiles(Array.isArray(sr?.files) ? sr.files : []);
        } catch {}
        try {
          const ar = await api(`/sandbox/games/${editingId}/assets`);
          setAssetIds(Array.isArray(ar?.asset_ids) ? ar.asset_ids : []);
        } catch {}
      } catch (e: any) { setErr(e.message); }
      setLoading(false);
    })();
  }, [editingId]);

  // Ieșirea din editor: daca sunt scripturi nesalvate, intreaba inainte sa se piarda
  function leave() {
    if (!scriptsDirty.current && !assetsDirty.current) { router.back(); return; }
    Alert.alert("Ieși fără să salvezi?", "Scripturile modificate nu au fost salvate și se vor pierde.", [
      { text: "Rămâi", style: "cancel" },
      { text: "Ieși", style: "destructive", onPress: () => router.back() },
    ]);
  }

  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (scriptsDirty.current || assetsDirty.current) { leave(); return true; }
      return false;
    });
    return () => sub.remove();
  }, []);

  function addObject(type: ObjType) {
    const tg = cameraTarget.current;
    const snap = (v: number) => Math.round(v * 2) / 2;
    const obj: SceneObj = {
      id: uid(),
      type,
      x: snap(tg.x + (Math.random() - 0.5) * 4),
      y: 0,
      z: snap(tg.z + (Math.random() - 0.5) * 4),
      color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
      scale: 1,
    };
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

  function duplicateSel() {
    const src = scene.objects.find(o => o.id === selId);
    if (!src) return;
    const copy: SceneObj = { ...src, id: uid(), x: src.x + 1, z: src.z + 1 };
    setScene(s => ({ ...s, objects: [...s.objects, copy] }));
    setSelId(copy.id);
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
    if (!title || title.length < 2) { setErr("Title too short"); setShowScript(false); setShowMeta(true); return; }
    setBusy(true); setErr(null);
    try {
      const body = { title, description, age_category: ageCategory, is_public: isPublic, category, thumbnail_url: thumbnail, scene };
      let gameId: string | null = editingId || createdId.current;
      if (gameId) {
        await api(`/games/${gameId}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        const r = await api("/games", { method: "POST", body: JSON.stringify(body) });
        gameId = r?.game?.game_id || null;
        createdId.current = gameId;
      }
      if (gameId && scriptsDirty.current) {
        await api(`/sandbox/games/${gameId}/files`, { method: "PUT", body: JSON.stringify({ files: scriptFiles }) });
        scriptsDirty.current = false;
      }
      if (gameId && assetsDirty.current) {
        await api(`/sandbox/games/${gameId}/assets`, { method: "PUT", body: JSON.stringify({ asset_ids: assetIds }) });
        assetsDirty.current = false;
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      if (!editingId && gameId) {
        // joc nou: trecem in modul de editare (apare ▶ si se reincarca de pe server ce s-a salvat)
        router.replace({ pathname: "/studio/edit/[id]", params: { id: gameId } });
      }
    } catch (e: any) { setErr(e.message); setShowScript(false); setShowMeta(true); }
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

  // Sync three.js scene with our state on every scene / selection change
  useEffect(() => {
    const s = sceneRef.current;
    if (!s) return;

    // scoate obiectele sterse
    Object.keys(meshMap.current).forEach(id => {
      if (!scene.objects.find(o => o.id === id)) {
        const old = meshMap.current[id];
        s.remove(old);
        old.geometry.dispose();
        (old.material as THREE.Material).dispose();
        delete meshMap.current[id];
      }
    });

    // adauga / actualizeaza obiectele
    scene.objects.forEach(o => {
      let m = meshMap.current[o.id];
      if (!m) {
        m = buildMesh(o);
        m.userData.objId = o.id;
        meshMap.current[o.id] = m;
        s.add(m);
      } else {
        applyTransform(m, o);
        (m.material as THREE.MeshStandardMaterial).color.set(o.color);
      }
    });

    if (groundRef.current) (groundRef.current.material as THREE.MeshStandardMaterial).color = new THREE.Color(scene.ground);

    // conturul obiectului selectat
    if (selBoxRef.current) {
      s.remove(selBoxRef.current);
      selBoxRef.current.geometry.dispose();
      selBoxRef.current = null;
    }
    const selMesh = selId ? meshMap.current[selId] : undefined;
    if (selMesh) {
      const box = new THREE.BoxHelper(selMesh, 0xCCFF00);
      s.add(box);
      selBoxRef.current = box;
    }
  }, [scene, selId, glReady]);

  function updateCameraPosition() {
    const cam = cameraRef.current;
    if (!cam) return;
    const r = cameraDistance.current;
    const theta = cameraAngle.current;
    const phi = cameraPolar.current;
    const tg = cameraTarget.current;
    cam.position.x = tg.x + r * Math.sin(phi) * Math.cos(theta);
    cam.position.z = tg.z + r * Math.sin(phi) * Math.sin(theta);
    cam.position.y = tg.y + r * Math.cos(phi);
    cam.lookAt(tg);
  }

  function focusSel() {
    const o = scene.objects.find(x => x.id === selId);
    if (!o) return;
    cameraTarget.current.set(o.x, o.y + 0.5 * o.scale * (o.sy ?? 1), o.z);
    updateCameraPosition();
  }

  function resetView() {
    cameraTarget.current.set(0, 0, 0);
    updateCameraPosition();
  }

  const onContextCreate = async (gl: any) => {
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setSize(w, h);
    renderer.setClearColor(new THREE.Color(scene.sky), 1);
    const s = new THREE.Scene();
    sceneRef.current = s;
    const camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 100);
    cameraRef.current = camera;
    updateCameraPosition();
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
    selBoxRef.current = null;

    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);
      renderer.render(s, camera);
      gl.endFrameEXP();
    };
    render();

    // scena 3D exista acum: sincronizam obiectele deja incarcate (jocuri existente)
    setGlReady(true);
  };

  // --- Selectie: atingi un obiect in scena ca sa-l alegi ---
  function pickAt(px: number, py: number) {
    const cam = cameraRef.current;
    if (!cam) return;
    const { w, h } = canvasSize.current;
    const ndc = new THREE.Vector2((px / w) * 2 - 1, -(py / h) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, cam);
    const hits = ray.intersectObjects(Object.values(meshMap.current), false);
    setSelId(hits.length > 0 ? (hits[0].object.userData.objId as string) : null);
  }

  const onTapStateChange = (e: any) => {
    if (e.nativeEvent.state === State.ACTIVE) pickAt(e.nativeEvent.x, e.nativeEvent.y);
  };

  // --- Gesture handlers: drag to orbit, pinch to zoom ---
  const onPanGestureEvent = (e: any) => {
    const { translationX, translationY } = e.nativeEvent;
    cameraAngle.current = lastAngle.current - translationX * 0.008;
    let newPolar = lastPolar.current - translationY * 0.008;
    newPolar = Math.max(0.2, Math.min(Math.PI - 0.2, newPolar)); // clamp to avoid flipping
    cameraPolar.current = newPolar;
    updateCameraPosition();
  };
  const onPanHandlerStateChange = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) {
      lastAngle.current = cameraAngle.current;
      lastPolar.current = cameraPolar.current;
    }
  };

  const onPinchGestureEvent = (e: any) => {
    const scaleFactor = e.nativeEvent.scale;
    let newDist = lastDistance.current / scaleFactor;
    newDist = Math.max(3, Math.min(25, newDist)); // clamp zoom range
    cameraDistance.current = newDist;
    updateCameraPosition();
  };
  const onPinchHandlerStateChange = (e: any) => {
    if (e.nativeEvent.oldState === State.ACTIVE) {
      lastDistance.current = cameraDistance.current;
    }
  };

  const sel = scene.objects.find(o => o.id === selId);

  if (loading) return <SafeAreaView style={styles.center}><ActivityIndicator color={colors.brand} /></SafeAreaView>;

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={leave} testID="editor-back"><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title} numberOfLines={1}>{saved ? "Salvat ✓" : (title || t("create_game"))}</Text>
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
          <>
            <PinchGestureHandler onGestureEvent={onPinchGestureEvent} onHandlerStateChange={onPinchHandlerStateChange}>
              <PanGestureHandler onGestureEvent={onPanGestureEvent} onHandlerStateChange={onPanHandlerStateChange} minPointers={1} maxPointers={1}>
                <TapGestureHandler maxDist={10} onHandlerStateChange={onTapStateChange}>
                  <View
                    style={StyleSheet.absoluteFillObject}
                    onLayout={e => { canvasSize.current = { w: e.nativeEvent.layout.width || 1, h: e.nativeEvent.layout.height || 1 }; }}
                  >
                    <GLView style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
                    <View style={styles.hintPill} pointerEvents="none">
                      <MaterialCommunityIcons name="gesture-swipe" size={14} color={colors.onSurface3} />
                      <Text style={styles.hintText}>Tap to select · Drag to rotate · Pinch to zoom</Text>
                    </View>
                  </View>
                </TapGestureHandler>
              </PanGestureHandler>
            </PinchGestureHandler>
            <Pressable testID="editor-reset-view" onPress={resetView} style={styles.viewBtn}>
              <MaterialCommunityIcons name="home-outline" size={20} color={colors.onSurface} />
            </Pressable>
          </>
        )}
      </View>

      <View style={styles.toolbar}>
        <Text style={styles.toolLabel}>ADD</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12 }}>
          <Pressable testID="editor-script" onPress={() => setShowScript(true)} style={[styles.toolBtn, { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="code-braces" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>{scriptFiles.length > 0 ? `scripts ${scriptFiles.length}` : "scripts"}</Text>
          </Pressable>
          <Pressable testID="editor-assets" onPress={() => setShowAssets(true)} style={[styles.toolBtn, { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="storefront-outline" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>{assetIds.length > 0 ? `shop ${assetIds.length}` : "shop"}</Text>
          </Pressable>
          {OBJ_TYPES.map(k => (
            <Pressable key={k} testID={`editor-add-${k}`} onPress={() => addObject(k)} style={styles.toolBtn}>
              <MaterialCommunityIcons name={iconFor(k) as any} size={22} color={colors.brand} />
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
        <Inspector
          obj={sel}
          onChange={updateSel}
          onDelete={removeSel}
          onDuplicate={duplicateSel}
          onFocus={focusSel}
          onClose={() => setSelId(null)}
        />
      ) : (
        <View style={styles.objList}>
          <Text style={styles.objListTitle}>{scene.objects.length} OBJECTS · tap to select</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingHorizontal: 12, paddingBottom: 8 }}>
            {scene.objects.map(o => (
              <Pressable key={o.id} testID={`editor-obj-${o.id}`} onPress={() => setSelId(o.id)} style={[styles.objChip, { borderColor: o.color }]}>
                <MaterialCommunityIcons name={iconFor(o.type) as any} size={14} color={o.color} />
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

      <ScriptEditor
        visible={showScript}
        files={scriptFiles}
        onChange={files => { scriptsDirty.current = true; setSaved(false); setScriptFiles(files); }}
        onClose={() => setShowScript(false)}
        onSave={save}
        saving={busy}
        saved={saved}
      />

      <AssetPicker
        visible={showAssets}
        assetIds={assetIds}
        onChange={ids => { assetsDirty.current = true; setSaved(false); setAssetIds(ids); }}
        onClose={() => setShowAssets(false)}
      />

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
  hintPill: { position: "absolute", bottom: 10, alignSelf: "center", flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "rgba(0,0,0,0.55)", paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill },
  hintText: { color: colors.onSurface3, fontSize: 11, fontWeight: "600" },
  viewBtn: { position: "absolute", top: 10, right: 10, width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(0,0,0,0.55)", alignItems: "center", justifyContent: "center" },
  toolbar: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingVertical: 10 },
  toolLabel: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 2, paddingHorizontal: 14, marginBottom: 6 },
  toolBtn: { alignItems: "center", justifyContent: "center", width: 68, paddingVertical: 8, backgroundColor: colors.surface3, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, gap: 2 },
  toolBtnText: { color: colors.onSurface, fontSize: 10, fontWeight: "700" },
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