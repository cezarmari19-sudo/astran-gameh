import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, ScrollView, Pressable, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import { Image } from "expo-image";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function GroupHub() {
  const router = useRouter();
  const [owned, setOwned] = useState<any>(null);
  const [memberships, setMemberships] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await api("/groups/mine");
      setOwned(r.owned_group || null);
      setMemberships(r.memberships || []);
    } catch {}
    setLoading(false);
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  if (loading) return <SafeAreaView style={styles.center}><ActivityIndicator color={colors.brand} /></SafeAreaView>;

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()}><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.headerTitle}>Groups</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }}>
        <Text style={styles.sectionLabel}>YOUR GROUP</Text>
        {owned ? (
          <Pressable onPress={() => router.push({ pathname: "/group/[id]", params: { id: owned.group_id } })} style={styles.groupCard}>
            {owned.logo_url ? <Image source={{ uri: owned.logo_url }} style={styles.logo} contentFit="cover" /> : (
              <View style={[styles.logo, { alignItems: "center", justifyContent: "center" }]}><MaterialCommunityIcons name="account-group" size={24} color={colors.brand} /></View>
            )}
            <View style={{ flex: 1 }}>
              <Text style={styles.groupName}>{owned.name}</Text>
              <Text style={styles.groupMeta}>{owned.member_count} members · you're the Owner</Text>
            </View>
            <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
          </Pressable>
        ) : (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyText}>You don't own a Group yet.</Text>
            <View style={{ marginTop: 12 }}>
              <PrimaryButton label="Create Group (200 Astrans)" icon="plus" onPress={() => router.push("/group/create")} />
            </View>
          </View>
        )}

        {memberships.length > 0 ? (
          <>
            <Text style={[styles.sectionLabel, { marginTop: 24 }]}>MEMBER OF</Text>
            {memberships.map(m => (
              <Pressable key={m.group_id} onPress={() => router.push({ pathname: "/group/[id]", params: { id: m.group_id } })} style={styles.groupCard}>
                {m.logo_url ? <Image source={{ uri: m.logo_url }} style={styles.logo} contentFit="cover" /> : (
                  <View style={[styles.logo, { alignItems: "center", justifyContent: "center" }]}><MaterialCommunityIcons name="account-group" size={24} color={colors.brand} /></View>
                )}
                <View style={{ flex: 1 }}>
                  <Text style={styles.groupName}>{m.name}</Text>
                  <Text style={styles.groupMeta}>{m.role === "admin" ? "Admin" : "Member"}</Text>
                </View>
                <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
              </Pressable>
            ))}
          </>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center" },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md },
  headerTitle: { color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  sectionLabel: { color: colors.onSurface3, fontSize: 11, fontWeight: "800", letterSpacing: 1.5, marginBottom: 8 },
  groupCard: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: colors.surface2, padding: 14, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, marginBottom: 10 },
  logo: { width: 48, height: 48, borderRadius: radius.md, backgroundColor: colors.surface3 },
  groupName: { color: colors.onSurface, fontWeight: "800", fontSize: 15 },
  groupMeta: { color: colors.onSurface3, fontSize: 11, marginTop: 2 },
  emptyCard: { backgroundColor: colors.surface2, padding: 20, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  emptyText: { color: colors.onSurface2, fontSize: 13, textAlign: "center" },
});