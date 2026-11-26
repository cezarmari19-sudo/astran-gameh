import React, { useState } from "react";
import { Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, Btn, Card, H, Input, KV, LoadState, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Expunerea de motive (Art. 17) pentru utilizatorul afectat SAU rezultatul notificarii pentru cel care a notificat,
// plus formularul de plangere (Art. 20) si starea plangerii. Serverul decide cine are acces (404 pentru restul).
export default function DecisionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => modApi.myDecision(String(id)), [id]);
  const [grounds, setGrounds] = useState("");
  const [evidence, setEvidence] = useState("");
  const [links, setLinks] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async () => {
    setBusy(true); setMsg(null);
    try {
      const urls = links.split("\n").map(x => x.trim()).filter(Boolean);
      await modApi.appeal(String(id), { grounds, evidence_text: evidence || undefined, evidence_urls: urls });
      setMsg({ ok: true, text: ms(lang, "appeal_done") });
      reload();
    } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };

  if (loading || error) return <Screen title={ms(lang, "dec_title")}><LoadState loading={loading} error={error} onRetry={reload} /></Screen>;
  const d = data!;
  const sor = d.statement_of_reasons;
  const ap = d.appeal;
  const redress = d.redress ?? d.outcome?.redress;
  return (
    <Screen title={ms(lang, "dec_title")} testID="decision-screen">
      {d.role === "affected" ? (
        <>
          <View style={{ flexDirection: "row", gap: 8, marginBottom: 8 }}>
            <Badge label={label(lang, "dec_", d.decision.status)} tone={d.decision.status === "reversed" ? "ok" : "warn"} />
          </View>
          {sor ? (
            <Card>
              <H>{ms(lang, "dec_sor")}</H>
              <KV k={ms(lang, "dec_decision")} v={label(lang, "act_", sor.decision.action)} />
              {sor.decision.restriction_until ? <KV k={ms(lang, "dec_until")} v={fmt(sor.decision.restriction_until)} /> : null}
              <KV k={ms(lang, "dec_content")} v={`${label(lang, "type_", sor.content.target_type)} · ${sor.content.summary ?? sor.content.target_id}`} />
              <KV k={ms(lang, "dec_facts")} v={sor.facts_and_circumstances} />
              <KV k={ms(lang, "dec_basis")} v={sor.basis.type === "illegal_content" ? ms(lang, "dec_basis_illegal") : ms(lang, "dec_basis_terms")} />
              {sor.basis.legal_ground ? <KV k={ms(lang, "dec_legal_ground")} v={sor.basis.legal_ground} /> : null}
              {sor.basis.tos_provision ? <KV k={ms(lang, "dec_tos")} v={`${sor.basis.tos_provision} (${sor.basis.tos_document})`} /> : null}
              <KV k={ms(lang, "dec_automated")} v={sor.automated_means.used_for_detection ? ms(lang, "dec_automated_detect") : ms(lang, "dec_automated_no")} />
              {sor.initiated_by ? <KV k={ms(lang, "dec_initiated")} v={ms(lang, ("dec_init_" + sor.initiated_by) as any)} /> : null}
              <KV k={ms(lang, "dec_issued")} v={fmt(sor.issued_at)} />
              {d.delivery ? <KV k={ms(lang, "dec_delivery")} v={ms(lang, ({ sent: "del_user_sent", pending: "del_user_pending", not_sent: "del_user_not_sent", no_email_on_file: "del_user_no_email" } as any)[d.delivery.status] ?? "del_user_pending")} /> : null}
            </Card>
          ) : <Body dim>{ms(lang, "dec_no_statement")}</Body>}
        </>
      ) : (
        <Card>
          <H>{ms(lang, "dec_notifier_note")}</H>
          <KV k={ms(lang, "dec_issued")} v={fmt(d.outcome.decided_at)} />
          <KV k={ms(lang, "appeal_reasoning")} v={d.outcome.reasoning} />
        </Card>
      )}

      {(d.corrections ?? []).length ? (
        <Card>
          <H>{ms(lang, "dec_corrections")}</H>
          {d.corrections.map((c: any) => <KV key={c.correction_id} k={fmt(c.created_at)} v={c.text} />)}
        </Card>
      ) : null}

      {redress ? (
        <Card>
          <H>{ms(lang, "dec_redress")}</H>
          <Body>{ms(lang, "dec_redress_internal")} {fmt(redress.internal_complaint.deadline)}</Body>
          <Text style={{ color: colors.onSurface3, marginTop: 8, fontSize: 12 }}>{ms(lang, "dec_redress_ods")}</Text>
          <Body>{redress.out_of_court_settlement?.bodies || ms(lang, "dec_redress_ods_none")}</Body>
          <Body dim>{ms(lang, "dec_redress_court")}</Body>
        </Card>
      ) : null}

      {d.decision?.status === "modified" ? <Body dim>{ms(lang, "dec_modified_note")}</Body> : null}
      <H>{ms(lang, "appeal_title")}</H>
      <Body dim>{ms(lang, "appeal_how")}</Body>
      {ap ? (
        <Card>
          <Badge label={label(lang, "appeal_", ap.status)} tone={tone(ap.status)} />
          <Text />
          <KV k={ms(lang, "appeal_grounds")} v={ap.grounds} />
          <KV k={ms(lang, "appeal_sent")} v={fmt(ap.created_at)} />
          {ap.review_started_at ? <KV k={ms(lang, "appeal_review_started")} v={fmt(ap.review_started_at)} /> : null}
          {ap.resolved_at ? <KV k={ms(lang, "appeal_resolved_at")} v={fmt(ap.resolved_at)} /> : null}
          {ap.outcome ? <KV k={ms(lang, "appeal_reasoning")} v={ap.outcome_reasoning} /> : null}
        </Card>
      ) : (
        <Card>
          <Input testID="appeal-grounds" label={ms(lang, "appeal_grounds")} value={grounds} onChangeText={setGrounds} multiline maxLength={4000} />
          <Input testID="appeal-evidence" label={ms(lang, "appeal_evidence")} value={evidence} onChangeText={setEvidence} multiline maxLength={4000} />
          <Input testID="appeal-links" label={ms(lang, "appeal_links")} value={links} onChangeText={setLinks} multiline />
          {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error, marginBottom: 8 }} testID="appeal-msg">{msg.text}</Text> : null}
          <Btn testID="appeal-submit" label={ms(lang, "appeal_submit")} onPress={submit} disabled={busy || grounds.trim().length < 20} />
        </Card>
      )}
    </Screen>
  );
}
