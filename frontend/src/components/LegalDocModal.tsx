import React, { useEffect, useState } from "react";
import { View, Text, Modal, Pressable, ScrollView, ActivityIndicator, StyleSheet } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing } from "@/src/theme";
import { consentApi, type LegalDocText, type LegalDocType } from "@/src/api/consent";
import { cs, docTitle } from "@/src/i18n/consentStrings";

// Afiseaza textul EXACT servit de backend (backend/legal/*), nu o copie din aplicatie: ce vede utilizatorul = ce
// este versionat si are amprenta SHA-256 in dovada de consimtamant.
export default function LegalDocModal({ visible, docType, lang, version, onClose }: {
  visible: boolean; docType: LegalDocType; lang: string; version?: string; onClose: () => void;
}) {
  const [doc, setDoc] = useState<LegalDocText | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setDoc(null); setFailed(false);
    consentApi.document(docType, lang, version).then(d => alive && setDoc(d)).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [visible, docType, lang, version]);
  return (
    <Modal visible={visible} animationType="slide" presentationStyle="fullScreen" onRequestClose={onClose}>
      <SafeAreaView style={styles.root} edges={["top", "left", "right", "bottom"]}>
        <View style={styles.header}>
          <Pressable testID="legal-doc-close" onPress={onClose} hitSlop={8} style={styles.back}>
            <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.title}>{docTitle(lang, docType)}{doc ? ` · ${cs(lang, "version")} ${doc.version}` : ""}</Text>
          <View style={styles.back} />
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.lg }}>
          {doc ? <Text style={styles.body}>{doc.text}</Text>
            : failed ? <Text style={styles.body}>{cs(lang, "docs_load_failed")}</Text>
            : <ActivityIndicator color={colors.brand} />}
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  back: { width: 36, height: 36, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, textAlign: "center", color: colors.onSurface, fontSize: 16, fontWeight: "700" },
  body: { color: colors.onSurface2, fontSize: 13, lineHeight: 19 },
});