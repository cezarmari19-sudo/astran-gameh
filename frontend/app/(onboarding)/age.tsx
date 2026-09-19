import React, { useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function AgeOnboardingScreen() {
  const router = useRouter();
  const { user, refresh } = useAuth();
  const { t } = useI18n();
  const [choice, setChoice] = useState<"under_18" | "adult_18">(user?.age_category || "under_18");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await api("/users/me", { method: "PATCH", body: JSON.stringify({ age_category: choice }) });
      await refresh();
      router.replace("/(tabs)");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>{t("age_select_title")}</Text>
        <Text style={styles.sub}>{t("age_select_subtitle")}</Text>

        <Pressable
          testID="onboarding-age-under-18"
          onPress={() => setChoice("under_18")}
          style={[styles.card, choice === "under_18" && styles.cardActive]}
        >
          <View style={styles.cardHead}>
            <MaterialCommunityIcons name="shield-check" size={26} color={choice === "under_18" ? colors.brand : colors.onSurface2} />
            <Text style={[styles.cardTitle, choice === "under_18" && { color: colors.brand }]}>{t("age_under_18")}</Text>
          </View>
          <Text style={styles.cardDesc}>{t("age_under_desc")}</Text>
        </Pressable>

        <Pressable
          testID="onboarding-age-18-plus"
          onPress={() => setChoice("adult_18")}
          style={[styles.card, choice === "adult_18" && styles.cardActive]}
        >
          <View style={styles.cardHead}>
            <MaterialCommunityIcons name="fire" size={26} color={choice === "adult_18" ? colors.brand : colors.onSurface2} />
            <Text style={[styles.cardTitle, choice === "adult_18" && { color: colors.brand }]}>{t("age_18_plus")}</Text>
          </View>
          <Text style={styles.cardDesc}>{t("age_18_desc")}</Text>
        </Pressable>
      </ScrollView>
      <View style={styles.footer}>
        <PrimaryButton testID="onboarding-continue" label={busy ? "..." : t("continue")} onPress={save} disabled={busy} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.lg, paddingTop: 40, gap: 16 },
  title: { color: colors.onSurface, fontSize: 26, fontWeight: "900", letterSpacing: -0.3 },
  sub: { color: colors.onSurface3, fontSize: 13, marginBottom: 12 },
  card: {
    padding: spacing.lg, borderRadius: radius.lg, backgroundColor: colors.surface2,
    borderWidth: 1.5, borderColor: colors.border, gap: 10,
  },
  cardActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  cardTitle: { color: colors.onSurface, fontSize: 22, fontWeight: "900", letterSpacing: -0.3 },
  cardDesc: { color: colors.onSurface2, fontSize: 13 },
  footer: { padding: spacing.lg, borderTopWidth: 1, borderTopColor: colors.border },
});