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
import DeleteProjectFlow from "@/src/components/DeleteProjectFlow";
import { useAuth } from "@/src/context/AuthContext";
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

// Scaffold-ul implicit cu care incepe ORICE joc nou (main.lua, fizica, anti-cheat,
// reguli de lume, randare) - IDENTIC, caracter cu caracter, cu backend/server.py:
// _default_game_scripts(). main.lua e punctul de intrare cerut de sandbox (ENTRY_NAME
// din runner.py) - fara el, Run esueaza mereu cu "Missing the 'main' script", indiferent
// ce modifici in celelalte fisiere (BUG REPARAT: scaffold-ul initial nu avea main.lua).
function defaultNewProject(): ProjectState {
  return {
    files: [
      { path: "main.lua", target: "server", source: `-- main.lua (runs on: SERVER)
--
-- Entry point: this is the first (and only) file the sandbox runs when you press Run in
-- the Code Editor. It doesn't do anything on its own - it just loads the other starter
-- files with require(...) and prints a few of their values, so pressing Run gives you
-- immediate, visible proof that your edits to those files are actually being picked up.
--
-- require() paths mirror the project's folder structure, without the .lua extension -
-- "server/physics.lua" becomes require("server/physics"). Feel free to rewrite this file
-- entirely once you have real game logic to run; it isn't special or protected.

local Physics = require("server/physics")
local AntiCheat = require("server/anticheat")
local World = require("server/world")

-- Aceste 3 linii sunt ce conecteaza physics.lua la Play Mode real: valorile tale de
-- mai jos (gravitate, saritura, viteza de mers) chiar schimba cum se joaca jocul, nu
-- doar ce apare printat in consola de Run. Sterge-le doar daca nu mai vrei ca Physics
-- din acest fisier sa controleze miscarea personajului.
workspace.Gravity = math.abs(Physics.GRAVITY)
workspace.JumpSpeed = Physics.JUMP_SPEED
workspace.MoveSpeed = Physics.MOVE_SPEED

print("physics.lua loaded - GRAVITY=" .. tostring(Physics.GRAVITY) .. ", JUMP_SPEED=" .. tostring(Physics.JUMP_SPEED) .. ", MOVE_SPEED=" .. tostring(Physics.MOVE_SPEED))
print("anticheat.lua loaded - MAX_MOVE_PER_TICK=" .. tostring(AntiCheat.MAX_MOVE_PER_TICK) .. ", MAX_VERTICAL_SPEED=" .. tostring(AntiCheat.MAX_VERTICAL_SPEED))
print("world.lua loaded - GRAVITY=" .. tostring(World.GRAVITY) .. ", AIR_DENSITY=" .. tostring(World.AIR_DENSITY))
print("Edit any value above, press Save, then Play - you'll see it change in the actual game.")
` },
      { path: "server/physics.lua", target: "server", source: `-- physics.lua (runs on: SERVER)
--
-- Defines the core physics constants and movement/jump rules for this game's characters.
-- GRAVITY, JUMP_SPEED and MOVE_SPEED below DO affect Play Mode: main.lua assigns them to
-- workspace.Gravity / workspace.JumpSpeed / workspace.MoveSpeed, which the actual player
-- movement in Play Mode now reads (instead of its old hardcoded defaults). Change a value,
-- Save, then Play - you'll feel the difference.
--
-- STILL A LIMITATION: Astran does not yet have a live, per-frame Luau server loop, so the
-- Physics.step()/Physics.tryJump() functions below are not called by Play Mode each tick -
-- only the 3 constants above (via workspace.*) are wired in today. Rewrite step()/tryJump()
-- freely for when a live runtime calls them, but don't expect them to run yet.

local Physics = {}

-- World gravity, in studs/second^2 (negative = pulls down). Matches Astran's current default.
Physics.GRAVITY = -18

-- Initial upward velocity applied when a character jumps, in studs/second.
-- With GRAVITY = -18, this produces a jump height of about 1.56 studs (v^2 / (2 * |g|)).
Physics.JUMP_SPEED = 7.5

-- Horizontal movement speed while walking, in studs/second.
Physics.MOVE_SPEED = 3.2

-- Character capsule radius, used for horizontal collision against solid objects, in studs.
Physics.PLAYER_RADIUS = 0.35

-- Default half-height of a character's collision capsule, in studs (used when a game has
-- no custom avatar body or character model attached).
Physics.PLAYER_HALF_HEIGHT_DEFAULT = 0.9

-- Called once per simulation step (per server tick) for a single player.
-- state: { x, y, z, velocityY, grounded, moveX, moveZ, groundY } - current known state
-- dt: time, in seconds, since the last step
-- Returns the updated { x, y, z, velocityY, grounded } for this player.
function Physics.step(state, dt)
	local velocityY = state.velocityY

	-- Horizontal movement (straight pass-through here; replace with your own logic, e.g.
	-- acceleration or friction, if you want something other than instant-speed walking).
	local x = state.x + state.moveX * Physics.MOVE_SPEED * dt
	local z = state.z + state.moveZ * Physics.MOVE_SPEED * dt

	-- Vertical integration (semi-implicit Euler, same approach as Astran's built-in engine).
	velocityY = velocityY + Physics.GRAVITY * dt
	local y = state.y + velocityY * dt

	local groundY = state.groundY or 0
	local grounded = false
	if y <= groundY then
		y = groundY
		velocityY = 0
		grounded = true
	end

	return { x = x, y = y, z = z, velocityY = velocityY, grounded = grounded }
end

-- Called when the player presses the Jump button (or jump key). Only takes effect if the
-- player is currently grounded - this is what prevents double-jumping / flying.
function Physics.tryJump(state)
	if not state.grounded then
		return state.velocityY
	end
	return Physics.JUMP_SPEED
end

return Physics
` },
      { path: "server/anticheat.lua", target: "server", source: `-- anticheat.lua (runs on: SERVER)
--
-- Server-authoritative validation of what a client claims about itself (position, speed,
-- actions). The core anti-cheat principle: NEVER trust a value a client sends outright -
-- always check it against what is physically possible, and correct or reject it otherwise.
--
-- CURRENT LIMITATION: Astran's live multiplayer sync (see backend/realtime.py) does NOT
-- yet call into this file - it currently relays whatever position a client sends,
-- unvalidated, to other players. This file defines what full server-authoritative
-- validation SHOULD look like; it will be wired into the live sync path once Astran's
-- server-side Luau runtime exists. Until then, treat this as the anti-cheat your game
-- WILL enforce once that exists - not as protection you already have today.

local AntiCheat = {}

-- Maximum distance (in studs) a player is allowed to move in a single server tick, given
-- Physics.MOVE_SPEED and a generous safety margin for lag/jitter. Anything beyond this is
-- treated as impossible movement (speedhack / teleport) and rejected.
AntiCheat.MAX_MOVE_PER_TICK = 0.6

-- Maximum vertical speed a player is allowed to report, in studs/second - a bit above
-- Physics.JUMP_SPEED, to allow for a legitimate jump plus falling speed, but well below
-- what a "fly hack" would produce.
AntiCheat.MAX_VERTICAL_SPEED = 12

-- Validates a claimed new position against the player's last known, server-trusted position.
-- lastState: { x, y, z, t } - last position this server considered valid, and when
-- claimedState: { x, y, z, t } - what the client is now claiming
-- Returns: isValid (boolean), correctedState (the state the server should actually use -
-- either the claimed one, if valid, or the last trusted one, if not)
function AntiCheat.validateMove(lastState, claimedState)
	local dt = math.max(0.001, claimedState.t - lastState.t)

	local dx = claimedState.x - lastState.x
	local dz = claimedState.z - lastState.z
	local horizontalDist = math.sqrt(dx * dx + dz * dz)

	local maxAllowed = AntiCheat.MAX_MOVE_PER_TICK * (dt / (1 / 20)) -- scaled for a ~20 ticks/second server
	if horizontalDist > maxAllowed then
		return false, lastState
	end

	local verticalSpeed = math.abs(claimedState.y - lastState.y) / dt
	if verticalSpeed > AntiCheat.MAX_VERTICAL_SPEED then
		return false, lastState
	end

	return true, claimedState
end

-- Example hook for validating a combat hit (PvP). Replace with real hit detection (e.g.
-- raycasting or hitbox checks performed by the SERVER, never trusting a client's claim of
-- "I hit player X").
function AntiCheat.validateHit(attackerState, targetState, weaponRange)
	local dx = attackerState.x - targetState.x
	local dz = attackerState.z - targetState.z
	local dist = math.sqrt(dx * dx + dz * dz)
	return dist <= weaponRange
end

return AntiCheat
` },
      { path: "server/world.lua", target: "server", source: `-- world.lua (runs on: SERVER)
--
-- Global rules for THIS game's world only - they never affect, and are never affected by,
-- any other game on Astran. Each game has its own fully independent copy of this file.
--
-- CURRENT LIMITATION: see physics.lua - this file is not executed by Play Mode yet.
-- Astran's built-in engine currently applies a single, fixed gravity value to every game.

local World = {}

-- Global gravity for this game's world, in studs/second^2. Change this to make your game's
-- world feel different (e.g. a low-gravity moon level, or a heavier, grounded feel) -
-- this ONLY affects this game, never any other game on the platform.
World.GRAVITY = -18

-- Air density / drag applied to falling or thrown objects in this game's world.
-- 0 = no air resistance.
World.AIR_DENSITY = 0

return World
` },
      { path: "client/rendering.lua", target: "client", source: `-- rendering.lua (runs on: CLIENT)
--
-- Visual/rendering settings for this game: graphics quality presets and render distance
-- options shown to players in the in-game Settings menu. Purely cosmetic - safe to run on
-- the client, since nothing here affects gameplay outcomes (unlike physics.lua/anticheat.lua,
-- which must stay server-side).
--
-- CURRENT LIMITATION: Play Mode currently uses its own built-in graphics/render distance
-- settings (10 fixed presets each), not this file. Once Astran's client-side Luau runtime
-- exists, this file will be what actually drives those settings for your game.

local Rendering = {}

-- Ten lighting presets, from flattest/cheapest (1) to richest/most expensive (10): ambient,
-- directional (sun), hemisphere, and fill light intensities.
Rendering.GRAPHICS_LEVELS = {
	{ ambient = 1.00, directional = 0.00, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.85, directional = 0.45, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.70, directional = 0.80, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.60, directional = 1.00, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.55, directional = 1.10, hemisphere = 0.00, fill = 0.00 },
	{ ambient = 0.45, directional = 1.10, hemisphere = 0.25, fill = 0.00 },
	{ ambient = 0.40, directional = 1.15, hemisphere = 0.30, fill = 0.25 },
	{ ambient = 0.35, directional = 1.20, hemisphere = 0.35, fill = 0.35 },
	{ ambient = 0.30, directional = 1.25, hemisphere = 0.40, fill = 0.45 },
	{ ambient = 0.25, directional = 1.30, hemisphere = 0.45, fill = 0.55 },
}

-- Ten render distance options, in studs, from nearest/cheapest (1) to farthest (10).
Rendering.RENDER_DISTANCES = { 20, 30, 45, 60, 80, 100, 125, 150, 200, 250 }

-- Returns the lighting preset for a given level (1-10), clamped to a valid range.
function Rendering.getGraphicsLevel(level)
	local clamped = math.max(1, math.min(10, level))
	return Rendering.GRAPHICS_LEVELS[clamped]
end

-- Returns the render distance, in studs, for a given level (1-10), clamped to a valid range.
function Rendering.getRenderDistance(level)
	local clamped = math.max(1, math.min(10, level))
	return Rendering.RENDER_DISTANCES[clamped]
end

return Rendering
` },
      { path: "README.md", target: "server", source: `# Game Scripts

This project comes with a starter set of Luau scripts that define how your game behaves.
You can edit, replace, or delete any of them - they are regular files in your project, not
special or protected in any way.

| File | Runs on | What it defines |
|---|---|---|
| \`server/physics.lua\` | Server | Gravity, jump strength, movement speed, and character collision size. |
| \`server/anticheat.lua\` | Server | Server-side validation of player movement and combat, so clients can't cheat by sending fake positions or claiming hits that didn't happen. |
| \`server/world.lua\` | Server | Global rules for this game's world only (gravity, air density) - never shared with or affected by other games. |
| \`client/rendering.lua\` | Client | Graphics quality presets and render distance options shown in the in-game Settings menu. Purely visual - has no effect on gameplay. |

## Server vs. Client

Each file is tagged **Server** or **Client** in the Code Editor (see the toggle next to the
file name when it's open). Server files run with full authority and can't be seen or
tampered with by players - use them for anything that affects fairness (physics, anti-cheat,
scoring). Client files run on each player's own device - use them only for things that are
purely visual or don't need to be trusted (graphics settings, UI, cosmetic effects).

## Current limitation (please read)

Astran does not yet have a live runtime that executes these Luau files every frame during
gameplay. One part IS wired in today though: \`main.lua\` reads GRAVITY/JUMP_SPEED/MOVE_SPEED
from \`server/physics.lua\` and assigns them to \`workspace.Gravity\` / \`workspace.JumpSpeed\` /
\`workspace.MoveSpeed\` - Play Mode's actual player movement uses those values. Everything else
(the rest of \`physics.lua\`'s functions, \`anticheat.lua\`, \`world.lua\`, \`rendering.lua\`) is still
just a starting point - it will take effect automatically, without any changes needed on your
part, once Astran's full Luau runtime ships for both server and client.

## Trying it out with Run

Press Run (top of the Code Editor) to actually execute these scripts right now, in a real
Luau sandbox - this works today, independently of the Play Mode limitation above. main.lua
is the entry point (required by the sandbox); it loads physics.lua, anticheat.lua and
world.lua with require(...) and prints their current values. Change a value in physics.lua,
press Run again, and you'll see your change reflected in the console immediately - that's
how you can confirm an edit actually took effect, even before Play Mode can run these files.
` },
    ],
    folders: [],
  };
}

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
  const { user } = useAuth();
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
  // SECURITY: parola de securitate a proiectului - obligatorie la crearea unui joc NOU
  // (vezi backend/server.py: GameCreateBody.project_password). Nu se foloseste la editare.
  const [projectPassword, setProjectPassword] = useState("");
  const [projectPasswordConfirm, setProjectPasswordConfirm] = useState("");
  const [showProjectPassword, setShowProjectPassword] = useState(false);
  const [showDeleteFlow, setShowDeleteFlow] = useState(false);
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
  // BUG REPARAT (vezi defaultNewProject() mai sus): un joc NOU porneste cu scaffold-ul
  // implicit, nu cu un proiect gol - altfel Code Editor-ul parea gol pana la primul Save,
  // si acel prim Save putea sterge definitiv scaffold-ul creat de backend (vezi save()).
  // Un joc existent (editingId setat) tot incarca normal din backend, prin useEffect de mai
  // jos - valoarea initiala de aici e doar un placeholder pana soseste raspunsul real.
  const [project, setProject] = useState<ProjectState>(() => (editingId ? EMPTY_PROJECT : defaultNewProject()));
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
    if (!editingId) {
      // SECURITY: parola de securitate e obligatorie DOAR la crearea unui proiect nou -
      // backend-ul oricum o cere si o re-valideaza (lungime 6-30, confirmare identica),
      // verificarea de aici e doar ca sa nu trimitem o cerere care va fi oricum refuzata.
      if (projectPassword.length < 6 || projectPassword.length > 30) {
        setErr("Parola de securitate a proiectului trebuie sa aiba intre 6 si 30 de caractere");
        setShowCode(false); setShowMeta(true); return;
      }
      if (projectPassword !== projectPasswordConfirm) {
        setErr("Parolele de securitate introduse nu coincid");
        setShowCode(false); setShowMeta(true); return;
      }
    }
    const maxPlayers = Math.max(MAX_PLAYERS_MIN, Math.min(MAX_PLAYERS_MAX, parseInt(maxPlayersText, 10) || 20));
    setBusy(true); setErr(null);
    try {
      const body: any = {
        title, description, age_category: ageCategory, is_public: isPublic, category,
        subgenre: subgenre.trim() || null,
        thumbnail_url: thumbnail, scene, max_players: maxPlayers,
      };
      if (!editingId) {
        body.project_password = projectPassword;
        body.project_password_confirm = projectPasswordConfirm;
      }
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

  // SECURITY: nu mai stergem direct la apasare - deschidem fluxul in 2 pasi (avertisment +
  // autentificare cu parola de securitate a proiectului). Vezi DeleteProjectFlow.tsx si
  // backend/server.py (DELETE /games/{id} face acum soft-delete + toate verificarile).
  function onDeleted() {
    setShowDeleteFlow(false);
    if (router.canGoBack()) router.back();
    else router.replace("/(tabs)/studio");
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
      if (!m) {        m = buildMesh(o);
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