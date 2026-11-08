import React, { useEffect } from "react";
import { View, Text, ActivityIndicator, StyleSheet, Platform } from "react-native";
import { Redirect, useRouter } from "expo-router";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { PrimaryButton } from "@/src/components/ui";
import { colors, spacing } from "@/src/theme";

WebBrowser.maybeCompleteAuthSession();

const processedSessionIds = new Set<string>();

function extractSessionId(url: string | null | undefined): string | null {
  if (!url) return null;
  const m = url.match(/[?#&]session_id=([^&#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export default function Index() {
  const { user, loading, connectionError, refresh, loginWithSessionId } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const [retrying, setRetrying] = React.useState(false);

  useEffect(() => {
    if (Platform.OS === "web" && typeof window !== "undefined") {
      const sid = extractSessionId(window.location.hash) || extractSessionId(window.location.search);
      if (sid && !processedSessionIds.has(sid)) {
        processedSessionIds.add(sid);
        loginWithSessionId(sid)
          .then(() => {
            try {
              const url = new URL(window.location.href);
              url.hash = "";
              url.searchParams.delete("session_id");
              window.history.replaceState(window.history.state, "", url.toString());
            } catch {}
          })
          .catch(() => processedSessionIds.delete(sid));
      }
      return;
    }
    (async () => {
      const initial = await Linking.getInitialURL();
      const sid = extractSessionId(initial);
      if (sid && !processedSessionIds.has(sid)) {
        processedSessionIds.add(sid);
        loginWithSessionId(sid).catch(() => processedSessionIds.delete(sid));
      }
    })();
    const sub = Linking.addEventListener("url", ({ url }) => {
      const sid = extractSessionId(url);
      if (sid && !processedSessionIds.has(sid)) {
        processedSessionIds.add(sid);
        loginWithSessionId(sid).catch(() => processedSessionIds.delete(sid));
      }
    });
    return () => sub.remove();
  }, [loginWithSessionId]);

  if (loading) {
    return (
      <View style={styles.center} testID="app-loading">
        <ActivityIndicator size="large" color={colors.brand} />
      </View>
    );
  }

  // Token salvat, dar serverul nu raspunde: NU trimitem utilizatorul la login (ar crede ca a fost
  // deconectat) - aratam un ecran cu "Incearca din nou".
  if (!user && connectionError) {
    return (
      <View style={styles.center} testID="app-server-down">
        <Text style={styles.title}>{t("server_unreachable_title")}</Text>
        <Text style={styles.body}>{t("server_unreachable_body")}</Text>
        <View style={{ marginTop: spacing.lg, alignSelf: "stretch" }}>
          <PrimaryButton
            testID="app-retry"
            label={retrying ? "..." : t("try_again")}
            disabled={retrying}
            onPress={async () => { setRetrying(true); try { await refresh(); } finally { setRetrying(false); } }}
          />
        </View>
      </View>
    );
  }

  if (!user) return <Redirect href="/(auth)/login" />;
  return <Redirect href="/(tabs)" />;
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface, padding: spacing.xl },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800", textAlign: "center" },
  body: { color: colors.onSurface3, fontSize: 13, textAlign: "center", marginTop: 8 },
});