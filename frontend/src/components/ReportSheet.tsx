import React, { useState } from "react";
import { Modal, Pressable, ScrollView, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi, type Receipt } from "@/src/api/moderation";
import { Body, Btn, Card, Check, Chips, Input, KV, fmt } from "@/src/components/modUi";
import { colors, spacing } from "@/src/theme";

const CATS = ["harassment", "hate_speech", "violence", "sexual_content", "self_harm", "scam_fraud", "ip_infringement", "spam", "child_safety", "other"];

// Raportare unificata: "incalca regulile" (POST /reports existent) SAU "notificare de continut ilegal" (Art. 16, POST /moderation/notices).
// Dupa trimitere afiseaza confirmarea de primire adusa de server (ID, data/ora, continut, cum urmaresti starea).
export default function ReportSheet({ visible, onClose, targetType, targetId }: { visible: boolean; onClose: () => void; targetType: "game" | "user" | "message"; targetId: string }) {
  const { lang } = useI18n();
  const router = useRouter();
  const [kind, setKind] = useState<"rule" | "notice">("rule");
  const [category, setCategory] = useState("other");
  const [reason, setReason] = useState(""); const [details, setDetails] = useState("");
  const [explanation, setExplanation] = useState(""); const [legal, setLegal] = useState(""); const [location, setLocation] = useState("");
  const [name, setName] = useState(""); const [email, setEmail] = useState("");
  const [exempt, setExempt] = useState(false); const [good, setGood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);

  const close = () => { setReceipt(null); setErr(null); onClose(); };
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      if (kind === "rule") {
        const r = await modApi.report({ target_type: targetType, target_id: targetId, reason: (reason.trim() || ms(lang, ("cat_" + category) as any)).slice(0, 100), details: details.trim() || undefined, category });
        setReceipt(r.receipt);
      } else {
        const r = await modApi.notice({
          target_type: targetType, target_id: targetId, category: category === "other" ? "illegal_content" : category, explanation,
          alleged_legal_basis: legal || undefined, content_location: location || undefined, reporter_name: name || undefined,
          reporter_email: email || undefined, identity_exempt: exempt, good_faith_declaration: good,
        });
        setReceipt(r.receipt);
      }
    } catch (e: any) { setErr(modErr(lang, e)); } finally { setBusy(false); }
  };
  const ready = kind === "rule" ? true : explanation.trim().length >= 30 && good && (exempt || (name.trim() && email.trim()));

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="fullScreen" onRequestClose={close}>
      <SafeAreaView style={{ flex: 1, backgroundColor: colors.surface }} edges={["top", "bottom"]}>
        <View style={{ flexDirection: "row", alignItems: "center", padding: spacing.md }}>
          <Pressable onPress={close} hitSlop={8} testID="report-close" style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center" }}>
            <MaterialCommunityIcons name="close" size={24} color={colors.onSurface} />
          </Pressable>
          <Text style={{ flex: 1, textAlign: "center", color: colors.onSurface, fontWeight: "800", fontSize: 16 }}>{ms(lang, "rep_title")}</Text>
          <View style={{ width: 36 }} />
        </View>
        <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60 }} keyboardShouldPersistTaps="handled">
          <View style={{ width: "100%", maxWidth: 760, alignSelf: "center" }}>
            {receipt ? (
              <Card>
                <Text style={{ color: colors.success, fontWeight: "800", fontSize: 16, marginBottom: 10 }} testID="report-receipt-title">
                  {receipt.is_dsa_notice ? ms(lang, "rep_sent_notice_title") : ms(lang, "rep_sent_title")}
                </Text>
                <KV k={ms(lang, "rep_id")} v={receipt.report_id} />
                <KV k={ms(lang, "rep_received_at")} v={fmt(receipt.received_at)} />
                <KV k={ms(lang, "rep_reported_content")} v={receipt.reported_content.summary ?? receipt.reported_content.location} />
                <Body dim>{ms(lang, "rep_follow")}</Body>
                <View style={{ height: 12 }} />
                <Btn label={ms(lang, "rep_open_mine")} onPress={() => { close(); router.push("/moderation/reports"); }} testID="report-open-mine" />
                <Btn kind="ghost" label={ms(lang, "close")} onPress={close} />
              </Card>
            ) : (
              <>
                <Chips value={kind} onChange={setKind} options={[{ value: "rule", label: ms(lang, "rep_kind_rule") }, { value: "notice", label: ms(lang, "rep_kind_notice") }]} />
                <Body dim>{kind === "rule" ? ms(lang, "rep_kind_rule_hint") : ms(lang, "rep_kind_notice_hint")}</Body>
                <View style={{ height: 12 }} />
                <Chips label={ms(lang, "rep_category")} value={category} onChange={setCategory} options={CATS.map(c => ({ value: c, label: ms(lang, ("cat_" + c) as any) }))} />
                {kind === "rule" ? (
                  <>
                    <Input testID="report-reason" label={ms(lang, "rep_reason")} value={reason} onChangeText={setReason} maxLength={100} />
                    <Input testID="report-details" label={ms(lang, "rep_details")} value={details} onChangeText={setDetails} multiline maxLength={2000} />
                  </>
                ) : (
                  <>
                    <Input testID="notice-explanation" label={ms(lang, "rep_explanation")} value={explanation} onChangeText={setExplanation} multiline maxLength={4000} />
                    <Input label={ms(lang, "rep_legal_basis")} value={legal} onChangeText={setLegal} maxLength={500} />
                    <Input label={ms(lang, "rep_location")} value={location} onChangeText={setLocation} maxLength={500} />
                    {category === "child_safety" ? <Check checked={exempt} onToggle={() => setExempt(!exempt)} label={ms(lang, "rep_identity_exempt")} /> : null}
                    {!exempt || category !== "child_safety" ? (
                      <>
                        <Input testID="notice-name" label={ms(lang, "rep_name")} value={name} onChangeText={setName} maxLength={120} />
                        <Input testID="notice-email" label={ms(lang, "rep_email")} value={email} onChangeText={setEmail} keyboardType="email-address" maxLength={320} />
                      </>
                    ) : null}
                    <Check testID="notice-good-faith" checked={good} onToggle={() => setGood(!good)} label={ms(lang, "rep_good_faith")} />
                  </>
                )}
                {err ? <Text style={{ color: colors.error, marginBottom: 10 }} testID="report-error">{err}</Text> : null}
                <Btn testID="report-submit" label={ms(lang, "rep_submit")} onPress={submit} disabled={busy || !ready} />
              </>
            )}
          </View>
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}
