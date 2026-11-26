import React, { useState } from "react";
import { Text, View } from "react-native";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Badge, Body, Btn, Card, Chips, KV, LoadState, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Starea livrarilor pe email (expuneri de motive, rezultate plangeri, raspunsuri catre autoritati). Fara adrese de email in aceasta lista.
export default function Deliveries() {
  const { lang } = useI18n();
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(() => dsaApi.deliveries(status || undefined), [status]);
  const [err, setErr] = useState<string | null>(null);
  const retry = async (id: string) => { setErr(null); try { await dsaApi.retryDelivery(id); await reload(); } catch (e: any) { setErr(modErr(lang, e)); } };
  const opts = ["failed", "abandoned", "pending", "sent", "skipped_no_contact"];
  return (
    <Screen title={ms(lang, "del_title")} testID="deliveries-screen">
      <Chips value={status} onChange={setStatus} options={[{ value: "", label: ms(lang, "status_all") }, ...opts.map(v => ({ value: v, label: ms(lang, ("del_" + v) as any) }))]} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data ? <Card>{Object.entries(data.counts).filter(([, n]) => n > 0).map(([k, n]) => <KV key={k} k={ms(lang, ("del_" + k) as any)} v={String(n)} />)}</Card> : null}
      {err ? <Text style={{ color: colors.error }}>{err}</Text> : null}
      {(data?.items ?? []).map((d: any) => (
        <Card key={d.delivery_id}>
          <View style={{ flexDirection: "row", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
            <Badge label={label(lang, "del_", d.status)} tone={tone(d.status === "sent" ? "resolved" : d.status === "failed" || d.status === "abandoned" ? "rejected" : "pending")} />
            <Badge label={d.kind} tone="info" />
          </View>
          <KV k={ms(lang, "del_attempts")} v={String(d.attempts)} />
          <KV k={ms(lang, "rep_received_at")} v={fmt(d.created_at)} />
          {d.last_error ? <KV k={ms(lang, "del_error")} v={d.last_error} /> : null}
          {["failed", "abandoned"].includes(d.status) ? <Btn kind="ghost" label={ms(lang, "del_retry")} onPress={() => retry(d.delivery_id)} /> : null}
        </Card>
      ))}
      {data && data.items.length === 0 ? <Body dim>{ms(lang, "empty")}</Body> : null}
    </Screen>
  );
}
