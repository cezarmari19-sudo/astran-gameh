import React, { useState } from "react";
import { Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, Btn, Card, KV, LoadState, Row, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";

// "Raportarile mele": serverul intoarce DOAR raportarile contului curent si fara note interne / date de moderatori.
export default function MyReports() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => modApi.myReports());
  const [open, setOpen] = useState<string | null>(null);
  const list = data?.reports ?? [];
  return (
    <Screen title={ms(lang, "mod_my_reports")} testID="my-reports">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {!loading && !error && list.length === 0 ? <Body dim>{ms(lang, "my_reports_empty")}</Body> : null}
      {list.map((r: any) => (
        <View key={r.report_id}>
          <Row onPress={() => setOpen(open === r.report_id ? null : r.report_id)} testID={`report-${r.report_id}`}>
            <View style={{ flexDirection: "row", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
              <Badge label={label(lang, "status_", r.status)} tone={tone(r.status)} />
              {r.is_dsa_notice ? <Badge label={ms(lang, "kind_illegal_content_notice")} tone="info" /> : null}
            </View>
            <Text style={{ color: "#fff", fontWeight: "700" }}>{label(lang, "type_", r.target_type)} · {label(lang, "cat_", r.category)}</Text>
            <Body dim>{fmt(r.created_at)}</Body>
          </Row>
          {open === r.report_id ? <ReportDetail id={r.report_id} onDecision={(id) => router.push({ pathname: "/moderation/decision/[id]", params: { id } })} /> : null}
        </View>
      ))}
    </Screen>
  );
}

function ReportDetail({ id, onDecision }: { id: string; onDecision: (id: string) => void }) {
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => modApi.myReport(id), [id]);
  if (loading || error) return <LoadState loading={loading} error={error} onRetry={reload} />;
  const d = data!;
  return (
    <Card>
      <KV k={ms(lang, "rep_id")} v={d.report_id} />
      <KV k={ms(lang, "rep_received_at")} v={fmt(d.created_at)} />
      <KV k={ms(lang, "rep_reported_content")} v={`${label(lang, "type_", d.target_type)} · ${d.target_id}`} />
      {d.reason ? <KV k={ms(lang, "rep_reason")} v={d.reason} /> : null}
      {d.notice?.explanation ? <KV k={ms(lang, "rep_explanation")} v={d.notice.explanation} /> : null}
      {(d.outcomes ?? []).map((o: any) => (
        <View key={o.decision_id}>
          <KV k={ms(lang, "my_outcome")} v={`${fmt(o.decided_at)} · ${o.reasoning}`} />
          <Btn kind="ghost" label={ms(lang, "my_view_outcome")} onPress={() => onDecision(o.decision_id)} testID={`open-outcome-${o.decision_id}`} />
        </View>
      ))}
    </Card>
  );
}
