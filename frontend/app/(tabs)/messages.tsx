// Sectiunea "Mesaje": lista conversatiilor private (date reale din /api/dm/conversations),
// ordonata dupa ultimul mesaj, cu necitite. Se actualizeaza live prin WebSocket-ul /ws/dm.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, FlatList, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { useDm } from "@/src/dm/DmContext";
import { DmAvatar } from "@/src/dm/DmAvatar";
import { applyIncoming, DmConversation, errorKey, formatTime } from "@/src/dm/dmStore";

export default function MessagesScreen() {
  const { t, lang } = useI18n();
  const router = useRouter();
  const { subscribe, connected, refreshUnread } = useDm();
  const [items, setItems] = useState<DmConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const itemsRef = useRef<DmConversation[]>([]);
  itemsRef.current = items;

  const load = useCallback(async () => {
    try {
      const r = await api<{ conversations: DmConversation[] }>("/dm/conversations");
      setItems(r.conversations || []);
      setError(null);
    } catch (e: any) {
      setError(t(errorKey(e?.code, e?.status)));
    }
    setLoading(false);
    setRefreshing(false);
  }, [t]);

  // la fiecare intrare pe tab reincarcam (necititele se schimba cand citesti o conversatie)
  useFocusEffect(useCallback(() => { load(); refreshUnread(); }, [load, refreshUnread]));

  useEffect(
    () =>
      subscribe((e) => {
        if (e.t === "dm") {
          const r = applyIncoming(itemsRef.current, e.message, null);
          if (r.needsRefresh) load();
          else setItems(r.list);
        } else {
          load(); // dm_sync / reconnected
        }
      }),
    [subscribe, load]
  );

  const open = (c: DmConversation) =>
    router.push({
      pathname: "/dm/[id]",
      params: { id: c.conversation_id, fid: c.friend.user_id, name: c.friend.display_name || c.friend.username, username: c.friend.username, avatar: c.friend.avatar_url || "" },
    });

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.page}>
        <View style={styles.header}>
          <Text style={styles.title}>{t("messages")}</Text>
          {!connected ? <Text style={styles.conn}>{t("dm_connecting")}</Text> : null}
        </View>
        {error ? <Text style={styles.err}>{error}</Text> : null}
        {loading ? (
          <ActivityIndicator style={{ marginTop: 20 }} color={colors.brand} />
        ) : items.length === 0 ? (
          <View style={styles.emptyWrap} testID="dm-empty">
            <Text style={styles.empty}>{t("dm_empty")}</Text>
            <Pressable onPress={() => router.push("/(tabs)/friends")} style={styles.emptyBtn} testID="dm-go-friends">
              <Text style={styles.emptyBtnText}>{t("dm_go_friends")}</Text>
            </Pressable>
          </View>
        ) : (
          <FlatList
            data={items}
            keyExtractor={(c) => c.conversation_id}
            contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingBottom: 40 }}
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            renderItem={({ item }) => {
              const last = item.last_message;
              const preview = last ? (last.mine ? `${t("dm_you")}: ${last.text}` : last.text) : "";
              return (
                <Pressable onPress={() => open(item)} style={styles.row} testID={`dm-row-${item.conversation_id}`}>
                  <DmAvatar friend={item.friend} />
                  <View style={{ flex: 1 }}>
                    <View style={styles.rowTop}>
                      <Text style={styles.name} numberOfLines={1}>{item.friend.display_name || item.friend.username}</Text>
                      {last ? <Text style={[styles.time, item.unread > 0 && { color: colors.brand }]}>{formatTime(last.ts, Date.now(), { yesterday: t("dm_yesterday") }, lang)}</Text> : null}
                    </View>
                    <View style={styles.rowBottom}>
                      <Text style={[styles.preview, item.unread > 0 && styles.previewUnread]} numberOfLines={1}>{preview}</Text>
                      {item.unread > 0 ? (
                        <View style={styles.badge} testID={`dm-unread-${item.conversation_id}`}>
                          <Text style={styles.badgeText}>{item.unread > 99 ? "99+" : item.unread}</Text>
                        </View>
                      ) : null}
                    </View>
                  </View>
                </Pressable>
              );
            }}
          />
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  page: { flex: 1, width: "100%", maxWidth: 760, alignSelf: "center" }, // desktop/tableta: coloana centrata
  header: { padding: spacing.lg, flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  title: { color: colors.onSurface, fontSize: 26, fontWeight: "900", letterSpacing: -0.3 },
  conn: { color: colors.warning, fontSize: 12, fontWeight: "700" },
  err: { color: colors.error, fontSize: 13, paddingHorizontal: spacing.lg, marginBottom: spacing.sm },
  emptyWrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.lg },
  empty: { color: colors.onSurface3, textAlign: "center", fontSize: 13, lineHeight: 19 },
  emptyBtn: { paddingVertical: 10, paddingHorizontal: 18, borderRadius: radius.pill, backgroundColor: colors.brand },
  emptyBtnText: { color: colors.onBrand, fontWeight: "800", fontSize: 13 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  rowTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  rowBottom: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 3 },
  name: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  time: { color: colors.onSurface3, fontSize: 11, fontWeight: "600" },
  preview: { flex: 1, color: colors.onSurface3, fontSize: 13 },
  previewUnread: { color: colors.onSurface, fontWeight: "700" },
  badge: { minWidth: 20, height: 20, borderRadius: 10, paddingHorizontal: 6, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  badgeText: { color: colors.onBrand, fontSize: 11, fontWeight: "900" },
});