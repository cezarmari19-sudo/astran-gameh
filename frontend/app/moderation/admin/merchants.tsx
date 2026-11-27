import React, { useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { cs, csErr, type CsKey } from "@/src/i18n/complianceStrings";
import { merchantAdminApi } from "@/src/api/compliance";
import { Badge, Body, Btn, Card, Chips, H, Input, KV, LoadState, Row, Screen, fmt, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const STATES = ["", "unverified", "in_review", "verified", "rejected"];
const vTone = (s: string) => (s === "verified" ? "ok" : s === "rejected" ? "bad" : "warn") as any;

// DSA Art. 30-32: verificarea informatiilor comerciantilor. Acces DOAR pentru admin / responsabil juridic (autorizarea reala e pe backend).
// NU se cer si NU se stocheaza copii ale documentelor de identitate: se inregistreaza metoda, rezultatul si nota.
export default function Merchants() {
  const { lang } = useI18n();
  const [state, setState] = useState(""); const [sel, setSel] = useState<string | null>(null);
  const list = useLoad(() => merchantAdminApi.list(state || undefined), [state]);
  if (list.error && /403|autoriz|authoriz|forbidden/i.test(list.error)) return <Screen title={cs(lang, "mer_title")}><Body>{cs(lang, "inc_staff_only")}</Body></Screen>;
  if (sel) return <Detail userId={sel} onBack={() => { setSel(null); list.reload(); }} />;
  return (
    <Screen title={cs(lang, "mer_title")} testID="admin-merchants">
      <Chips label={cs(lang, "mer_filter")} value={state} onChange={setState} options={STATES.map(s => ({ value: s, label: s ? cs(lang, `vs_${s}` as CsKey) : cs(lang, "mer_all") }))} />
      <LoadState loading={list.loading} error={list.error} onRetry={list.reload} />
      {!list.loading && !list.error && !(list.data?.merchants ?? []).length ? <Body dim>{cs(lang, "mer_empty")}</Body> : null}
      {(list.data?.merchants ?? []).map((m: any) => (
        <Row key={m.user_id} onPress={() => setSel(m.user_id)} testID={`mer-${m.user_id}`}>
          <Body bold>{m.trader?.name ?? m.user_id}</Body>
          <Badge label={cs(lang, `vs_${m.verification?.state ?? "unverified"}` as CsKey)} tone={vTone(m.verification?.state)} />
        </Row>
      ))}
    </Screen>
  );
}

function Detail({ userId, onBack }: { userId: string; onBack: () => void }) {
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => merchantAdminApi.get(userId), [userId]);
  const [note, setNote] = useState(""); const [reason, setReason] = useState(""); const [err, setErr] = useState("");
  const act = async (fn: () => Promise<any>) => { setErr(""); try { await fn(); await reload(); } catch (e: any) { setErr(csErr(lang, e)); } };
  const p = data?.profile; const t = p?.trader;
  return (
    <Screen title={t?.name ?? userId} testID="admin-merchant-detail">
      <Btn kind="ghost" label={cs(lang, "inc_back")} onPress={onBack} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {p ? (
        <>
          <Card>
            <Badge label={cs(lang, `vs_${p.verification?.state ?? "unverified"}` as CsKey)} tone={vTone(p.verification?.state)} />
            {t ? (<>
              <KV k={cs(lang, "f_name")} v={`${t.name} (${t.trader_type})`} />
              <KV k={cs(lang, "si_address")} v={`${t.address.line1}${t.address.line2 ? ", " + t.address.line2 : ""}, ${t.address.postal_code} ${t.address.city}, ${t.address.country}`} />
              <KV k={cs(lang, "si_phone")} v={t.phone} /><KV k={cs(lang, "si_email")} v={t.contact_email} />
              <KV k={cs(lang, "si_registry")} v={t.registry?.number ? `${t.registry.register_name} · ${t.registry.number}` : (t.registry?.not_applicable_reason ?? "—")} />
              <KV k={cs(lang, "f_vat")} v={t.vat_id || "—"} />
            </>) : null}
            {p.verification?.note ? <Body dim>{p.verification.note}</Body> : null}
          </Card>
          <Card>
            <H>{cs(lang, "mer_signals")}</H>
            <KV k={cs(lang, "mer_sales")} v={String(data.signals.sales_12m)} /><KV k={cs(lang, "mer_revenue")} v={String(data.signals.revenue_astrans_12m)} />
            {data.signals.review_suggested ? <Badge label={cs(lang, "mer_suggested")} tone="warn" /> : null}
          </Card>
          {t ? (
            <Card>
              <Input label={cs(lang, "mer_note")} value={note} onChangeText={setNote} multiline testID="mer-note" />
              <Btn label={cs(lang, "mer_verify")} onPress={() => act(() => merchantAdminApi.review(userId, "verified", note))} testID="mer-verify" />
              <Btn kind="ghost" label={cs(lang, "mer_in_review")} onPress={() => act(() => merchantAdminApi.review(userId, "in_review", note))} />
              <Btn kind="danger" label={cs(lang, "mer_reject")} onPress={() => act(() => merchantAdminApi.review(userId, "rejected", note))} testID="mer-reject" />
            </Card>
          ) : null}
          <Card>
            <Input label={cs(lang, "mer_reason")} value={reason} onChangeText={setReason} multiline />
            <Btn kind="ghost" label={cs(lang, "mer_reclassify")} onPress={() => act(() => merchantAdminApi.reclassify(userId, reason))} disabled={reason.trim().length < 10} testID="mer-reclassify" />
          </Card>
          {err ? <Text style={{ color: colors.error }} testID="mer-error">{err}</Text> : null}
          <Card>
            <H>{cs(lang, "mer_history")}</H>
            {(data.history ?? []).map((h: any) => <Body key={h.event_id} dim>{fmt(h.ts)} · {h.action} · {h.actor_role ?? ""}</Body>)}
          </Card>
        </>
      ) : null}
    </Screen>
  );
}
