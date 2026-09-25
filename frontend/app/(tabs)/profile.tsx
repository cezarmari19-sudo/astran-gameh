import React, { useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { GLView } from "expo-gl";
import { Renderer } from "expo-three";
import * as THREE from "three";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { AstransPill } from "@/src/components/ui";
import { colors, radius, spacing } from "@/src/theme";
import { api } from "@/src/api/client";
import {
  Slot, SLOT_DEFS, AvatarBody, Equipped, defaultBody,
  buildBodyMeshes, layoutBody, bodyHeightWorld, buildEquippedGroup, disposeGroup, SLOT_ANCHOR_Y,
} from "@/src/avatar/avatarTypes";
import type { Part } from "@/src/studio3d/modelTypes";

export default function ProfileScreen() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const { t } = useI18n();

  const Row = ({ icon, label, onPress, testID, danger }: any) => (
    <Pressable testID={testID} onPress={onPress} style={styles.row}>
      <MaterialCommunityIcons name={icon} size={22} color={danger ? colors.error : colors.brand} />
      <Text style={[styles.rowText, danger && { color: colors.error }]}>{label}</Text>
      <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
    </Pressable>
  );

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }}>
        <View style={styles.headerCard}>
          <Pressable testID="profile-avatar-preview" onPress={() => router.push("/(tabs)/avatar" as any)} style={styles.avatarPreviewWrap}>
            <AvatarPreview />
            <View style={styles.editBadge}>
              <MaterialCommunityIcons name="pencil" size={12} color={colors.onBrand} />
            </View>
          </Pressable>
          <Text style={styles.name} testID="profile-name">{user?.display_name || user?.username}</Text>
          <Text style={styles.uname}>@{user?.username}</Text>
          <View style={styles.badges}>
            {user?.is_platform_owner ? (
              <View style={[styles.badge, { backgroundColor: colors.ownerRed }]}><Text style={styles.badgeText}>OWNER</Text></View>
            ) : null}
            {user?.is_platform_admin && !user?.is_platform_owner ? (
              <View style={[styles.badge, { backgroundColor: colors.ownerRed }]}><Text style={styles.badgeText}>ADMIN</Text></View>
            ) : null}
            <View style={[styles.badge, { backgroundColor: user?.age_category === "adult_18" ? colors.error : colors.brandTint, borderColor: user?.age_category === "adult_18" ? colors.error : colors.brand, borderWidth: 1 }]}>
              <Text style={[styles.badgeText, { color: user?.age_category === "adult_18" ? "#fff" : colors.brand }]}>
                {user?.age_category === "adult_18" ? "18+" : "UNDER 18"}
              </Text>
            </View>
          </View>
          <View style={{ marginTop: 16 }}>
            <AstransPill balance={user?.astrans_balance || 0} onPress={() => router.push("/wallet")} />
          </View>
        </View>

        <Row icon="account-outline" label="Avatar" onPress={() => router.push("/(tabs)/avatar" as any)} testID="profile-avatar" />
        <Row icon="wallet-outline" label={t("wallet")} onPress={() => router.push("/wallet")} testID="profile-wallet" />
        <Row icon="cog-outline" label={t("settings")} onPress={() => router.push("/settings")} testID="profile-settings" />
        <Row icon="translate" label={t("language")} onPress={() => router.push("/language")} testID="profile-language" />
        <Row icon="logout" label={t("logout")} onPress={async () => { await logout(); router.replace("/(auth)/login"); }} testID="profile-logout" danger />
      </ScrollView>
    </SafeAreaView>
  );
}

