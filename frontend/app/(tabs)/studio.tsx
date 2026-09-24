import React, { useCallback, useState } from "react";
import { View, Text, StyleSheet, FlatList, RefreshControl, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Image } from "expo-image";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function StudioScreen() {
  const router = useRouter();
  const { t } = useI18n();
  const [games, setGames] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api("/games/mine");
      setGames(res.games || []);
    } catch {}
    setLoading(false);
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>{t("studio")}</Text>
          <Text style={styles.sub}>{t("my_games")}</Text>
        </View>
        <View style={styles.headerBtns}>
          <Pressable testID="studio-shop-btn" onPress={() => router.push("/shop" as any)} style={styles.shopBtn}>
            <MaterialCommunityIcons name="storefront-outline" size={20} color={colors.brand} />
          </Pressable>
          <Pressable testID="studio-create-btn" onPress={() => router.push("/studio/create")} style={styles.newBtn}>
            <MaterialCommunityIcons name="plus" size={20} color={colors.onBrand} />
            <Text style={styles.newBtnText}>{t("create_game")}</Text>
          </Pressable>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : games.length === 0 ? (
        <View style={styles.empty}>
          <MaterialCommunityIcons name="cube-outline" size={64} color={colors.onSurface3} />
          <Text style={styles.emptyText}>Astran Studio</Text>
          <Text style={styles.emptySub}>{t("create_game")}</Text>
          <View style={{ marginTop: 16 }}>
            <PrimaryButton testID="studio-empty-create" label={t("create_game")} onPress={() => router.push("/studio/create")} icon="plus" />
          </View>
        </View>
      ) : (
        <FlatList
          data={games}
          keyExtractor={(g) => g.game_id}
          contentContainerStyle={{ padding: spacing.lg }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.brand} />}
          renderItem={({ item }) => (
            <Pressable
              testID={`studio-game-${item.game_id}`}
              onPress={() => router.push({ pathname: "/studio/edit/[id]", params: { id: item.game_id } })}
              style={styles.row}
            >
              {item.thumbnail_url ? (
                <Image source={{ uri: item.thumbnail_url }} style={styles.rowImg} contentFit="cover" />
              ) : (
                <View style={[styles.rowImg, { backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" }]}>
                  <MaterialCommunityIcons name="cube-outline" size={22} color={colors.onSurface3} />
                </View>
              )}
              <View style={{ flex: 1 }}>
                <Text style={styles.rowTitle}>{item.title}</Text>
                <Text style={styles.rowMeta}>
                  {item.total_plays} plays · {item.age_category === "adult_18" ? "18+" : "Under 18"}
                </Text>
              </View>
              <MaterialCommunityIcons name="chevron-right" size={22} color={colors.onSurface3} />
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 26, fontWeight: "900", letterSpacing: -0.3 },
  sub: { color: colors.brand, fontSize: 11, fontWeight: "800", letterSpacing: 2, marginTop: 2 },
  headerBtns: { flexDirection: "row", alignItems: "center", gap: 8 },
  shopBtn: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  newBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: colors.brand, paddingHorizontal: 14, paddingVertical: 10, borderRadius: radius.pill },
  newBtnText: { color: colors.onBrand, fontWeight: "800", fontSize: 13 },
  empty: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg, gap: 4 },
  emptyText: { color: colors.onSurface, fontSize: 18, fontWeight: "800", marginTop: 12 },
  emptySub: { color: colors.onSurface3, fontSize: 13 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 10, borderWidth: 1, borderColor: colors.border },
  rowImg: { width: 60, height: 60, borderRadius: radius.sm, overflow: "hidden" },
  rowTitle: { color: colors.onSurface, fontSize: 15, fontWeight: "700" },
  rowMeta: { color: colors.onSurface3, fontSize: 12, marginTop: 3 },
});