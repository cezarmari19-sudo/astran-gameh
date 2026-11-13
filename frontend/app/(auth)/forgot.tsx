import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, Pressable, ScrollView, KeyboardAvoidingView, Platform } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function ForgotPasswordScreen() {
  const router = useRouter();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function sendCode() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await api("/auth/password/forgot", { method: "POST", body: JSON.stringify({ email: email.trim() }) });
      setSent(true); setMsg(t("forgot_sent"));
    } catch (e: any) { setErr(e?.message || "Error"); } finally { setBusy(false); }
  }

  async function reset() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      await api("/auth/password/reset", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), code: code.trim(), new_password: pw }),
      });
      setMsg(t("forgot_done"));
      setTimeout(() => router.replace("/(auth)/login"), 1200);
    } catch (e: any) { setErr(e?.message || "Error"); } finally { setBusy(false); }
  }

  return (
    <View style={styles.root}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.title}>{t("forgot_title")}</Text>
          <View style={styles.card}>
            <Text style={styles.label}>{t("email")}</Text>
            <TextInput testID="forgot-email-input" value={email} onChangeText={setEmail} autoCapitalize="none"
              keyboardType="email-address" placeholder="you@astran.game" placeholderTextColor={colors.onSurface3} style={styles.input} />
            <View style={{ marginTop: 12 }}>
              <PrimaryButton testID="forgot-send" label={busy ? "..." : t("forgot_send_code")} onPress={sendCode} disabled={busy || !email} />
            </View>
            {sent ? (
              <>
                <Text style={[styles.label, { marginTop: 16 }]}>{t("forgot_code")}</Text>
                <TextInput testID="forgot-code-input" value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={12}
                  placeholderTextColor={colors.onSurface3} style={styles.input} />
                <Text style={[styles.label, { marginTop: 12 }]}>{t("forgot_new_password")}</Text>
                <TextInput testID="forgot-password-input" value={pw} onChangeText={setPw} secureTextEntry maxLength={128}
                  placeholderTextColor={colors.onSurface3} style={styles.input} />
                <View style={{ marginTop: 12 }}>
                  <PrimaryButton testID="forgot-reset" label={busy ? "..." : t("forgot_reset")} onPress={reset}
                    disabled={busy || code.trim().length < 4 || pw.length < 6} />
                </View>
              </>
            ) : null}
            {msg ? <Text style={styles.msg}>{msg}</Text> : null}
            {err ? <Text style={styles.err}>{err}</Text> : null}
          </View>
          <Pressable onPress={() => router.replace("/(auth)/login")} style={{ marginTop: 24, alignItems: "center" }}>
            <Text style={styles.link}>← {t("login")}</Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.lg, paddingTop: 80, paddingBottom: 40 },
  title: { color: colors.onSurface, fontSize: 22, fontWeight: "900", marginBottom: 16 },
  card: { backgroundColor: colors.surface2, padding: spacing.lg, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border },
  label: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase" },
  input: {
    marginTop: 6, backgroundColor: colors.surface3, color: colors.onSurface, fontSize: 15,
    borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, borderWidth: 1, borderColor: colors.border,
  },
  msg: { color: colors.onSurface2, marginTop: 12, fontSize: 12, fontWeight: "600" },
  err: { color: colors.error, marginTop: 12, fontSize: 12, fontWeight: "600" },
  link: { color: colors.brand, fontSize: 13, fontWeight: "700" },
});