import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Image } from "expo-image";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function titleCase(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}

export default function GameDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useI18n();
  const [game, setGame] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api(`/games/${id}`);
        setGame(res.game);
      } catch (e: any) {
        setErr(e.message || "Failed");
      }
    })();
  }, [id]);

  if (err) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={{ color: colors.error }} testID="game-error">{err}</Text>
      </SafeAreaView>
    );
  }
  if (!game) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator color={colors.brand} />
      </SafeAreaView>
    );
  }

  const createdLabel = formatDate(game.created_at);
  const updatedLabel = formatDate(game.updated_at);
  const hasGroup = !!game.group_id && !!game.group_name;

  return (
    <View style={styles.root}>
      {/* ---------- 1. HEADER / COVER ---------- */}
      <View style={styles.cover}>
        {game.thumbnail_url ? (
          <Image source={{ uri: game.thumbnail_url }} style={StyleSheet.absoluteFillObject} contentFit="cover" transition={150} />
        ) : (
          <View style={[StyleSheet.absoluteFillObject, styles.coverPlaceholder]}>
            <MaterialCommunityIcons name="cube-outline" size={56} color={colors.border} />
          </View>
        )}
        <SafeAreaView edges={["top"]} style={styles.coverBar} pointerEvents="box-none">
          <Pressable onPress={() => router.back()} style={styles.roundBtn} testID="game-back-btn">
            <MaterialCommunityIcons name="chevron-left" size={24} color={colors.onSurface} />
          </Pressable>
          {game.age_category === "adult_18" ? (
            <View style={styles.ageBadge}><Text style={styles.ageBadgeText}>18+</Text></View>
          ) : null}
        </SafeAreaView>
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
        {/* ---------- 2. TITLU + DEVELOPER/GROUP ---------- */}
        <Text style={styles.title} testID="game-title">{game.title}</Text>

        <View style={styles.identityRow}>
          <Pressable
            testID="game-creator"
            onPress={() => router.push({ pathname: "/user/[id]", params: { id: game.owner_id } })}
            style={styles.identityItem}
          >
            <MaterialCommunityIcons name="account-circle-outline" size={16} color={colors.onSurface2} />
            <Text style={styles.identityText}>{game.owner_username}</Text>
          </Pressable>

          {hasGroup ? (
            <Pressable
              testID="game-group"
              onPress={() => router.push({ pathname: "/group/[id]", params: { id: game.group_id } })}
              style={styles.identityItem}
            >
              <View style={styles.identityDivider} />
              <MaterialCommunityIcons name="account-group-outline" size={16} color={colors.brand} />
              <Text style={[styles.identityText, { color: colors.brand }]}>{game.group_name}</Text>
            </Pressable>
          ) : null}
        </View>

        {/* ---------- 4. STATISTICI ---------- */}
        <View style={styles.statsGrid}>
          <StatCard icon="account-multiple" value={game.player_count} label="Playing" />
          <StatCard icon="play-circle-outline" value={game.total_plays} label="Visits" />
          <StatCard icon="heart-outline" value={game.likes} label="Likes" />
          <StatCard icon="server" value={game.max_players} label="Server Size" />
        </View>

        {/* ---------- 5. CREATED / UPDATED ---------- */}
        {createdLabel || updatedLabel ? (
          <View style={styles.dateRow}>
            <View style={styles.dateCard}>
              <Text style={styles.dateLabel}>CREATED</Text>
              <Text style={styles.dateValue}>{createdLabel || "—"}</Text>
            </View>
            <View style={styles.dateCard}>
              <Text style={styles.dateLabel}>UPDATED</Text>
              <Text style={styles.dateValue}>{updatedLabel || "—"}</Text>
            </View>
          </View>
        ) : (
          <View style={styles.draftCard}>
            <MaterialCommunityIcons name="eye-off-outline" size={16} color={colors.onSurface3} />
            <Text style={styles.draftText}>Not published publicly yet</Text>
          </View>
        )}

        {/* ---------- 8. GROUP CARD ---------- */}
        {hasGroup ? (
          <Pressable
            testID="game-group-card"
            onPress={() => router.push({ pathname: "/group/[id]", params: { id: game.group_id } })}
            style={styles.groupCard}
          >
            {game.group_logo_url ? (
              <Image source={{ uri: game.group_logo_url }} style={styles.groupLogo} contentFit="cover" />
            ) : (
              <View style={[styles.groupLogo, styles.groupLogoFallback]}>
                <MaterialCommunityIcons name="account-group" size={20} color={colors.brand} />
              </View>
            )}
            <View style={{ flex: 1 }}>
              <Text style={styles.groupCardLabel}>GROUP</Text>
              <Text style={styles.groupCardName}>{game.group_name}</Text>
            </View>
            <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
          </Pressable>
        ) : null}

        {/* ---------- 7. GENRE / SUBGENRE ---------- */}
        <Section title="Genre">
          <View style={styles.tagsRow}>
            <View style={styles.tag}><Text style={styles.tagText}>{titleCase(game.category)}</Text></View>
            {game.subgenre ? (
              <View style={[styles.tag, styles.tagSecondary]}><Text style={styles.tagText}>{game.subgenre}</Text></View>
            ) : null}
          </View>
        </Section>

        {/* ---------- 6. DESCRIERE ---------- */}
        {game.description ? (
          <Section title="About">
            <Text style={styles.description}>{game.description}</Text>
          </Section>
        ) : null}
      </ScrollView>

      {/* ---------- 3. BUTON JOACĂ, STICKY ---------- */}
      <SafeAreaView edges={["bottom"]} style={styles.playBar}>
        <Pressable
          testID="game-play-button"
          onPress={() => router.push({ pathname: "/play/[id]", params: { id: game.game_id } })}
          style={styles.playBtn}
        >
          <MaterialCommunityIcons name="play" size={22} color={colors.onBrand} />
          <Text style={styles.playBtnText}>{t("play")?.toUpperCase?.() || "PLAY"}</Text>
        </Pressable>
      </SafeAreaView>
    </View>
  );
}

