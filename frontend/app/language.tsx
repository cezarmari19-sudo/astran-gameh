import React from "react";
import { View, Text, StyleSheet, Pressable, FlatList } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useI18n, LANGUAGES } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";

export default function LanguageScreen() {
  const router = useRouter();
  const { lang, setLang, t } = useI18n();

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable onPress={() => router.back()} testID="lang-back"><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
        <Text style={styles.title}>{t("language")}</Text>
        <View style={{ width: 26 }} />
      </View>
      <FlatList
        data={LANGUAGES}
        keyExtractor={(l) => l.code}
        contentContainerStyle={{ padding: spacing.lg }}
        renderItem={({ item }) => (
          <Pressable testID={`lang-${item.code}`} onPress={() => { setLang(item.code); router.back(); }} style={[styles.row, lang === item.code && styles.rowActive]}>
            <View style={{ flex: 1 }}>
              <Text style={styles.native}>{item.native}</Text>
              <Text style={styles.label}>{item.label}</Text>
            </View>
            {lang === item.code ? <MaterialCommunityIcons name="check-circle" size={22} color={colors.brand} /> : null}
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  row: { flexDirection: "row", alignItems: "center", padding: 14, backgroundColor: colors.surface2, borderRadius: radius.md, marginBottom: 8, borderWidth: 1, borderColor: colors.border },
  rowActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  native: { color: colors.onSurface, fontWeight: "800", fontSize: 15 },
  label: { color: colors.onSurface3, fontSize: 12, marginTop: 2 },
});