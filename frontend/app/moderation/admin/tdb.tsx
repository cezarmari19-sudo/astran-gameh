import React, { useState } from "react";
import { Text, View } from "react-native";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Badge, Body, Btn, Card, H, Input, KV, LoadState, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Coada Art. 24(5) (doar admin; serverul refuza altfel). Nimic nu e marcat "trimis" fara confirmarea serviciului oficial;
// o inregistrare manuala are nevoie de referinta data de serviciu si e etichetata ca neverificata.
export default function TdbAdmin() {
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => dsaApi.tdb());
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [exp, setExp] = useState<any>(null);
  const [ref, setRef] = useState<Record<string, string>>({}); const [cat, setCat] = useState<Record<string, string>>({});
  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setMsg(null);
    try { await fn(); await reload(); } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };
  const c = data?.config;
  return (
    <Screen title={ms(lang, "tdb_title")} testID="tdb-screen">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {c ? (
        <Card>
          <KV k={ms(lang, "tdb_api")} v={<Badge label={c.api_configured ? "OK" : "—"} tone={c.api_configured ? "ok" : "warn"} />} />
          <KV k={ms(lang, "tdb_required")} v={<Badge label={c.applicability?.status ?? "—"} tone={c.required_by_configured_legal_status ? "info" : "warn"} />} />
          {!c.api_configured ? <Body dim>{ms(lang, "tdb_missing")}: {(c.missing ?? []).join(", ")}</Body> : null}
        </Card>
      ) : null}
      {data ? <Card><Text style={{ color: colors.onSurface, fontWeight: "700", marginBottom: 6 }}>{ms(lang, "tdb_status")}</Text>
        {Object.entries(data.counts as Record<string, number>).filter(([, n]) => n > 0).map(([k, n]) => <KV key={k} k={ms(lang, ("tdb_st_" + k) as any)} v={String(n)} />)}
      </Card> : null}
      <Btn testID="tdb-export" kind="ghost" label={ms(lang, "tdb_export")} disabled={busy} onPress={() => act(async () => setExp(await dsaApi.tdbExport()))} />
      {exp ? (
        <Card>
          <KV k={ms(lang, "tdb_exported")} v={String(exp.statements.length)} />
          <KV k={ms(lang, "tdb_skipped")} v={String(exp.skipped.length)} />
          <Body dim>{exp.instructions}</Body>
          <Text selectable style={{ color: colors.onSurface, fontSize: 11 }}>{JSON.stringify(exp.statements, null, 1)}</Text>
        </Card>
      ) : null}
      {msg ? <Text style={{ color: colors.error }}>{msg.text}</Text> : null}
      {(data?.items ?? []).map((s: any) => (
        <Card key={s.submission_id}>
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
            <Badge label={label(lang, "tdb_st_", s.status)} tone={tone(s.status === "submitted" || s.status === "submitted_manual" ? "resolved" : s.status === "pending" ? "pending" : "rejected")} />
          </View>
          <KV k="SoR" v={s.sor_id} />
          <KV k="↻" v={`${s.attempts} · ${fmt(s.last_attempt_at)}`} />
          {s.last_error ? <Body dim>{s.last_error}</Body> : null}
          {(s.issues ?? []).length ? <Body dim>{s.issues.join(", ")}</Body> : null}
          {s.external_ref ? <KV k={ms(lang, "tdb_ref")} v={s.external_ref.permalink || s.external_ref.uuid || s.external_ref.reference || "—"} /> : null}
          {s.external_ref?.verified_with_service === false ? <Body dim>{ms(lang, "tdb_manual_note")}</Body> : null}
          {s.status === "needs_review" ? (
            <>
              <Input label={ms(lang, "tdb_category")} value={cat[s.submission_id] ?? ""} onChangeText={v => setCat(x => ({ ...x, [s.submission_id]: v }))} maxLength={100} />
              <Btn kind="ghost" label={ms(lang, "tdb_set_category")} disabled={busy || (cat[s.submission_id] ?? "").length < 10} onPress={() => act(() => dsaApi.tdbCategory(s.submission_id, cat[s.submission_id]))} />
            </>
          ) : null}
          {["failed", "abandoned"].includes(s.status) ? <Btn kind="ghost" label={ms(lang, "tdb_retry")} disabled={busy} onPress={() => act(() => dsaApi.tdbRetry(s.submission_id))} /> : null}
          {["exported", "failed", "abandoned", "rejected", "needs_reconciliation", "pending"].includes(s.status) ? (
            <>
              <Input label={ms(lang, "tdb_confirm")} value={ref[s.submission_id] ?? ""} onChangeText={v => setRef(x => ({ ...x, [s.submission_id]: v }))} maxLength={300} />
              <Btn kind="ghost" label={ms(lang, "tdb_confirm")} disabled={busy || (ref[s.submission_id] ?? "").length < 4} onPress={() => act(() => dsaApi.tdbConfirm(s.submission_id, ref[s.submission_id]))} />
            </>
          ) : null}
        </Card>
      ))}
    </Screen>
  );
}
