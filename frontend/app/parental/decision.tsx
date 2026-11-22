import React, { useEffect, useState } from "react";
import { View, Text, ScrollView, StyleSheet, Pressable, Linking, ActivityIndicator } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing, type } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { useI18n } from "@/src/i18n";
import { as, errText } from "@/src/i18n/ageStrings";
import { parentalApi, Acks } from "@/src/api/age";

// Pagina deschisa din linkul din email. Tokenul vine din URL, dar decizia se trimite in CORPUL unui POST (single-use).
// IMPORTANT: aprobarea de aici NU activeaza contul copilului; mai e necesara verificarea suplimentara a calitatii de parinte.
export default function ParentalDecision() {
  const { token } = useLocalSearchParams<{ token?: string }>();
  const { lang } = useI18n();
  const router = useRouter();
  const [data, setData] = useState<any>(null);
  const [acks, setAcks] = useState<Acks>({ read_notice: false, is_parent_or_guardian: false, understands_withdrawal: false });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<null | { kind: "reject" | "approve"; res?: any }>(null);

  useEffect(() => {
    if (!token) { setErr(as(lang, "pd_missing")); return; }
    parentalApi.notice(String(token), lang).then(setData).catch((e) => setErr(errText(lang, e)));
  }, [token, lang]);

  async function decide(decision: "approve" | "reject") {
    setBusy(true); setErr(null);
    try {
      const res = await parentalApi.decide(String(token), decision, acks);
      setDone({ kind: decision, res });
    } catch (e: any) { setErr(errText(lang, e)); } finally { setBusy(false); }
  }
  const allAcked = acks.read_notice && acks.is_parent_or_guardian && acks.understands_withdrawal;
  const toggle = (k: keyof Acks) => setAcks((a) => ({ ...a, [k]: !a[k] }));

  if (done) {
    const r = done.res;
    return (
      <SafeAreaView style={styles.root}><ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{as(lang, "pd_title")}</Text>
        {done.kind === "reject" ? <Text style={styles.p} testID="decision-done">{as(lang, "pd_done_reject")}</Text> : (
          <>
            <Text style={styles.p} testID="decision-done">{as(lang, "pd_done_approve_pending")}</Text>
            {r?.verification?.redirect_url ? (
              <View style={{ marginTop: spacing.lg }}><PrimaryButton testID="verify-parent" label={as(lang, "pd_verify_cta")} onPress={() => Linking.openURL(r.verification.redirect_url)} /></View>
            ) : <Text style={styles.warn} testID="verifier-missing">{as(lang, "pd_verifier_missing")}</Text>}
            {r?.claim_token ? <View style={{ marginTop: spacing.md }}><SecondaryButton testID="open-panel" label={as(lang, "pd_open_panel")} onPress={() => router.push({ pathname: "/parental/panel", params: { claim: r.claim_token } })} /></View> : null}
          </>
        )}
      </ScrollView></SafeAreaView>
    );
  }
  if (!data) {
    return <SafeAreaView style={styles.center}>{err ? <Text style={styles.err} testID="decision-error">{err}</Text> : <ActivityIndicator color={colors.brand} />}</SafeAreaView>;
  }
  const n = data.notice;
  const Box = ({ k, label }: { k: keyof Acks; label: string }) => (
    <Pressable testID={`ack-${k}`} onPress={() => toggle(k)} style={styles.ackRow} accessibilityRole="checkbox" accessibilityState={{ checked: acks[k] }}>
      <MaterialCommunityIcons name={acks[k] ? "checkbox-marked" : "checkbox-blank-outline"} size={24} color={acks[k] ? colors.brand : colors.onSurface3} />
      <Text style={styles.ackText}>{label}</Text>
    </Pressable>
  );
  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{n.title}</Text>
        <Text style={styles.small}>{as(lang, "pd_child")}: {data.child_label} · v{n.version}</Text>
        <Text style={styles.warn}>{as(lang, "pd_note_email")}</Text>
        {([["pd_what", n.what], ["pd_data", n.data], ["pd_purposes", n.purposes], ["pd_features", n.features], ["pd_how", n.how], ["pd_withdraw", n.withdraw]] as const).map(([k, v]) => (
          <View key={k} style={{ marginTop: spacing.lg }}><Text style={styles.h3}>{as(lang, k)}</Text><Text style={styles.p}>{v}</Text></View>
        ))}
        <View style={{ marginTop: spacing.lg }}>
          <Text style={styles.h3}>{as(lang, "pd_basis")}</Text>
          {n.activities.map((a: any) => <Text key={a.id} style={styles.small}>• {a.text} — {a.basis}{a.note ? ` (${a.note})` : ""}</Text>)}
        </View>
        <View style={{ marginTop: spacing.xl, gap: spacing.sm }}>
          <Box k="read_notice" label={as(lang, "pd_ack_read")} />
          <Box k="is_parent_or_guardian" label={as(lang, "pd_ack_parent")} />
          <Box k="understands_withdrawal" label={as(lang, "pd_ack_withdraw")} />
        </View>
        {err ? <Text style={styles.err} testID="decision-error">{err}</Text> : null}
        <View style={{ marginTop: spacing.xl, gap: spacing.sm }}>
          <PrimaryButton testID="decision-approve" label={as(lang, "pd_approve")} onPress={() => decide("approve")} disabled={busy || !allAcked} />
          <SecondaryButton testID="decision-reject" label={as(lang, "pd_reject")} onPress={() => decide("reject")} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl }, center: { flex: 1, backgroundColor: colors.surface, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, h3: { ...type.h3, color: colors.onSurface }, p: { ...type.body, color: colors.onSurface2, marginTop: 6, lineHeight: 20 },
  small: { ...type.small, color: colors.onSurface3, marginTop: 4, lineHeight: 17 }, warn: { ...type.body, color: colors.warning, marginTop: spacing.md, lineHeight: 19 }, err: { color: colors.error, marginTop: spacing.md },
  ackRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: colors.surface2, borderRadius: radius.md, padding: 12 }, ackText: { flex: 1, color: colors.onSurface2 },
});