// Preview 3D mic, doar rotire automata (fara gesturi) - foloseste avatarul salvat al userului.
function AvatarPreview() {
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const bodyRef = useRef<ReturnType<typeof buildBodyMeshes> | null>(null);
  const equippedGroups = useRef<Partial<Record<Slot, THREE.Group>>>({});
  const rafId = useRef<number | null>(null);
  const alive = useRef(true);
  const angle = useRef(0.4);
  const [body, setBody] = useState<AvatarBody>(defaultBody());
  const [equipped, setEquipped] = useState<Equipped>({});
  const partsCache = useRef<Record<string, Part[]>>({});

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (rafId.current !== null) cancelAnimationFrame(rafId.current);
    };
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const r = await api("/avatar/me");
        const a = r?.avatar;
        if (a?.body) setBody({ ...defaultBody(), ...a.body });
        if (a?.equipped) setEquipped(a.equipped);
      } catch {
        // preview ramane cu avatarul implicit daca cererea esueaza
      }
    })();
  }, []);

  useEffect(() => {
    if (!bodyRef.current) return;
    layoutBody(bodyRef.current, body);
  }, [body]);

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
          if (existing) { s.remove(existing); disposeGroup(existing); delete equippedGroups.current[slot]; }
          continue;
        }
        if (existing && (existing.userData as any).itemId === itemId) continue;
        if (existing) { s.remove(existing); disposeGroup(existing); delete equippedGroups.current[slot]; }
        let parts = partsCache.current[itemId];
        if (!parts) {
          try {
            const r = await api(`/shop/items/${itemId}`);
            parts = Array.isArray(r?.item?.parts) ? r.item.parts : [];
            partsCache.current[itemId] = parts;
          } catch { parts = []; }
        }
        if (cancelled || parts.length === 0) continue;
        const g = buildEquippedGroup(parts);
        (g.userData as any).itemId = itemId;
        s.add(g);
        equippedGroups.current[slot] = g;
      }
      const h = bodyHeightWorld(body);
      (Object.keys(equippedGroups.current) as Slot[]).forEach(slot => {
        const g = equippedGroups.current[slot];
        if (!g) return;
        g.position.set(0, h * (SLOT_ANCHOR_Y[slot] ?? 0.5), 0);
        g.scale.setScalar(body.width);
      });
    })();
    return () => { cancelled = true; };
  }, [equipped, body]);

  const onContextCreate = async (gl: any) => {
    const { drawingBufferWidth: w, drawingBufferHeight: h } = gl;
    const renderer = new Renderer({ gl });
    renderer.setSize(w, h);
    renderer.setClearColor(new THREE.Color(colors.surface3), 1);

    const s = new THREE.Scene();
    sceneRef.current = s;
    const camera = new THREE.PerspectiveCamera(40, w / h, 0.05, 20);
    cameraRef.current = camera;

    s.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(2, 4, 3);
    s.add(dir);

    const bm = buildBodyMeshes();
    bodyRef.current = bm;
    layoutBody(bm, body);
    s.add(bm.group);

    const target = bodyHeightWorld(body) * 0.55;

    const render = () => {
      if (!alive.current) return;
      rafId.current = requestAnimationFrame(render);
      angle.current += 0.012;
      const r = 2.4;
      camera.position.set(r * Math.sin(angle.current), target, r * Math.cos(angle.current));
      camera.lookAt(0, target, 0);
      renderer.render(s, camera);
      gl.endFrameEXP();
    };
    render();
  };

  if (Platform.OS === "web") {
    return (
      <View style={[styles.avatarPreview, { alignItems: "center", justifyContent: "center" }]}>
        <MaterialCommunityIcons name="account-outline" size={32} color={colors.brand} />
      </View>
    );
  }

  return <GLView style={styles.avatarPreview} onContextCreate={onContextCreate} />;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  headerCard: { alignItems: "center", padding: spacing.xl, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, marginBottom: spacing.lg },
  avatarPreviewWrap: { width: 96, height: 96, borderRadius: 48, overflow: "hidden", borderWidth: 2, borderColor: colors.brand },
  avatarPreview: { width: "100%", height: "100%" },
  editBadge: { position: "absolute", bottom: 0, right: 0, width: 24, height: 24, borderRadius: 12, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center", borderWidth: 2, borderColor: colors.surface2 },
  name: { color: colors.onSurface, fontSize: 20, fontWeight: "800", marginTop: 12 },
  uname: { color: colors.onSurface3, fontSize: 13, marginTop: 2 },
  badges: { flexDirection: "row", gap: 6, marginTop: 10 },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.sm },
  badgeText: { color: "#fff", fontWeight: "900", fontSize: 10, letterSpacing: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: 14, backgroundColor: colors.surface2, padding: 16, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  rowText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 14 },
});