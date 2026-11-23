import React, { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, spacing } from "@/src/theme";
import { useI18n } from "@/src/i18n";
import { cr } from "@/src/i18n/consumerStrings";
import { consumerApi } from "@/src/api/consumer";

/** Corpul politicii: textul EXACT servit de backend (legal/consumer, versionat, cu amprenta SHA-256). Folosit in modal si in ruta /refund-policy. */
export function ConsumerDocBody({ doc = "refund_policy" }: { doc?: string }) {
  const { lang } = useI18n();
  const [d, setD] = useState<{ text: string; version: string } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true; setD(null); setFailed(false);
    consumerApi.document(doc, lang).then(x => alive && setD(x)).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [doc, lang]);
  if (failed) return <Text style={s.err}>{cr(lang, "err_generic")}</Text>;
  if (!d) return <ActivityIndicator color={colors.brand} style={{ margin: 24 }} />;
  return <Text style={s.text} selectable testID="refund-policy-text">{d.text}{"\n\n"}v{d.version}</Text>;
}

export default function ConsumerDocModal({ visible, onClose, doc = "refund_policy" }: { visible: boolean; onClose: () => void; doc?: string }) {
  const { lang } = useI18n();
  return (
    <Modal visible={visible} animationType="slide" presentationStyle="fullScreen" onRequestClose={onClose}>
      <SafeAreaView style={s.root} edges={["top", "left", "right", "bottom"]}>
        <View style={s.header}>
          <Pressable testID="consumer-doc-close" onPress={onClose} hitSlop={8}><MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} /></Pressable>
          <Text style={s.title}>{cr(lang, "policy_title")}</Text>
        </View>
        <ScrollView contentContainerStyle={s.content}><View style={s.column}><ConsumerDocBody doc={doc} /></View></ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

/** Link standard catre politica de rambursare (Portofel, Magazin, Setari, comanda, checkout). */
export function RefundPolicyLink({ testID = "refund-policy-link", style }: { testID?: string; style?: any }) {
  const { lang } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Pressable onPress={() => setOpen(true)} testID={testID} hitSlop={6}><Text style={[s.link, style]}>{cr(lang, "policy_open")}</Text></Pressable>
      {open ? <ConsumerDocModal visible onClose={() => setOpen(false)} /> : null}
    </>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", gap: 8, padding: spacing.md, borderBottomWidth: 1, borderBottomColor: colors.border },
  title: { color: colors.onSurface, fontSize: 17, fontWeight: "700", flex: 1 },
  content: { padding: spacing.md, alignItems: "center" },
  column: { width: "100%", maxWidth: 720 },   // telefon: latime completa; desktop: coloana centrata, text lizibil
  text: { color: colors.onSurface2, fontSize: 15, lineHeight: 22 },
  err: { color: colors.error },
  link: { color: colors.brand, textDecorationLine: "underline", fontSize: 14 },
});
