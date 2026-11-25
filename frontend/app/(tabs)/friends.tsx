import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, FlatList, Pressable, TextInput, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { errorKey, friendRequestErrorKey } from "@/src/dm/dmStore";
import { BlockedUser, isAlreadyUnblocked, normalizeBlocked, removeBlocked, unblockErrorKey } from "@/src/friends/blockedStore";

type Tab = "friends" | "requests" | "search" | "blocked";

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
  // „Utilizatori blocati”: lista vine de la server (GET /friends/blocked); deblocarea cere confirmare inline
  // (Alert.alert nu are butoane pe web), iar lista se actualizeaza local, fara reincarcarea aplicatiei.
  const [blocked, setBlocked] = useState<BlockedUser[]>([]);
  const [blockedLoading, setBlockedLoading] = useState(false);
  const [blockedError, setBlockedError] = useState<string | null>(null);
  const [blockedNotice, setBlockedNotice] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

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

  const loadBlocked = useCallback(async () => {
    setBlockedLoading(true);
    setBlockedError(null);
    try {
      setBlocked(normalizeBlocked(await api("/friends/blocked")));
    } catch {
      setBlockedError(t("blocked_load_failed"));
    }
    setBlockedLoading(false);
  }, [t]);

  useEffect(() => {
    if (tab === "blocked") loadBlocked();
    else { setConfirmId(null); setBlockedNotice(null); }
  }, [tab, loadBlocked]);

  async function unblock(userId: string) {
    if (busyId) return; // dublu-tap: o singura cerere pe rand
    setBusyId(userId);
    setBlockedNotice(null);
    setBlockedError(null);
    try {
      await api("/friends/unblock", { method: "POST", body: JSON.stringify({ target_user_id: userId }) });
      setBlocked((l) => removeBlocked(l, userId));
      setBlockedNotice(t("unblock_success"));
      setConfirmId(null);
    } catch (e: any) {
      if (isAlreadyUnblocked(e?.code, e?.status)) {
        // deja deblocat (ex. de pe alt dispozitiv): rezultatul dorit; aliniem lista cu serverul
        setBlocked((l) => removeBlocked(l, userId));
        setBlockedNotice(t("unblock_success"));
        setConfirmId(null);
      } else {
        setBlockedError(t(unblockErrorKey(e?.code, e?.status)));
      }
    }
    setBusyId(null);
  }

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
    setDmError(null);
    try {
      await api(path, { method: "POST", body: JSON.stringify({ target_user_id }) });
    } catch (e: any) {
      // Serverul impune restrictiile (ex. contact_restricted = restrictie de siguranta); UI-ul doar le afiseaza clar.
      setDmError(t(friendRequestErrorKey(e?.code, e?.status)));
      // o cerere veche/ilegala a fost scoasa de server: reimprospatam listele
      await load();
      return;
    }
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
        <TabBtn id="blocked" label={`${t("blocked_users")}${blocked.length ? ` (${blocked.length})` : ""}`} testID="friends-tab-blocked" />
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

      {tab === "blocked" && blockedNotice ? <Text style={styles.notice} testID="blocked-notice">{blockedNotice}</Text> : null}
      {tab === "blocked" && blockedError ? (
        <View style={{ alignItems: "center", marginBottom: spacing.sm }}>
          <Text style={styles.dmError} testID="blocked-error">{blockedError}</Text>
          <Pressable testID="blocked-retry" onPress={loadBlocked} style={styles.textBtn}>
            <Text style={styles.textBtnLabel}>{t("try_again")}</Text>
          </Pressable>
        </View>
      ) : null}

      {(tab === "blocked" ? blockedLoading : loading) ? (
        <ActivityIndicator style={{ marginTop: 20 }} color={colors.brand} testID={tab === "blocked" ? "blocked-loading" : undefined} />
      ) : (
        <FlatList
          data={tab === "friends" ? friends : tab === "requests" ? incoming : tab === "blocked" ? blocked : results}
          keyExtractor={(u) => u.user_id}
          contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingBottom: 40 }}
          ListHeaderComponent={tab === "blocked" && blocked.length ? <Text style={styles.hint}>{t("blocked_users_hint")}</Text> : null}
          ListEmptyComponent={
            tab === "blocked" ? (
              blockedError ? null : <Text style={styles.empty} testID="blocked-empty">{t("blocked_users_empty")}</Text>
            ) : (
              <Text style={styles.empty}>
                {tab === "search" ? "Search users..." : tab === "requests" ? "No requests" : "No friends yet"}
              </Text>
            )
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
                {tab === "blocked" && confirmId === item.user_id ? (
                  <Text style={styles.confirmBody} testID={`blocked-confirm-body-${item.user_id}`}>{t("unblock_confirm_body")}</Text>
                ) : null}
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
              ) : tab === "blocked" ? (
                confirmId === item.user_id ? (
                  <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
                    <Pressable testID={`unblock-confirm-${item.user_id}`} disabled={!!busyId} onPress={() => unblock(item.user_id)} style={[styles.pillBtn, { backgroundColor: colors.brand }, !!busyId && { opacity: 0.6 }]}>
                      {busyId === item.user_id
                        ? <ActivityIndicator size="small" color={colors.onBrand} />
                        : <Text style={[styles.pillLabel, { color: colors.onBrand }]}>{t("unblock_confirm")}</Text>}
                    </Pressable>
                    <Pressable testID={`unblock-cancel-${item.user_id}`} disabled={!!busyId} onPress={() => setConfirmId(null)} style={styles.pillBtn}>
                      <Text style={styles.pillLabel}>{t("cancel")}</Text>
                    </Pressable>
                  </View>
                ) : (
                  <Pressable testID={`unblock-${item.user_id}`} disabled={!!busyId} onPress={() => { setBlockedNotice(null); setBlockedError(null); setConfirmId(item.user_id); }} style={styles.pillBtn}>
                    <Text style={styles.pillLabel}>{t("unblock")}</Text>
                  </Pressable>
                )
              ) : item.contact_restricted ? (
                // Restrictie de siguranta (decisa de server): fara buton de cerere; mesaj generic, fara a spune cine e minor.
                <View testID={`friend-restricted-${item.user_id}`} style={{ flex: 0, maxWidth: 150 }} accessibilityRole="text">
                  <Text style={styles.restricted}>{t("friend_request_restricted")}</Text>
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
  tabsRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, paddingHorizontal: spacing.lg, marginBottom: spacing.md },
  tab: { paddingVertical: 8, paddingHorizontal: 14, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  tabActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  tabText: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  tabTextActive: { color: colors.brand },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface2, paddingHorizontal: 12, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  searchInput: { flex: 1, color: colors.onSurface, paddingVertical: 10, fontSize: 14 },
  empty: { color: colors.onSurface3, textAlign: "center", marginTop: 40, fontSize: 13 },
  restricted: { color: colors.onSurface3, fontSize: 10, lineHeight: 13 },
  notice: { color: colors.success, fontSize: 12, textAlign: "center", marginBottom: spacing.sm },
  hint: { color: colors.onSurface3, fontSize: 12, marginBottom: spacing.sm },
  confirmBody: { color: colors.onSurface2, fontSize: 11, lineHeight: 15, marginTop: 4 },
  pillBtn: { minHeight: 36, paddingHorizontal: 12, alignItems: "center", justifyContent: "center", borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.borderStrong },
  pillLabel: { color: colors.onSurface, fontSize: 12, fontWeight: "700" },
  textBtn: { paddingVertical: 6, paddingHorizontal: 12 },
  textBtnLabel: { color: colors.brand, fontSize: 12, fontWeight: "700" },
  dmError: { color: colors.error, fontSize: 12, textAlign: "center", marginBottom: spacing.sm },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  avatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center", position: "relative" },
  avatarText: { color: colors.brand, fontWeight: "900", fontSize: 16 },
  onlineDot: { position: "absolute", right: 0, bottom: 0, width: 10, height: 10, borderRadius: 5, backgroundColor: colors.success, borderWidth: 2, borderColor: colors.surface2 },
  name: { color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  meta: { color: colors.onSurface3, fontSize: 12, marginTop: 2 },
  actBtn: { width: 36, height: 36, alignItems: "center", justifyContent: "center", borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.borderStrong },
});