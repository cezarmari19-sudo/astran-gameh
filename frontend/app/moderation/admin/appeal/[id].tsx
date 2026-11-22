import React, { useState } from "react";
import { Text } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, Btn, Card, Chips, H, Input, KV, LoadState, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Revizuirea unei plangeri (Art. 20): un om, diferit de cel care a decis. Regula "alt revizor" e impusa de server (403).
export default function AdminAppeal() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const aid = String(id);
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => modApi.adminAppeal(aid), [aid]);
  const [outcome, setOutcome] = useState<"maintain" | "reverse">("maintain");
  const [reasoning, setReasoning] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ ok: true, text: "OK" }); await reload(); } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };
  if (loading || error) return <Screen title={ms(lang, "appeal_title")}><LoadState loading={loading} error={error} onRetry={reload} /></Screen>;
  const { appeal: a, decision: d, statement_of_reasons: sor, history } = data!;
  const open = a.status === "received" || a.status === "under_review";
  return (
    <Screen title={ms(lang, "appeal_title")} testID="admin-appeal-detail">
      <Badge label={label(lang, "appeal_", a.status)} tone={tone(a.status)} />
      {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error, marginVertical: 8 }} testID="admin-msg">{msg.text}</Text> : null}
      <Card>
        <KV k={ms(lang, "adm_grounds")} v={a.grounds} />
        {a.evidence_text ? <KV k={ms(lang, "appeal_evidence")} v={a.evidence_text} /> : null}
        {(a.evidence_urls ?? []).map((u: string) => <KV key={u} k="link" v={u} />)}
        <KV k={ms(lang, "appeal_sent")} v={fmt(a.created_at)} />
        {a.outcome ? <KV k={ms(lang, "appeal_reasoning")} v={a.outcome_reasoning} /> : null}
      </Card>
      <H>{ms(lang, "adm_original_decision")}</H>
      <Card>
        <Body bold>{label(lang, "act_", d.action)}</Body>
        <Body dim>{ms(lang, "adm_decision_by")} {d.moderator_id} · {fmt(d.created_at)}</Body>
        <Body>{d.reasoning}</Body>
        {sor ? <Body dim>{ms(lang, "dec_sor")}: {sor.sor_id}</Body> : null}
      </Card>
      {open ? (
        <Card>
          {a.status === "received" ? <Btn kind="ghost" label={ms(lang, "adm_start_review")} disabled={busy} onPress={() => act(() => modApi.startReview(aid))} testID="appeal-start" /> : null}
          <Chips value={outcome} onChange={setOutcome} options={[{ value: "maintain", label: ms(lang, "appeal_maintain") }, { value: "reverse", label: ms(lang, "appeal_reverse") }]} />
          <Input testID="appeal-resolve-reasoning" label={ms(lang, "appeal_reasoning")} value={reasoning} onChangeText={setReasoning} multiline maxLength={4000} />
          <Btn testID="appeal-resolve" label={ms(lang, "adm_resolve")} disabled={busy || reasoning.trim().length < 20} onPress={() => act(() => modApi.resolveAppeal(aid, outcome, reasoning))} />
        </Card>
      ) : null}
      <H>{ms(lang, "adm_history")}</H>
      {history.map((e: any) => (
        <Card key={e.event_id}><Body bold>{e.event_type}</Body><Body dim>{fmt(e.ts)} · {e.actor_id}</Body>{e.reasoning ? <Body>{e.reasoning}</Body> : null}</Card>
      ))}
    </Screen>
  );
}
