import React, { useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Body, Btn, Card, Input, LoadState, Screen, fmt, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Jurnal de audit (doar citire; serverul nu are rute de modificare/stergere) + verificarea lantului de hash-uri.
export default function AuditLog() {
  const { lang } = useI18n();
  const [rid, setRid] = useState("");
  const [applied, setApplied] = useState("");
  const [chain, setChain] = useState<string | null>(null);
  const [chainOk, setChainOk] = useState(true);
  const { data, error, loading, reload } = useLoad(() => modApi.audit({ report_id: applied || undefined }), [applied]);
  const verify = async () => {
    try {
      const r = await modApi.auditVerify();
      setChainOk(r.ok); setChain(r.ok ? `${ms(lang, "adm_chain_ok")} (${r.checked})` : `${ms(lang, "adm_chain_broken")} ${r.broken_at_seq}`);
    } catch (e: any) { setChainOk(false); setChain(modErr(lang, e)); }
  };
  return (
    <Screen title={ms(lang, "adm_audit")} testID="admin-audit">
      <Card>
        <Input value={rid} onChangeText={setRid} placeholder={ms(lang, "adm_report_id_filter")} />
        <Btn kind="ghost" label={ms(lang, "adm_load")} onPress={() => setApplied(rid.trim())} />
        <Btn kind="ghost" label={ms(lang, "adm_verify")} onPress={verify} testID="audit-verify" />
        {chain ? <Text style={{ color: chainOk ? colors.success : colors.error }} testID="audit-chain">{chain}</Text> : null}
      </Card>
      <LoadState loading={loading} error={error} onRetry={reload} />
      {(data?.events ?? []).map((e: any) => (
        <Card key={e.event_id}>
          <Body bold>#{e.seq} {e.event_type}{e.action ? ` · ${e.action}` : ""}</Body>
          <Body dim>{fmt(e.ts)} · {e.actor_id} · {e.report_id ?? ""}</Body>
          {e.previous_status || e.new_status ? <Body dim>{e.previous_status ?? "—"} → {e.new_status ?? "—"}{e.outcome ? ` · ${e.outcome}` : ""}</Body> : null}
          {e.reasoning ? <Body>{e.reasoning}</Body> : null}
        </Card>
      ))}
    </Screen>
  );
}