function StatCard({ icon, value, label }: { icon: any; value: number; label: string }) {
  return (
    <View style={styles.statCard}>
      <MaterialCommunityIcons name={icon} size={18} color={colors.brand} />
      <Text style={styles.statValue}>{(value ?? 0).toLocaleString()}</Text>
      <Text style={styles.statLabel}>{label.toUpperCase()}</Text>
    </View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title.toUpperCase()}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },

  cover: { width: "100%", aspectRatio: 16 / 9, backgroundColor: colors.surface2 },
  coverPlaceholder: { alignItems: "center", justifyContent: "center", backgroundColor: colors.surface2 },
  coverBar: { position: "absolute", top: 0, left: 0, right: 0, flexDirection: "row", justifyContent: "space-between", padding: spacing.md },
  roundBtn: { width: 38, height: 38, borderRadius: 19, backgroundColor: "rgba(15,16,18,0.72)", alignItems: "center", justifyContent: "center" },
  ageBadge: { backgroundColor: colors.error, paddingHorizontal: 9, paddingVertical: 4, borderRadius: radius.sm, alignSelf: "flex-start" },
  ageBadgeText: { color: "#fff", fontWeight: "900", fontSize: 11 },

  scrollContent: { padding: spacing.lg, paddingBottom: 110 },

  title: { color: colors.onSurface, fontSize: 24, fontWeight: "900", letterSpacing: -0.4 },

  identityRow: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 2, marginTop: 8 },
  identityItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  identityDivider: { width: 1, height: 12, backgroundColor: colors.border, marginHorizontal: 8 },
  identityText: { color: colors.onSurface2, fontSize: 13, fontWeight: "700" },

  statsGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 20 },
  statCard: {
    flexBasis: "23%", flexGrow: 1, alignItems: "center", gap: 4,
    backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border,
    paddingVertical: 14,
  },
  statValue: { color: colors.onSurface, fontWeight: "800", fontSize: 15 },
  statLabel: { color: colors.onSurface3, fontSize: 9, fontWeight: "700", letterSpacing: 0.6 },

  dateRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  dateCard: { flex: 1, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: 12 },
  dateLabel: { color: colors.onSurface3, fontSize: 9, fontWeight: "800", letterSpacing: 0.8 },
  dateValue: { color: colors.onSurface, fontSize: 13, fontWeight: "700", marginTop: 4 },
  draftCard: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10, padding: 10 },
  draftText: { color: colors.onSurface3, fontSize: 12 },

  groupCard: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 14, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: 12 },
  groupLogo: { width: 40, height: 40, borderRadius: radius.sm, backgroundColor: colors.surface3 },
  groupLogoFallback: { alignItems: "center", justifyContent: "center" },
  groupCardLabel: { color: colors.onSurface3, fontSize: 9, fontWeight: "800", letterSpacing: 0.8 },
  groupCardName: { color: colors.onSurface, fontWeight: "700", fontSize: 14, marginTop: 2 },

  section: { marginTop: 22 },
  sectionTitle: { color: colors.onSurface3, fontSize: 11, fontWeight: "800", letterSpacing: 1, marginBottom: 10 },
  tagsRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  tag: { backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 6 },
  tagSecondary: { borderColor: colors.brand },
  tagText: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  description: { color: colors.onSurface2, fontSize: 14, lineHeight: 21 },

  playBar: { position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border, paddingHorizontal: spacing.lg, paddingTop: spacing.md },
  playBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, backgroundColor: colors.brand, borderRadius: radius.pill, paddingVertical: 16, marginBottom: spacing.sm },
  playBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 15, letterSpacing: 0.8 },
});