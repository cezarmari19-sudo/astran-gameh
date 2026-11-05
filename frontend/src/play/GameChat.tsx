// Chatul din joc: buton discret + panou cu mesaje + camp de text + Trimite.
// Se monteaza DIN ecranul de joc (app/play/[id].tsx) peste scena, in acelasi strat ca butoanele
// existente (meniul "A", Jump). Starea vine din RealtimeHandle.chat (socket-ul existent), iar
// serverul decide totul (identitate, varsta, filtrare, blocari) - vezi chatStore.ts.
//
// Reguli de layout ca sa NU blocheze gameplay-ul:
//  * containerul e pointerEvents="box-none": doar butonul/panoul prind atingeri, restul ecranului
//    (camera, joystick, Jump) ramane al jocului;
//  * panoul e ancorat SUS-STANGA si nu coboara in zona joystick-ului (reservedBottom);
//  * cand apare tastatura, panoul se muta in varful ecranului si isi limiteaza inaltimea la
//    spatiul ramas deasupra tastaturii - tastatura nu il acopera si nu il deformeaza;
//  * cat e inchis, mesajele noi apar ca o previzualizare cu pointerEvents="none" (atingerile trec prin ea).
import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { FlatList, Keyboard, Platform, Pressable, StyleSheet, Text, TextInput, View, useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";
import type { ChatApi, RealtimeHandle } from "@/src/play/useRealtime";
import { ChatMsg, PendingMsg, visibleMessages } from "@/src/play/chatStore";

type Row = { key: string; kind: "msg"; msg: ChatMsg } | { key: string; kind: "pending"; p: PendingMsg };

const NAME_COLORS = ["#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF", "#C77DFF", "#FF9F45", "#00E5FF", "#FF7AC6"];
const BTN = 40; // aceeasi dimensiune ca butonul "A" din ecranul de joc
const PREVIEW_MS = 7000;

function nameColor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++) h = (h * 31 + userId.charCodeAt(i)) >>> 0;
  return NAME_COLORS[h % NAME_COLORS.length];
}

export default function GameChat({ handle, reservedBottom = 0 }: { handle: RealtimeHandle | null; reservedBottom?: number }) {
  if (!handle) return null;
  return <GameChatInner chat={handle.chat} reservedBottom={reservedBottom} />;
}

