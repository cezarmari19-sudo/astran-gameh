import React, { useState } from "react";
import { Linking } from "react-native";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Body, Btn, Card, H, KV, LoadState, Screen, fmt, useLoad } from "@/src/components/modUi";

const day = (d: any) => { try { return new Date(d).toISOString().slice(0, 10); } catch { return String(d); } };

// Pagina PUBLICA a rapoartelor de transparenta (Art. 15). Cifrele vin din rapoartele publicate de un administrator, calculate din date
// reale; aici nu exista valori fixe. Descarcarile JSON/CSV sunt endpointuri publice.
export default function Transparency() {
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => dsaApi.transparencyList());
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const toggle = async (id: string) => {
    if (open === id) { setOpen(null); return; }
    setOpen(id); setDetail(null);
    try { setDetail((await dsaApi.transparencyGet(id)).report); } catch { setDetail(null); }
  };
  const n = detail?.notices_art_16, c = detail?.complaints_art_20, o9 = detail?.orders?.art_9_illegal_content, o10 = detail?.orders?.art_10_information;
  return (
    <Screen title={ms(lang, "tr_title")} testID="transparency-screen">
      <Body dim>{ms(lang, "tr_intro")}</Body>
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && data.reports.length === 0 ? <Body dim>{ms(lang, "tr_none")}</Body> : null}
      {(data?.reports ?? []).map((r: any) => (
        <Card key={r.report_id}>
          <KV k={ms(lang, "tr_period")} v={`${day(r.period_start)} → ${day(r.period_end)}`} />
          <KV k={ms(lang, "tr_version")} v={String(r.version)} />
          <KV k={ms(lang, "tr_published")} v={fmt(r.published_at)} />
          <Btn kind="ghost" testID={`tr-json-${r.report_id}`} label={ms(lang, "tr_json")} onPress={() => Linking.openURL(dsaApi.transparencyJsonUrl(r.report_id))} />
          <Btn kind="ghost" testID={`tr-csv-${r.report_id}`} label={ms(lang, "tr_csv")} onPress={() => Linking.openURL(dsaApi.transparencyCsvUrl(r.report_id))} />
          <Btn kind="ghost" label={open === r.report_id ? ms(lang, "close") : ms(lang, "tr_view")} onPress={() => toggle(r.report_id)} testID={`tr-open-${r.report_id}`} />
          {open === r.report_id && detail ? (
            <>
              <H>{ms(lang, "tr_notices")}</H>
              <KV k={ms(lang, "tr_notices")} v={String(n.received_total)} />
              <KV k={ms(lang, "tr_decided")} v={String(n.decided)} />
              <KV k={ms(lang, "tr_median")} v={n.median_hours_to_decision ?? "—"} />
              <KV k={ms(lang, "tr_own")} v={String(detail.own_initiative_moderation.decisions_total)} />
              <KV k={ms(lang, "tr_automated")} v={String(detail.automated_moderation.decisions_with_automated_detection)} />
              <KV k={ms(lang, "tr_complaints")} v={String(c.received_total)} />
              <KV k={ms(lang, "tr_reversed")} v={String(c.outcomes.decision_reversed + c.outcomes.decision_modified)} />
              <KV k={ms(lang, "tr_orders9")} v={String(o9.received_total)} />
              <KV k={ms(lang, "tr_orders10")} v={String(o10.received_total)} />
              <Body dim>{ms(lang, "tr_note")}</Body>
            </>
          ) : null}
        </Card>
      ))}
    </Screen>
  );
}
