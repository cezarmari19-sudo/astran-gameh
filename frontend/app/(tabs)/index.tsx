import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, FlatList, Pressable, RefreshControl, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { AstransPill, Chip, GameCard, SectionTitle } from "@/src/components/ui";
import { colors, radius, spacing } from "@/src/theme";

const CATEGORIES = [
  { key: "all", label: "All" },
  { key: "adventure", label: "Adventure" },
  { key: "shooter", label: "Shooter" },
  { key: "simulation", label: "Simulation" },
  { key: "racing", label: "Racing" },
  { key: "roleplay", label: "Roleplay" },
  { key: "puzzle", label: "Puzzle" },
];

export default function DiscoverScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const { t } = useI18n();
  const [forYou, setForYou] = useState<any[]>([]);
  const [trending, setTrending] = useState<any[]>([]);
  const [newest, setNewest] = useState<any[]>([]);
  const [popular, setPopular] = useState<any[]>([]);
  const [recent, setRecent] = useState<any[]>([]);
  const [category, setCategory] = useState("all");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [a, b, c, d, r] = await Promise.all([
        api("/games/discover?section=for_you"),
        api("/games/discover?section=trending"),
        api("/games/discover?section=new"),
        api("/games/discover?section=popular"),
        api("/games/recently-played").catch(() => ({ games: [] })),
      ]);
      setForYou(a.games || []);
      setTrending(b.games || []);
      setNewest(c.games || []);
      setPopular(d.games || []);
      setRecent(r.games || []);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filter = (g: any[]) => category === "all" ? g : g.filter(x => x.category === category);

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.greeting}>Hey, {user?.display_name?.split(" ")[0] || user?.username}</Text>
          <Text style={styles.subGreeting}>{t("for_you")}</Text>
        </View>
        <AstransPill balance={user?.astrans_balance || 0} onPress={() => router.push("/wallet")} />
      </View>

      <ScrollView
        contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.brand} />}
        stickyHeaderIndices={[0]}
      >
        <View style={styles.chipsWrap}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ gap: 8, paddingHorizontal: spacing.lg }}
          >
            {CATEGORIES.map(c => (
              <Chip key={c.key} label={c.label} active={category === c.key} onPress={() => setCategory(c.key)} testID={`chip-${c.key}`} />
            ))}
          </ScrollView>
        </View>

        {loading ? (
          <View style={{ padding: 40, alignItems: "center" }}>
            <ActivityIndicator color={colors.brand} />
          </View>
        ) : (
          <>
            {filter(forYou)[0] ? (
              <>
                <SectionTitle title={t("for_you")} />
                <View style={{ paddingHorizontal: spacing.lg }}>
                  <GameCard testID={`game-featured`} game={filter(forYou)[0]} wide onPress={() => router.push({ pathname: "/game/[id]", params: { id: filter(forYou)[0].game_id } })} />
                </View>
              </>
            ) : null}

            <SectionTitle title={t("trending")} />
            <FlatList
              data={filter(trending)}
              keyExtractor={(item) => item.game_id}
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ paddingHorizontal: spacing.lg }}
              renderItem={({ item }) => (
                <GameCard testID={`game-trending-${item.game_id}`} game={item} onPress={() => router.push({ pathname: "/game/[id]", params: { id: item.game_id } })} />
              )}
            />

            <SectionTitle title={t("new_games")} />
            <FlatList
              data={filter(newest)}
              keyExtractor={(item) => item.game_id}
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ paddingHorizontal: spacing.lg }}
              renderItem={({ item }) => (
                <GameCard testID={`game-new-${item.game_id}`} game={item} onPress={() => router.push({ pathname: "/game/[id]", params: { id: item.game_id } })} />
              )}
            />

            <SectionTitle title={t("popular")} />
            <FlatList
              data={filter(popular)}
              keyExtractor={(item) => item.game_id}
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ paddingHorizontal: spacing.lg }}
              renderItem={({ item }) => (
                <GameCard testID={`game-popular-${item.game_id}`} game={item} onPress={() => router.push({ pathname: "/game/[id]", params: { id: item.game_id } })} />
              )}
            />

            {recent.length ? (
              <>
                <SectionTitle title={t("recently_played")} />
                <FlatList
                  data={recent}
                  keyExtractor={(item) => item.game_id}
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={{ paddingHorizontal: spacing.lg }}
                  renderItem={({ item }) => (
                    <GameCard game={item} onPress={() => router.push({ pathname: "/game/[id]", params: { id: item.game_id } })} />
                  )}
                />
              </>
            ) : null}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.md,
    backgroundColor: colors.surface,
  },
  greeting: { color: colors.onSurface, fontSize: 20, fontWeight: "800", letterSpacing: -0.3 },
  subGreeting: { color: colors.brand, fontSize: 11, fontWeight: "800", letterSpacing: 2, marginTop: 2 },
  chipsWrap: {
    backgroundColor: colors.surface,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    height: 56,
    justifyContent: "center",
  },
});