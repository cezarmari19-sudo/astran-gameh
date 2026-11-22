import React, { useState } from "react";
import { Text, ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams } from "expo-router";
import { colors, spacing, type } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";
import { useI18n } from "@/src/i18n";
import { as, errText } from "@/src/i18n/ageStrings";
import { parentalApi } from "@/src/api/age";

// Retragerea acordului din linkul (single-use) primit pe email dupa aprobare.
export default function ParentalWithdraw() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const { lang } = useI18n();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(token ? null : as(lang, "pd_missing"));
  async function go() {
    setBusy(true); setErr(null);
    try { await parentalApi.withdrawByToken(String(token)); setDone(true); } catch (e: any) { setErr(errText(lang, e)); } finally { setBusy(false); }
  }
  return (
    <SafeAreaView style={styles.root}><ScrollView contentContainerStyle={styles.pad}>
      <Text style={styles.h1}>{as(lang, "pw_title")}</Text>
      <Text style={styles.p}>{done ? as(lang, "pw_done") : as(lang, "pw_intro")}</Text>
      {err ? <Text style={styles.err} testID="withdraw-error">{err}</Text> : null}
      {!done && token ? <View style={{ marginTop: spacing.xl }}><PrimaryButton testID="withdraw-confirm" label={as(lang, "pw_confirm")} onPress={go} disabled={busy} /></View> : null}
    </ScrollView></SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, p: { ...type.body, color: colors.onSurface2, marginTop: spacing.md, lineHeight: 20 }, err: { color: colors.error, marginTop: spacing.md },
});
