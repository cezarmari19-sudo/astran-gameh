import React, { useState } from "react";
import { View, Text, StyleSheet, Modal, TextInput, ActivityIndicator } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { projectSecurityApi } from "@/src/api/client";
import { biometricsAvailable, authenticateWithPasskey } from "@/src/security/webauthnWeb";

type Stage = "account_password" | "email_code" | "biometric" | "biometric_unavailable" | "done";

export default function RecoverProjectFlow({
  visible, gameId, projectTitle, onClose, onRecovered,
}: {
  visible: boolean;
  gameId: string;
  projectTitle?: string;
  onClose: () => void;
  onRecovered: () => void;
}) {
  const [stage, setStage] = useState<Stage>("account_password");
  const [accountPassword, setAccountPassword] = useState("");
  const [code, setCode] = useState("");
  const [tokenId, setTokenId] = useState<string | null>(null);
  const [maskedEmail, setMaskedEmail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function reset() {
    setStage("account_password"); setAccountPassword(""); setCode(""); setTokenId(null);
    setMaskedEmail(null); setBusy(false); setErr(null);
  }
  function close() { reset(); onClose(); }

  async function submitAccountPassword() {
    setBusy(true); setErr(null);
    try {
      const r = await projectSecurityApi.recoverStart(gameId, accountPassword);
      setTokenId(r.token_id);
      setMaskedEmail(r.email_sent_to);
      setStage("email_code");
    } catch (e: any) {
      setErr(e?.message || "Parola contului este incorectă");
    } finally { setBusy(false); }
  }

  async function submitCode() {
    if (!tokenId) return;
    setBusy(true); setErr(null);
    try {
      const r = await projectSecurityApi.recoverVerifyEmail(gameId, tokenId, code);
      if (r.biometric_required) {
        setStage(biometricsAvailable() ? "biometric" : "biometric_unavailable");
      } else {
        await finishRecover();
      }
    } catch (e: any) {
      setErr(e?.message || "Cod de verificare incorect");
    } finally { setBusy(false); }
  }

  async function doBiometric() {
    if (!tokenId) return;
    setBusy(true); setErr(null);
    try {
      const opts = await projectSecurityApi.webauthnAuthOptions();
      const credentialJson = await authenticateWithPasskey(opts.options);
      await projectSecurityApi.webauthnForToken(gameId, tokenId, opts.challenge_id, credentialJson);
      await finishRecover();
    } catch (e: any) {
      setErr(e?.message || "Verificarea biometrică a eșuat sau a fost anulată");
    } finally { setBusy(false); }
  }

  async function finishRecover() {
    if (!tokenId) return;
    await projectSecurityApi.recoverConfirm(gameId, tokenId);
    setStage("done");
    onRecovered();
  }

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={close}>
      <View style={styles.overlay}>
        <View style={styles.card}>
          <Text style={styles.title}>Recuperează proiectul{projectTitle ? `: ${projectTitle}` : ""}</Text>

          {stage === "account_password" && (
            <>
              <Text style={styles.body}>Reintrodu parola contului tău Owner pentru a continua.</Text>
              <TextInput
                testID="recover-account-password" value={accountPassword} onChangeText={setAccountPassword}
                secureTextEntry autoCapitalize="none" placeholder="Parola contului"
                placeholderTextColor={colors.onSurface3} style={styles.input}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.brand} /> : (
                    <PrimaryButton testID="recover-submit-password" label="Continuă" onPress={submitAccountPassword} disabled={!accountPassword} />
                  )}
                </View>
              </View>
            </>
          )}

          {stage === "email_code" && (
            <>
              <Text style={styles.body}>Am trimis un cod la {maskedEmail || "adresa ta de email"}. Introdu-l mai jos (expiră în 15 minute).</Text>
              <TextInput
                testID="recover-email-code" value={code} onChangeText={setCode}
                keyboardType="number-pad" placeholder="123456" maxLength={6}
                placeholderTextColor={colors.onSurface3} style={[styles.input, { textAlign: "center", letterSpacing: 4, fontSize: 18 }]}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.brand} /> : (
                    <PrimaryButton testID="recover-submit-code" label="Verifică" onPress={submitCode} disabled={code.length < 4} />
                  )}
                </View>
              </View>
            </>
          )}

          {stage === "biometric" && (
            <>
              <MaterialCommunityIcons name="fingerprint" size={40} color={colors.brand} style={{ alignSelf: "center" }} />
              <Text style={styles.body}>Acest cont are activată verificarea biometrică. Confirmă cu Face ID / Touch ID / Windows Hello pentru a finaliza recuperarea.</Text>
              {err ? <Text style={styles.err}>{err}</Text> : null}
              {busy ? <ActivityIndicator color={colors.brand} /> : (
                <PrimaryButton testID="recover-biometric" label="Verifică biometric" onPress={doBiometric} />
              )}
            </>
          )}

          {stage === "biometric_unavailable" && (
            <>
              <MaterialCommunityIcons name="fingerprint-off" size={40} color={colors.warning} style={{ alignSelf: "center" }} />
              <Text style={styles.body}>
                Acest cont are activată verificarea biometrică (passkey), dar dispozitivul sau
                aplicația folosită acum nu o pot efectua. Nu sărim peste acest pas - finalizează
                recuperarea din Astran Game Studio pe web, unde biometria browserului este
                disponibilă.
              </Text>
              <PrimaryButton label="Am înțeles" onPress={close} />
            </>
          )}

          {stage === "done" && (
            <>
              <MaterialCommunityIcons name="check-circle-outline" size={40} color={colors.success} style={{ alignSelf: "center" }} />
              <Text style={styles.body}>Proiectul a fost recuperat cu succes.</Text>
              <PrimaryButton label="Închide" onPress={close} />
            </>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.lg },
  card: { width: "100%", maxWidth: 420, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: spacing.sm },
  title: { color: colors.onSurface, fontSize: 17, fontWeight: "800", textAlign: "center" },
  body: { color: colors.onSurface3, fontSize: 13, textAlign: "center", lineHeight: 19 },
  input: { backgroundColor: colors.surface3, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 12, paddingVertical: 10, color: colors.onSurface, fontSize: 14 },
  row: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  err: { color: colors.error, fontSize: 12, textAlign: "center" },
});