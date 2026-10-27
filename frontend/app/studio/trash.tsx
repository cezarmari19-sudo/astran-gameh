import React, { useCallback, useState } from "react";
import { View, Text, StyleSheet, FlatList, RefreshControl, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Image } from "expo-image";
import { colors, radius, spacing } from "@/src/theme";
import { projectSecurityApi } from "@/src/api/client";
import RecoverProjectFlow from "@/src/components/RecoverProjectFlow";

type TrashedGame = {
  game_id: string;
  title: string;
  deleted_at: string | null;
  thumbnail_url: string | null;
  recovery_state: string;
};

function formatDate(iso: string | null): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export default function TrashScreen() {
  const router = useRouter();
  const [games, setGames] = useState<TrashedGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [recoverTarget, setRecoverTarget] = useState<TrashedGame | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await projectSecurityApi.listTrash();
      setGames(res.games || []);
    } catch {}
    setLoading(false);
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable testID="trash-back" onPress={() => (router.canGoBack() ? router.back() : router.replace("/(tabs)/studio"))} hitSlop={10}>
          <MaterialCommunityIcons name="chevron-left" size={28} color={colors.onSurface} />
        </Pressable>
        <View>
          <Text style={styles.title}>Coș de gunoi</Text>
          <Text style={styles.sub}>PROIECTE ȘTERSE</Text>
        </View>
        <View style={{ width: 28 }} />
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.brand} />
      ) : games.length === 0 ? (
        <View style={styles.empty}>
          <MaterialCommunityIcons name="trash-can-outline" size={64} color={colors.onSurface3} />
          <Text style={styles.emptyText}>Coșul de gunoi este gol</Text>
          <Text style={styles.emptySub}>Proiectele șterse apar aici și pot fi recuperate doar de Owner.</Text>
        </View>
      ) : (
        <FlatList
          data={games}
          keyExtractor={(g) => g.game_id}
          contentContainerStyle={{ padding: spacing.lg }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} tintColor={colors.brand} />}
          renderItem={({ item }) => (
            <View testID={`trash-row-${item.game_id}`} style={styles.row}>
              {item.thumbnail_url ? (
                <Image source={{ uri: item.thumbnail_url }} style={styles.rowImg} contentFit="cover" />
              ) : (
                <View style={[styles.rowImg, { backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" }]}>
                  <MaterialCommunityIcons name="cube-outline" size={22} color={colors.onSurface3} />
                </View>
              )}
              <View style={{ flex: 1 }}>
                <Text style={styles.rowTitle} numberOfLines={1}>{item.title}</Text>
                <Text style={styles.rowMeta}>Șters la {formatDate(item.deleted_at)}</Text>
              </View>
              <Pressable testID={`trash-recover-${item.game_id}`} onPress={() => setRecoverTarget(item)} style={styles.recoverBtn}>
                <MaterialCommunityIcons name="backup-restore" size={16} color={colors.onBrand} />
                <Text style={styles.recoverBtnText}>Recuperează</Text>
              </Pressable>
            </View>
          )}
        />
      )}

      {recoverTarget ? (
        <RecoverProjectFlow
          visible={!!recoverTarget}
          gameId={recoverTarget.game_id}
          projectTitle={recoverTarget.title}
          onClose={() => setRecoverTarget(null)}
          onRecovered={() => { setRecoverTarget(null); load(); }}
        />
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg, gap: spacing.sm },
  title: { color: colors.onSurface, fontSize: 22, fontWeight: "900", letterSpacing: -0.3, textAlign: "center" },
  sub: { color: colors.brand, fontSize: 10, fontWeight: "800", letterSpacing: 2, textAlign: "center", marginTop: 2 },
  empty: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.lg, gap: 4 },
  emptyText: { color: colors.onSurface, fontSize: 18, fontWeight: "800", marginTop: 12 },
  emptySub: { color: colors.onSurface3, fontSize: 13, textAlign: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 10, borderWidth: 1, borderColor: colors.border },
  rowImg: { width: 52, height: 52, borderRadius: radius.sm, overflow: "hidden" },
  rowTitle: { color: colors.onSurface, fontSize: 15, fontWeight: "700" },
  rowMeta: { color: colors.onSurface3, fontSize: 12, marginTop: 3 },
  recoverBtn: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.brand, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 8 },
  recoverBtnText: { color: colors.onBrand, fontWeight: "800", fontSize: 11 },
});