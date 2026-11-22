import React, { useCallback, useEffect, useState } from "react";
import { View, Text, ScrollView, StyleSheet, Pressable, Linking } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing, type } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { as, errText } from "@/src/i18n/ageStrings";
import { parentalApi, Acks } from "@/src/api/age";

// Panou parental: cont logat (email verificat, adult). Fiecare actiune e re-autorizata de server (link + email potrivit).
const fmt = (d?: string | null) => (d ? new Date(d).toLocaleString() : "—");
const ALL_ACKS: Acks = { read_notice: true, is_parent_or_guardian: true, understands_withdrawal: true };

export default function ParentalPanel() {
  const { claim } = useLocalSearchParams<{ claim?: string }>();
  const { lang } = useI18n();
  const router = useRouter();
  const { user } = useAuth();
  const [data, setData] = useState<{ children: any[]; pending_requests: any[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [ackFor, setAckFor] = useState<string | null>(null);      // consent_id pentru care se afiseaza confirmarile de aprobare
  const [acks, setAcks] = useState<Acks>({ read_notice: false, is_parent_or_guardian: false, understands_withdrawal: false });
  const [eraseFor, setEraseFor] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setData((await parentalApi.children()) as any); } catch (e: any) { setErr(errText(lang, e)); }
  }, [lang]);
  useEffect(() => {
    if (!user) return;
    (async () => {
      if (claim) {
        try { await parentalApi.claim(String(claim)); setInfo(as(lang, "pp_claim_ok")); }
        catch (e: any) { setErr(`${as(lang, "pp_claim_fail")}: ${errText(lang, e)}`); }
      }
      await load();
    })();
  }, [user, claim, lang, load]);

  async function run(fn: () => Promise<any>, after?: (r: any) => void) {
    setErr(null); setInfo(null);
    try { const r = await fn(); after?.(r); await load(); } catch (e: any) { setErr(errText(lang, e)); }
  }

  if (!user) {
    return (
      <SafeAreaView style={styles.root}><ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{as(lang, "pp_title")}</Text>
        <Text style={styles.p}>{as(lang, "pp_need_login")}</Text>
        <View style={{ marginTop: spacing.lg }}><PrimaryButton testID="panel-login" label={as(lang, "pp_login")} onPress={() => router.replace("/(auth)/login")} /></View>
      </ScrollView></SafeAreaView>
    );
  }
  const Card = ({ c, pending }: { c: any; pending?: boolean }) => (
    <View style={styles.card} testID={`child-${c.consent_id}`}>
      <Text style={styles.h3}>{c.child?.display_name || c.child?.username}</Text>
      <Text style={styles.small}>{as(lang, "pp_state")}: {as(lang, ("st_" + c.state) as any)}</Text>
      <Text style={styles.small}>{as(lang, "pp_requested")}: {fmt(c.requested_at)}</Text>
      <Text style={styles.small}>{as(lang, "pp_approved")}: {fmt(c.approved_at)}{c.withdrawn_at ? ` · ${as(lang, "pp_withdrawn")}: ${fmt(c.withdrawn_at)}` : ""}</Text>
      <Text style={styles.small}>{as(lang, "pp_notice")}: v{c.notice_version} · {as(lang, "pp_method")}: {c.verification_method ?? "—"}</Text>
      {c.state === "PENDING_PARENTAL_CONSENT" ? (
        ackFor === c.consent_id ? (
          <View style={{ gap: 8, marginTop: 8 }}>
            {(["read_notice", "is_parent_or_guardian", "understands_withdrawal"] as (keyof Acks)[]).map((k) => (
              <Pressable key={k} onPress={() => setAcks((a) => ({ ...a, [k]: !a[k] }))} style={styles.ackRow}>
                <MaterialCommunityIcons name={acks[k] ? "checkbox-marked" : "checkbox-blank-outline"} size={22} color={acks[k] ? colors.brand : colors.onSurface3} />
                <Text style={styles.ackText}>{as(lang, k === "read_notice" ? "pd_ack_read" : k === "is_parent_or_guardian" ? "pd_ack_parent" : "pd_ack_withdraw")}</Text>
              </Pressable>
            ))}
            <PrimaryButton testID={`approve-${c.consent_id}`} label={as(lang, "pp_approve")} disabled={!(acks.read_notice && acks.is_parent_or_guardian && acks.understands_withdrawal)}
              onPress={() => run(() => parentalApi.panelDecision(c.consent_id, "approve", acks), (r) => { setAckFor(null); if (r?.verification?.redirect_url) Linking.openURL(r.verification.redirect_url); else setInfo(as(lang, "pd_verifier_missing")); })} />
          </View>
        ) : <View style={{ marginTop: 8, gap: 8 }}>
          <PrimaryButton testID={`start-approve-${c.consent_id}`} label={as(lang, "pp_approve")} onPress={() => setAckFor(c.consent_id)} />
          <SecondaryButton testID={`reject-${c.consent_id}`} label={as(lang, "pp_reject")} onPress={() => run(() => parentalApi.panelDecision(c.consent_id, "reject", ALL_ACKS))} />
        </View>
      ) : null}
      {!pending && c.state === "PARENTAL_VERIFICATION_REQUIRED" ? <View style={{ marginTop: 8 }}><SecondaryButton label={as(lang, "pp_restart")} onPress={() => run(() => parentalApi.restartVerification(c.consent_id), (r) => { if (r?.verification?.redirect_url) Linking.openURL(r.verification.redirect_url); else setInfo(as(lang, "pd_verifier_missing")); })} /></View> : null}
      {!pending ? (
        <View style={{ marginTop: 8, gap: 8 }}>
          {c.state !== "PARENTAL_CONSENT_WITHDRAWN" && c.state !== "PARENTAL_CONSENT_REJECTED" ? <SecondaryButton testID={`withdraw-${c.consent_id}`} label={as(lang, "pp_withdraw")} onPress={() => run(() => parentalApi.withdraw(c.consent_id))} /> : null}
          <SecondaryButton testID={`info-${c.consent_id}`} label={as(lang, "pp_info")} onPress={() => run(() => parentalApi.dataRequest(c.consent_id, "access"), (r) => setInfo(`${as(lang, "pp_summary")}: ${(r.summary?.categories || []).join("; ")} · ${JSON.stringify(r.summary?.counts || {})}`))} />
          {eraseFor === c.consent_id ? (
            <PrimaryButton testID={`erase-confirm-${c.consent_id}`} label={as(lang, "pp_erase_confirm")} onPress={() => run(() => parentalApi.dataRequest(c.consent_id, "erasure", true), (r) => { setEraseFor(null); setInfo(`${as(lang, "pp_req_ok")}: ${r.status}`); })} />
          ) : <SecondaryButton testID={`erase-${c.consent_id}`} label={as(lang, "pp_erase")} onPress={() => setEraseFor(c.consent_id)} />}
        </View>
      ) : null}
    </View>
  );
  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.pad}>
        <Text style={styles.h1}>{as(lang, "pp_title")}</Text>
        {info ? <Text style={styles.ok} testID="panel-info">{info}</Text> : null}
        {err ? <Text style={styles.err} testID="panel-error">{err}</Text> : null}
        <Text style={[styles.h3, { marginTop: spacing.xl }]}>{as(lang, "pp_pending")}</Text>
        {(data?.pending_requests ?? []).map((c) => <Card key={c.consent_id} c={c} pending />)}
        <Text style={[styles.h3, { marginTop: spacing.xl }]}>{as(lang, "pp_children")}</Text>
        {data && data.children.length === 0 ? <Text style={styles.p}>{as(lang, "pp_none")}</Text> : null}
        {(data?.children ?? []).map((c) => <Card key={c.consent_id} c={c} />)}
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, h3: { ...type.h3, color: colors.onSurface }, p: { ...type.body, color: colors.onSurface2, marginTop: 6 },
  small: { ...type.small, color: colors.onSurface3, marginTop: 3 }, ok: { color: colors.success, marginTop: spacing.md }, err: { color: colors.error, marginTop: spacing.md },
  card: { marginTop: spacing.md, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  ackRow: { flexDirection: "row", alignItems: "center", gap: 8 }, ackText: { flex: 1, color: colors.onSurface2, fontSize: 13 },
});
