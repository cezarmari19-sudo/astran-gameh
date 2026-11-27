import React, { useEffect, useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { cs, csErr } from "@/src/i18n/complianceStrings";
import { recommendationApi } from "@/src/api/compliance";
import { Body, Btn, Card, Check, H } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// DSA Art. 27: controlul utilizatorului asupra personalizarii din Discover. Implicit OPRIT (opt-in). Starea traieste pe server
// (`user_settings.personalized_recommendations`) si e singura care conteaza la ordonarea listei „Pentru tine”.
export default function RecommendationControls({ onOpenTerms }: { onOpenTerms?: () => void }) {
  const { lang } = useI18n();
  const [on, setOn] = useState<boolean | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { recommendationApi.getSetting().then(setOn).catch(() => setOn(false)); }, []);
  const toggle = async () => {
    const next = !on; setMsg(null);
    try { await recommendationApi.setSetting(next); setOn(next); setMsg({ ok: true, text: cs(lang, "rec_saved") }); }
    catch (e: any) { setMsg({ ok: false, text: csErr(lang, e) }); }       // starea ramane cea de pe server
  };
  const clear = async () => {
    try { await recommendationApi.clearHistory(); setMsg({ ok: true, text: cs(lang, "rec_cleared") }); }
    catch (e: any) { setMsg({ ok: false, text: csErr(lang, e) }); }
  };
  return (
    <Card>
      <H>{cs(lang, "rec_title")}</H>
      <Check checked={!!on} onToggle={toggle} label={cs(lang, "rec_personalize")} testID="rec-personalize" />
      <Body dim>{cs(lang, "rec_personalize_desc")}</Body>
      <Btn kind="ghost" label={cs(lang, "rec_clear")} onPress={clear} testID="rec-clear" />
      {onOpenTerms ? <Btn kind="ghost" label={cs(lang, "rec_terms")} onPress={onOpenTerms} testID="rec-terms" /> : null}
      {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error }} testID="rec-message">{msg.text}</Text> : null}
    </Card>
  );
}
