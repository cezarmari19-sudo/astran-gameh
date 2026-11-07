// Conversatie privata 1-la-1 intre prieteni. Date reale: istoric paginat din /api/dm/..., trimitere
// prin POST (serverul valideaza, filtreaza dupa varsta si pune expeditorul din sesiune), mesaje
// primite live prin WebSocket-ul /ws/dm. Nu are legatura cu chatul din joc (src/play/GameChat.tsx).
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, TextInput, Pressable, KeyboardAvoidingView, Platform, Keyboard,
  ActivityIndicator, Modal, AppState, NativeScrollEvent, NativeSyntheticEvent,
} from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import { colors, radius, spacing } from "@/src/theme";
import { useDm } from "@/src/dm/DmContext";
import { DmAvatar } from "@/src/dm/DmAvatar";
import {
  addPending, DmConversation, DmMsg, DmPending, errorKey, failPending, hourMinute, isPermanentError,
  MAX_DM_LEN, mergeMessages, newClientId, resolvePending,
} from "@/src/dm/dmStore";

type Page = { messages: DmMsg[]; has_more: boolean; next_before: string | null };
type Item = { kind: "msg"; m: DmMsg } | { kind: "pending"; p: DmPending };

const PAGE = 30;
const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) || "";

export default function DmConversationScreen() {
  const params = useLocalSearchParams<{ id: string; name?: string; username?: string; avatar?: string; fid?: string }>();
  const id = first(params.id);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { t, lang } = useI18n();
  const { user } = useAuth();
  const { subscribe, setActiveConversation, refreshUnread, connected } = useDm();

  const [friend, setFriend] = useState({
    user_id: first(params.fid), name: first(params.name), username: first(params.username), avatar: first(params.avatar),
  });
  const [messages, setMessages] = useState<DmMsg[]>([]);
  const [pending, setPending] = useState<DmPending[]>([]);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [newBelow, setNewBelow] = useState(0);
  const [canSend, setCanSend] = useState(true);
  const [kbVisible, setKbVisible] = useState(false);
  const [sheet, setSheet] = useState<null | { kind: "msg"; msg: DmMsg } | { kind: "block" }>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const listRef = useRef<FlatList<Item>>(null);
  const messagesRef = useRef<DmMsg[]>([]);
  messagesRef.current = messages;
  const atBottom = useRef(true);
  const focused = useRef(true);
  const initialised = useRef(false);
  const loadingMoreRef = useRef(false);

  // ---- tastatura: pe ecranele fara bara de jos, padding-ul de siguranta nu trebuie dublat cand tastatura e sus
  useEffect(() => {
    const show = Keyboard.addListener(Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow", () => setKbVisible(true));
    const hide = Keyboard.addListener(Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide", () => setKbVisible(false));
    return () => { show.remove(); hide.remove(); };
  }, []);

  const markRead = useCallback(async () => {
    try {
      await api(`/dm/conversations/${encodeURIComponent(id)}/read`, { method: "POST" });
      refreshUnread();
    } catch { /* ramane necitit; se reincearca la urmatoarea deschidere/mesaj */ }
  }, [id, refreshUnread]);

  const loadLatest = useCallback(async () => {
    try {
      const r = await api<Page>(`/dm/conversations/${encodeURIComponent(id)}/messages?limit=${PAGE}`);
      const prev = messagesRef.current;
      const known = new Set(prev.map((m) => m.id));
      const gap = prev.length > 0 && r.has_more && r.messages.length > 0 && !r.messages.some((m) => known.has(m.id));
      if (gap || !initialised.current) {
        // prima incarcare, sau am lipsit atat de mult incat intre ce aveam si ce e acum ar fi o "gaura"
        setMessages(r.messages);
        setHasMore(r.has_more);
        setNextBefore(r.next_before);
        initialised.current = true;
      } else {
        setMessages((p) => mergeMessages(p, r.messages));
      }
      setLoadError(null);
      if (focused.current && AppState.currentState === "active") markRead();
    } catch (e: any) {
      if (e?.status === 403 || e?.status === 404) setDenied(true);
      else setLoadError(t("dm_load_failed"));
    }
    setLoading(false);
  }, [id, markRead, t]);

  const loadMore = useCallback(async () => {
    if (!hasMore || !nextBefore || loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const r = await api<Page>(`/dm/conversations/${encodeURIComponent(id)}/messages?limit=${PAGE}&before=${encodeURIComponent(nextBefore)}`);
      setMessages((p) => mergeMessages(p, r.messages));
      setHasMore(r.has_more);
      setNextBefore(r.next_before);
    } catch { /* se reincearca la urmatorul scroll */ }
    loadingMoreRef.current = false;
    setLoadingMore(false);
  }, [hasMore, nextBefore, id]);

  // antetul cand ecranul e deschis fara parametri (ex. link direct): il luam din lista reala
  useEffect(() => {
    if (friend.name) return;
    (async () => {
      try {
        const r = await api<{ conversations: DmConversation[] }>("/dm/conversations");
        const c = r.conversations.find((x) => x.conversation_id === id);
        if (c) {
          setFriend({ user_id: c.friend.user_id, name: c.friend.display_name || c.friend.username, username: c.friend.username, avatar: c.friend.avatar_url || "" });
          setCanSend(c.can_send);
        }
      } catch { /* antetul ramane generic */ }
    })();
  }, [friend.name, id]);

  // focus: conversatia e "activa" (nu numaram/anuntam mesajele pe care le vedem deja)
  useFocusEffect(
    useCallback(() => {
      focused.current = true;
      setActiveConversation(id);
      loadLatest();
      return () => { focused.current = false; setActiveConversation(null); refreshUnread(); };
    }, [id, loadLatest, setActiveConversation, refreshUnread])
  );

  useEffect(
    () =>
      subscribe((e) => {
        if (e.t === "dm") {
          if (e.conversation_id !== id) return;
          setMessages((p) => mergeMessages(p, [e.message]));
          if (!e.message.mine) {
            if (focused.current && AppState.currentState === "active") markRead();
            if (!atBottom.current) setNewBelow((n) => n + 1);
          }
        } else if (focused.current) {
          loadLatest(); // dm_sync / reconnected: recuperam ce s-a pierdut cat timp nu eram conectati
        }
      }),
    [subscribe, id, markRead, loadLatest]
  );

  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => { if (s === "active" && focused.current) loadLatest(); });
    return () => sub.remove();
  }, [loadLatest]);

  const scrollToNewest = () => { listRef.current?.scrollToOffset({ offset: 0, animated: true }); setNewBelow(0); };

  const onScroll = (ev: NativeSyntheticEvent<NativeScrollEvent>) => {
    const bottom = ev.nativeEvent.contentOffset.y < 80; // lista e inversata: offset 0 = cele mai noi
    atBottom.current = bottom;
    if (bottom && newBelow > 0) setNewBelow(0);
  };

  // ---- trimitere
  const doSend = useCallback(async (body: string, cid: string) => {
    setPending((p) => addPending(p, { cid, text: body, status: "sending" }));
    try {
      const r = await api<{ message: DmMsg }>(`/dm/conversations/${encodeURIComponent(id)}/messages`, {
        method: "POST", body: JSON.stringify({ text: body, client_id: cid }),
      });
      setMessages((p) => mergeMessages(p, [r.message])); // textul asa cum l-a decis serverul (filtrat daca e cazul)
      setPending((p) => resolvePending(p, cid));
      listRef.current?.scrollToOffset({ offset: 0, animated: true });
    } catch (e: any) {
      const code: string = e?.code || (e?.status === 0 ? "offline" : "unavailable");
      if (code === "not_allowed" || code === "not_found") setCanSend(false);
      setPending((p) => failPending(p, cid, code));
    }
  }, [id]);

  const submit = () => {
    const body = text.trim();
    if (!body || !canSend) return;
    setText("");
    doSend(body, newClientId());
  };

  const tapPending = (p: DmPending) => {
    if (p.status !== "failed") return;
    if (isPermanentError(p.error)) setPending((l) => resolvePending(l, p.cid));
    else doSend(p.text, p.cid);
  };

  // ---- raportare / blocare (sistemele existente: db.reports, /friends/block)
  const report = async (m: DmMsg) => {
    setSheet(null);
    try {
      await api(`/dm/messages/${encodeURIComponent(m.id)}/report`, { method: "POST", body: JSON.stringify({ reason: "inappropriate" }) });
      setNotice(t("dm_reported"));
    } catch (e: any) { setNotice(t(errorKey(e?.code, e?.status))); }
    setTimeout(() => setNotice(null), 3500);
  };
  const block = async () => {
    setSheet(null);
    try {
      await api("/friends/block", { method: "POST", body: JSON.stringify({ target_user_id: friend.user_id }) });
      refreshUnread();
      router.back();
    } catch (e: any) { setNotice(t(errorKey(e?.code, e?.status))); setTimeout(() => setNotice(null), 3500); }
  };

  const items: Item[] = useMemo(
    () => [...[...pending].reverse().map((p): Item => ({ kind: "pending", p })), ...messages.map((m): Item => ({ kind: "msg", m }))],
    [pending, messages]
  );

  const title = friend.name || friend.username || "…";
  const minor = user?.age_category === "under_18";

  if (denied) {
    return (
      <SafeAreaView style={styles.root} edges={["top"]}>
        <View style={styles.page}>
          <View style={styles.header}>
            <Pressable onPress={() => router.back()} hitSlop={10} testID="dm-back"><MaterialCommunityIcons name="chevron-left" size={28} color={colors.onSurface} /></Pressable>
          </View>
          <Text style={styles.emptyText}>{t("dm_err_not_allowed")}</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
        <View style={styles.page}>
          <View style={styles.header}>
            <Pressable onPress={() => router.back()} hitSlop={10} testID="dm-back">
              <MaterialCommunityIcons name="chevron-left" size={28} color={colors.onSurface} />
            </Pressable>
            <DmAvatar friend={{ display_name: friend.name, username: friend.username, avatar_url: friend.avatar }} size={38} />
            <View style={{ flex: 1 }}>
              <Text style={styles.headerName} numberOfLines={1}>{title}</Text>
              <Text style={styles.headerSub} numberOfLines={1}>
                {!connected ? t("dm_connecting") : minor ? t("chat_mode_filtered") : friend.username ? `@${friend.username}` : ""}
              </Text>
            </View>
            {friend.user_id ? (
              <Pressable onPress={() => setSheet({ kind: "block" })} hitSlop={10} testID="dm-block-btn">
                <MaterialCommunityIcons name="account-cancel-outline" size={22} color={colors.onSurface3} />
              </Pressable>
            ) : null}
          </View>

          {notice ? <Text style={styles.notice}>{notice}</Text> : null}
          {loadError ? <Pressable onPress={loadLatest}><Text style={styles.err}>{loadError}</Text></Pressable> : null}

          <View style={{ flex: 1 }}>
            {loading ? (
              <ActivityIndicator style={{ marginTop: 24 }} color={colors.brand} />
            ) : items.length === 0 ? (
              <View style={styles.emptyWrap}><Text style={styles.emptyText} testID="dm-empty-chat">{t("dm_empty_chat")}</Text></View>
            ) : (
              <FlatList
                ref={listRef}
                inverted
                data={items}
                keyExtractor={(it) => (it.kind === "msg" ? it.m.id : `p-${it.p.cid}`)}
                contentContainerStyle={{ padding: spacing.lg, gap: 6 }}
                onScroll={onScroll}
                scrollEventThrottle={100}
                onEndReached={loadMore}
                onEndReachedThreshold={0.4}
                keyboardShouldPersistTaps="handled"
                ListFooterComponent={loadingMore ? <ActivityIndicator style={{ marginVertical: 12 }} color={colors.brand} /> : null}
                renderItem={({ item }) => {
                  if (item.kind === "pending") {
                    const p = item.p;
                    return (
                      <View style={[styles.row, { justifyContent: "flex-end" }]}>
                        <Pressable onPress={() => tapPending(p)} style={[styles.bubble, styles.bubbleMine, { opacity: 0.7 }]} testID={`dm-pending-${p.cid}`}>
                          <Text style={[styles.bubbleText, { color: colors.onBrand }]}>{p.text}</Text>
                          <Text style={[styles.time, p.status === "failed" && { color: colors.error }]}>
                            {p.status === "sending" ? t("dm_sending") : `${t(errorKey(p.error))} · ${isPermanentError(p.error) ? t("dm_dismiss") : t("dm_retry")}`}
                          </Text>
                        </Pressable>
                      </View>
                    );
                  }
                  const m = item.m;
                  return (
                    <View style={[styles.row, m.mine && { justifyContent: "flex-end" }]}>
                      <Pressable
                        onLongPress={() => { if (!m.mine) setSheet({ kind: "msg", msg: m }); }}
                        delayLongPress={350}
                        style={[styles.bubble, m.mine ? styles.bubbleMine : styles.bubbleTheirs]}
                        testID={`dm-msg-${m.id}`}
                      >
                        <Text style={[styles.bubbleText, m.mine && { color: colors.onBrand }]} selectable>{m.text}</Text>
                        <Text style={[styles.time, m.mine && { color: colors.onBrand, opacity: 0.6 }]}>{hourMinute(m.ts, lang)}</Text>
                      </Pressable>
                    </View>
                  );
                }}
              />
            )}
            {newBelow > 0 ? (
              <Pressable onPress={scrollToNewest} style={styles.newPill} testID="dm-new-messages">
                <MaterialCommunityIcons name="arrow-down" size={14} color={colors.onBrand} />
                <Text style={styles.newPillText}>{t("dm_new_messages")} ({newBelow})</Text>
              </Pressable>
            ) : null}
          </View>

          {!canSend ? <Text style={styles.banner} testID="dm-cannot-send">{t("dm_not_friends")}</Text> : null}
          <View style={[styles.composer, { paddingBottom: kbVisible ? 8 : Math.max(insets.bottom, 8) }]}>
            <TextInput
              testID="dm-input"
              value={text}
              onChangeText={setText}
              placeholder={t("dm_placeholder")}
              placeholderTextColor={colors.onSurface3}
              style={styles.input}
              maxLength={MAX_DM_LEN}
              editable={canSend}
              returnKeyType="send"
              blurOnSubmit={false}
              onSubmitEditing={submit}
            />
            <Pressable
              testID="dm-send"
              onPress={submit}
              disabled={!canSend || !text.trim()}
              style={[styles.sendBtn, (!canSend || !text.trim()) && { opacity: 0.4 }]}
            >
              <MaterialCommunityIcons name="send" size={18} color={colors.onBrand} />
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>

      <Modal visible={sheet !== null} transparent animationType="fade" onRequestClose={() => setSheet(null)}>
        <Pressable style={styles.backdrop} onPress={() => setSheet(null)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            {sheet?.kind === "msg" ? (
              <>
                <Pressable style={styles.sheetBtn} onPress={() => report(sheet.msg)} testID="dm-report-confirm">
                  <MaterialCommunityIcons name="flag-outline" size={18} color={colors.error} />
                  <Text style={[styles.sheetText, { color: colors.error }]}>{t("dm_report")}</Text>
                </Pressable>
              </>
            ) : sheet?.kind === "block" ? (
              <>
                <Text style={styles.sheetTitle}>{t("dm_block_title")}</Text>
                <Text style={styles.sheetBody}>{t("dm_block_body")}</Text>
                <Pressable style={styles.sheetBtn} onPress={block} testID="dm-block-confirm">
                  <MaterialCommunityIcons name="account-cancel-outline" size={18} color={colors.error} />
                  <Text style={[styles.sheetText, { color: colors.error }]}>{t("dm_block_confirm")}</Text>
                </Pressable>
              </>
            ) : null}
            <Pressable style={styles.sheetBtn} onPress={() => setSheet(null)}>
              <Text style={styles.sheetText}>{t("cancel")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  // desktop/tableta: coloana centrata cu margini; pe telefon ocupa tot ecranul
  page: { flex: 1, width: "100%", maxWidth: 760, alignSelf: "center", backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
  headerName: { color: colors.onSurface, fontWeight: "800", fontSize: 16 },
  headerSub: { color: colors.onSurface3, fontSize: 11, marginTop: 1 },
  notice: { color: colors.success, fontSize: 12, textAlign: "center", paddingVertical: 6 },
  err: { color: colors.error, fontSize: 12, textAlign: "center", paddingVertical: 6 },
  banner: { color: colors.warning, fontSize: 12, textAlign: "center", paddingVertical: 8, paddingHorizontal: spacing.lg, backgroundColor: colors.surface2 },
  emptyWrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  emptyText: { color: colors.onSurface3, textAlign: "center", fontSize: 13, padding: spacing.lg },
  row: { flexDirection: "row" },
  bubble: { maxWidth: "80%", paddingHorizontal: 12, paddingVertical: 8, borderRadius: 16 },
  bubbleMine: { backgroundColor: colors.brand, borderBottomRightRadius: 4 },
  bubbleTheirs: { backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: 4 },
  bubbleText: { color: colors.onSurface, fontSize: 15, lineHeight: 20 },
  time: { color: colors.onSurface3, fontSize: 10, marginTop: 3, alignSelf: "flex-end" },
  newPill: { position: "absolute", alignSelf: "center", bottom: 12, flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.brand, paddingVertical: 7, paddingHorizontal: 14, borderRadius: radius.pill },
  newPillText: { color: colors.onBrand, fontSize: 12, fontWeight: "800" },
  composer: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingTop: 8, borderTopWidth: 1, borderTopColor: colors.border, backgroundColor: colors.surface },
  input: { flex: 1, color: colors.onSurface, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 10, fontSize: 15 },
  sendBtn: { width: 42, height: 42, borderRadius: 21, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  sheet: { width: "100%", maxWidth: 360, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: 4 },
  sheetTitle: { color: colors.onSurface, fontWeight: "800", fontSize: 16 },
  sheetBody: { color: colors.onSurface3, fontSize: 13, lineHeight: 18, marginBottom: 6 },
  sheetBtn: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 12 },
  sheetText: { color: colors.onSurface, fontSize: 14, fontWeight: "700" },
});