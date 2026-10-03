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
import CodeStudio from "@/src/components/CodeStudio";
import AssetPicker from "@/src/components/AssetPicker";
import GameAccessManager from "@/src/components/GameAccessManager";
import Inspector, { MultiSelectBar } from "@/src/studio/Inspector";
import {
  ObjType, Scene, SceneObj, PALETTE, OBJ_TYPES, SPAWN_TYPE, SpawnKind,
  iconFor, iconForObj, isSolidDefault, buildMesh, applyTransform, spawnKindOf, spawnMarkerColor,
  GROUND_MIN, GROUND_MAX, groundWidthOf, groundDepthOf, clampGroundSize, buildGroundGeometry,
} from "@/src/studio/sceneShared";
import { ProjectState } from "@/src/studio/projectTree";

function uid() { return Math.random().toString(36).slice(2, 10); }

const MAX_PLAYERS_MIN = 1;
const MAX_PLAYERS_MAX = 10000;

type ModelPick = { model_id: string; name: string; part_count: number };

const EMPTY_PROJECT: ProjectState = { files: [], folders: [] };

// ---------- camera liberă (poziție + yaw/pitch) - DOAR pentru Studio, Play Mode e neatins ----------
const ROTATE_SPEED = 0.006;      // sensibilitate swipe -> rotire cameră (nemodificat ca "feel" fata de orbit)
const MAX_PITCH = 1.4;           // ~80°, evita flip-ul camerei quando privesti drept in sus/jos
const MOVE_SPEED = 6;            // unitati pe secunda la deplasare cu D-pad/tastatura
const DOLLY_SPEED = 10;          // viteza de deplasare inainte/inapoi din pinch
type MoveKey = "forward" | "back" | "left" | "right" | "up" | "down";

// Pas de ajustare rapida pentru butoanele +/- din panoul de Baseplate.
const GROUND_STEP = 10;

