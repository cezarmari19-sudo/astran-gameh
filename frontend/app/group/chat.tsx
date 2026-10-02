import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, FlatList, TextInput, Pressable, KeyboardAvoidingView, Platform } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import { colors, radius, spacing } from "@/src/theme";

export default function GroupChat() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const [messages, setMessages] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const listRef = useRef<FlatList>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api(`/groups/${id}/chat/messages`);
      setMessages(r.messages || []);
    } catch {}
  }, [id]);

  useFocusEffect(useCallback(() => {
    load();
    poll.current = setInterval(load, 4000);
    return () => { if (poll.current) clearInterval(poll.current); };
  }, [load]));

  async function send() {
    const body = text.trim();
    if (!body) return;
    setText(""); setErr(null);
    try {
      await api(`/groups/${id}/chat/messages`, { method: "POST", body: JSON.stringify({ text: body }) });
      await load();
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
    } catch (e: any) {
      setErr(e.message);
    }
  }

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()}><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.headerTitle}>Group Chat</Text>
        <View style={{ width: 26 }} />
      </View>

      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={m => m.message_id}
        contentContainerStyle={{ padding: spacing.lg, gap: 8 }}
        onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
        renderItem={({ item }) => {
          const mine = item.user_id === user?.user_id;
          return (
            <View style={[styles.bubbleRow, mine && { justifyContent: "flex-end" }]}>
              <View style={[styles.bubble, mine && styles.bubbleMine]}>
                {!mine ? <Text style={styles.bubbleAuthor}>{item.username}</Text> : null}
                <Text style={[styles.bubbleText, mine && { color: colors.onBrand }]}>{item.text}</Text>
              </View>
            </View>
          );
        }}
        ListEmptyComponent={<Text style={styles.empty}>No messages yet.</Text>}
      />

      {err ? <Text style={styles.err}>{err}</Text> : null}

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <View style={styles.inputRow}>
          <TextInput
            value={text}
            onChangeText={setText}
            placeholder="Message..."
            placeholderTextColor={colors.onSurface3}
            style={styles.input}
            onSubmitEditing={send}
          />
          <Pressable onPress={send} style={styles.sendBtn}>
            <MaterialCommunityIcons name="send" size={18} color={colors.onBrand} />
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md },
  headerTitle: { color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  bubbleRow: { flexDirection: "row" },
  bubble: { maxWidth: "78%", backgroundColor: colors.surface2, borderRadius: radius.md, padding: 10, borderWidth: 1, borderColor: colors.border },
  bubbleMine: { backgroundColor: colors.brand, borderColor: colors.brand },
  bubbleAuthor: { color: colors.brand, fontSize: 10, fontWeight: "800", marginBottom: 2 },
  bubbleText: { color: colors.onSurface, fontSize: 14 },
  empty: { color: colors.onSurface3, textAlign: "center", marginTop: 40 },
  err: { color: colors.error, fontSize: 11, textAlign: "center", marginBottom: 4 },
  inputRow: { flexDirection: "row", gap: 8, padding: spacing.md, borderTopWidth: 1, borderColor: colors.border, alignItems: "center" },
  input: { flex: 1, backgroundColor: colors.surface2, color: colors.onSurface, borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  sendBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
});