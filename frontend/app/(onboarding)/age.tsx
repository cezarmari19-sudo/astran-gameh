import React, { useState } from "react";
import { View, Text, ScrollView, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { colors, spacing, type } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import DateOfBirthPicker, { Dob, EMPTY_DOB, dobToIso, dobClientError, previewBand } from "@/src/components/DateOfBirthPicker";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { as, errText } from "@/src/i18n/ageStrings";
import { ageApi } from "@/src/api/age";

// Conturi FARA data nasterii (create inainte de aceasta functie, sau prin Google): data se introduce O SINGURA DATA.
// Nu mai exista alegerea "Sub 18 / 18+": categoria o calculeaza serverul, iar 18+ se obtine doar prin age assurance.
export default function AgeInfoScreen() {
  const { lang } = useI18n();
  const router = useRouter();
  const { refresh, logout } = useAuth();
  const [dob, setDob] = useState<Dob>(EMPTY_DOB);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const band = previewBand(dob);

  async function save() {
    const ce = dobClientError(dob, lang);
    if (ce) { setErr(ce); return; }
    setBusy(true); setErr(null);
    try {
      await ageApi.setDateOfBirth(dobToIso(dob)!);
      await refresh();
      const st = await ageApi.me();
      router.replace(st.restricted ? "/(onboarding)/parental-wait" : "/(tabs)");
    } catch (e: any) {
      setErr(errText(lang, e));
    } finally { setBusy(false); }
  }

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{as(lang, "legacy_title")}</Text>
        <Text style={styles.p}>{as(lang, "legacy_intro")}</Text>
        <Text style={styles.label}>{as(lang, "dob_title")}</Text>
        <DateOfBirthPicker value={dob} onChange={setDob} lang={lang} testPrefix="age-dob" />
        <Text style={styles.hint}>{as(lang, "dob_why")}</Text>
        {band ? <Text style={styles.explain} testID="age-explain">{band === "under_16" ? as(lang, "band_under16") : band === "16_17" ? as(lang, "band_16_17") : as(lang, "band_adult")}</Text> : null}
        {err ? <Text style={styles.err} testID="age-error">{err}</Text> : null}
        <View style={{ marginTop: spacing.xl }}>
          <PrimaryButton testID="age-save" label={busy ? as(lang, "saving") : as(lang, "save")} onPress={save} disabled={busy} />
        </View>
        <View style={{ marginTop: spacing.md }}>
          <SecondaryButton testID="age-logout" label={as(lang, "logout")} onPress={async () => { await logout(); router.replace("/"); }} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, p: { ...type.body, color: colors.onSurface2, marginTop: spacing.md, lineHeight: 20 },
  label: { ...type.micro, color: colors.onSurface3, textTransform: "uppercase", marginTop: spacing.xl, marginBottom: spacing.sm },
  hint: { color: colors.onSurface3, fontSize: 12, marginTop: 8, lineHeight: 17 }, explain: { color: colors.brand, fontSize: 13, marginTop: 10, lineHeight: 18 },
  err: { color: colors.error, marginTop: spacing.md },
});
