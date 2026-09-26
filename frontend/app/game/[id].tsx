import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Dimensions } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { useI18n } from "@/src/i18n";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { colors, radius, spacing } from "@/src/theme";

const { width: SW } = Dimensions.get("window");

export default function GameDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { t } = useI18n();
  const [game, setGame] = useState<any>(null);
  const [isOwner, setIsOwner] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [gameRes, meRes] = await Promise.all([api(`/games/${id}`), api("/auth/me")]);
        setGame(gameRes.game);
        setIsOwner(!!meRes?.user?.user_id && meRes.user.user_id === gameRes.game?.owner_id);
      } catch (e: any) {
        setErr(e.message || "Failed");
      }
    })();
  }, [id]);

  if (err) return <SafeAreaView style={styles.center}><Text style={{ color: colors.error }} testID="game-error">{err}</Text></SafeAreaView>;
  if (!game) return <SafeAreaView style={styles.center}><ActivityIndicator color={colors.brand} /></SafeAreaView>;

  return (
    <View style={styles.root}>
      <View style={styles.hero}>
        {game.thumbnail_url ? (
          <Image source={{ uri: game.thumbnail_url }} style={StyleSheet.absoluteFillObject} contentFit="cover" transition={200} />
        ) : <View style={[StyleSheet.absoluteFillObject, { backgroundColor: colors.surface3 }]} />}
        <LinearGradient colors={["rgba(0,0,0,0.4)", "transparent", colors.surface]} style={StyleSheet.absoluteFillObject} />
        <SafeAreaView edges={["top"]} style={styles.heroBar}>
          <Pressable onPress={() => router.back()} style={styles.iconBtn} testID="game-back-btn">
            <MaterialCommunityIcons name="chevron-left" size={24} color={colors.onSurface} />
          </Pressable>
          {game.age_category === "adult_18" ? (
            <View style={styles.ageBadge}><Text style={styles.ageBadgeText}>18+</Text></View>
          ) : null}
        </SafeAreaView>
      </View>

      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 120 }}>
        <Text style={styles.title} testID="game-title">{game.title}</Text>
        <View style={styles.metaRow}>
          <MaterialCommunityIcons name="account" size={14} color={colors.brand} />
          <Text style={styles.creator}>{game.owner_username}</Text>
        </View>
        <View style={styles.stats}>
          <Stat icon="account-multiple" value={game.player_count} label="Playing" />
          <Stat icon="play-circle" value={game.total_plays} label="Plays" />
          <Stat icon="heart" value={game.likes} label="Likes" />
        </View>
        <Text style={styles.desc}>{game.description}</Text>
      </ScrollView>

      <View style={styles.footer}>
        {isOwner ? (
          <View style={styles.footerRow}>
            <View style={{ flex: 1 }}>
              <SecondaryButton
                testID="game-edit-button"
                label="Edit"
                icon="pencil-outline"
                onPress={() => router.push({ pathname: "/studio/edit/[id]", params: { id: game.game_id } } as any)}
              />
            </View>
            <View style={{ flex: 1 }}>
              <PrimaryButton
                testID="game-play-button"
                label={t("play")}
                icon="play"
                onPress={() => router.push({ pathname: "/play/[id]", params: { id: game.game_id } })}
              />
            </View>
          </View>
        ) : (
          <PrimaryButton
            testID="game-play-button"
            label={t("play")}
            icon="play"
            onPress={() => router.push({ pathname: "/play/[id]", params: { id: game.game_id } })}
          />
        )}
      </View>
    </View>
  );
}

function Stat({ icon, value, label }: any) {
  return (
    <View style={{ alignItems: "center" }}>
      <MaterialCommunityIcons name={icon} size={18} color={colors.brand} />
      <Text style={{ color: colors.onSurface, fontWeight: "800", fontSize: 16, marginTop: 4 }}>{(value ?? 0).toLocaleString()}</Text>
      <Text style={{ color: colors.onSurface3, fontSize: 10, fontWeight: "700", letterSpacing: 1 }}>{label.toUpperCase()}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  hero: { width: SW, height: SW * 0.6, backgroundColor: colors.surface2 },
  heroBar: { position: "absolute", top: 0, left: 0, right: 0, flexDirection: "row", justifyContent: "space-between", padding: spacing.md },
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: "rgba(0,0,0,0.5)", alignItems: "center", justifyContent: "center" },
  ageBadge: { backgroundColor: colors.error, paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.sm },
  ageBadgeText: { color: "#fff", fontWeight: "900", fontSize: 12 },
  title: { color: colors.onSurface, fontSize: 28, fontWeight: "900", letterSpacing: -0.5 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 6 },
  creator: { color: colors.brand, fontSize: 13, fontWeight: "700" },
  stats: { flexDirection: "row", justifyContent: "space-around", marginTop: 20, marginBottom: 20, padding: spacing.lg, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border },
  desc: { color: colors.onSurface2, fontSize: 14, lineHeight: 21 },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, padding: spacing.lg, backgroundColor: colors.surface, borderTopWidth: 1, borderTopColor: colors.border },
  footerRow: { flexDirection: "row", gap: 10 },
});