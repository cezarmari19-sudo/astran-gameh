import React, { useCallback, useEffect, useState } from "react";
import { View, Text, ScrollView, StyleSheet, Linking } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { colors, radius, spacing, type } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { useI18n } from "@/src/i18n";
import { errText } from "@/src/i18n/ageStrings";
import { ass } from "@/src/i18n/ageStatusStrings";
import { ageApi, Entitlements, FeatureKey } from "@/src/api/age";

// Pagina „Vârstă și acces”. TOT ce se afișează vine de la server (GET /age/entitlements); clientul nu calculează și nu poate
// schimba nimic: fiecare funcție e reverificată pe backend la fiecare cerere.
const ORDER: FeatureKey[] = ["play", "friends", "block", "dm", "groups", "wallet_view", "shop_browse", "shop_purchase",
  "real_money_purchase", "transfer", "game_publish", "creator_monetization", "adult_content"];

export default function AgeStatus() {
  const { lang } = useI18n();
  const router = useRouter();
  const [ent, setEnt] = useState<Entitlements | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setEnt(await ageApi.entitlements()); setErr(null); } catch (e: any) { setErr(errText(lang, e)); }
  }, [lang]);
  useEffect(() => { load(); }, [load]);

  async function startVerification() {
    setBusy(true); setErr(null);
    try {
      const r = await ageApi.startAssurance();
      if (r.redirect_url) await Linking.openURL(r.redirect_url);
      await load();
    } catch (e: any) { setErr(errText(lang, e)); } finally { setBusy(false); }
  }

  const status = ent?.ui_status;
  const band = ent?.declared_age_band;
  const allowed = ent ? ORDER.filter((f) => ent.features[f]?.allowed) : [];
  const denied = ent ? ORDER.filter((f) => !ent.features[f]?.allowed) : [];
  const nextActions = new Set(denied.map((f) => ent!.features[f].next_action).filter(Boolean) as string[]);
  const canVerify = nextActions.has("verify_age_16") || nextActions.has("verify_age_18");
  const needParent = nextActions.has("parent_email") || status === "parental_consent_revoked";

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{ass(lang, "title")}</Text>
        {!ent ? (err ? <Text style={styles.err} testID="age-status-error">{err}</Text> : null) : (
          <>
            <View style={styles.card}>
              <Text style={styles.label}>{ass(lang, "declared")}</Text>
              <Text style={styles.value} testID="age-declared">{ass(lang, band ? "band_" + band : "band_none")}</Text>
              <Text style={[styles.label, { marginTop: spacing.md }]}>{ass(lang, "status")}</Text>
              <Text style={styles.value} testID="age-ui-status">{ass(lang, "st_" + status)}</Text>
              {status === "verification_pending" ? <Text style={styles.note}>{ass(lang, "pending_note")}</Text> : null}
              {status === "verification_failed" ? <Text style={styles.note}>{ass(lang, "failed_note")}</Text> : null}
              {status === "parental_consent_revoked" ? <Text style={styles.note}>{ass(lang, "revoked_note")}</Text> : null}
              {ent.valid_scopes.length ? <Text style={styles.note}>{ass(lang, "scopes")}: {ent.valid_scopes.join(", ")}</Text> : null}
            </View>

            <Text style={styles.h2}>{ass(lang, "available")}</Text>
            {allowed.map((f) => <Text key={f} style={styles.ok} testID={"feat-ok-" + f}>✓ {ass(lang, "f_" + f)}</Text>)}

            <Text style={styles.h2}>{ass(lang, "restricted")}</Text>
            {denied.map((f) => (
              <View key={f} style={styles.deniedRow} testID={"feat-no-" + f}>
                <Text style={styles.no}>✕ {ass(lang, "f_" + f)}</Text>
                <Text style={styles.reason}>{ass(lang, "r_" + ent.features[f].reason)}</Text>
              </View>
            ))}

            {canVerify && !ent.age_assurance_available ? <Text style={styles.warn} testID="verification-unavailable">{ass(lang, "unavailable")}</Text> : null}
            <View style={{ marginTop: spacing.lg, gap: spacing.sm }}>
              {canVerify && ent.age_assurance_available && status !== "verification_pending"
                ? <PrimaryButton testID="age-start-verification" label={ass(lang, "next_verify")} onPress={startVerification} disabled={busy} /> : null}
              {nextActions.has("enter_date_of_birth")
                ? <SecondaryButton label={ass(lang, "next_dob")} onPress={() => router.push("/(onboarding)/age")} /> : null}
              {needParent ? <SecondaryButton label={ass(lang, "next_parent")} onPress={() => router.push("/(onboarding)/parental-wait")} /> : null}
            </View>
            {err ? <Text style={styles.err} testID="age-status-error">{err}</Text> : null}
          </>
        )}
        <View style={{ marginTop: spacing.lg, gap: spacing.sm }}>
          <SecondaryButton label={ass(lang, "refresh")} onPress={load} />
          <SecondaryButton label={ass(lang, "back")} onPress={() => (router.canGoBack() ? router.back() : router.replace("/(tabs)"))} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, h2: { ...type.h2, color: colors.onSurface, marginTop: spacing.xl, marginBottom: spacing.sm },
  card: { marginTop: spacing.lg, padding: spacing.lg, borderRadius: radius.md, backgroundColor: colors.surface2 },
  label: { ...type.body, color: colors.onSurface2 }, value: { ...type.h2, color: colors.onSurface },
  note: { ...type.body, color: colors.onSurface2, marginTop: spacing.sm, lineHeight: 20 },
  ok: { ...type.body, color: colors.onSurface, marginTop: 4 },
  deniedRow: { marginTop: spacing.sm }, no: { ...type.body, color: colors.onSurface }, reason: { ...type.body, color: colors.onSurface2, marginLeft: spacing.lg, lineHeight: 20 },
  warn: { ...type.body, color: colors.onSurface2, marginTop: spacing.lg, lineHeight: 20 }, err: { ...type.body, color: colors.error, marginTop: spacing.md },
});
