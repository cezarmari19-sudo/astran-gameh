import React, { useState } from "react";
import { View, Text, TextInput, StyleSheet, KeyboardAvoidingView, Platform, ScrollView, Pressable, Modal } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function RegisterScreen() {
  const router = useRouter();
  const { register } = useAuth();
  const { t, lang } = useI18n();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ageCategory, setAgeCategory] = useState<"under_18" | "adult_18">("under_18");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Checkbox-ul Politicii de Confidentialitate. Independent de modalul de mai jos: deschiderea
  // politicii NU bifeaza nimic, doar afiseaza textul - bifarea e mereu o actiune separata,
  // explicita a utilizatorului (vezi cerinta). Modalul fiind in aceeasi componenta (nu o ruta
  // noua), formularul si privacyAccepted raman neatinse la deschidere/inchidere - nu exista
  // nicio demontare a ecranului de Inregistrare intre timp.
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [showPrivacy, setShowPrivacy] = useState(false);

  async function doRegister() {
    setBusy(true); setErr(null);
    try {
      await register({ email: email.trim(), password, username: username.trim(), age_category: ageCategory, language: lang });
      router.replace("/(onboarding)/age");
    } catch (e: any) {
      setErr(e.message || "Register failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <View style={styles.root}>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Pressable onPress={() => router.back()} style={styles.back} testID="register-back">
            <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.title}>{t("register")}</Text>
          <Text style={styles.sub}>Astran Game</Text>

          <Text style={styles.label}>{t("email")}</Text>
          <TextInput testID="register-email-input" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholderTextColor={colors.onSurface3} placeholder="you@astran.game" style={styles.input} />

          <Text style={styles.label}>{t("username")}</Text>
          <TextInput testID="register-username-input" value={username} onChangeText={setUsername} autoCapitalize="none" placeholderTextColor={colors.onSurface3} placeholder="astronaut42" style={styles.input} />

          <Text style={styles.label}>{t("password")}</Text>
          <TextInput testID="register-password-input" value={password} onChangeText={setPassword} secureTextEntry placeholderTextColor={colors.onSurface3} placeholder="••••••••" style={styles.input} />

          <Text style={[styles.label, { marginTop: 20 }]}>{t("age_select_title")}</Text>
          <View style={styles.ageRow}>
            <Pressable
              testID="register-age-under-18"
              onPress={() => setAgeCategory("under_18")}
              style={[styles.ageBtn, ageCategory === "under_18" && styles.ageBtnActive]}
            >
              <Text style={[styles.ageBtnText, ageCategory === "under_18" && styles.ageBtnTextActive]}>{t("age_under_18")}</Text>
            </Pressable>
            <Pressable
              testID="register-age-18-plus"
              onPress={() => setAgeCategory("adult_18")}
              style={[styles.ageBtn, ageCategory === "adult_18" && styles.ageBtnActive]}
            >
              <Text style={[styles.ageBtnText, ageCategory === "adult_18" && styles.ageBtnTextActive]}>{t("age_18_plus")}</Text>
            </Pressable>
          </View>

          <View style={styles.privacyRow}>
            <Pressable
              testID="register-privacy-checkbox"
              onPress={() => setPrivacyAccepted(v => !v)}
              hitSlop={8}
              style={styles.checkbox}
            >
              <MaterialCommunityIcons
                name={privacyAccepted ? "checkbox-marked" : "checkbox-blank-outline"}
                size={18}
                color={privacyAccepted ? colors.brand : colors.onSurface3}
              />
            </Pressable>
            <Text style={styles.privacyText}>
              Am citit și accept{" "}
              <Text testID="register-privacy-link" style={styles.privacyLink} onPress={() => setShowPrivacy(true)}>
                Politica de Confidențialitate
              </Text>
            </Text>
          </View>

          {err ? <Text style={styles.err} testID="register-error">{err}</Text> : null}
          <View style={{ marginTop: 24 }}>
            <PrimaryButton
              testID="register-submit-button"
              label={busy ? "..." : t("continue")}
              onPress={doRegister}
              disabled={busy || !email || !username || !password || !privacyAccepted}
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      {/* Pagina Politicii de Confidentialitate: modal full-screen in interiorul aplicatiei (nu
          browser, nu ruta noua) - cerinta explicita. Fiind Modal, nu router.push, ecranul de
          Inregistrare de dedesubt nu se demonteaza niciodata: formularul si privacyAccepted
          supravietuiesc neschimbate cat timp userul citeste si apoi apasa Inapoi. */}
      <Modal
        visible={showPrivacy}
        animationType="slide"
        presentationStyle="fullScreen"
        onRequestClose={() => setShowPrivacy(false)}
      >
        <SafeAreaView style={styles.privacyRoot} edges={["top", "left", "right", "bottom"]}>
          <View style={styles.privacyHeader}>
            <Pressable testID="register-privacy-close" onPress={() => setShowPrivacy(false)} style={styles.privacyBack} hitSlop={8}>
              <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
            </Pressable>
            <Text style={styles.privacyTitle}>Politica de Confidențialitate</Text>
            <View style={styles.privacyHeaderSpacer} />
          </View>
          <ScrollView contentContainerStyle={styles.privacyContent} showsVerticalScrollIndicator>
            {/* Placeholder temporar, la cererea explicita - se inlocuieste cu textul complet
                al politicii cand va fi furnizat. */}
            <Text style={styles.privacyBody}>Aici va fi afișată Politica de Confidențialitate Astran Game.</Text>
          </ScrollView>
        </SafeAreaView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.lg, paddingTop: 60, paddingBottom: 40 },
  back: { width: 40, height: 40, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  title: { color: colors.onSurface, fontSize: 30, fontWeight: "900", letterSpacing: -0.5 },
  sub: { color: colors.brand, fontSize: 12, fontWeight: "800", letterSpacing: 3, marginBottom: 24 },
  label: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 12 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, borderWidth: 1, borderColor: colors.border },
  ageRow: { flexDirection: "row", gap: 12, marginTop: 10 },
  ageBtn: { flex: 1, paddingVertical: 14, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, alignItems: "center" },
  ageBtnActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  ageBtnText: { color: colors.onSurface2, fontWeight: "700", fontSize: 14 },
  ageBtnTextActive: { color: colors.brand },
  err: { color: colors.error, marginTop: 12, fontSize: 12, fontWeight: "600" },
  privacyRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 16 },
  checkbox: { width: 18, height: 18, alignItems: "center", justifyContent: "center", marginTop: 1 },
  privacyText: { flex: 1, color: colors.onSurface2, fontSize: 12, lineHeight: 17 },
  privacyLink: { color: colors.brand, fontWeight: "700", textDecorationLine: "underline" },
  privacyRoot: { flex: 1, backgroundColor: colors.surface },
  privacyHeader: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
  privacyBack: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  privacyTitle: { flex: 1, color: colors.onSurface, fontSize: 16, fontWeight: "900", textAlign: "center" },
  privacyHeaderSpacer: { width: 40 },
  privacyContent: { padding: spacing.lg, paddingBottom: 48 },
  privacyBody: { color: colors.onSurface2, fontSize: 14, lineHeight: 22 },
});