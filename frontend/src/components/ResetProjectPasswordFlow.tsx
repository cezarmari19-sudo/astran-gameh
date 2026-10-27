import React, { useState } from "react";
import { View, Text, StyleSheet, Modal, TextInput, ActivityIndicator, Pressable } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { projectSecurityApi } from "@/src/api/client";
import { biometricsAvailable, authenticateWithPasskey } from "@/src/security/webauthnWeb";

type Stage = "account_password" | "email_code" | "biometric" | "biometric_unavailable" | "new_password" | "done";

export default function ResetProjectPasswordFlow({
  visible, gameId, onClose, onReset,
}: {
  visible: boolean;
  gameId: string;
  onClose: () => void;
  onReset: () => void;
}) {
  const [stage, setStage] = useState<Stage>("account_password");
  const [accountPassword, setAccountPassword] = useState("");
  const [code, setCode] = useState("");
  const [tokenId, setTokenId] = useState<string | null>(null);
  const [maskedEmail, setMaskedEmail] = useState<string | null>(null);
  const [newPw, setNewPw] = useState("");
  const [newPwConfirm, setNewPwConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function reset() {
    setStage("account_password"); setAccountPassword(""); setCode(""); setTokenId(null);
    setMaskedEmail(null); setNewPw(""); setNewPwConfirm(""); setShowPw(false); setBusy(false); setErr(null);
  }
  function close() { reset(); onClose(); }

  async function submitAccountPassword() {
    setBusy(true); setErr(null);
    try {
      const r = await projectSecurityApi.resetPasswordStart(gameId, accountPassword);
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
      const r = await projectSecurityApi.resetPasswordVerifyEmail(gameId, tokenId, code);
      if (r.biometric_required) setStage(biometricsAvailable() ? "biometric" : "biometric_unavailable");
      else setStage("new_password");
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
      setStage("new_password");
    } catch (e: any) {
      setErr(e?.message || "Verificarea biometrică a eșuat sau a fost anulată");
    } finally { setBusy(false); }
  }

  async function submitNewPassword() {
    if (!tokenId) return;
    setBusy(true); setErr(null);
    try {
      await projectSecurityApi.resetPasswordConfirm(gameId, tokenId, newPw, newPwConfirm);
      setStage("done");
      onReset();
    } catch (e: any) {
      setErr(e?.message || "Nu am putut seta parola nouă");
    } finally { setBusy(false); }
  }

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={close}>
      <View style={styles.overlay}>
        <View style={styles.card}>
          <Text style={styles.title}>Resetează parola de securitate</Text>

          {stage === "account_password" && (
            <>
              <Text style={styles.body}>Reintrodu parola contului tău Owner pentru a începe resetarea.</Text>
              <TextInput
                value={accountPassword} onChangeText={setAccountPassword}
                secureTextEntry autoCapitalize="none" placeholder="Parola contului"
                placeholderTextColor={colors.onSurface3} style={styles.input}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.brand} /> : (
                    <PrimaryButton label="Continuă" onPress={submitAccountPassword} disabled={!accountPassword} />
                  )}
                </View>
              </View>
            </>
          )}

          {stage === "email_code" && (
            <>
              <Text style={styles.body}>Am trimis un cod la {maskedEmail || "adresa ta de email"}. Introdu-l mai jos.</Text>
              <TextInput
                value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={6}
                placeholder="123456" placeholderTextColor={colors.onSurface3}
                style={[styles.input, { textAlign: "center", letterSpacing: 4, fontSize: 18 }]}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.brand} /> : (
                    <PrimaryButton label="Verifică" onPress={submitCode} disabled={code.length < 4} />
                  )}
                </View>
              </View>
            </>
          )}

          {stage === "biometric" && (
            <>
              <MaterialCommunityIcons name="fingerprint" size={40} color={colors.brand} style={{ alignSelf: "center" }} />
              <Text style={styles.body}>Confirmă cu Face ID / Touch ID / Windows Hello pentru a continua.</Text>
              {err ? <Text style={styles.err}>{err}</Text> : null}
              {busy ? <ActivityIndicator color={colors.brand} /> : (
                <PrimaryButton label="Verifică biometric" onPress={doBiometric} />
              )}
            </>
          )}

          {stage === "biometric_unavailable" && (
            <>
              <MaterialCommunityIcons name="fingerprint-off" size={40} color={colors.warning} style={{ alignSelf: "center" }} />
              <Text style={styles.body}>
                Acest cont are activată verificarea biometrică, dar dispozitivul curent nu o
                poate efectua. Finalizează resetarea din Astran Game Studio pe web.
              </Text>
              <PrimaryButton label="Am înțeles" onPress={close} />
            </>
          )}

          {stage === "new_password" && (
            <>
              <Text style={styles.body}>Alege o nouă parolă de securitate pentru proiect (6-30 caractere).</Text>
              <View style={styles.pwRow}>
                <TextInput
                  value={newPw} onChangeText={setNewPw} secureTextEntry={!showPw} autoCapitalize="none"
                  placeholder="Parolă nouă" placeholderTextColor={colors.onSurface3} style={[styles.input, { flex: 1 }]} maxLength={30}
                />
                <Pressable onPress={() => setShowPw(v => !v)} hitSlop={10} style={{ padding: 8 }}>
                  <MaterialCommunityIcons name={showPw ? "eye-off-outline" : "eye-outline"} size={20} color={colors.onSurface3} />
                </Pressable>
              </View>
              <TextInput
                value={newPwConfirm} onChangeText={setNewPwConfirm} secureTextEntry={!showPw} autoCapitalize="none"
                placeholder="Confirmă parola nouă" placeholderTextColor={colors.onSurface3} style={styles.input} maxLength={30}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.brand} /> : (
                    <PrimaryButton label="Salvează" onPress={submitNewPassword} disabled={!newPw || !newPwConfirm} />
                  )}
                </View>
              </View>
            </>
          )}

          {stage === "done" && (
            <>
              <MaterialCommunityIcons name="check-circle-outline" size={40} color={colors.success} style={{ alignSelf: "center" }} />
              <Text style={styles.body}>Parola de securitate a fost resetată cu succes.</Text>
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
  pwRow: { flexDirection: "row", alignItems: "center" },
  row: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  err: { color: colors.error, fontSize: 12, textAlign: "center" },
});