export default function StudioEditor() {
  const router = useRouter();
  const { t } = useI18n();
  const params = useLocalSearchParams<{ id?: string }>();
  const editingId = params.id && params.id !== "new" ? params.id : null;

  const [loading, setLoading] = useState(!!editingId);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("adventure");
  const [subgenre, setSubgenre] = useState("");
  const [ageCategory, setAgeCategory] = useState<"under_18" | "adult_18">("under_18");
  const [isPublic, setIsPublic] = useState(true);
  const [thumbnail, setThumbnail] = useState<string | null>(null);
  const [maxPlayersText, setMaxPlayersText] = useState("20");
  const [playerCharacterId, setPlayerCharacterId] = useState<string | null>(null);
  const [scene, setScene] = useState<Scene>({ objects: [], sky: "#0F1012", ground: "#1A1D21" });
  const [selIds, setSelIds] = useState<string[]>([]);
  // BASEPLATE: selectie separata de selIds (care e doar pentru obiecte din scene.objects) -
  // niciodata ambele adevarate in acelasi timp, vezi pickAt() mai jos.
  const [baseplateSelected, setBaseplateSelected] = useState(false);
  const [groundWidthText, setGroundWidthText] = useState(String(groundWidthOf({})));
  const [groundDepthText, setGroundDepthText] = useState(String(groundDepthOf({})));
  const [showMeta, setShowMeta] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [glReady, setGlReady] = useState(false);

  // GROUPS: Group curent al jocului (doar nume/logo - vine din /games/{id}, niciodata tokenul)
  // si campul pentru a atasa/schimba Group-ul prin token privat (vezi save()).
  const [groupName, setGroupName] = useState<string | null>(null);
  const [groupTokenInput, setGroupTokenInput] = useState("");
  const [groupCleared, setGroupCleared] = useState(false);

  // GAME COLLABORATION: modal de gestionare Editors/Testers - doar pentru jocuri deja create.
  const [showAccess, setShowAccess] = useState(false);

  // Code Editor / File Explorer: proiect real de foldere+fisiere (vezi src/studio/projectTree.ts),
  // salvat separat prin /sandbox/games/{id}/files (backend/astran_sandbox/routes.py).
  const [project, setProject] = useState<ProjectState>(EMPTY_PROJECT);
  const projectDirty = useRef(false);
  const [showCode, setShowCode] = useState(false);
  const createdId = useRef<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Modele din Shop atasate jocului (folosite din script cu Assets.load("id"))
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const assetsDirty = useRef(false);
  const [showAssets, setShowAssets] = useState(false);

  // Modelele publicate ale utilizatorului, pentru selectorul de Player Character
  const [myModels, setMyModels] = useState<ModelPick[]>([]);
  const [showCharacterPicker, setShowCharacterPicker] = useState(false);

  const meshMap = useRef<Record<string, THREE.Mesh>>({});
  const sceneRef = useRef<THREE.Scene | null>(null);
  const groundRef = useRef<THREE.Mesh | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const selBoxes = useRef<THREE.BoxHelper[]>([]);
  const rafId = useRef<number | null>(null);
  const alive = useRef(true);
  const canvasSize = useRef({ w: 1, h: 1 });

  // --- Camera liberă: poziție în lume + unghiuri de privire (yaw = orizontal, pitch = vertical) ---
  const cameraPos = useRef(new THREE.Vector3(0, 6, 10));
  const yaw = useRef(0);
  const pitch = useRef(0);
  const lastYaw = useRef(0);
  const lastPitch = useRef(0);
  const pinchStartPos = useRef(new THREE.Vector3());
  // Taste/butoane de mișcare active în acest moment (Studio only - Play Mode nu e atins)
  const activeMoves = useRef<Set<MoveKey>>(new Set());
  const lastFrameTime = useRef<number>(0);

  function forwardVector(): THREE.Vector3 {
    return new THREE.Vector3(
      Math.sin(yaw.current) * Math.cos(pitch.current),
      Math.sin(pitch.current),
      Math.cos(yaw.current) * Math.cos(pitch.current),
    );
  }

  // Strafe orizontal (stânga/dreapta) - independent de pitch, ca într-un editor 3D real:
  // nu vrei să "cazi" când te uiți în sus/jos și apeși stânga/dreapta.
  function rightVector(): THREE.Vector3 {
    return new THREE.Vector3(Math.cos(yaw.current), 0, -Math.sin(yaw.current));
  }

  // Orientează camera spre un punct din lume, fără să-i schimbe poziția - folosit de
  // Focus (pe obiectul selectat) și de Reset View (spre originea scenei).
  function lookAtPoint(point: THREE.Vector3) {
    const dir = point.clone().sub(cameraPos.current);
    const len = dir.length();
    if (len < 0.0001) return;
    dir.normalize();
    yaw.current = Math.atan2(dir.x, dir.z);
    pitch.current = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, Math.asin(Math.max(-1, Math.min(1, dir.y)))));
    updateCameraPosition();
  }

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
        setSubgenre(g.subgenre || "");
        setAgeCategory(g.age_category); setIsPublic(g.is_public);
        setThumbnail(g.thumbnail_url || null);
        setMaxPlayersText(String(g.max_players ?? 20));
        setPlayerCharacterId(g.player_character_model_id ?? null);
        setGroupName(g.group_name || null);
        const loadedScene: Scene = g.scene && g.scene.objects ? g.scene : { objects: [], sky: "#0F1012", ground: "#1A1D21" };
        setScene(loadedScene);
        setGroundWidthText(String(groundWidthOf(loadedScene)));
        setGroundDepthText(String(groundDepthOf(loadedScene)));
        try {
          const pr = await api(`/sandbox/games/${editingId}/files`);
          setProject({
            files: Array.isArray(pr?.files) ? pr.files : [],
            folders: Array.isArray(pr?.folders) ? pr.folders : [],
          });
        } catch {}
        try {
          const ar = await api(`/sandbox/games/${editingId}/assets`);
          setAssetIds(Array.isArray(ar?.asset_ids) ? ar.asset_ids : []);
        } catch {}
      } catch (e: any) { setErr(e.message); }
      setLoading(false);
    })();
  }, [editingId]);

  useEffect(() => {
    api("/shop/models?mine=true").then(r => {
      const items = Array.isArray(r?.items) ? r.items : [];
      setMyModels(items.map((it: any) => ({ model_id: it.item_id, name: it.name, part_count: it.preview?.part_count ?? 0 })));
    }).catch(() => {});
  }, []);

  // --- Tastatură desktop/web (WASD + Space/Ctrl) - inert pe mobil; pe web devine activ doar
  // când viewport-ul 3D rulează acolo (momentan Studio arată un placeholder pe web, vezi mai jos) ---
  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined") return;
    const KEY_MAP: Record<string, MoveKey> = {
      KeyW: "forward", ArrowUp: "forward",
      KeyS: "back", ArrowDown: "back",
      KeyA: "left", ArrowLeft: "left",
      KeyD: "right", ArrowRight: "right",
      Space: "up",
      ControlLeft: "down", ControlRight: "down",
    };
    const onDown = (e: KeyboardEvent) => {
      const mv = KEY_MAP[e.code];
      if (mv) { activeMoves.current.add(mv); e.preventDefault(); }
    };
    const onUp = (e: KeyboardEvent) => {
      const mv = KEY_MAP[e.code];
      if (mv) activeMoves.current.delete(mv);
    };
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
    };
  }, []);

  function leave() {
    if (!projectDirty.current && !assetsDirty.current) { router.back(); return; }
    Alert.alert("Ieși fără să salvezi?", "Codul modificat nu a fost salvat și se va pierde.", [
      { text: "Rămâi", style: "cancel" },
      { text: "Ieși", style: "destructive", onPress: () => router.back() },
    ]);
  }

  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (projectDirty.current || assetsDirty.current) { leave(); return true; }
      return false;
    });
    return () => sub.remove();
  }, []);

  function addObject(type: ObjType) {
    // Obiectele noi apar în fața camerei (pe planul orizontal la care privește momentan),
    // nu mereu lângă același punct fix - util acum că te poți deplasa liber prin scenă.
    const fwd = forwardVector();
    const spawnPoint = cameraPos.current.clone().addScaledVector(new THREE.Vector3(fwd.x, 0, fwd.z).normalize() || fwd, 4);
    const snap = (v: number) => Math.round(v * 2) / 2;
    const obj: SceneObj = {
      id: uid(),
      type,
      x: snap(spawnPoint.x + (Math.random() - 0.5) * 2),
      y: 0,
      z: snap(spawnPoint.z + (Math.random() - 0.5) * 2),
      color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
      scale: 1,
      visible: true,
      solid: isSolidDefault(type),
    };
    setScene(s => ({ ...s, objects: [...s.objects, obj] }));
    setBaseplateSelected(false);
    setSelIds([obj.id]);
  }

  // Adauga un Spawn Point sau un Checkpoint - oricate, spre deosebire de limita de "un singur
  // spawn point" de dinainte. Primul Spawn Point (nu Checkpoint) adaugat in joc devine automat
  // spawn-ul initial, ca jocul sa aiba mereu un loc de start valid din prima; poate fi schimbat
  // oricand din Inspector ("Fa spawn initial" - vezi setInitialSpawn mai jos).
  function addSpawnPoint(kind: SpawnKind) {
    const fwd = forwardVector();
    const horiz = new THREE.Vector2(fwd.x, fwd.z);
    if (horiz.lengthSq() < 0.0001) horiz.set(0, 1);
    horiz.normalize();
    const spawnPoint = new THREE.Vector2(cameraPos.current.x, cameraPos.current.z).addScaledVector(horiz, 4);
    const noSpawnYet = kind === "spawn" && !scene.objects.some(o => o.type === SPAWN_TYPE && spawnKindOf(o) === "spawn");
    const obj: SceneObj = {
      id: uid(),
      type: SPAWN_TYPE,
      spawnKind: kind,
      x: Math.round(spawnPoint.x * 2) / 2, y: 0, z: Math.round(spawnPoint.y * 2) / 2,
      color: kind === "checkpoint" ? "#00E5FF" : "#CCFF00",
      scale: 1, visible: false, solid: false, enabled: true,
      initial: noSpawnYet ? true : undefined,
    };
    setScene(s => ({ ...s, objects: [...s.objects, obj] }));
    setBaseplateSelected(false);
    setSelIds([obj.id]);
  }

  // Marcheaza un Spawn Point ca fiind spawn-ul initial al jocului; se asigura ca doar unul
  // singur are initial=true la un moment dat (Checkpoint-urile nu sunt afectate, ele nu au
  // niciodata initial=true - vezi Inspector, care nu arata butonul pentru ele).
  function setInitialSpawn(id: string) {
    setScene(s => ({
      ...s,
      objects: s.objects.map(o => {
        if (o.type !== SPAWN_TYPE || spawnKindOf(o) !== "spawn") return o;
        return { ...o, initial: o.id === id };
      }),
    }));
  }

  function updateSel(patch: Partial<SceneObj>) {
    if (selIds.length === 0) return;
    const ids = new Set(selIds);
    setScene(s => ({ ...s, objects: s.objects.map(o => (ids.has(o.id) ? { ...o, ...patch } : o)) }));
  }

  function removeSel() {
    if (selIds.length === 0) return;
    const ids = new Set(selIds);
    setScene(s => ({ ...s, objects: s.objects.filter(o => !ids.has(o.id)) }));
    setSelIds([]);
  }

  function duplicateSel() {
    if (selIds.length !== 1) return;
    const src = scene.objects.find(o => o.id === selIds[0]);
    if (!src) return;
    // Un Spawn Point duplicat NU mosteneste initial=true - altfel am avea doua spawn-uri
    // initiale simultan. Ramane doar ca Spawn Point normal, selectabil manual ca initial.
    const copy: SceneObj = { ...src, id: uid(), x: src.x + 1, z: src.z + 1, initial: undefined };
    setScene(s => ({ ...s, objects: [...s.objects, copy] }));
    setSelIds([copy.id]);
  }

  function setSolidForSelection(solid: boolean) {
    updateSel({ solid });
  }

  // ---------- BASEPLATE: Width/Depth ----------
  // Singurul loc din tot editorul care poate schimba dimensiunea Baseplate-ului - nu trece
  // niciodata prin updateSel()/scene.objects, deci nu poate fi atins accidental de Move/Rotate/
  // Scale sau de duplicare/stergere in masa, care opereaza exclusiv pe scene.objects.
  function applyGroundSize(rawWidth: number, rawDepth: number) {
    const width = clampGroundSize(rawWidth);
    const depth = clampGroundSize(rawDepth);
    setScene(s => ({ ...s, groundWidth: width, groundDepth: depth }));
    setGroundWidthText(String(width));
    setGroundDepthText(String(depth));
  }
  function commitGroundWidth() {
    applyGroundSize(parseFloat(groundWidthText) || groundWidthOf(scene), groundDepthOf(scene));
  }
  function commitGroundDepth() {
    applyGroundSize(groundWidthOf(scene), parseFloat(groundDepthText) || groundDepthOf(scene));
  }
  function nudgeGround(dw: number, dd: number) {
    applyGroundSize(groundWidthOf(scene) + dw, groundDepthOf(scene) + dd);
  }
  function resetGroundSize() {
    setScene(s => { const { groundWidth, groundDepth, ...rest } = s; return rest as Scene; });
    setGroundWidthText(String(groundWidthOf({})));
    setGroundDepthText(String(groundDepthOf({})));
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
    if (!title || title.length < 2) { setErr("Title too short"); setShowCode(false); setShowMeta(true); return; }
    const maxPlayers = Math.max(MAX_PLAYERS_MIN, Math.min(MAX_PLAYERS_MAX, parseInt(maxPlayersText, 10) || 20));
    setBusy(true); setErr(null);
    try {
      const body: any = {
        title, description, age_category: ageCategory, is_public: isPublic, category,
        subgenre: subgenre.trim() || null,
        thumbnail_url: thumbnail, scene, max_players: maxPlayers,
      };
      if (playerCharacterId) { body.player_character_model_id = playerCharacterId; body.player_character_source = "shop_model"; }
      else body.clear_player_character = true;

      // GROUPS: trimitem group_token DOAR daca userul a scris ceva in campul "Attach Group"
      // in aceasta sesiune, sau clear_group daca a apasat X pe grupul curent - altfel asocierea
      // existenta ramane neschimbata (nu vrem sa o stergem accidental la fiecare Save).
      if (groupCleared) body.clear_group = true;
      else if (groupTokenInput.trim()) body.group_token = groupTokenInput.trim();

      let gameId: string | null = editingId || createdId.current;
      let savedGame: any = null;
      if (gameId) {
        const r = await api(`/games/${gameId}`, { method: "PATCH", body: JSON.stringify(body) });
        savedGame = r?.game;
      } else {
        const r = await api("/games", { method: "POST", body: JSON.stringify(body) });
        gameId = r?.game?.game_id || null;
        savedGame = r?.game;
        createdId.current = gameId;
      }
      if (savedGame) {
        setGroupName(savedGame.group_name || null);
        setGroupTokenInput("");
        setGroupCleared(false);
      }
      if (gameId && projectDirty.current) {
        await api(`/sandbox/games/${gameId}/files`, {
          method: "PUT",
          body: JSON.stringify({ files: project.files, folders: project.folders }),
        });
        projectDirty.current = false;
      }
      if (gameId && assetsDirty.current) {
        await api(`/sandbox/games/${gameId}/assets`, { method: "PUT", body: JSON.stringify({ asset_ids: assetIds }) });
        assetsDirty.current = false;
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      if (!editingId && gameId) {
        router.replace({ pathname: "/studio/edit/[id]", params: { id: gameId } });
      }
    } catch (e: any) { setErr(e.message); setShowCode(false); setShowMeta(true); }
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

    Object.keys(meshMap.current).forEach(id => {
      if (!scene.objects.find(o => o.id === id)) {
        const old = meshMap.current[id];
        s.remove(old);
        old.geometry.dispose();
        (old.material as THREE.Material).dispose();
        delete meshMap.current[id];
      }
    });

    scene.objects.forEach(o => {
      let m = meshMap.current[o.id];
      if (!m) {
        m = buildMesh(o);
        m.userData.objId = o.id;
        meshMap.current[o.id] = m;
        s.add(m);
      } else {
        applyTransform(m, o);
        (m.material as THREE.MeshStandardMaterial).color.set(o.type === "spawn" ? spawnMarkerColor(o) : o.color);
      }
      m.visible = o.visible !== false || true; // in Studio TOATE obiectele raman vizibile (semi-transparent daca visible=false), ca sa poata fi editate
      const isHidden = o.visible === false;
      (m.material as THREE.MeshStandardMaterial).transparent = isHidden || o.type === "spawn";
      (m.material as THREE.MeshStandardMaterial).opacity = isHidden ? 0.3 : (o.type === "spawn" ? 0.55 : 1);
    });

    if (groundRef.current) (groundRef.current.material as THREE.MeshStandardMaterial).color = new THREE.Color(scene.ground);

    selBoxes.current.forEach(b => { s.remove(b); b.geometry.dispose(); });
    selBoxes.current = [];
    selIds.forEach(id => {
      const m = meshMap.current[id];
      if (!m) return;
      const box = new THREE.BoxHelper(m, 0xCCFF00);
      s.add(box);
      selBoxes.current.push(box);
    });
    // BASEPLATE: acelasi tip de contur galben ca la orice obiect selectat, dar in jurul
    // mesh-ului de ground - confirma vizual ca Baseplate-ul e selectat ca un obiect real.
    if (baseplateSelected && groundRef.current) {
      const gbox = new THREE.BoxHelper(groundRef.current, 0xCCFF00);
      s.add(gbox);
      selBoxes.current.push(gbox);
    }
  }, [scene, selIds, baseplateSelected, glReady]);

  // BASEPLATE: reconstruieste geometria reala a gridului de cate ori Width/Depth se schimba -
  // nu e un fundal static, grila chiar se extinde/micsoreaza (vezi buildGroundGeometry).
  useEffect(() => {
    const g = groundRef.current;
    if (!g || !glReady) return;
    g.geometry.dispose();
    g.geometry = buildGroundGeometry(groundWidthOf(scene), groundDepthOf(scene));
  }, [scene.groundWidth, scene.groundDepth, glReady]);

  // Aplica rotatia camerei (pozitie + lookAt derivat din yaw/pitch) catre obiectul three.js real.
  function updateCameraPosition() {
    const cam = cameraRef.current;
    if (!cam) return;
    cam.position.copy(cameraPos.current);
    const lookTarget = cameraPos.current.clone().add(forwardVector());
    cam.lookAt(lookTarget);
  }

  // Deplaseaza camera in functie de butoanele/tastele tinute apasate, in directia in care
  // priveste (nu muta obiectele) - apelata in fiecare frame din bucla de render de mai jos.
  function applyMovement(dt: number) {
    if (activeMoves.current.size === 0) return;
    const fwd = forwardVector();
    const right = rightVector();
    const step = MOVE_SPEED * dt;
    const moves = activeMoves.current;
    if (moves.has("forward")) cameraPos.current.addScaledVector(fwd, step);
    if (moves.has("back")) cameraPos.current.addScaledVector(fwd, -step);
    if (moves.has("right")) cameraPos.current.addScaledVector(right, step);
    if (moves.has("left")) cameraPos.current.addScaledVector(right, -step);
    if (moves.has("up")) cameraPos.current.y += step;
    if (moves.has("down")) cameraPos.current.y -= step;
    updateCameraPosition();
  }

  function focusSel() {
    const o = scene.objects.find(x => x.id === selIds[selIds.length - 1]);
    if (!o) return;
    lookAtPoint(new THREE.Vector3(o.x, o.y + 0.5 * o.scale * (o.sy ?? 1), o.z));
  }

  function resetView() {
    cameraPos.current.set(0, 6, 10);
    lookAtPoint(new THREE.Vector3(0, 0, 0));
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
    resetView();
    s.add(new THREE.AmbientLight(0xffffff, 0.5));
    const dir = new THREE.DirectionalLight(0xffffff, 1.1);
    dir.position.set(5, 8, 4);
    s.add(dir);
    // BASEPLATE: dimensiunea reala vine din scena (groundWidth/groundDepth), nu mai e fixa.
    const ground = new THREE.Mesh(
      buildGroundGeometry(groundWidthOf(scene), groundDepthOf(scene)),
      new THREE.MeshStandardMaterial({ color: new THREE.Color(scene.ground), wireframe: true })
    );
    ground.rotation.x = -Math.PI / 2;
    s.add(ground);
    groundRef.current = ground;
    Object.keys(meshMap.current).forEach(k => delete meshMap.current[k]);
    selBoxes.current = [];

    lastFrameTime.current = 0;
    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);
      const now = (typeof performance !== "undefined" ? performance.now() : Date.now());
      const dt = lastFrameTime.current ? Math.min((now - lastFrameTime.current) / 1000, 0.1) : 0;
      lastFrameTime.current = now;
      applyMovement(dt);
      renderer.render(s, camera);
      gl.endFrameEXP();
    };
    render();
    setGlReady(true);
  };

  // Proiecteaza pozitia 3D a unui obiect pe coordonate de ecran (px) - folosit de Box Select
  function projectToScreen(o: SceneObj): { x: number; y: number } | null {
    const cam = cameraRef.current;
    if (!cam) return null;
    const { w, h } = canvasSize.current;
    const v = new THREE.Vector3(o.x, o.y + 0.5 * o.scale * (o.sy ?? 1), o.z);
    v.project(cam);
    if (v.z > 1) return null; // in spatele camerei
    return { x: (v.x * 0.5 + 0.5) * w, y: (-v.y * 0.5 + 0.5) * h };
  }

  function pickAt(px: number, py: number) {
    const cam = cameraRef.current;
    if (!cam) return;
    const { w, h } = canvasSize.current;
    const ndc = new THREE.Vector2((px / w) * 2 - 1, -(py / h) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, cam);
    const hits = ray.intersectObjects(Object.values(meshMap.current), false);
    if (hits.length > 0) {
      setBaseplateSelected(false);
      setSelIds([hits[0].object.userData.objId as string]);
      return;
    }
    // BASEPLATE: daca n-am lovit niciun obiect, incercam Baseplate-ul insusi - devine
    // selectabil exact ca un obiect real al scenei, cu propriul panou (vezi randarea de mai jos).
    if (groundRef.current) {
      const gHits = ray.intersectObject(groundRef.current, false);
      if (gHits.length > 0) {
        setSelIds([]);
        setBaseplateSelected(true);
        return;
      }
    }
    setSelIds([]);
    setBaseplateSelected(false);
  }

  const onTapStateChange = (e: any) => {
    if (selectMode) return; // in Box Select tap-ul nu selecteaza individual, doar dreptunghiul conteaza
    if (e.nativeEvent.state === State.ACTIVE) pickAt(e.nativeEvent.x, e.nativeEvent.y);
  };

  // --- Box Select: tragi un deget, se deseneaza un dreptunghi, la final selectam ce cade in el ---
  const [selectMode, setSelectMode] = useState(false);
  const [boxRect, setBoxRect] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const boxStart = useRef<{ x: number; y: number } | null>(null);

  const onBoxPanEvent = (e: any) => {
    if (!selectMode) return;
    const { x, y, state } = e.nativeEvent;
    if (state === State.BEGAN) { boxStart.current = { x, y }; setBoxRect({ x0: x, y0: y, x1: x, y1: y }); return; }
    if (!boxStart.current) return;
    setBoxRect({ x0: boxStart.current.x, y0: boxStart.current.y, x1: x, y1: y });
  };
  const onBoxPanStateChange = (e: any) => {
    if (!selectMode) return;
    if (e.nativeEvent.oldState === State.ACTIVE && boxStart.current) {
      const r = boxRect;
      if (r) {
        const minX = Math.min(r.x0, r.x1), maxX = Math.max(r.x0, r.x1);
        const minY = Math.min(r.y0, r.y1), maxY = Math.max(r.y0, r.y1);
        const inside = scene.objects.filter(o => {
          const p = projectToScreen(o);
          return p && p.x >= minX && p.x <= maxX && p.y >= minY && p.y <= maxY;
        });
        setBaseplateSelected(false);
        setSelIds(inside.map(o => o.id));
      }
      boxStart.current = null;
      setBoxRect(null);
    }
  };

  // --- Rotire cameră prin swipe (dezactivat cat timp Box Select e activ) - acum ajustează
  // direct yaw/pitch ale camerei libere, nu un unghi de orbit în jurul unei ținte ---
  const onPanGestureEvent = (e: any) => {
    if (selectMode) { onBoxPanEvent(e); return; }
    const { translationX, translationY } = e.nativeEvent;
    yaw.current = lastYaw.current - translationX * ROTATE_SPEED;
    pitch.current = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, lastPitch.current - translationY * ROTATE_SPEED));
    updateCameraPosition();
  };
  const onPanHandlerStateChange = (e: any) => {
    if (selectMode) { onBoxPanStateChange(e); return; }
    if (e.nativeEvent.oldState === State.ACTIVE) {
      lastYaw.current = yaw.current;
      lastPitch.current = pitch.current;
    }
  };

  // --- Pinch: acum deplaseaza camera inainte/inapoi pe directia privirii (dolly), nu mai
  // schimba o "distanta de orbit" care nu mai exista in modelul de camera libera ---
  const onPinchGestureEvent = (e: any) => {
    const scaleFactor = e.nativeEvent.scale;
    const delta = (scaleFactor - 1) * DOLLY_SPEED;
    cameraPos.current.copy(pinchStartPos.current).addScaledVector(forwardVector(), delta);
    updateCameraPosition();
  };
  const onPinchHandlerStateChange = (e: any) => {
    if (e.nativeEvent.state === State.BEGAN) {
      pinchStartPos.current.copy(cameraPos.current);
    }
  };

  // --- Butoane de mișcare (D-pad + Up/Down): apasă și ții - mișcarea continuă e aplicată
  // în bucla de render (applyMovement), nu aici - acestea doar pornesc/opresc direcția ---
  function startMove(k: MoveKey) { activeMoves.current.add(k); }
  function stopMove(k: MoveKey) { activeMoves.current.delete(k); }

  const singleSel = selIds.length === 1 ? scene.objects.find(o => o.id === selIds[0]) : undefined;
  const hasSpawnPoint = scene.objects.some(o => o.type === SPAWN_TYPE && spawnKindOf(o) === "spawn");
  const hasCheckpoint = scene.objects.some(o => o.type === SPAWN_TYPE && spawnKindOf(o) === "checkpoint");
  const selectedCharacter = myModels.find(m => m.model_id === playerCharacterId);

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
            <PinchGestureHandler onGestureEvent={onPinchGestureEvent} onHandlerStateChange={onPinchHandlerStateChange} enabled={!selectMode}>
              <PanGestureHandler onGestureEvent={onPanGestureEvent} onHandlerStateChange={onPanHandlerStateChange} minPointers={1} maxPointers={1}>
                <TapGestureHandler maxDist={10} onHandlerStateChange={onTapStateChange}>
                  <View
                    style={StyleSheet.absoluteFillObject}
                    onLayout={e => { canvasSize.current = { w: e.nativeEvent.layout.width || 1, h: e.nativeEvent.layout.height || 1 }; }}
                  >
                    <GLView style={StyleSheet.absoluteFillObject} onContextCreate={onContextCreate} />
                    {boxRect ? (
                      <View
                        pointerEvents="none"
                        style={[
                          styles.boxSelectRect,
                          {
                            left: Math.min(boxRect.x0, boxRect.x1),
                            top: Math.min(boxRect.y0, boxRect.y1),
                            width: Math.abs(boxRect.x1 - boxRect.x0),
                            height: Math.abs(boxRect.y1 - boxRect.y0),
                          },
                        ]}
                      />
                    ) : null}
                    <View style={styles.hintPill} pointerEvents="none">
                      <MaterialCommunityIcons name="gesture-swipe" size={14} color={colors.onSurface3} />
                      <Text style={styles.hintText}>{selectMode ? "Drag to box-select objects" : "Swipe to look around · Tap to select · Pinch to move forward/back"}</Text>
                    </View>
                  </View>
                </TapGestureHandler>
              </PanGestureHandler>
            </PinchGestureHandler>

            {/* D-pad: deplasare pe planul orizontal, în direcția în care privește camera */}
            <View style={styles.dpad} pointerEvents="box-none">
              <Pressable
                testID="editor-move-forward"
                onPressIn={() => startMove("forward")}
                onPressOut={() => stopMove("forward")}
                style={[styles.dpadBtn, { top: 0, left: 46 }]}
              >
                <MaterialCommunityIcons name="chevron-up" size={26} color={colors.onSurface} />
              </Pressable>
              <Pressable
                testID="editor-move-left"
                onPressIn={() => startMove("left")}
                onPressOut={() => stopMove("left")}
                style={[styles.dpadBtn, { top: 46, left: 0 }]}
              >
                <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
              </Pressable>
              <Pressable
                testID="editor-move-right"
                onPressIn={() => startMove("right")}
                onPressOut={() => stopMove("right")}
                style={[styles.dpadBtn, { top: 46, left: 92 }]}
              >
                <MaterialCommunityIcons name="chevron-right" size={26} color={colors.onSurface} />
              </Pressable>
              <Pressable
                testID="editor-move-back"
                onPressIn={() => startMove("back")}
                onPressOut={() => stopMove("back")}
                style={[styles.dpadBtn, { top: 92, left: 46 }]}
              >
                <MaterialCommunityIcons name="chevron-down" size={26} color={colors.onSurface} />
              </Pressable>
            </View>

            {/* Up / Down: deplasare pe verticală (axa lumii, nu direcția privirii) */}
            <View style={styles.vpad} pointerEvents="box-none">
              <Pressable
                testID="editor-move-up"
                onPressIn={() => startMove("up")}
                onPressOut={() => stopMove("up")}
                style={[styles.dpadBtn, { top: 0 }]}
              >
                <MaterialCommunityIcons name="chevron-up" size={22} color={colors.onSurface} />
                <Text style={styles.vpadLabel}>UP</Text>
              </Pressable>
              <Pressable
                testID="editor-move-down"
                onPressIn={() => startMove("down")}
                onPressOut={() => stopMove("down")}
                style={[styles.dpadBtn, { top: 92 }]}
              >
                <MaterialCommunityIcons name="chevron-down" size={22} color={colors.onSurface} />
                <Text style={styles.vpadLabel}>DOWN</Text>
              </Pressable>
            </View>

            <Pressable testID="editor-select-mode" onPress={() => { setSelectMode(v => !v); setSelIds([]); setBaseplateSelected(false); }} style={[styles.viewBtn, { right: 54 }, selectMode && { borderColor: colors.brand, borderWidth: 1 }]}>
              <MaterialCommunityIcons name="selection-drag" size={20} color={selectMode ? colors.brand : colors.onSurface} />
            </Pressable>
            <Pressable testID="editor-reset-view" onPress={resetView} style={styles.viewBtn}>
              <MaterialCommunityIcons name="home-outline" size={20} color={colors.onSurface} />
            </Pressable>
          </>
        )}
      </View>

      <View style={styles.toolbar}>
        <Text style={styles.toolLabel}>ADD</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12 }}>
          <Pressable testID="editor-script" onPress={() => setShowCode(true)} style={[styles.toolBtn, { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="code-braces" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>{project.files.length > 0 ? `code ${project.files.length}` : "code"}</Text>
          </Pressable>
          <Pressable testID="editor-assets" onPress={() => setShowAssets(true)} style={[styles.toolBtn, { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="storefront-outline" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>{assetIds.length > 0 ? `shop ${assetIds.length}` : "shop"}</Text>
          </Pressable>
          <Pressable
            testID="editor-select-baseplate"
            onPress={() => { setSelIds([]); setBaseplateSelected(true); }}
            style={[styles.toolBtn, baseplateSelected && { borderColor: colors.brand }]}
          >
            <MaterialCommunityIcons name="grid" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>baseplate</Text>
          </Pressable>
          <Pressable testID="editor-add-spawn" onPress={() => addSpawnPoint("spawn")} style={[styles.toolBtn, hasSpawnPoint && { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="map-marker-radius-outline" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>spawn</Text>
          </Pressable>
          <Pressable testID="editor-add-checkpoint" onPress={() => addSpawnPoint("checkpoint")} style={[styles.toolBtn, hasCheckpoint && { borderColor: colors.brand }]}>
            <MaterialCommunityIcons name="flag-checkered" size={22} color={colors.brand} />
            <Text style={styles.toolBtnText}>checkpoint</Text>
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

      {selIds.length > 1 ? (
        <MultiSelectBar
          count={selIds.length}
          onSolid={() => setSolidForSelection(true)}
          onUnsolid={() => setSolidForSelection(false)}
          onDelete={removeSel}
          onClear={() => setSelIds([])}
        />
      ) : singleSel ? (
        <Inspector
          obj={singleSel}
          onChange={updateSel}
          onDelete={removeSel}
          onDuplicate={duplicateSel}
          onFocus={focusSel}
          onClose={() => setSelIds([])}
          onSetInitial={() => setInitialSpawn(singleSel.id)}
        />
      ) : baseplateSelected ? (
        <View style={styles.baseplatePanel}>
          <View style={styles.baseplateHeader}>
            <MaterialCommunityIcons name="grid" size={16} color={colors.brand} />
            <Text style={styles.baseplateTitle}>BASEPLATE</Text>
            <Pressable testID="baseplate-close" onPress={() => setBaseplateSelected(false)} style={styles.baseplateCloseBtn}>
              <MaterialCommunityIcons name="close" size={16} color={colors.onSurface3} />
            </Pressable>
          </View>
          <View style={styles.baseplateRow}>
            <View style={styles.baseplateField}>
              <Text style={styles.baseplateLabel}>WIDTH (X)</Text>
              <View style={styles.baseplateStepperRow}>
                <Pressable testID="baseplate-width-minus" onPress={() => nudgeGround(-GROUND_STEP, 0)} style={styles.baseplateStepBtn}>
                  <MaterialCommunityIcons name="minus" size={16} color={colors.onSurface} />
                </Pressable>
                <TextInput
                  testID="baseplate-width-input"
                  value={groundWidthText}
                  onChangeText={setGroundWidthText}
                  onEndEditing={commitGroundWidth}
                  keyboardType="number-pad"
                  style={styles.baseplateInput}
                />
                <Pressable testID="baseplate-width-plus" onPress={() => nudgeGround(GROUND_STEP, 0)} style={styles.baseplateStepBtn}>
                  <MaterialCommunityIcons name="plus" size={16} color={colors.onSurface} />
                </Pressable>
              </View>
            </View>
            <View style={styles.baseplateField}>
              <Text style={styles.baseplateLabel}>DEPTH (Z)</Text>
              <View style={styles.baseplateStepperRow}>
                <Pressable testID="baseplate-depth-minus" onPress={() => nudgeGround(0, -GROUND_STEP)} style={styles.baseplateStepBtn}>
                  <MaterialCommunityIcons name="minus" size={16} color={colors.onSurface} />
                </Pressable>
                <TextInput
                  testID="baseplate-depth-input"
                  value={groundDepthText}
                  onChangeText={setGroundDepthText}
                  onEndEditing={commitGroundDepth}
                  keyboardType="number-pad"
                  style={styles.baseplateInput}
                />
                <Pressable testID="baseplate-depth-plus" onPress={() => nudgeGround(0, GROUND_STEP)} style={styles.baseplateStepBtn}>
                  <MaterialCommunityIcons name="plus" size={16} color={colors.onSurface} />
                </Pressable>
              </View>
            </View>
          </View>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
            <Text style={styles.baseplateHint}>{GROUND_MIN}–{GROUND_MAX} units · grid updates live</Text>
            <Pressable testID="baseplate-reset" onPress={resetGroundSize}>
              <Text style={styles.baseplateResetText}>Reset to default</Text>
            </Pressable>
          </View>
        </View>
      ) : (
        <View style={styles.objList}>
          <Text style={styles.objListTitle}>{scene.objects.length} OBJECTS · tap to select, or use Box Select</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingHorizontal: 12, paddingBottom: 8 }}>
            {scene.objects.map(o => {
              const spawnLike = o.type === SPAWN_TYPE;
              const label = spawnLike ? (spawnKindOf(o) === "checkpoint" ? "checkpoint" : "spawn") : o.type;
              return (
                <Pressable key={o.id} testID={`editor-obj-${o.id}`} onPress={() => { setBaseplateSelected(false); setSelIds([o.id]); }} style={[styles.objChip, { borderColor: spawnLike ? colors.brand : o.color }]}>
                  <MaterialCommunityIcons name={iconForObj(o) as any} size={14} color={spawnLike ? colors.brand : o.color} />
                  <Text style={styles.objChipText}>{label}</Text>
                </Pressable>
              );
            })}
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

      <CodeStudio
        visible={showCode}
        project={project}
        onChange={next => { projectDirty.current = true; setSaved(false); setProject(next); }}
        onClose={() => setShowCode(false)}
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

      {editingId ? (
        <GameAccessManager visible={showAccess} gameId={editingId} onClose={() => setShowAccess(false)} />
      ) : null}

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
            <Text style={styles.lab}>Genre</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 4 }}>
              {["adventure", "shooter", "simulation", "racing", "roleplay", "puzzle", "other"].map(c => (
                <Pressable key={c} onPress={() => setCategory(c)} style={[styles.pill, category === c && styles.pillActive]} testID={`editor-cat-${c}`}>
                  <Text style={[styles.pillText, category === c && { color: colors.brand }]}>{c}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <Text style={styles.lab}>Subgenre (optional)</Text>
            <TextInput
              testID="editor-subgenre"
              value={subgenre}
              onChangeText={setSubgenre}
              style={styles.input}
              placeholder="e.g. Battle Royale, Tycoon, Horror..."
              placeholderTextColor={colors.onSurface3}
              maxLength={40}
            />
            <Text style={styles.lab}>{t("age_select_title")}</Text>
            <View style={{ flexDirection: "row", gap: 8 }}>
              <Pressable testID="editor-age-under" onPress={() => setAgeCategory("under_18")} style={[styles.ageBtn, ageCategory === "under_18" && styles.ageBtnActive]}><Text style={[styles.ageBtnText, ageCategory === "under_18" && { color: colors.brand }]}>{t("age_under_18")}</Text></Pressable>
              <Pressable testID="editor-age-adult" onPress={() => setAgeCategory("adult_18")} style={[styles.ageBtn, ageCategory === "adult_18" && styles.ageBtnActive]}><Text style={[styles.ageBtnText, ageCategory === "adult_18" && { color: colors.brand }]}>{t("age_18_plus")}</Text></Pressable>
            </View>

            <Text style={styles.lab}>Max Players</Text>
            <TextInput
              testID="editor-max-players"
              value={maxPlayersText}
              onChangeText={t => setMaxPlayersText(t.replace(/[^0-9]/g, ""))}
              keyboardType="number-pad"
              style={styles.input}
              placeholder="20"
              placeholderTextColor={colors.onSurface3}
            />
            <Text style={styles.hintSmall}>Servers fill up to this number, then new players join a new server automatically.</Text>

            <Text style={styles.lab}>Player Character</Text>
            <Pressable testID="editor-pick-character" onPress={() => setShowCharacterPicker(true)} style={styles.characterPick}>
              <MaterialCommunityIcons name={selectedCharacter ? "cube-scan" : "account-outline"} size={20} color={colors.brand} />
              <Text style={styles.characterPickText}>{selectedCharacter ? selectedCharacter.name : "Personal avatar (default)"}</Text>
              <MaterialCommunityIcons name="chevron-right" size={18} color={colors.onSurface3} />
            </Pressable>

            <Text style={styles.lab}>Group</Text>
            {groupName && !groupCleared ? (
              <View style={styles.groupPill}>
                <MaterialCommunityIcons name="account-group" size={16} color={colors.brand} />
                <Text style={styles.groupPillText}>{groupName}</Text>
                <Pressable testID="editor-clear-group" onPress={() => { setGroupCleared(true); setGroupName(null); }}>
                  <MaterialCommunityIcons name="close-circle" size={18} color={colors.onSurface3} />
                </Pressable>
              </View>
            ) : (
              <TextInput
                testID="editor-group-token"
                value={groupTokenInput}
                onChangeText={text => { setGroupTokenInput(text); setGroupCleared(false); }}
                style={styles.input}
                placeholder="Group Token (optional)"
                placeholderTextColor={colors.onSurface3}
                autoCapitalize="none"
              />
            )}
            <Text style={styles.hintSmall}>Leave empty to publish under your personal account. Paste your Group's private token to publish under that Group.</Text>

            {editingId ? (
              <Pressable testID="editor-manage-access" onPress={() => setShowAccess(true)} style={styles.characterPick}>
                <MaterialCommunityIcons name="account-key-outline" size={20} color={colors.brand} />
                <Text style={styles.characterPickText}>Access & Collaboration</Text>
                <MaterialCommunityIcons name="chevron-right" size={18} color={colors.onSurface3} />
              </Pressable>
            ) : null}

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

      <Modal visible={showCharacterPicker} transparent animationType="fade" onRequestClose={() => setShowCharacterPicker(false)}>
        <Pressable style={styles.pickerBackdrop} onPress={() => setShowCharacterPicker(false)}>
          <View style={styles.pickerBox}>
            <Text style={styles.sheetTitle}>Player Character</Text>
            <Pressable
              testID="character-pick-personal"
              onPress={() => { setPlayerCharacterId(null); setShowCharacterPicker(false); }}
              style={[styles.characterRow, !playerCharacterId && styles.characterRowActive]}
            >
              <MaterialCommunityIcons name="account-outline" size={20} color={colors.brand} />
              <Text style={styles.characterRowText}>Personal avatar (each player's own)</Text>
              {!playerCharacterId ? <MaterialCommunityIcons name="check" size={18} color={colors.brand} /> : null}
            </Pressable>
            <ScrollView style={{ maxHeight: 260 }}>
              {myModels.map(m => (
                <Pressable
                  key={m.model_id}
                  testID={`character-pick-${m.model_id}`}
                  onPress={() => { setPlayerCharacterId(m.model_id); setShowCharacterPicker(false); }}
                  style={[styles.characterRow, playerCharacterId === m.model_id && styles.characterRowActive]}
                >
                  <MaterialCommunityIcons name="cube-scan" size={20} color={colors.brand} />
                  <Text style={styles.characterRowText} numberOfLines={1}>{m.name}</Text>
                  {playerCharacterId === m.model_id ? <MaterialCommunityIcons name="check" size={18} color={colors.brand} /> : null}
                </Pressable>
              ))}
              {myModels.length === 0 ? <Text style={styles.hintSmall}>Publish a model in Shop first to use it as a character.</Text> : null}
            </ScrollView>
          </View>
        </Pressable>
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
  boxSelectRect: { position: "absolute", borderWidth: 2, borderColor: colors.brand, backgroundColor: "rgba(204,255,0,0.15)" },
  viewBtn: { position: "absolute", top: 10, right: 10, width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(0,0,0,0.55)", alignItems: "center", justifyContent: "center" },
  dpad: { position: "absolute", left: 14, bottom: 14, width: 138, height: 138 },
  vpad: { position: "absolute", right: 14, bottom: 14, width: 52, height: 138 },
  dpadBtn: {
    position: "absolute", width: 46, height: 46, borderRadius: 23,
    backgroundColor: "rgba(15,16,18,0.72)", borderWidth: 1, borderColor: colors.border,
    alignItems: "center", justifyContent: "center",
  },
  vpadLabel: { color: colors.onSurface3, fontSize: 7, fontWeight: "800", marginTop: -2 },
  toolbar: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingVertical: 10 },
  toolLabel: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 2, paddingHorizontal: 14, marginBottom: 6 },
  toolBtn: { alignItems: "center", justifyContent: "center", width: 68, paddingVertical: 8, backgroundColor: colors.surface3, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, gap: 2 },
  toolBtnText: { color: colors.onSurface, fontSize: 10, fontWeight: "700" },
  objList: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingTop: 8 },
  objListTitle: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 1.5, paddingHorizontal: 14, marginBottom: 6 },
  objChip: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.surface3, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1 },
  objChipText: { color: colors.onSurface, fontSize: 11, fontWeight: "700", textTransform: "capitalize" },
  baseplatePanel: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, padding: spacing.md },
  baseplateHeader: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 10 },
  baseplateTitle: { flex: 1, color: colors.onSurface, fontSize: 11, fontWeight: "800", letterSpacing: 1.5 },
  baseplateCloseBtn: { width: 24, height: 24, borderRadius: 12, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" },
  baseplateRow: { flexDirection: "row", gap: 12 },
  baseplateField: { flex: 1 },
  baseplateLabel: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 1, marginBottom: 6 },
  baseplateStepperRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  baseplateStepBtn: { width: 32, height: 32, borderRadius: radius.sm, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  baseplateInput: { flex: 1, textAlign: "center", backgroundColor: colors.surface3, color: colors.onSurface, fontWeight: "800", fontSize: 14, borderRadius: radius.sm, paddingVertical: 8, borderWidth: 1, borderColor: colors.border },
  baseplateHint: { color: colors.onSurface3, fontSize: 10, marginTop: 10 },
  baseplateResetText: { color: colors.brand, fontSize: 11, fontWeight: "700", marginTop: 10 },
  footer: { flexDirection: "row", alignItems: "center", gap: 8, padding: spacing.md, borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface },
  delBtn: { width: 44, height: 44, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.error, alignItems: "center", justifyContent: "center" },
  playBtn: { width: 44, height: 44, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  sheet: { maxHeight: "88%", backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border },
  sheetTitle: { color: colors.onSurface, fontSize: 20, fontWeight: "900", marginBottom: 12 },
  thumbBox: { height: 140, borderRadius: radius.md, overflow: "hidden", marginBottom: 4, borderWidth: 1, borderColor: colors.border },
  thumbOverlay: { position: "absolute", right: 8, bottom: 8, flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: "rgba(0,0,0,0.6)", paddingHorizontal: 10, paddingVertical: 6, borderRadius: radius.pill },
  thumbText: { color: "#fff", fontSize: 11, fontWeight: "700" },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14 },
  hintSmall: { color: colors.onSurface3, fontSize: 11, marginTop: 6 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  pill: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  pillActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  pillText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12, textTransform: "capitalize" },
  ageBtn: { flex: 1, padding: 12, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  ageBtnActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  ageBtnText: { color: colors.onSurface2, fontWeight: "700" },
  characterPick: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 6, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  characterPickText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  groupPill: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6, padding: 12, backgroundColor: colors.brandTint, borderRadius: radius.md, borderWidth: 1, borderColor: colors.brand },
  groupPillText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  toggle: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  switch: { width: 44, height: 26, borderRadius: 13, backgroundColor: colors.surface3, padding: 3 },
  switchOn: { backgroundColor: colors.brand },
  knob: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.onSurface2 },
  knobOn: { backgroundColor: colors.onBrand, marginLeft: "auto" },
  err: { color: colors.error, marginTop: 8, fontSize: 12, fontWeight: "600" },
  pickerBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  pickerBox: { width: "100%", maxWidth: 400, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  characterRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: 12, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, marginBottom: 8 },
  characterRowActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  characterRowText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 13 },
});