function GameChatInner({ chat, reservedBottom }: { chat: ChatApi; reservedBottom: number }) {
  const { t } = useI18n();
  const { user } = useAuth();
  const insets = useSafeAreaInsets();
  const { width: W, height: H } = useWindowDimensions();
  const state = useSyncExternalStore(chat.subscribe, chat.getState, chat.getState);

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [kb, setKb] = useState(0);
  const [selected, setSelected] = useState<ChatMsg | null>(null);
  const [confirmBlock, setConfirmBlock] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [seenTs, setSeenTs] = useState<number | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const listRef = useRef<FlatList<Row>>(null);
  const stickRef = useRef(true);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const myId = user?.user_id;

  const messages = useMemo(() => visibleMessages(state), [state]);
  const lastMsg = messages.length ? messages[messages.length - 1] : null;

  const rows: Row[] = useMemo(() => {
    const out: Row[] = messages.map(m => ({ key: m.id, kind: "msg" as const, msg: m }));
    for (const p of state.pending) out.push({ key: "p_" + p.cid, kind: "pending" as const, p });
    return out;
  }, [messages, state.pending]);

  // ---- tastatura (iOS: Will*, Android: Did*; pe web nu exista evenimentele - kb ramane 0)
  useEffect(() => {
    const showEv = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
    const hideEv = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const s = Keyboard.addListener(showEv, (e: any) => setKb(e?.endCoordinates?.height ?? 0));
    const h = Keyboard.addListener(hideEv, () => setKb(0));
    return () => { s.remove(); h.remove(); };
  }, []);

  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    if (previewTimer.current) clearTimeout(previewTimer.current);
  }, []);

  const flash = useCallback((text: string) => {
    setToast(text);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3500);
  }, []);

  const errText = useCallback((code: string): string => {
    const key = "chat_err_" + code;
    const v = t(key as any);
    return v === key ? t("chat_err_unavailable") : v;
  }, [t]);

  // ---- "necitite": istoricul de la intrare nu conteaza, doar ce soseste dupa el
  useEffect(() => {
    if (seenTs === null && state.historyLoaded) setSeenTs(lastMsg ? lastMsg.ts : 0);
  }, [seenTs, state.historyLoaded, lastMsg]);
  useEffect(() => {
    if (open && lastMsg && (seenTs === null || lastMsg.ts > seenTs)) setSeenTs(lastMsg.ts);
  }, [open, lastMsg, seenTs]);
  const unread = !open && seenTs !== null ? messages.filter(m => m.ts > seenTs && m.user_id !== myId).length : 0;

  // previzualizare scurta cand sosesc mesaje de la altii, cu panoul inchis
  const lastId = lastMsg?.id;
  useEffect(() => {
    if (open || !lastMsg || lastMsg.user_id === myId || seenTs === null) return;
    setShowPreview(true);
    if (previewTimer.current) clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => setShowPreview(false), PREVIEW_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastId]);

  // ---- notificari de la server (raport trimis / erori fara mesaj asociat)
  useEffect(() => {
    if (!state.notice) return;
    flash(state.notice.code === "reported" ? t("chat_reported") : errText(state.notice.code));
    chat.clearNotice();
  }, [state.notice, chat, flash, t, errText]);

  function openChat() {
    setOpen(true);
    stickRef.current = true;
    setAtBottom(true);
    setShowPreview(false);
    setTimeout(() => listRef.current?.scrollToEnd({ animated: false }), 50);
  }
  function closeChat() {
    Keyboard.dismiss();
    setOpen(false);
    setSelected(null);
    setConfirmBlock(false);
  }

  function submit() {
    if (!draft.trim()) return;
    const r = chat.send(draft);
    if (r.ok) {
      setDraft("");
      stickRef.current = true;
    } else if (r.error !== "empty") {
      flash(errText(r.error));
    }
  }

  function doReport() {
    if (!selected) return;
    if (!chat.report(selected.id, "inappropriate")) flash(errText("offline"));
    setSelected(null); setConfirmBlock(false);
  }
  async function doBlock() {
    if (!selected) return;
    const target = selected;
    try {
      await api("/friends/block", { method: "POST", body: JSON.stringify({ target_user_id: target.user_id }) });
      chat.blockLocal(target.user_id);
      flash(t("chat_blocked_done"));
    } catch {
      flash(t("chat_err_action"));
    }
    setSelected(null); setConfirmBlock(false);
  }

  // ---- dimensiuni
  const isLandscape = W > H;
  const left = insets.left + spacing.md;
  const btnTop = insets.top + spacing.md;
  const chatBtnLeft = left + BTN + 8;
  const panelTop = kb > 0 ? insets.top + 4 : btnTop + BTN + 8;
  const panelW = Math.max(220, Math.min(isLandscape ? Math.min(360, Math.round(W * 0.5)) : 360, W - left - Math.max(insets.right, spacing.md)));
  const bottomLimit = kb > 0 ? H - kb - 8 : H - reservedBottom;
  const desiredH = isLandscape ? 230 : Math.round(H * 0.4);
  const panelH = Math.max(120, Math.min(desiredH, bottomLimit - panelTop));

  function renderRow({ item }: { item: Row }) {
    if (item.kind === "pending") {
      const failed = item.p.status === "failed";
      return (
        <Pressable
          disabled={!failed}
          onPress={() => { const r = chat.retry(item.p.cid); if (!r.ok) flash(errText(r.error)); }}
          onLongPress={() => chat.dismissPending(item.p.cid)}
          style={styles.rowWrap}
        >
          <Text style={[styles.line, !failed && { opacity: 0.55 }]}>
            <Text style={[styles.name, { color: colors.brand }]}>{user?.username ?? ""}: </Text>
            <Text style={[styles.body, failed && { color: colors.error }]}>{item.p.text}</Text>
          </Text>
          <Text style={[styles.pendingNote, failed && { color: colors.error }]}>
            {failed ? `${errText(item.p.error || "unavailable")} · ${t("chat_retry")}` : t("chat_sending")}
          </Text>
        </Pressable>
      );
    }
    const m = item.msg;
    const mine = m.user_id === myId;
    const isSel = selected?.id === m.id;
    return (
      <Pressable
        onPress={() => { if (!mine) { setSelected(isSel ? null : m); setConfirmBlock(false); } }}
        style={[styles.rowWrap, isSel && styles.rowSelected]}
      >
        <Text style={styles.line}>
          <Text style={[styles.name, { color: mine ? colors.brand : nameColor(m.user_id) }]}>{m.username}: </Text>
          <Text style={styles.body}>{m.text}</Text>
        </Text>
      </Pressable>
    );
  }

  const previewMsgs = !open && showPreview ? messages.slice(-3) : [];

  return (
    <View pointerEvents="box-none" style={StyleSheet.absoluteFillObject}>
      {!open ? (
        <Pressable
          testID="play-chat-btn"
          accessibilityRole="button"
          accessibilityLabel={t("chat_open")}
          onPress={openChat}
          hitSlop={6}
          style={[styles.chatBtn, { left: chatBtnLeft, top: btnTop }]}
        >
          <MaterialCommunityIcons name="chat-outline" size={20} color={colors.brand} />
          {unread > 0 ? (
            <View style={styles.badge}><Text style={styles.badgeText}>{unread > 9 ? "9+" : String(unread)}</Text></View>
          ) : null}
        </Pressable>
      ) : null}

      {previewMsgs.length > 0 ? (
        <View pointerEvents="none" style={[styles.preview, { left, top: btnTop + BTN + 8, width: Math.min(320, Math.round(W * 0.5)) }]}>
          {previewMsgs.map(m => (
            <Text key={m.id} numberOfLines={2} style={styles.previewLine}>
              <Text style={{ color: m.user_id === myId ? colors.brand : nameColor(m.user_id), fontWeight: "800" }}>{m.username}: </Text>
              {m.text}
            </Text>
          ))}
        </View>
      ) : null}

      {open ? (
        <View testID="play-chat-panel" style={[styles.panel, { left, top: panelTop, width: panelW, height: panelH }]}>
          <View style={styles.header}>
            <Text style={styles.title}>{t("chat")}</Text>
            {state.mode ? (
              <View style={[styles.modeBadge, state.mode === "adult_18" && styles.modeBadgeAdult]}>
                <Text style={[styles.modeText, state.mode === "adult_18" && { color: "#fff" }]}>
                  {state.mode === "adult_18" ? t("chat_mode_adult") : t("chat_mode_filtered")}
                </Text>
              </View>
            ) : null}
            <View style={{ flex: 1 }} />
            <Pressable testID="play-chat-close" accessibilityRole="button" accessibilityLabel={t("chat_close")} onPress={closeChat} hitSlop={12}>
              <MaterialCommunityIcons name="close" size={20} color={colors.onSurface2} />
            </Pressable>
          </View>

          {!state.connected ? <Text style={styles.offline}>{t("chat_offline")}</Text> : null}

          <View style={{ flex: 1 }}>
            <FlatList
              ref={listRef}
              data={rows}
              keyExtractor={r => r.key}
              renderItem={renderRow}
              extraData={selected?.id}
              keyboardShouldPersistTaps="handled"
              contentContainerStyle={styles.listContent}
              ListEmptyComponent={<Text style={styles.empty}>{t("chat_empty")}</Text>}
              onContentSizeChange={() => { if (stickRef.current) listRef.current?.scrollToEnd({ animated: false }); }}
              onScroll={e => {
                const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
                const near = contentSize.height - layoutMeasurement.height - contentOffset.y < 40;
                stickRef.current = near;
                setAtBottom(prev => (prev === near ? prev : near));
              }}
              scrollEventThrottle={64}
            />
            {!atBottom ? (
              <Pressable
                accessibilityRole="button"
                onPress={() => { stickRef.current = true; listRef.current?.scrollToEnd({ animated: true }); }}
                style={styles.jumpDown}
              >
                <MaterialCommunityIcons name="chevron-down" size={18} color={colors.onBrand} />
              </Pressable>
            ) : null}
          </View>

          {selected ? (
            <View style={styles.actions}>
              {confirmBlock ? (
                <>
                  <Text style={styles.actionsInfo} numberOfLines={2}>{t("chat_block_info")}</Text>
                  <Pressable testID="play-chat-block-confirm" onPress={doBlock} style={[styles.actionBtn, { backgroundColor: colors.error }]}>
                    <Text style={[styles.actionText, { color: "#fff" }]}>{t("chat_block_confirm")}</Text>
                  </Pressable>
                </>
              ) : (
                <>
                  <Text style={styles.actionsInfo} numberOfLines={1}>{selected.username}</Text>
                  <Pressable testID="play-chat-report" onPress={doReport} style={styles.actionBtn}>
                    <Text style={styles.actionText}>{t("chat_report")}</Text>
                  </Pressable>
                  <Pressable testID="play-chat-block" onPress={() => setConfirmBlock(true)} style={styles.actionBtn}>
                    <Text style={[styles.actionText, { color: colors.error }]}>{t("chat_block")}</Text>
                  </Pressable>
                </>
              )}
              <Pressable onPress={() => { setSelected(null); setConfirmBlock(false); }} hitSlop={10}>
                <MaterialCommunityIcons name="close" size={16} color={colors.onSurface3} />
              </Pressable>
            </View>
          ) : null}

          {toast ? <Text style={styles.toast} numberOfLines={2}>{toast}</Text> : null}

          <View style={styles.inputRow}>
            <TextInput
              testID="play-chat-input"
              value={draft}
              onChangeText={setDraft}
              placeholder={t("chat_placeholder")}
              placeholderTextColor={colors.onSurface3}
              maxLength={state.maxLen}
              returnKeyType="send"
              blurOnSubmit={false}
              onSubmitEditing={submit}
              style={[styles.input, Platform.OS === "web" ? ({ outlineStyle: "none" } as any) : null]}
            />
            <Pressable
              testID="play-chat-send"
              accessibilityRole="button"
              accessibilityLabel={t("chat_send")}
              onPress={submit}
              disabled={!draft.trim()}
              style={[styles.sendBtn, !draft.trim() && { opacity: 0.4 }]}
            >
              <MaterialCommunityIcons name="send" size={16} color={colors.onBrand} />
            </Pressable>
          </View>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  chatBtn: { position: "absolute", width: BTN, height: BTN, borderRadius: BTN / 2, backgroundColor: "rgba(0,0,0,0.6)", borderWidth: 1, borderColor: "rgba(255,255,255,0.2)", alignItems: "center", justifyContent: "center" },
  badge: { position: "absolute", top: -4, right: -4, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4, backgroundColor: colors.error, alignItems: "center", justifyContent: "center" },
  badgeText: { color: "#fff", fontSize: 10, fontWeight: "900" },
  preview: { position: "absolute", gap: 2 },
  previewLine: { color: "#fff", fontSize: 13, textShadowColor: "rgba(0,0,0,0.9)", textShadowRadius: 3, textShadowOffset: { width: 0, height: 1 } },
  panel: { position: "absolute", backgroundColor: "rgba(15,16,18,0.88)", borderRadius: radius.md, borderWidth: 1, borderColor: "rgba(255,255,255,0.18)", overflow: "hidden" },
  header: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 10, height: 32, borderBottomWidth: 1, borderBottomColor: "rgba(255,255,255,0.1)" },
  title: { color: colors.onSurface, fontSize: 13, fontWeight: "900" },
  modeBadge: { paddingHorizontal: 7, paddingVertical: 2, borderRadius: radius.pill, backgroundColor: colors.brandTint, borderWidth: 1, borderColor: colors.brand },
  modeBadgeAdult: { backgroundColor: colors.error, borderColor: colors.error },
  modeText: { color: colors.brand, fontSize: 9, fontWeight: "800" },
  offline: { color: colors.warning, fontSize: 11, textAlign: "center", paddingVertical: 3 },
  listContent: { paddingHorizontal: 10, paddingVertical: 6, gap: 3 },
  rowWrap: { paddingVertical: 2, paddingHorizontal: 4, borderRadius: 6 },
  rowSelected: { backgroundColor: "rgba(255,255,255,0.1)" },
  line: { fontSize: 13, lineHeight: 18 },
  name: { fontWeight: "800" },
  body: { color: colors.onSurface },
  pendingNote: { color: colors.onSurface3, fontSize: 10, marginTop: 1 },
  empty: { color: colors.onSurface3, fontSize: 12, textAlign: "center", marginTop: 16 },
  jumpDown: { position: "absolute", right: 8, bottom: 6, width: 26, height: 26, borderRadius: 13, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  actions: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 10, paddingVertical: 6, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.1)" },
  actionsInfo: { flex: 1, color: colors.onSurface2, fontSize: 11, fontWeight: "700" },
  actionBtn: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: "rgba(255,255,255,0.12)" },
  actionText: { color: colors.onSurface, fontSize: 11, fontWeight: "800" },
  toast: { color: colors.warning, fontSize: 11, textAlign: "center", paddingHorizontal: 10, paddingVertical: 3 },
  inputRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 8, paddingVertical: 6, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.1)" },
  input: { flex: 1, height: 36, color: colors.onSurface, backgroundColor: "rgba(255,255,255,0.08)", borderRadius: radius.pill, paddingHorizontal: 12, fontSize: 14 },
  sendBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
});