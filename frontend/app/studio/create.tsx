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

print("physics.lua loaded - GRAVITY=" .. tostring(Physics.GRAVITY) .. ", JUMP_SPEED=" .. tostring(Physics.JUMP_SPEED) .. ", MOVE_SPEED=" .. tostring(Physics.MOVE_SPEED))
print("anticheat.lua loaded - MAX_MOVE_PER_TICK=" .. tostring(AntiCheat.MAX_MOVE_PER_TICK) .. ", MAX_VERTICAL_SPEED=" .. tostring(AntiCheat.MAX_VERTICAL_SPEED))
print("world.lua loaded - GRAVITY=" .. tostring(World.GRAVITY) .. ", AIR_DENSITY=" .. tostring(World.AIR_DENSITY))
print("Edit any value above, press Run again, and you'll see it change here.")
` },
      { path: "server/physics.lua", target: "server", source: `-- physics.lua (runs on: SERVER)
--
-- Defines the core physics constants and movement/jump rules for this game's characters.
-- This mirrors the default physics engine built into Astran's Play Mode. You can freely
-- change any value or rewrite the functions below - once Astran's live Luau server runtime
-- is available, this file is what will actually compute player movement, instead of the
-- hardcoded engine Play Mode currently uses.
--
-- CURRENT LIMITATION: Astran does not yet have a live, per-frame Luau server loop that
-- executes this file during gameplay. Today, Play Mode still uses its own built-in
-- movement/physics code. This file is provided now so you can already start customizing
-- your game's physics rules; it will take effect automatically once that runtime ships.

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

Astran does not yet have a live runtime that executes these Luau files during gameplay.
Play Mode currently uses its own built-in physics/rendering/anti-cheat behavior, with the
same default values these files start with. These files are provided now so you can already
start customizing your game's rules - they will take effect automatically, without any
changes needed on your part, once Astran's Luau runtime ships for both server and client.

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