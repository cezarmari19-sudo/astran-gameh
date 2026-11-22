import React, { useEffect, useState } from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Body, Row, Screen } from "@/src/components/modUi";
import LegalDocModal from "@/src/components/LegalDocModal";
import { colors } from "@/src/theme";

// Centrul utilizatorului pentru moderare + informatii legale. "role" doar arata/ascunde intrarea in panou:
// accesul real e refuzat de server pe fiecare ruta /api/moderation/admin/*.
export default function ModerationHub() {
  const router = useRouter();
  const { lang } = useI18n();
  const [role, setRole] = useState<string | null>(null);
  const [terms, setTerms] = useState(false);
  useEffect(() => { modApi.role().then(r => setRole(r.role)).catch(() => setRole(null)); }, []);
  const items: { icon: string; key: Parameters<typeof ms>[1]; go: () => void; id: string }[] = [
    { icon: "flag-outline", key: "mod_my_reports", go: () => router.push("/moderation/reports"), id: "mod-my-reports" },
    { icon: "bell-outline", key: "mod_notifications", go: () => router.push("/moderation/notifications"), id: "mod-notifications" },
    { icon: "gavel", key: "mod_decisions", go: () => router.push("/moderation/decisions"), id: "mod-decisions" },
    { icon: "message-alert-outline", key: "mod_appeals", go: () => router.push("/moderation/appeals"), id: "mod-appeals" },
    { icon: "file-document-outline", key: "mod_terms", go: () => setTerms(true), id: "mod-terms" },
    { icon: "email-outline", key: "mod_contact", go: () => router.push("/moderation/contact"), id: "mod-contact" },
  ];
  return (
    <Screen title={ms(lang, "mod_title")} testID="moderation-hub">
      <Body dim>{ms(lang, "mod_hub_intro")}</Body>
      <Text />
      {items.map(i => (
        <Row key={i.id} onPress={i.go} testID={i.id}>
          <Text style={{ color: colors.onSurface, fontWeight: "700", fontSize: 14 }}>
            <MaterialCommunityIcons name={i.icon as any} size={18} color={colors.brand} />{"  "}{ms(lang, i.key)}
          </Text>
        </Row>
      ))}
      {role ? (
        <Row onPress={() => router.push("/moderation/admin")} testID="mod-staff">
          <Text style={{ color: colors.adminYellow, fontWeight: "800", fontSize: 14 }}>
            <MaterialCommunityIcons name="shield-account" size={18} color={colors.adminYellow} />{"  "}{ms(lang, "mod_staff")}
          </Text>
        </Row>
      ) : null}
      {terms ? <LegalDocModal visible docType="terms_of_service" lang={lang} onClose={() => setTerms(false)} /> : null}
    </Screen>
  );
}
