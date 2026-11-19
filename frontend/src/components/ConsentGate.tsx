import React, { useCallback, useEffect, useState } from "react";
import { View, Text, Modal, StyleSheet, ScrollView } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { colors, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { consentApi, type ConsentOverview, type LegalDocType } from "@/src/api/consent";
import { cs } from "@/src/i18n/consentStrings";
import ConsentCheckboxes from "@/src/components/ConsentCheckboxes";

// Poarta pentru conturi care NU au dovada acceptarii versiunii curente: conturi create inainte de acest sistem
// (nu inventam dovezi retroactive) sau dupa o versiune noua care cere re-acceptare. Se afiseaza doar documentele
// care chiar cer acceptare; modificarile "doar informare" nu blocheaza nimic (apar in Setari > Privacy).
// Obs.: poarta este aplicata in client; serverul nu blocheaza inca API-urile pentru aceste conturi (vezi raportul).
export default function ConsentGate() {
  const { user, logout } = useAuth();
  const { lang } = useI18n();
  const uid = user?.user_id;
  const [ov, setOv] = useState<ConsentOverview | null>(null);
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!uid) { setOv(null); return; }
    try { setOv(await consentApi.me(lang)); } catch { setOv(null); }
  }, [uid, lang]);
  useEffect(() => { load(); }, [load]);

  const pending = (ov?.pending_acceptance ?? []) as LegalDocType[];
  if (!uid || !ov || pending.length === 0) return null;

  async function accept() {
    setBusy(true); setErr(null);
    try {
      const presented = Object.fromEntries(ov!.documents.map(d => [d.subject, d.current_version]));
      setOv(await consentApi.acceptDocuments(pending, presented, lang));
      setTerms(false); setPrivacy(false);
    } catch (e: any) {
      setErr(e?.message || "Error");
      if (e?.code === "document_version_changed") { setTerms(false); setPrivacy(false); load(); }
    } finally { setBusy(false); }
  }

  const needTerms = pending.includes("terms_of_service");
  const needPrivacy = pending.includes("privacy_policy");
  return (
    <Modal visible animationType="slide" presentationStyle="fullScreen" onRequestClose={() => {}}>
      <SafeAreaView style={styles.root} edges={["top", "bottom", "left", "right"]}>
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.title}>{cs(lang, "gate_title")}</Text>
          <Text style={styles.p}>{cs(lang, "gate_intro")}</Text>
          <ConsentCheckboxes lang={lang} testPrefix="gate"
            terms={terms || !needTerms} privacy={privacy || !needPrivacy}
            onTerms={needTerms ? setTerms : () => {}} onPrivacy={needPrivacy ? setPrivacy : () => {}} />
          {err ? <Text style={styles.err}>{err}</Text> : null}
          <View style={{ marginTop: spacing.xl, gap: spacing.sm }}>
            <PrimaryButton testID="gate-accept" label={busy ? "..." : cs(lang, "gate_accept")} disabled={busy || (needTerms && !terms) || (needPrivacy && !privacy)} onPress={accept} />
            <SecondaryButton label="Logout" onPress={() => { logout(); }} />
          </View>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  content: { padding: spacing.xl, paddingTop: spacing.xxxl },
  title: { color: colors.onSurface, fontSize: 24, fontWeight: "800", marginBottom: spacing.sm },
  p: { color: colors.onSurface2, fontSize: 14, lineHeight: 20 },
  err: { color: colors.error, marginTop: spacing.md, fontSize: 12 },
});