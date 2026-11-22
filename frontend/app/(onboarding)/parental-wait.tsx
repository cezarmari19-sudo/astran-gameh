import React, { useCallback, useEffect, useState } from "react";
import { View, Text, TextInput, ScrollView, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { colors, radius, spacing, type } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { as, errText } from "@/src/i18n/ageStrings";
import { ageApi, AgeState } from "@/src/api/age";

// Pagina de asteptare a aprobarii parentale. Starea vine DIN SERVER (GET /api/age/me); nimic de aici nu "aproba" contul.
export default function ParentalWait() {
  const { lang } = useI18n();
  const router = useRouter();
  const { refresh, logout } = useAuth();
  const [st, setSt] = useState<AgeState | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await ageApi.me(); setSt(s);
      if (!s.restricted) { await refresh(); router.replace("/(tabs)"); }
    } catch (e: any) { setErr(errText(lang, e)); }
  }, [lang, refresh, router]);
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, [load]);

  async function send() {
    setBusy(true); setErr(null); setInfo(null);
    try {
      const r: any = await ageApi.requestParent(email.trim(), lang);
      if (r.delivery === "failed") setErr(as(lang, "wait_delivery_failed"));
      else if (r.delivery === "not_configured") setErr(as(lang, "wait_delivery_unavailable"));
      else setInfo(`${as(lang, "wait_sent_to")} ${r.parent_email_hint}`);
      await load();
    } catch (e: any) { setErr(errText(lang, e)); } finally { setBusy(false); }
  }
  async function ownConsent() {
    setBusy(true); setErr(null);
    try { await ageApi.ownConsent(lang); await load(); } catch (e: any) { setErr(errText(lang, e)); } finally { setBusy(false); }
  }

  const ps = st?.parental;
  const state = ps?.state ?? null;
  const canOwn = st?.next_action === "confirm_own_consent";
  const stateText = !ps ? as(lang, "wait_state_none") : state === "PENDING_PARENTAL_CONSENT" ? as(lang, "wait_state_pending")
    : state === "PARENTAL_VERIFICATION_REQUIRED" ? as(lang, "wait_state_verification") : state === "PARENTAL_CONSENT_REJECTED" ? as(lang, "wait_state_rejected")
    : state === "PARENTAL_CONSENT_WITHDRAWN" ? as(lang, "wait_state_withdrawn") : as(lang, "wait_state_approved");
  const canRequest = !ps || state === "PARENTAL_CONSENT_REJECTED" || state === "PARENTAL_CONSENT_WITHDRAWN" || state === "PENDING_PARENTAL_CONSENT";

  return (
    <SafeAreaView style={styles.root}>
      <ScrollView contentContainerStyle={styles.pad}>
        {canOwn ? (
          <>
            <Text style={styles.h1}>{as(lang, "own_title")}</Text>
            <Text style={styles.p}>{as(lang, "own_intro")}</Text>
            <View style={{ marginTop: spacing.lg }}><PrimaryButton testID="own-consent" label={as(lang, "own_confirm")} onPress={ownConsent} disabled={busy} /></View>
          </>
        ) : (
          <>
            <Text style={styles.h1}>{as(lang, "wait_title")}</Text>
            <Text style={styles.p}>{as(lang, "wait_intro")}</Text>
            <Text style={styles.p}>{as(lang, "wait_what")}</Text>
            <View style={styles.card} testID="parental-state">
              <Text style={styles.cardText}>{stateText}</Text>
              {ps?.parent_email_hint ? <Text style={styles.small}>{as(lang, "wait_sent_to")} {ps.parent_email_hint}</Text> : null}
              {ps?.expires_at && state === "PENDING_PARENTAL_CONSENT" ? <Text style={styles.small}>{as(lang, "wait_expires")}: {new Date(ps.expires_at).toLocaleString()}</Text> : null}
              {ps?.delivery === "failed" ? <Text style={styles.warn}>{as(lang, "wait_delivery_failed")}</Text> : null}
              {ps?.delivery === "not_configured" ? <Text style={styles.warn}>{as(lang, "wait_delivery_unavailable")}</Text> : null}
              {state === "PARENTAL_VERIFICATION_REQUIRED" && st && !st.parental_verification_available ? <Text style={styles.warn}>{as(lang, "wait_verification_unavailable")}</Text> : null}
            </View>
            {canRequest ? (
              <>
                <Text style={styles.label}>{as(lang, "wait_email_label")}</Text>
                <TextInput testID="parent-email-input" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="parent@example.com" placeholderTextColor={colors.onSurface3} style={styles.input} />
                <View style={{ marginTop: spacing.md }}>
                  <PrimaryButton testID="parent-email-send" label={ps ? as(lang, "wait_resend") : as(lang, "wait_send")} onPress={send} disabled={busy || email.trim().length < 5} />
                </View>
              </>
            ) : null}
          </>
        )}
        {info ? <Text style={styles.ok}>{info}</Text> : null}
        {err ? <Text style={styles.err} testID="parental-error">{err}</Text> : null}
        <View style={{ marginTop: spacing.lg, gap: spacing.sm }}>
          <SecondaryButton testID="parental-refresh" label={as(lang, "refresh")} onPress={load} />
          <SecondaryButton testID="parental-logout" label={as(lang, "logout")} onPress={async () => { await logout(); router.replace("/"); }} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface }, pad: { padding: spacing.xl },
  h1: { ...type.h1, color: colors.onSurface }, p: { ...type.body, color: colors.onSurface2, marginTop: spacing.md, lineHeight: 20 },
  card: { marginTop: spacing.lg, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: 6 },
  cardText: { ...type.h3, color: colors.onSurface }, small: { ...type.small, color: colors.onSurface3 }, warn: { ...type.small, color: colors.warning },
  label: { ...type.micro, color: colors.onSurface3, textTransform: "uppercase", marginTop: spacing.xl, marginBottom: spacing.sm },
  input: { backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 14, color: colors.onSurface },
  ok: { color: colors.success, marginTop: spacing.md }, err: { color: colors.error, marginTop: spacing.md },
});
