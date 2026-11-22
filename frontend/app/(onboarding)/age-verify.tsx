import React, { useCallback, useEffect, useState } from "react";
import { View, Text, ScrollView, StyleSheet, Linking } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { colors, radius, spacing, type } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { as, errText } from "@/src/i18n/ageStrings";
import { ageApi, AgeState } from "@/src/api/age";

// Verificarea vârstei pentru 18+ (age assurance). Rezultatul vine DOAR de la provider -> server; aici nu se poate "bifa".
export default function AgeVerify() {
  const { lang } = useI18n();
  const router = useRouter();
  const { refresh } = useAuth();
  const [st, setSt] = useState<AgeState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { const s = await ageApi.me(); setSt(s); if (s.age_assurance === "verified") await refresh(); } catch (e: any) { setErr(errText(lang, e)); }
  }, [lang, refresh]);
  useEffect(() => { load(); }, [load]);

  async function start() {
    setBusy(true); setErr(null);
    try {
      const r = await ageApi.startAssurance();
      if (r.redirect_url) await Linking.openURL(r.redirect_url);
      await load();
    } catch (e: any) { setErr(errText(lang, e)); } finally { setBusy(false); }
  }

  const minor = !!st && (st.age_band === "under_16" || st.age_band === "16_17");
  const status = st?.age_assurance ?? "none";
  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{as(lang, "av_title")}</Text>
        <Text style={styles.p}>{as(lang, "av_intro")}</Text>
        <View style={styles.card} testID="assurance-status">
          <Text style={styles.cardText}>{as(lang, status === "verified" ? "av_status_verified" : status === "pending" ? "av_status_pending" : status === "failed" ? "av_status_failed" : "av_status_none")}</Text>
        </View>
        {minor ? <Text style={styles.warn}>{as(lang, "av_minor")}</Text>
          : st?.needs_age_info ? <Text style={styles.warn}>{as(lang, "av_needs_dob")}</Text>
          : st && !st.age_assurance_available ? <Text style={styles.warn} testID="assurance-unavailable">{as(lang, "av_unavailable")}</Text>
          : status !== "verified" ? <View style={{ marginTop: spacing.lg }}><PrimaryButton testID="assurance-start" label={as(lang, "av_start")} onPress={start} disabled={busy} /></View> : null}
        {err ? <Text style={styles.err} testID="assurance-error">{err}</Text> : null}
        <View style={{ marginTop: spacing.lg, gap: spacing.sm }}>
          <SecondaryButton label={as(lang, "refresh")} onPress={load} />
          <SecondaryButton label={as(lang, "back")} onPress={() => (router.canGoBack() ? router.back() : router.replace("/(tabs)"))} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, p: { ...type.body, color: colors.onSurface2, marginTop: spacing.md, lineHeight: 20 },
  card: { marginTop: spacing.lg, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  cardText: { ...type.h3, color: colors.onSurface }, warn: { ...type.body, color: colors.warning, marginTop: spacing.lg }, err: { color: colors.error, marginTop: spacing.md },
});
