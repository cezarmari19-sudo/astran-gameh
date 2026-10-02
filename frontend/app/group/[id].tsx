import React, { useCallback, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator, Alert, Modal, TextInput } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import { Image } from "expo-image";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import { api } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";

export default function GroupPage() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const [group, setGroup] = useState<any>(null);
  const [members, setMembers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showToken, setShowToken] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [chatMode, setChatMode] = useState<"owner_only" | "owner_admins" | "everyone">("everyone");

  const load = useCallback(async () => {
    try {
      const [g, m] = await Promise.all([api(`/groups/${id}`), api(`/groups/${id}/members`)]);
      setGroup(g.group);
      setMembers(m.members || []);
      setName(g.group.name); setDescription(g.group.description); setChatMode(g.group.chat_mode);
    } catch (e: any) { Alert.alert("Error", e.message); }
    setLoading(false);
  }, [id]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const isOwner = group?.owner_id === user?.user_id;
  const myRole = group?.my_membership?.role;
  const isAdmin = myRole === "admin";
  const isMember = !!group?.my_membership && group.my_membership.status === "active";

  async function join() {
    try { await api(`/groups/${id}/join`, { method: "POST" }); await load(); }
    catch (e: any) { Alert.alert("Error", e.message); }
  }
  async function leave() {
    try { await api(`/groups/${id}/leave`, { method: "POST" }); await load(); }
    catch (e: any) { Alert.alert("Error", e.message); }
  }
  async function viewToken() {
    try { const r = await api(`/groups/${id}/token`); setToken(r.token); setShowToken(true); }
    catch (e: any) { Alert.alert("Error", e.message); }
  }
  async function copyToken() {
    if (token) { await Clipboard.setStringAsync(token); Alert.alert("Copied", "Group token copied to clipboard."); }
  }
  async function regenerateToken() {
    try { const r = await api(`/groups/${id}/token/regenerate`, { method: "POST" }); setToken(r.token); }
    catch (e: any) { Alert.alert("Error", e.message); }
  }
  async function saveSettings() {
    try {
      await api(`/groups/${id}`, { method: "PATCH", body: JSON.stringify({ name, description, chat_mode: chatMode }) });
      setShowSettings(false);
      await load();
    } catch (e: any) { Alert.alert("Error", e.message); }
  }
  async function promote(userId: string) { await api(`/groups/${id}/members/${userId}/promote`, { method: "POST" }); await load(); }
  async function demote(userId: string) { await api(`/groups/${id}/members/${userId}/demote`, { method: "POST" }); await load(); }
  async function mute(userId: string) { await api(`/groups/${id}/members/${userId}/mute`, { method: "POST" }); await load(); }
  async function unmute(userId: string) { await api(`/groups/${id}/members/${userId}/unmute`, { method: "POST" }); await load(); }
  async function ban(userId: string) {
    Alert.alert("Ban member?", "They will not be able to rejoin this Group.", [
      { text: "Cancel", style: "cancel" },
      { text: "Ban", style: "destructive", onPress: async () => { await api(`/groups/${id}/members/${userId}/ban`, { method: "POST" }); await load(); } },
    ]);
  }

  if (loading || !group) return <SafeAreaView style={styles.center}><ActivityIndicator color={colors.brand} /></SafeAreaView>;

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()}><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.headerTitle} numberOfLines={1}>{group.name}</Text>
        {isOwner ? (
          <Pressable onPress={() => setShowSettings(true)}><MaterialCommunityIcons name="cog" size={22} color={colors.onSurface} /></Pressable>
        ) : <View style={{ width: 22 }} />}
      </View>

      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
        <View style={styles.topCard}>
          {group.logo_url ? <Image source={{ uri: group.logo_url }} style={styles.logo} contentFit="cover" /> : (
            <View style={[styles.logo, { alignItems: "center", justifyContent: "center" }]}><MaterialCommunityIcons name="account-group" size={32} color={colors.brand} /></View>
          )}
          <Text style={styles.name}>{group.name}</Text>
          <Text style={styles.memberCount}>{group.member_count} members</Text>
          {group.description ? <Text style={styles.desc}>{group.description}</Text> : null}

          <View style={{ flexDirection: "row", gap: 8, marginTop: 14, width: "100%" }}>
            {isOwner ? (
              <View style={{ flex: 1 }}><SecondaryButton label="Group Token" icon="key-outline" onPress={viewToken} /></View>
            ) : isMember ? (
              <View style={{ flex: 1 }}><SecondaryButton label="Leave Group" icon="exit-to-app" onPress={leave} /></View>
            ) : (
              <View style={{ flex: 1 }}><PrimaryButton label="Join Group" icon="account-plus" onPress={join} /></View>
            )}
            {isMember || isOwner ? (
              <View style={{ flex: 1 }}><PrimaryButton label="Chat" icon="forum-outline" onPress={() => router.push({ pathname: "/group/chat", params: { id: group.group_id } })} /></View>
            ) : null}
          </View>
        </View>

        <Text style={styles.sectionLabel}>MEMBERS</Text>
        {members.map(m => (
          <View key={m.user_id} style={styles.memberRow}>
            <View style={styles.avatar}><Text style={styles.avatarText}>{(m.display_name || m.username)[0].toUpperCase()}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={styles.memberName}>{m.display_name || m.username}</Text>
              <View style={{ flexDirection: "row", gap: 6, marginTop: 2, alignItems: "center" }}>
                <View style={[styles.roleTag, m.role === "owner" && { backgroundColor: colors.ownerRed }, m.role === "admin" && { backgroundColor: colors.adminYellow }]}>
                  <Text style={[styles.roleTagText, m.role === "member" && { color: colors.onSurface2 }]}>{m.role.toUpperCase()}</Text>
                </View>
                {m.muted ? <MaterialCommunityIcons name="microphone-off" size={12} color={colors.error} /> : null}
              </View>
            </View>
            {(isOwner && m.role !== "owner") ? (
              <View style={{ flexDirection: "row", gap: 6 }}>
                {m.role === "member" ? (
                  <Pressable onPress={() => promote(m.user_id)} style={styles.iconBtn}><MaterialCommunityIcons name="arrow-up-bold" size={16} color={colors.onSurface} /></Pressable>
                ) : (
                  <Pressable onPress={() => demote(m.user_id)} style={styles.iconBtn}><MaterialCommunityIcons name="arrow-down-bold" size={16} color={colors.onSurface} /></Pressable>
                )}
                <Pressable onPress={() => (m.muted ? unmute(m.user_id) : mute(m.user_id))} style={styles.iconBtn}>
                  <MaterialCommunityIcons name={m.muted ? "microphone" : "microphone-off"} size={16} color={colors.onSurface} />
                </Pressable>
                <Pressable onPress={() => ban(m.user_id)} style={styles.iconBtn}><MaterialCommunityIcons name="cancel" size={16} color={colors.error} /></Pressable>
              </View>
            ) : (isAdmin && m.role === "member") ? (
              <Pressable onPress={() => (m.muted ? unmute(m.user_id) : mute(m.user_id))} style={styles.iconBtn}>
                <MaterialCommunityIcons name={m.muted ? "microphone" : "microphone-off"} size={16} color={colors.onSurface} />
              </Pressable>
            ) : null}
          </View>
        ))}
      </ScrollView>

      <Modal visible={showToken} transparent animationType="fade" onRequestClose={() => setShowToken(false)}>
        <Pressable style={styles.backdrop} onPress={() => setShowToken(false)}>
          <View style={styles.tokenBox}>
            <Text style={styles.sheetTitle}>Group Token</Text>
            <Text style={styles.tokenHint}>Share this only with people you trust to publish games under your Group. Anyone with it can attach a game to this Group.</Text>
            <Text selectable style={styles.tokenText}>{token}</Text>
            <View style={{ flexDirection: "row", gap: 8, marginTop: 14 }}>
              <View style={{ flex: 1 }}><SecondaryButton label="Copy" icon="content-copy" onPress={copyToken} /></View>
              <View style={{ flex: 1 }}><SecondaryButton label="Regenerate" icon="refresh" onPress={regenerateToken} /></View>
            </View>
          </View>
        </Pressable>
      </Modal>

      <Modal visible={showSettings} transparent animationType="slide" onRequestClose={() => setShowSettings(false)}>
        <View style={{ flex: 1, justifyContent: "flex-end" }}>
          <Pressable style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.6)" }} onPress={() => setShowSettings(false)} />
          <View style={styles.settingsSheet}>
            <Text style={styles.sheetTitle}>Group Settings</Text>
            <Text style={styles.lab}>Name</Text>
            <TextInput value={name} onChangeText={setName} style={styles.input} maxLength={50} />
            <Text style={styles.lab}>Description</Text>
            <TextInput value={description} onChangeText={setDescription} style={[styles.input, { height: 80 }]} multiline maxLength={500} />
            <Text style={styles.lab}>Who can chat</Text>
            <View style={{ gap: 6, marginTop: 6 }}>
              {([
                { k: "owner_only", label: "Only Owner" },
                { k: "owner_admins", label: "Owner + Admins" },
                { k: "everyone", label: "Everyone" },
              ] as const).map(opt => (
                <Pressable key={opt.k} onPress={() => setChatMode(opt.k)} style={[styles.chatModeRow, chatMode === opt.k && styles.chatModeRowActive]}>
                  <MaterialCommunityIcons name={chatMode === opt.k ? "radiobox-marked" : "radiobox-blank"} size={18} color={chatMode === opt.k ? colors.brand : colors.onSurface3} />
                  <Text style={styles.chatModeText}>{opt.label}</Text>
                </Pressable>
              ))}
            </View>
            <View style={{ marginTop: 16 }}><PrimaryButton label="Save" onPress={saveSettings} /></View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md, gap: 10 },
  headerTitle: { flex: 1, color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  topCard: { alignItems: "center", padding: spacing.xl, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, marginBottom: spacing.lg },
  logo: { width: 80, height: 80, borderRadius: radius.lg, backgroundColor: colors.surface3 },
  name: { color: colors.onSurface, fontSize: 20, fontWeight: "900", marginTop: 12 },
  memberCount: { color: colors.onSurface3, fontSize: 12, marginTop: 2 },
  desc: { color: colors.onSurface2, fontSize: 13, textAlign: "center", marginTop: 10, lineHeight: 19 },
  sectionLabel: { color: colors.onSurface3, fontSize: 11, fontWeight: "800", letterSpacing: 1.5, marginBottom: 8 },
  memberRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.surface2, padding: 10, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" },
  avatarText: { color: colors.brand, fontWeight: "900" },
  memberName: { color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  roleTag: { backgroundColor: colors.surface3, paddingHorizontal: 6, paddingVertical: 2, borderRadius: radius.sm },
  roleTagText: { color: colors.onBrand, fontSize: 9, fontWeight: "900" },
  iconBtn: { width: 30, height: 30, borderRadius: 15, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  tokenBox: { width: "100%", maxWidth: 400, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  sheetTitle: { color: colors.onSurface, fontSize: 18, fontWeight: "900", marginBottom: 8 },
  tokenHint: { color: colors.onSurface3, fontSize: 11, lineHeight: 16, marginBottom: 12 },
  tokenText: { color: colors.brand, fontSize: 13, fontWeight: "700", backgroundColor: colors.surface2, padding: 12, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  settingsSheet: { maxHeight: "85%", backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  chatModeRow: { flexDirection: "row", alignItems: "center", gap: 8, padding: 10, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  chatModeRowActive: { borderColor: colors.brand },
  chatModeText: { color: colors.onSurface, fontWeight: "600", fontSize: 13 },
});