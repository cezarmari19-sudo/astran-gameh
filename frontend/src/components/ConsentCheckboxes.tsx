import React, { useState } from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors } from "@/src/theme";
import type { LegalDocType } from "@/src/api/consent";
import { cs } from "@/src/i18n/consentStrings";
import LegalDocModal from "@/src/components/LegalDocModal";

// Casute separate: (1) acceptarea Termenilor (contract), (2) luarea la cunostinta a Politicii de Confidentialitate
// (informare, NU consimtamant), (3) consimtamant optional pentru marketing - necompletat implicit, doar pentru adulti.
// Casutele sunt doar UI: dovada o scrie serverul, la inregistrare/acceptare (backend/astran_sandbox/consent.py).
function Row({ checked, onToggle, id, prefix, children }: { checked: boolean; onToggle: () => void; id: string; prefix: string; children?: React.ReactNode }) {
  return (
    <View style={styles.row}>
      <Pressable testID={`${prefix}-${id}-checkbox`} onPress={onToggle} hitSlop={8} style={styles.box}>
        <MaterialCommunityIcons name={checked ? "checkbox-marked" : "checkbox-blank-outline"} size={18}
          color={checked ? colors.brand : colors.onSurface3} />
      </Pressable>
      <Text style={styles.text}>{children}</Text>
    </View>
  );
}

export default function ConsentCheckboxes({ lang, terms, privacy, marketing, onTerms, onPrivacy, onMarketing, showMarketing, testPrefix = "consent" }: {
  lang: string; terms: boolean; privacy: boolean; marketing?: boolean;
  onTerms: (v: boolean) => void; onPrivacy: (v: boolean) => void; onMarketing?: (v: boolean) => void;
  showMarketing?: boolean; testPrefix?: string;
}) {
  const [open, setOpen] = useState<LegalDocType | null>(null);
  return (
    <View>
      <Row prefix={testPrefix} id="terms" checked={terms} onToggle={() => onTerms(!terms)}>
        {cs(lang, "accept_terms_pre")}
        <Text testID={`${testPrefix}-terms-link`} style={styles.link} onPress={() => setOpen("terms_of_service")}>{cs(lang, "terms_link")}</Text>
      </Row>
      <Row prefix={testPrefix} id="privacy" checked={privacy} onToggle={() => onPrivacy(!privacy)}>
        {cs(lang, "ack_privacy_pre")}
        <Text testID={`${testPrefix}-privacy-link`} style={styles.link} onPress={() => setOpen("privacy_policy")}>{cs(lang, "privacy_link")}</Text>
      </Row>
      {showMarketing && onMarketing ? (
        <Row prefix={testPrefix} id="marketing" checked={!!marketing} onToggle={() => onMarketing(!marketing)}>{cs(lang, "marketing_optional")}</Row>
      ) : null}
      {open ? <LegalDocModal visible docType={open} lang={lang} onClose={() => setOpen(null)} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "flex-start", gap: 8, marginTop: 14 },
  box: { width: 18, height: 18, alignItems: "center", justifyContent: "center", marginTop: 1 },
  text: { flex: 1, color: colors.onSurface2, fontSize: 12, lineHeight: 17 },
  link: { color: colors.brand, fontWeight: "700", textDecorationLine: "underline" },
});