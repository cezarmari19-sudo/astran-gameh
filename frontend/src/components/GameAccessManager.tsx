import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, Modal, Pressable, TextInput, ScrollView, ActivityIndicator, Alert } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

type Level = "view" | "code_edit" | "full";

const LEVEL_LABEL: Record<Level, string> = {
  view: "View Only",
  code_edit: "Code Edit",
  full: "Full Access",
};

export default function GameAccessManager({ visible, gameId, onClose }: { visible: boolean; gameId: string; onClose: () => void }) {
  const [tab, setTab] = useState<"editors" | "testers">("editors");
  const [collaborators, setCollaborators] = useState<any[]>([]);
  const [testers, setTesters] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [picked, setPicked] = useState<any>(null);
  const [level, setLevel] = useState<Level>("view");
  const [allowedPaths, setAllowedPaths] = useState("");
  const [deniedPaths, setDeniedPaths] = useState("");
  const [busy, setBusy] = useState(false);
  const [editingPathsFor, setEditingPathsFor] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [c, t] = await Promise.all([
        api(`/games/${gameId}/collaborators`),
        api(`/games/${gameId}/testers`),
      ]);
      setCollaborators(c.collaborators || []);
      setTesters(t.testers || []);
    } catch {}
    setLoading(false);
  }, [gameId]);

  useEffect(() => { if (visible) load(); }, [visible, load]);

  useEffect(() => {
    if (!showAdd || q.length < 2) { setResults([]); return; }
    const to = setTimeout(async () => {
      try {
        const res = await api(`/users/search?q=${encodeURIComponent(q)}`);
        setResults(res.users || []);
      } catch {}
    }, 300);
    return () => clearTimeout(to);
  }, [q, showAdd]);

  function resetAddForm() {
    setShowAdd(false); setQ(""); setResults([]); setPicked(null);
    setLevel("view"); setAllowedPaths(""); setDeniedPaths("");
  }

  function parsePaths(text: string): string[] {
    return text.split(",").map(s => s.trim()).filter(Boolean);
  }

  async function confirmAddEditor() {
    if (!picked) return;
    setBusy(true);
    try {
      await api(`/games/${gameId}/collaborators`, {
        method: "POST",
        body: JSON.stringify({
          target_user_id: picked.user_id,
          level,
          allowed_paths: parsePaths(allowedPaths),
          denied_paths: parsePaths(deniedPaths),
        }),
      });
      resetAddForm();
      await load();
    } catch (e: any) { Alert.alert("Error", e.message); }
    finally { setBusy(false); }
  }

  async function confirmAddTester() {
    if (!picked) return;
    setBusy(true);
    try {
      await api(`/games/${gameId}/testers`, { method: "POST", body: JSON.stringify({ target_user_id: picked.user_id }) });
      resetAddForm();
      await load();
    } catch (e: any) { Alert.alert("Error", e.message); }
    finally { setBusy(false); }
  }

  async function removeEditor(userId: string) {
    await api(`/games/${gameId}/collaborators/${userId}`, { method: "DELETE" });
    await load();
  }

  async function removeTester(userId: string) {
    await api(`/games/${gameId}/testers/${userId}`, { method: "DELETE" });
    await load();
  }

  async function saveEditorPaths() {
    if (!editingPathsFor) return;
    setBusy(true);
    try {
      await api(`/games/${gameId}/collaborators/${editingPathsFor.user_id}`, {
        method: "PATCH",
        body: JSON.stringify({ allowed_paths: parsePaths(allowedPaths), denied_paths: parsePaths(deniedPaths) }),
      });
      setEditingPathsFor(null);
      await load();
    } catch (e: any) { Alert.alert("Error", e.message); }
    finally { setBusy(false); }
  }

  async function changeLevel(userId: string, newLevel: Level) {
    await api(`/games/${gameId}/collaborators/${userId}`, { method: "PATCH", body: JSON.stringify({ level: newLevel }) });
    await load();
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: "flex-end" }}>
        <Pressable style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.6)" }} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>Access & Collaboration</Text>
            <Pressable onPress={onClose}><MaterialCommunityIcons name="close" size={22} color={colors.onSurface} /></Pressable>
          </View>

          <View style={styles.tabsRow}>
            <Pressable onPress={() => setTab("editors")} style={[styles.tab, tab === "editors" && styles.tabActive]}>
              <Text style={[styles.tabText, tab === "editors" && styles.tabTextActive]}>Editors ({collaborators.length})</Text>
            </Pressable>
            <Pressable onPress={() => setTab("testers")} style={[styles.tab, tab === "testers" && styles.tabActive]}>
              <Text style={[styles.tabText, tab === "testers" && styles.tabTextActive]}>Testers ({testers.length})</Text>
            </Pressable>
          </View>

          {loading ? (
            <ActivityIndicator color={colors.brand} style={{ marginTop: 20 }} />
          ) : (
            <ScrollView style={{ maxHeight: 360 }} contentContainerStyle={{ paddingBottom: 12 }}>
              {!showAdd && !editingPathsFor && (tab === "editors" ? collaborators : testers).length === 0 ? (
                <Text style={styles.empty}>{tab === "editors" ? "No editors yet." : "No testers yet."}</Text>
              ) : null}

              {!showAdd && !editingPathsFor && tab === "editors" ? collaborators.map(c => (
                <View key={c.user_id} style={styles.personRow}>
                  <View style={styles.avatar}><Text style={styles.avatarText}>{(c.display_name || c.username)[0].toUpperCase()}</Text></View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.name}>{c.display_name || c.username}</Text>
                    <View style={{ flexDirection: "row", gap: 6, marginTop: 4 }}>
                      {(["view", "code_edit", "full"] as Level[]).map(lv => (
                        <Pressable key={lv} onPress={() => changeLevel(c.user_id, lv)} style={[styles.levelPill, c.level === lv && styles.levelPillActive]}>
                          <Text style={[styles.levelPillText, c.level === lv && { color: colors.brand }]}>{LEVEL_LABEL[lv]}</Text>
                        </Pressable>
                      ))}
                    </View>
                    {(c.allowed_paths?.length || c.denied_paths?.length) ? (
                      <Text style={styles.pathsHint} numberOfLines={1}>
                        {c.allowed_paths?.length ? `only: ${c.allowed_paths.join(", ")}` : ""}
                        {c.denied_paths?.length ? `  not: ${c.denied_paths.join(", ")}` : ""}
                      </Text>
                    ) : null}
                  </View>
                  <Pressable onPress={() => { setEditingPathsFor(c); setAllowedPaths((c.allowed_paths || []).join(", ")); setDeniedPaths((c.denied_paths || []).join(", ")); }} style={styles.iconBtn}>
                    <MaterialCommunityIcons name="folder-key-outline" size={18} color={colors.onSurface} />
                  </Pressable>
                  <Pressable onPress={() => removeEditor(c.user_id)} style={styles.iconBtn}>
                    <MaterialCommunityIcons name="account-remove" size={18} color={colors.error} />
                  </Pressable>
                </View>
              )) : null}

              {!showAdd && !editingPathsFor && tab === "testers" ? testers.map(t => (
                <View key={t.user_id} style={styles.personRow}>
                  <View style={styles.avatar}><Text style={styles.avatarText}>{(t.display_name || t.username)[0].toUpperCase()}</Text></View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.name}>{t.display_name || t.username}</Text>
                    <Text style={styles.pathsHint}>Can play only, no code access</Text>
                  </View>
                  <Pressable onPress={() => removeTester(t.user_id)} style={styles.iconBtn}>
                    <MaterialCommunityIcons name="account-remove" size={18} color={colors.error} />
                  </Pressable>
                </View>
              )) : null}

              {editingPathsFor ? (
                <View style={{ gap: 8 }}>
                  <Text style={styles.lab}>Restrict "{editingPathsFor.display_name}" to these folders/files (comma separated, empty = whole project)</Text>
                  <TextInput value={allowedPaths} onChangeText={setAllowedPaths} placeholder="scripts/player, scripts/enemies" placeholderTextColor={colors.onSurface3} style={styles.input} />
                  <Text style={styles.lab}>Always deny (even inside the above)</Text>
                  <TextInput value={deniedPaths} onChangeText={setDeniedPaths} placeholder="server, assets" placeholderTextColor={colors.onSurface3} style={styles.input} />
                  <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
                    <Pressable style={styles.cancelBtn} onPress={() => setEditingPathsFor(null)}><Text style={styles.cancelBtnText}>Cancel</Text></Pressable>
                    <View style={{ flex: 1 }}><PrimaryButton label={busy ? "..." : "Save"} onPress={saveEditorPaths} disabled={busy} /></View>
                  </View>
                </View>
              ) : null}

              {showAdd ? (
                <View style={{ gap: 8 }}>
                  {!picked ? (
                    <>
                      <View style={styles.searchBox}>
                        <MaterialCommunityIcons name="magnify" size={18} color={colors.onSurface3} />
                        <TextInput value={q} onChangeText={setQ} placeholder="Search username..." placeholderTextColor={colors.onSurface3} style={styles.searchInput} autoCapitalize="none" />
                      </View>
                      {results.map(u => (
                        <Pressable key={u.user_id} onPress={() => setPicked(u)} style={styles.personRow}>
                          <View style={styles.avatar}><Text style={styles.avatarText}>{(u.display_name || u.username)[0].toUpperCase()}</Text></View>
                          <Text style={styles.name}>{u.display_name || u.username}</Text>
                        </Pressable>
                      ))}
                    </>
                  ) : tab === "editors" ? (
                    <>
                      <Text style={styles.lab}>Adding: {picked.display_name || picked.username}</Text>
                      <View style={{ flexDirection: "row", gap: 8 }}>
                        {(["view", "code_edit", "full"] as Level[]).map(lv => (
                          <Pressable key={lv} onPress={() => setLevel(lv)} style={[styles.levelPill, level === lv && styles.levelPillActive]}>
                            <Text style={[styles.levelPillText, level === lv && { color: colors.brand }]}>{LEVEL_LABEL[lv]}</Text>
                          </Pressable>
                        ))}
                      </View>
                      <Text style={styles.lab}>Restrict to folders/files (optional, comma separated)</Text>
                      <TextInput value={allowedPaths} onChangeText={setAllowedPaths} placeholder="scripts/player, scripts/enemies" placeholderTextColor={colors.onSurface3} style={styles.input} />
                      <Text style={styles.lab}>Always deny (optional)</Text>
                      <TextInput value={deniedPaths} onChangeText={setDeniedPaths} placeholder="server, assets" placeholderTextColor={colors.onSurface3} style={styles.input} />
                      <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
                        <Pressable style={styles.cancelBtn} onPress={resetAddForm}><Text style={styles.cancelBtnText}>Cancel</Text></Pressable>
                        <View style={{ flex: 1 }}><PrimaryButton label={busy ? "..." : "Grant Access"} onPress={confirmAddEditor} disabled={busy} /></View>
                      </View>
                    </>
                  ) : (
                    <>
                      <Text style={styles.lab}>Adding tester: {picked.display_name || picked.username}</Text>
                      <Text style={styles.pathsHint}>Can play/test the game, no code access.</Text>
                      <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
                        <Pressable style={styles.cancelBtn} onPress={resetAddForm}><Text style={styles.cancelBtnText}>Cancel</Text></Pressable>
                        <View style={{ flex: 1 }}><PrimaryButton label={busy ? "..." : "Grant Tester Access"} onPress={confirmAddTester} disabled={busy} /></View>
                      </View>
                    </>
                  )}
                </View>
              ) : null}
            </ScrollView>
          )}

          {!showAdd && !editingPathsFor ? (
            <Pressable onPress={() => setShowAdd(true)} style={styles.addBtn}>
              <MaterialCommunityIcons name="plus" size={18} color={colors.brand} />
              <Text style={styles.addBtnText}>{tab === "editors" ? "Add Editor" : "Add Tester"}</Text>
            </Pressable>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  sheet: { maxHeight: "85%", backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 12 },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "900" },
  tabsRow: { flexDirection: "row", gap: 8, marginBottom: 12 },
  tab: { flex: 1, alignItems: "center", paddingVertical: 10, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  tabActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  tabText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12 },
  tabTextActive: { color: colors.brand },
  empty: { color: colors.onSurface3, textAlign: "center", marginTop: 20, fontSize: 13 },
  personRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.surface2, padding: 10, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" },
  avatarText: { color: colors.brand, fontWeight: "900" },
  name: { color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  levelPill: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  levelPillActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  levelPillText: { color: colors.onSurface3, fontSize: 10, fontWeight: "700" },
  pathsHint: { color: colors.onSurface3, fontSize: 10, marginTop: 4 },
  iconBtn: { width: 32, height: 32, borderRadius: 16, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center", marginLeft: 4 },
  addBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 10, padding: 12, borderRadius: radius.md, borderWidth: 1, borderColor: colors.brand },
  addBtnText: { color: colors.brand, fontWeight: "800", fontSize: 13 },
  searchBox: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface2, paddingHorizontal: 12, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  searchInput: { flex: 1, color: colors.onSurface, paddingVertical: 10, fontSize: 14 },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 0.5, marginTop: 6 },
  input: { backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 14, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  cancelBtn: { paddingHorizontal: 16, justifyContent: "center", borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  cancelBtnText: { color: colors.onSurface2, fontWeight: "700" },
});