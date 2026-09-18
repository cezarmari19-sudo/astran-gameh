import React, { useState } from "react";
import {
  View, Text, TextInput, StyleSheet, Pressable, KeyboardAvoidingView, Platform,
  ScrollView, ActivityIndicator,
} from "react-native";
import { useRouter } from "expo-router";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { colors, radius, spacing, type } from "@/src/theme";
import { PrimaryButton } from "@/src/components/ui";

export default function LoginScreen() {
  const router = useRouter();
  const { login, loginWithSessionId } = useAuth();
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function doLogin() {
    setBusy(true); setErr(null);
    try {
      await login(email.trim(), password);
      router.replace("/(tabs)");
    } catch (e: any) {
      setErr(e.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  async function doGoogle() {
    setErr(null);
    try {
      const redirectUrl = Platform.OS === "web"
        ? (typeof window !== "undefined" ? window.location.origin + "/" : "")
        : Linking.createURL("");
      const authUrl = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
      if (Platform.OS === "web") {
        if (typeof window !== "undefined") window.location.href = authUrl;
        return;
      }
      const result = await WebBrowser.openAuthSessionAsync(authUrl, redirectUrl);
      let url: string | null = (result as any)?.url || null;
      if (!url) url = await Linking.getInitialURL();
      const m = url ? url.match(/[?#&]session_id=([^&#]+)/) : null;
      if (m) {
        await loginWithSessionId(decodeURIComponent(m[1]));
        router.replace("/(tabs)");
      }
    } catch (e: any) {
      setErr(e.message || "Google login failed");
    }
  }

  return (
    <View style={styles.root}>
      <Image
        source={{ uri: "https://images.unsplash.com/photo-1672872476232-da16b45c9001?w=1200" }}
        style={StyleSheet.absoluteFillObject}
        contentFit="cover"
        transition={300}
      />
      <LinearGradient
        colors={["rgba(15,16,18,0.2)", "rgba(15,16,18,0.85)", "#0F1012"]}
        style={StyleSheet.absoluteFillObject}
      />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.brandRow}>
            <MaterialCommunityIcons name="hexagon-slice-6" size={28} color={colors.brand} />
            <Text style={styles.brand}>ASTRAN</Text>
          </View>
          <Text style={styles.tagline}>{t("tagline")}</Text>

          <View style={styles.card}>
            <Text style={styles.label}>{t("email")}</Text>
            <TextInput
              testID="login-email-input"
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              placeholderTextColor={colors.onSurface3}
              placeholder="you@astran.game"
              style={styles.input}
            />
            <Text style={[styles.label, { marginTop: 12 }]}>{t("password")}</Text>
            <TextInput
              testID="login-password-input"
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              placeholderTextColor={colors.onSurface3}
              placeholder="••••••••"
              style={styles.input}
            />
            {err ? <Text style={styles.err} testID="login-error">{err}</Text> : null}
            <View style={{ marginTop: 16 }}>
              <PrimaryButton
                testID="login-submit-button"
                label={busy ? "..." : t("login")}
                onPress={doLogin}
                disabled={busy || !email || !password}
              />
            </View>
            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>{t("or")}</Text>
              <View style={styles.dividerLine} />
            </View>
            <Pressable testID="login-google-button" onPress={doGoogle} style={styles.googleBtn}>
              <MaterialCommunityIcons name="google" size={18} color={colors.onSurface} />
              <Text style={styles.googleBtnText}>{t("google_login")}</Text>
            </Pressable>
          </View>

          <Pressable testID="go-register-button" onPress={() => router.push("/(auth)/register")} style={styles.footerLink}>
            <Text style={styles.footerLinkText}>
              {t("register")} →
            </Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.lg, paddingTop: 80, paddingBottom: 40, minHeight: "100%", justifyContent: "flex-end" },
  brandRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  brand: { color: colors.brand, fontSize: 26, fontWeight: "900", letterSpacing: 3 },
  tagline: { color: colors.onSurface2, fontSize: 14, fontWeight: "500", marginTop: 4, marginBottom: 24 },
  card: {
    backgroundColor: colors.surface2, padding: spacing.lg, borderRadius: radius.lg,
    borderWidth: 1, borderColor: colors.border,
  },
  label: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase" },
  input: {
    marginTop: 6, backgroundColor: colors.surface3, color: colors.onSurface, fontSize: 15,
    borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 12, borderWidth: 1, borderColor: colors.border,
  },
  err: { color: colors.error, marginTop: 12, fontSize: 12, fontWeight: "600" },
  divider: { flexDirection: "row", alignItems: "center", marginVertical: 16, gap: 10 },
  dividerLine: { flex: 1, height: 1, backgroundColor: colors.border },
  dividerText: { color: colors.onSurface3, fontSize: 11, fontWeight: "700" },
  googleBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: colors.surface3, borderRadius: radius.pill, paddingVertical: 12,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  googleBtnText: { color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  footerLink: { marginTop: 24, alignItems: "center" },
  footerLinkText: { color: colors.brand, fontSize: 13, fontWeight: "700" },
});