import React, { useState } from "react";
import { View, Text, StyleSheet, Modal, Pressable, TextInput, ActivityIndicator } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { projectSecurityApi } from "@/src/api/client";
import ResetProjectPasswordFlow from "@/src/components/ResetProjectPasswordFlow";

type Stage = "warning" | "setup_password" | "auth" | "locked";

export default function DeleteProjectFlow({
  visible, gameId, ownerLabel, onClose, onDeleted,
}: {
  visible: boolean;
  gameId: string;
  ownerLabel?: string; // ex: username/email-ul contului curent, DOAR afisaj - verificarea reala e pe server
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [stage, setStage] = useState<Stage>("warning");
  const [projectPassword, setProjectPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [newPw, setNewPw] = useState("");
  const [newPwConfirm, setNewPwConfirm] = useState("");
  const [accountPassword, setAccountPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lockMsg, setLockMsg] = useState<string | null>(null);
  const [showForgot, setShowForgot] = useState(false);

  function reset() {
    setStage("warning"); setProjectPassword(""); setShowPw(false);
    setNewPw(""); setNewPwConfirm(""); setAccountPassword(""); setErr(null); setLockMsg(null); setBusy(false);
  }
  function close() { reset(); onClose(); }

  async function confirmDelete() {
    setBusy(true); setErr(null);
    try {
      await projectSecurityApi.deleteProject(gameId, projectPassword);
      reset();
      onDeleted();
    } catch (e: any) {
      const detail = e?.message || "";
      if (e?.status === 409 && detail.includes("PASSWORD_SETUP_REQUIRED")) {
        setStage("setup_password");
        setErr(null);
      } else if (e?.status === 429) {
        setLockMsg(detail);
        setStage("locked");
      } else {
        setErr(detail || "Parola de securitate a proiectului este incorecta");
      }
    } finally {
      setBusy(false);
    }
  }

  async function submitPasswordSetup() {
    setBusy(true); setErr(null);
    try {
      await projectSecurityApi.setupPassword(gameId, {
        account_password: accountPassword,
        project_password: newPw,
        project_password_confirm: newPwConfirm,
      });
      setStage("auth");
      setAccountPassword(""); setNewPw(""); setNewPwConfirm("");
      setErr("Parola de securitate a fost configurata. Introdu-o acum pentru a continua stergerea.");
    } catch (e: any) {
      setErr(e?.message || "Nu am putut configura parola proiectului");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={close}>
      <View style={styles.overlay}>
        <View style={styles.card}>
          {stage === "warning" && (
            <>
              <View style={styles.iconWrap}>
                <MaterialCommunityIcons name="trash-can-outline" size={28} color={colors.error} />
              </View>
              <Text style={styles.title}>Ștergerea proiectului</Text>
              <Text style={styles.body}>
                Ești pe cale să muți acest proiect în Coșul de gunoi securizat. Proiectul nu va
                mai fi disponibil pentru utilizare normală. Numai Owner-ul îl poate recupera,
                după verificarea identității.
              </Text>
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton testID="delete-cancel" label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  <PrimaryButton testID="delete-continue" label="Continuă" onPress={() => setStage("auth")} style={{ backgroundColor: colors.error }} />
                </View>
              </View>
            </>
          )}

          {stage === "auth" && (
            <>
              <Text style={styles.title}>Confirmă identitatea</Text>
              {ownerLabel ? (
                <View style={styles.ownerPill}>
                  <MaterialCommunityIcons name="shield-account" size={16} color={colors.brand} />
                  <Text style={styles.ownerPillText}>Conectat ca {ownerLabel}</Text>
                </View>
              ) : null}
              <Text style={styles.label}>Parola de securitate a proiectului</Text>
              <View style={styles.pwRow}>
                <TextInput
                  testID="delete-project-password"
                  value={projectPassword}
                  onChangeText={setProjectPassword}
                  secureTextEntry={!showPw}
                  autoCapitalize="none"
                  placeholder="••••••••"
                  placeholderTextColor={colors.onSurface3}
                  style={styles.input}
                />
                <Pressable onPress={() => setShowPw(v => !v)} hitSlop={10} style={styles.eyeBtn}>
                  <MaterialCommunityIcons name={showPw ? "eye-off-outline" : "eye-outline"} size={20} color={colors.onSurface3} />
                </Pressable>
              </View>
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton testID="delete-back" label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.error} /> : (
                    <PrimaryButton
                      testID="delete-confirm"
                      label="Mută în Coș"
                      disabled={!projectPassword}
                      onPress={confirmDelete}
                      style={{ backgroundColor: colors.error }}
                    />
                  )}
                </View>
              </View>
              <Pressable testID="delete-forgot-password" onPress={() => setShowForgot(true)} style={{ alignSelf: "center", marginTop: 4 }}>
                <Text style={styles.forgotLink}>Ai uitat parola proiectului?</Text>
              </Pressable>
            </>
          )}

          {stage === "setup_password" && (
            <>
              <Text style={styles.title}>Configurează parola de securitate</Text>
              <Text style={styles.body}>
                Acest proiect a fost creat înainte de activarea parolei de securitate. Pentru a-l
                putea șterge sau recupera în siguranță, stabilește acum o parolă (6–30 caractere).
              </Text>
              <Text style={styles.label}>Parola contului tău</Text>
              <TextInput
                testID="setup-account-password" value={accountPassword} onChangeText={setAccountPassword}
                secureTextEntry autoCapitalize="none" placeholder="Parola contului"
                placeholderTextColor={colors.onSurface3} style={styles.input}
              />
              <Text style={styles.label}>Parolă nouă de proiect</Text>
              <TextInput
                testID="setup-new-password" value={newPw} onChangeText={setNewPw}
                secureTextEntry autoCapitalize="none" placeholder="6-30 caractere"
                placeholderTextColor={colors.onSurface3} style={styles.input}
              />
              <Text style={styles.label}>Confirmă parola</Text>
              <TextInput
                testID="setup-new-password-confirm" value={newPwConfirm} onChangeText={setNewPwConfirm}
                secureTextEntry autoCapitalize="none" placeholder="Reintrodu parola"
                placeholderTextColor={colors.onSurface3} style={styles.input}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <View style={styles.row}>
                <View style={{ flex: 1 }}><SecondaryButton testID="setup-cancel" label="Anulează" onPress={close} /></View>
                <View style={{ flex: 1 }}>
                  {busy ? <ActivityIndicator color={colors.brand} /> : (
                    <PrimaryButton testID="setup-submit" label="Salvează parola" onPress={submitPasswordSetup} disabled={!accountPassword || !newPw || !newPwConfirm} />
                  )}
                </View>
              </View>
            </>
          )}

          {stage === "locked" && (
            <>
              <MaterialCommunityIcons name="lock-clock-outline" size={28} color={colors.warning} style={{ alignSelf: "center" }} />
              <Text style={styles.title}>Prea multe încercări</Text>
              <Text style={styles.body}>{lockMsg || "Încearcă din nou mai târziu."}</Text>
              <PrimaryButton testID="locked-close" label="Închide" onPress={close} />
            </>
          )}
        </View>
      </View>
      <ResetProjectPasswordFlow
        visible={showForgot}
        gameId={gameId}
        onClose={() => setShowForgot(false)}
        onReset={() => setShowForgot(false)}
      />
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.lg },
  card: { width: "100%", maxWidth: 420, backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: spacing.sm },
  iconWrap: { alignSelf: "center", width: 56, height: 56, borderRadius: 28, backgroundColor: "rgba(255,51,102,0.12)", alignItems: "center", justifyContent: "center", marginBottom: 4 },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800", textAlign: "center" },
  body: { color: colors.onSurface3, fontSize: 13, textAlign: "center", lineHeight: 19 },
  label: { color: colors.onSurface2, fontSize: 12, fontWeight: "700", marginTop: spacing.xs },
  input: { flex: 1, backgroundColor: colors.surface3, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 12, paddingVertical: 10, color: colors.onSurface, fontSize: 14 },
  pwRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  eyeBtn: { padding: 8 },
  row: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  err: { color: colors.error, fontSize: 12, textAlign: "center" },
  ownerPill: { flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "center", backgroundColor: colors.brandTint, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4, marginBottom: 4 },
  ownerPillText: { color: colors.brand, fontSize: 11, fontWeight: "700" },
  forgotLink: { color: colors.onSurface3, fontSize: 12, textDecorationLine: "underline" },
});