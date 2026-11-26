import React, { useState } from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Btn, Card, Check, Chips, Input, Screen } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const CATS = ["illegal_content", "child_safety", "harassment", "hate_speech", "violence", "sexual_content", "self_harm", "scam_fraud", "ip_infringement", "spam", "other"];

// Caz din initiativa proprie (fara notificator). Decizia se ia apoi in fluxul normal (expunere de motive, drept de plangere, audit).
export default function OwnInitiative() {
  const { lang } = useI18n();
  const router = useRouter();
  const [type, setType] = useState<"game" | "user" | "message">("game");
  const [tid, setTid] = useState(""); const [cat, setCat] = useState("other"); const [sum, setSum] = useState(""); const [auto, setAuto] = useState(false);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setErr(null);
    try { const r = await dsaApi.ownInitiative({ target_type: type, target_id: tid.trim(), category: cat, summary: sum, automated_detection: auto }); router.replace({ pathname: "/moderation/admin/report/[id]", params: { id: r.report_id } }); }
    catch (e: any) { setErr(modErr(lang, e)); } finally { setBusy(false); }
  };
  return (
    <Screen title={ms(lang, "adm_own")} testID="own-initiative">
      <Card>
        <Chips value={type} onChange={setType} options={(["game", "user", "message"] as const).map(v => ({ value: v, label: ms(lang, ("type_" + v) as any) }))} />
        <Input testID="own-target" label={ms(lang, "rep_id")} value={tid} onChangeText={setTid} maxLength={128} />
        <Chips label={ms(lang, "rep_category")} value={cat} onChange={setCat} options={CATS.map(v => ({ value: v, label: ms(lang, ("cat_" + v) as any) }))} />
        <Input testID="own-summary" label={ms(lang, "own_summary")} value={sum} onChangeText={setSum} multiline maxLength={4000} />
        <Check checked={auto} onToggle={() => setAuto(!auto)} label={ms(lang, "own_automated")} />
        {err ? <Text style={{ color: colors.error, marginBottom: 8 }}>{err}</Text> : null}
        <Btn testID="own-submit" label={ms(lang, "send")} onPress={go} disabled={busy || !tid.trim() || sum.trim().length < 10} />
      </Card>
    </Screen>
  );
}
