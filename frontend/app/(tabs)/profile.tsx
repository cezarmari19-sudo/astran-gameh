import React from "react";
import { View, Text, StyleSheet, Pressable, ScrollView } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { AstransPill } from "@/src/components/ui";
import { colors, radius, spacing } from "@/src/theme";

export default function ProfileScreen() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const { t } = useI18n();

  const Row = ({ icon, label, onPress, testID, danger }: any) => (
    <Pressable testID={testID} onPress={onPress} style={styles.row}>
      <MaterialCommunityIcons name={icon} size={22} color={danger ? colors.error : colors.brand} />
      <Text style={[styles.rowText, danger && { color: colors.error }]}>{label}</Text>
      <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
    </Pressable>
  );

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }}>
        <View style={styles.headerCard}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{(user?.display_name || user?.username || "?")[0]?.toUpperCase()}</Text>
          </View>
          <Text style={styles.name} testID="profile-name">{user?.display_name || user?.username}</Text>
          <Text style={styles.uname}>@{user?.username}</Text>
          <View style={styles.badges}>
            {user?.is_platform_owner ? (
              <View style={[styles.badge, { backgroundColor: colors.ownerRed }]}><Text style={styles.badgeText}>OWNER</Text></View>
            ) : null}
            {user?.is_platform_admin && !user?.is_platform_owner ? (
              <View style={[styles.badge, { backgroundColor: colors.ownerRed }]}><Text style={styles.badgeText}>ADMIN</Text></View>
            ) : null}
            <View style={[styles.badge, { backgroundColor: user?.age_category === "adult_18" ? colors.error : colors.brandTint, borderColor: user?.age_category === "adult_18" ? colors.error : colors.brand, borderWidth: 1 }]}>
              <Text style={[styles.badgeText, { color: user?.age_category === "adult_18" ? "#fff" : colors.brand }]}>
                {user?.age_category === "adult_18" ? "18+" : "UNDER 18"}
              </Text>
            </View>
          </View>
          <View style={{ marginTop: 16 }}>
            <AstransPill balance={user?.astrans_balance || 0} onPress={() => router.push("/wallet")} />
          </View>
        </View>

        <Row icon="wallet-outline" label={t("wallet")} onPress={() => router.push("/wallet")} testID="profile-wallet" />
        <Row icon="cog-outline" label={t("settings")} onPress={() => router.push("/settings")} testID="profile-settings" />
        <Row icon="translate" label={t("language")} onPress={() => router.push("/language")} testID="profile-language" />
        <Row icon="logout" label={t("logout")} onPress={async () => { await logout(); router.replace("/(auth)/login"); }} testID="profile-logout" danger />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  headerCard: { alignItems: "center", padding: spacing.xl, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, marginBottom: spacing.lg },
  avatar: { width: 80, height: 80, borderRadius: 40, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  avatarText: { color: colors.onBrand, fontSize: 32, fontWeight: "900" },
  name: { color: colors.onSurface, fontSize: 20, fontWeight: "800", marginTop: 12 },
  uname: { color: colors.onSurface3, fontSize: 13, marginTop: 2 },
  badges: { flexDirection: "row", gap: 6, marginTop: 10 },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: radius.sm },
  badgeText: { color: "#fff", fontWeight: "900", fontSize: 10, letterSpacing: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: 14, backgroundColor: colors.surface2, padding: 16, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  rowText: { flex: 1, color: colors.onSurface, fontWeight: "700", fontSize: 14 },
});