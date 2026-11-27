import React, { useEffect, useState } from "react";
import { Text } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { cs, csErr } from "@/src/i18n/complianceStrings";
import { personalApi } from "@/src/api/compliance";
import { Btn, Card, Screen } from "@/src/components/modUi";
import { setToken } from "@/src/api/client";
import { colors } from "@/src/theme";

// Deschis din linkul trimis la NOUA adresa (GDPR Art. 16). Tokenul single-use se trimite in corpul cererii, nu se pastreaza.
// Dupa confirmare sesiunile existente sunt invalidate de server: stergem tokenul local si trimitem utilizatorul la autentificare.
export default function ConfirmEmail() {
  const { lang } = useI18n();
  const router = useRouter();
  const { token } = useLocalSearchParams<{ token?: string }>();
  const [state, setState] = useState<"working" | "done" | "failed" | "missing">("working");
  const [err, setErr] = useState("");
  useEffect(() => {
    const t = Array.isArray(token) ? token[0] : token;
    if (!t) { setState("missing"); return; }
    personalApi.confirmEmail(t).then(async () => { await setToken(null).catch(() => {}); setState("done"); })
      .catch((e: any) => { setErr(csErr(lang, e)); setState("failed"); });
  }, [token, lang]);
  return (
    <Screen title={cs(lang, "ce_title")} testID="confirm-email">
      <Card>
        <Text style={{ color: state === "done" ? colors.success : state === "working" ? colors.onSurface : colors.error }} testID="confirm-email-status">
          {state === "working" ? cs(lang, "ce_working") : state === "done" ? cs(lang, "ce_done") : state === "missing" ? cs(lang, "ce_missing") : err || cs(lang, "ce_failed")}
        </Text>
        {state !== "working" ? <Btn label={cs(lang, "go_signin")} onPress={() => router.replace("/")} testID="confirm-email-signin" /> : null}
      </Card>
    </Screen>
  );
}
