import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, FlatList, Pressable, TextInput, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { errorKey } from "@/src/dm/dmStore";

type Tab = "friends" | "requests" | "search";

export default function FriendsScreen() {
  const { t } = useI18n();
  const router = useRouter();
  const [dmError, setDmError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("friends");
  const [friends, setFriends] = useState<any[]>([]);
  const [incoming, setIncoming] = useState<any[]>([]);
  const [outgoing, setOutgoing] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([api("/friends"), api("/friends/requests")]);
      setFriends(a.friends || []);
      setIncoming(b.incoming || []);
      setOutgoing(b.outgoing || []);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (tab !== "search" || q.length < 2) { setResults([]); return; }
    const to = setTimeout(async () => {
      try {
        const res = await api(`/users/search?q=${encodeURIComponent(q)}`);
        setResults(res.users || []);
      } catch {}
    }, 300);
    return () => clearTimeout(to);
  }, [q, tab]);

  async function act(path: string, target_user_id: string) {
    await api(path, { method: "POST", body: JSON.stringify({ target_user_id }) });
    await load();
  }

  // Mesagerie privata: apasarea pe un prieten deschide (sau creeaza O SINGURA DATA) conversatia
  // persistenta cu el; serverul verifica prietenia/blocarea.
  async function openChat(item: any) {
    setDmError(null);
    try {
      const r = await api("/dm/open", { method: "POST", body: JSON.stringify({ friend_id: item.user_id }) });
      router.push({
        pathname: "/dm/[id]",
        params: { id: r.conversation_id, fid: item.user_id, name: item.display_name || item.username, username: item.username, avatar: item.avatar_url || "" },
      });
    } catch (e: any) {
      setDmError(t(errorKey(e?.code, e?.status)));
    }
  }

  const TabBtn = ({ id, label, testID }: { id: Tab; label: string; testID: string }) => (
    <Pressable testID={testID} onPress={() => setTab(id)} style={[styles.tab, tab === id && styles.tabActive]}>
      <Text style={[styles.tabText, tab === id && styles.tabTextActive]}>{label}</Text>
    </Pressable>
  );

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>{t("friends")}</Text>
      </View>
      <View style={styles.tabsRow}>
        <TabBtn id="friends" label={`${t("friends")} (${friends.length})`} testID="friends-tab-friends" />
        <TabBtn id="requests" label={`${t("incoming")} (${incoming.length})`} testID="friends-tab-requests" />
        <TabBtn id="search" label={t("search")} testID="friends-tab-search" />
      </View>

      {tab === "search" ? (
        <View style={{ paddingHorizontal: spacing.lg, marginBottom: spacing.md }}>
          <View style={styles.searchBox}>
            <MaterialCommunityIcons name="magnify" size={18} color={colors.onSurface3} />
            <TextInput
              testID="friends-search-input"
              value={q}
              onChangeText={setQ}
              placeholder={t("search")}
              placeholderTextColor={colors.onSurface3}
              style={styles.searchInput}
              autoCapitalize="none"
            />
          </View>
        </View>
      ) : null}

      {dmError ? <Text style={styles.dmError} testID="friends-dm-error">{dmError}</Text> : null}

      {loading ? (
        <ActivityIndicator style={{ marginTop: 20 }} color={colors.brand} />
      ) : (
        <FlatList
          data={tab === "friends" ? friends : tab === "requests" ? incoming : results}
          keyExtractor={(u) => u.user_id}
          contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingBottom: 40 }}
          ListEmptyComponent={
            <Text style={styles.empty}>
              {tab === "search" ? "Search users..." : tab === "requests" ? "No requests" : "No friends yet"}
            </Text>
          }
          renderItem={({ item }) => (
            <Pressable
              style={styles.row}
              testID={`friend-row-${item.user_id}`}
              disabled={tab !== "friends"}
              onPress={() => openChat(item)}
            >
              <View style={styles.avatar}>
                <Text style={styles.avatarText}>{(item.display_name || item.username || "?")[0].toUpperCase()}</Text>
                {item.online ? <View style={styles.onlineDot} /> : null}
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.name}>{item.display_name || item.username}</Text>
                <Text style={styles.meta}>@{item.username}</Text>
              </View>
              {tab === "friends" ? (
                <View style={{ flexDirection: "row", gap: 8 }}>
                  <Pressable testID={`friend-message-${item.user_id}`} onPress={() => openChat(item)} style={[styles.actBtn, { backgroundColor: colors.brand }]}>
                    <MaterialCommunityIcons name="message-text" size={18} color={colors.onBrand} />
                  </Pressable>
                  <Pressable testID={`friend-remove-${item.user_id}`} onPress={() => act("/friends/remove", item.user_id)} style={styles.actBtn}>
                    <MaterialCommunityIcons name="account-minus" size={18} color={colors.onSurface} />
                  </Pressable>
                </View>
              ) : tab === "requests" ? (
                <View style={{ flexDirection: "row", gap: 8 }}>
                  <Pressable testID={`friend-accept-${item.user_id}`} onPress={() => act("/friends/accept", item.user_id)} style={[styles.actBtn, { backgroundColor: colors.brand }]}>
                    <MaterialCommunityIcons name="check" size={18} color={colors.onBrand} />
                  </Pressable>
                  <Pressable testID={`friend-reject-${item.user_id}`} onPress={() => act("/friends/reject", item.user_id)} style={styles.actBtn}>
                    <MaterialCommunityIcons name="close" size={18} color={colors.onSurface} />
                  </Pressable>
                </View>
              ) : (
                <Pressable testID={`friend-add-${item.user_id}`} onPress={() => act("/friends/request", item.user_id)} style={[styles.actBtn, { backgroundColor: colors.brand }]}>
                  <MaterialCommunityIcons name="account-plus" size={18} color={colors.onBrand} />
                </Pressable>
              )}
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 26, fontWeight: "900", letterSpacing: -0.3 },
  tabsRow: { flexDirection: "row", gap: 8, paddingHorizontal: spacing.lg, marginBottom: spacing.md },
  tab: { paddingVertical: 8, paddingHorizontal: 14, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  tabActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  tabText: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  tabTextActive: { color: colors.brand },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface2, paddingHorizontal: 12, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  searchInput: { flex: 1, color: colors.onSurface, paddingVertical: 10, fontSize: 14 },
  empty: { color: colors.onSurface3, textAlign: "center", marginTop: 40, fontSize: 13 },
  dmError: { color: colors.error, fontSize: 12, textAlign: "center", marginBottom: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  avatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center", position: "relative" },
  avatarText: { color: colors.brand, fontWeight: "900", fontSize: 16 },
  onlineDot: { position: "absolute", right: 0, bottom: 0, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.success, borderWidth: 2, borderColor: colors.surface2 },
  name: { color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  meta: { color: colors.onSurface3, fontSize: 12, marginTop: 2 },
  actBtn: { width: 36, height: 36, alignItems: "center", justifyContent: "center", borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.borderStrong },
});