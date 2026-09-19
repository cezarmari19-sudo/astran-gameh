import React, { useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";

export default function SettingsScreen() {
  const router = useRouter();
  const { user, refresh } = useAuth();
  const { t } = useI18n();
  const [graphics, setGraphics] = useState(2);
  const [distance, setDistance] = useState(2);
  const [ageCat, setAgeCat] = useState<"under_18" | "adult_18">(user?.age_category || "under_18");

  async function changeAge(v: "under_18" | "adult_18") {
    setAgeCat(v);
    await api("/users/me", { method: "PATCH", body: JSON.stringify({ age_category: v }) });
    await refresh();
  }

  const Slider = ({ value, onChange, testID }: any) => (
    <View style={styles.sliderRow} testID={testID}>
      {[0, 1, 2, 3, 4].map(i => (
        <Pressable key={i} onPress={() => onChange(i)} style={[styles.slot, value >= i && styles.slotOn]} testID={`${testID}-${i}`} />
      ))}
    </View>
  );

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} testID="settings-back"><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title}>{t("settings")}</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }}>
        <Text style={styles.section}>{t("language").toUpperCase()}</Text>
        <Pressable onPress={() => router.push("/language")} style={styles.row} testID="settings-language">
          <MaterialCommunityIcons name="translate" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{t("language")}</Text>
          <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
        </Pressable>

        <Text style={styles.section}>{t("age_select_title").toUpperCase()}</Text>
        <View style={styles.ageRow}>
          <Pressable testID="settings-age-under-18" onPress={() => changeAge("under_18")} style={[styles.ageBtn, ageCat === "under_18" && styles.ageBtnActive]}>
            <Text style={[styles.ageBtnText, ageCat === "under_18" && { color: colors.brand }]}>{t("age_under_18")}</Text>
          </Pressable>
          <Pressable testID="settings-age-18-plus" onPress={() => changeAge("adult_18")} style={[styles.ageBtn, ageCat === "adult_18" && styles.ageBtnActive]}>
            <Text style={[styles.ageBtnText, ageCat === "adult_18" && { color: colors.brand }]}>{t("age_18_plus")}</Text>
          </Pressable>
        </View>

        <Text style={styles.section}>{t("graphics").toUpperCase()}</Text>
        <Slider value={graphics} onChange={setGraphics} testID="graphics-slider" />
        <Text style={styles.hint}>Independent from view distance</Text>

        <Text style={styles.section}>{t("view_distance").toUpperCase()}</Text>
        <Slider value={distance} onChange={setDistance} testID="distance-slider" />
        {graphics >= 3 && distance >= 3 ? (
          <Text style={styles.warn}>⚠ High graphics + high view distance can reduce performance on older devices.</Text>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  section: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 2, marginTop: 22, marginBottom: 10 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  rowText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  ageRow: { flexDirection: "row", gap: 8 },
  ageBtn: { flex: 1, padding: 14, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  ageBtnActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  ageBtnText: { color: colors.onSurface2, fontWeight: "700" },
  sliderRow: { flexDirection: "row", gap: 6 },
  slot: { flex: 1, height: 14, borderRadius: 7, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  slotOn: { backgroundColor: colors.brand, borderColor: colors.brand },
  hint: { color: colors.onSurface3, fontSize: 11, marginTop: 6 },
  warn: { color: colors.warning, fontSize: 12, marginTop: 8, fontWeight: "600" },
});