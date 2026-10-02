import React, { useState } from "react";
import { View, Text, StyleSheet, TextInput, ScrollView, Pressable, Alert } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import { Image } from "expo-image";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

const GROUP_COST = 200;

export default function CreateGroup() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [logo, setLogo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function pickLogo() {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) return;
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.6, base64: true, allowsEditing: true, aspect: [1, 1] });
    if (res.canceled) return;
    const a = res.assets[0];
    if (a.base64) setLogo(`data:image/jpeg;base64,${a.base64}`);
  }

  async function create() {
    if (name.trim().length < 2) { setErr("Name too short"); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api("/groups", { method: "POST", body: JSON.stringify({ name, description, logo_url: logo }) });
      router.replace({ pathname: "/group/[id]", params: { id: r.group.group_id } });
    } catch (e: any) {
      setErr(e.message);
    } finally { setBusy(false); }
  }

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()}><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.headerTitle}>Create Group</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
        <Pressable onPress={pickLogo} style={styles.logoBox}>
          {logo ? <Image source={{ uri: logo }} style={StyleSheet.absoluteFillObject} contentFit="cover" /> : (
            <MaterialCommunityIcons name="account-group-outline" size={36} color={colors.brand} />
          )}
        </Pressable>
        <Text style={styles.hintSmall}>Tap to add a logo (optional)</Text>

        <Text style={styles.lab}>Name</Text>
        <TextInput value={name} onChangeText={setName} style={styles.input} placeholder="My Group" placeholderTextColor={colors.onSurface3} maxLength={50} />

        <Text style={styles.lab}>Description</Text>
        <TextInput value={description} onChangeText={setDescription} style={[styles.input, { height: 90 }]} multiline placeholder="What's this group about?" placeholderTextColor={colors.onSurface3} maxLength={500} />

        <View style={styles.costCard}>
          <MaterialCommunityIcons name="hexagon-slice-6" size={20} color={colors.brand} />
          <Text style={styles.costText}>Creating a Group costs {GROUP_COST} Astrans. You can own only one Group per account.</Text>
        </View>

        {err ? <Text style={styles.err}>{err}</Text> : null}
        <View style={{ marginTop: 16 }}>
          <PrimaryButton label={busy ? "..." : "Create Group"} onPress={create} disabled={busy} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md },
  headerTitle: { color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  logoBox: { width: 88, height: 88, borderRadius: 44, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignSelf: "center", alignItems: "center", justifyContent: "center", overflow: "hidden" },
  hintSmall: { color: colors.onSurface3, fontSize: 11, textAlign: "center", marginTop: 8 },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 16 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  costCard: { flexDirection: "row", gap: 10, alignItems: "center", backgroundColor: colors.surface2, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, marginTop: 20 },
  costText: { flex: 1, color: colors.onSurface2, fontSize: 12, lineHeight: 17 },
  err: { color: colors.error, marginTop: 12, fontSize: 12, fontWeight: "600" },
});