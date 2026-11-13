import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet } from "react-native";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

/** Apare doar cat timp emailul contului nu e verificat (user.email_verified === false). */
export function EmailVerifyCard() {
  const { user, refresh } = useAuth();
  const { t } = useI18n();
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // `=== false`: conturile ale caror date nu contin inca campul nu primesc cardul din greseala
  if (!user || user.email_verified !== false) return null;

  async function sendCode() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await api("/auth/email/send-code", { method: "POST" });
      setSent(true); setMsg(t("verify_email_sent"));
    } catch (e: any) {
      setErr(e?.message || "Error");
    } finally { setBusy(false); }
  }

  async function confirm() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await api("/auth/email/verify", { method: "POST", body: JSON.stringify({ code: code.trim() }) });
      setMsg(t("verify_email_done"));
      await refresh();
    } catch (e: any) {
      setErr(e?.message || "Error");
    } finally { setBusy(false); }
  }

  return (
    <View style={styles.card} testID="email-verify-card">
      <Text style={styles.title}>{t("verify_email_title")}</Text>
      <Text style={styles.body}>{t("verify_email_body")}</Text>
      {sent ? (
        <>
          <TextInput
            testID="email-verify-code-input"
            value={code}
            onChangeText={setCode}
            keyboardType="number-pad"
            maxLength={12}
            placeholder={t("verify_email_code")}
            placeholderTextColor={colors.onSurface3}
            style={styles.input}
          />
          <View style={{ marginTop: 12 }}>
            <PrimaryButton testID="email-verify-confirm" label={busy ? "..." : t("verify_email_confirm")}
              onPress={confirm} disabled={busy || code.trim().length < 4} />
          </View>
        </>
      ) : null}
      <View style={{ marginTop: 12 }}>
        <PrimaryButton testID="email-verify-send" label={busy ? "..." : t("verify_email_send")} onPress={sendCode} disabled={busy} />
      </View>
      {msg ? <Text style={styles.msg}>{msg}</Text> : null}
      {err ? <Text style={styles.err}>{err}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface2, padding: spacing.lg, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border, marginBottom: spacing.lg,
  },
  title: { color: colors.onSurface, fontSize: 15, fontWeight: "800" },
  body: { color: colors.onSurface2, fontSize: 13, marginTop: 6 },
  input: {
    marginTop: 12, backgroundColor: colors.surface3, color: colors.onSurface, fontSize: 16, letterSpacing: 4,
    borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, borderWidth: 1, borderColor: colors.border,
  },
  msg: { color: colors.onSurface2, marginTop: 10, fontSize: 12, fontWeight: "600" },
  err: { color: colors.error, marginTop: 10, fontSize: 12, fontWeight: "600" },
});