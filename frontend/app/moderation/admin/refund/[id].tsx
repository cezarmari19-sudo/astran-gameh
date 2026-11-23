import React, { useState } from "react";
import { Alert, Platform, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { Badge, Body, Btn, Card, Check, H, Input, KV, LoadState, Screen, fmt, useLoad } from "@/src/components/modUi";
import { useI18n } from "@/src/i18n";
import { cr, crErr, type CrKey } from "@/src/i18n/consumerStrings";
import { consumerApi, refundTone } from "@/src/api/consumer";

function confirmAction(msg: string, ok: () => void) {
  if (Platform.OS === "web") { if (typeof window !== "undefined" && window.confirm(msg)) ok(); return; }
  Alert.alert(msg, undefined, [{ text: "Cancel", style: "cancel" }, { text: "OK", onPress: ok }]);
}

export default function AdminRefundDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { lang } = useI18n();
  const { data: d, error, loading, reload } = useLoad(() => consumerApi.admin.detail(String(id)), [id]);
  const [reasoning, setReasoning] = useState("");
  const [override, setOverride] = useState(false);
  const [ext, setExt] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function act(fn: () => Promise<any>) {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg(cr(lang, "adm_done")); await reload(); } catch (e: any) { setMsg(crErr(lang, e)); } finally { setBusy(false); }
  }
  const r = d?.request;
  const plan = r?.plan || d?.plan_preview;
  return (
    <Screen title={cr(lang, "adm_detail")} testID="admin-refund-detail">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {r ? (
        <>
          <Card>
            <KV k={cr(lang, "status")} v={<Badge label={cr(lang, `st_${r.status}` as CrKey)} tone={refundTone(r.status)} />} />
            {r.processing_state && r.processing_state !== "idle" ? <KV k="processing" v={`${r.processing_state}${r.last_error ? ` (${r.last_error})` : ""}`} /> : null}
            <KV k={cr(lang, "reason")} v={cr(lang, `reason_${r.reason}` as CrKey)} />
            {r.explanation ? <KV k={cr(lang, "explanation")} v={r.explanation} /> : null}
            <KV k="user" v={r.user_id} />
            <KV k={cr(lang, "adm_user_balance")} v={String(d.user_balance ?? "—")} />
            <KV k={cr(lang, "withdrawal")} v={r.eligibility.waiver_valid ? cr(lang, "withdrawal_waived") : r.eligibility.withdrawal_right_applies ? `${cr(lang, "withdrawal_applies")} ${fmt(r.eligibility.withdrawal_deadline)}` : cr(lang, "withdrawal_expired")} />
          </Card>
          <Card>
            <H>{cr(lang, "adm_tx")}</H>
            <Body>{JSON.stringify({ tx_id: d.transaction?.tx_id, type: d.transaction?.type, amount: d.transaction?.amount, price: d.transaction?.price, currency: d.transaction?.currency, provider: d.transaction?.provider, provider_ref: d.transaction?.provider_ref, at: d.transaction?.timestamp }, null, 1)}</Body>
            {d.order ? <><H>{cr(lang, "adm_order")}</H><Body>{`${d.order.order_id}\n${d.order.product_name} · ${d.order.price_display} ${d.order.currency}\n${d.order.withdrawal_right.status} · terms v${d.order.terms_version} · policy v${d.order.refund_policy_version}`}</Body></> : <Body dim>—</Body>}
            {d.usage ? <Body dim>{`${cr(lang, "adm_usage")}: ${d.usage.content_first_accessed_at ? `${cr(lang, "adm_content_accessed")} (${fmt(d.usage.content_first_accessed_at)})` : cr(lang, "adm_not_accessed")}`}</Body> : null}
          </Card>
          <Card>
            <H>{cr(lang, "adm_consents")}</H>
            {d.consents.length === 0 ? <Body dim>—</Body> : d.consents.map((c: any) => <Body key={c.consent_id}>{`${fmt(c.created_at)} · v${c.texts_version} · ${c.text_sha256.slice(0, 12)}…\n${c.text}`}</Body>)}
          </Card>
          {plan && !plan.error ? (
            <Card>
              <H>{cr(lang, "adm_plan")}</H>
              {plan.kind === "purchase_refund" ? (
                <>
                  <KV k={cr(lang, "plan_money")} v={`${plan.money_refund_minor} ${plan.currency}`} />
                  <KV k={cr(lang, "plan_reclaim")} v={String(plan.astrans_to_reclaim)} />
                  <KV k={cr(lang, "plan_unrecovered")} v={String(plan.spent_astrans_unrecovered)} />
                </>
              ) : (
                <>
                  <KV k={cr(lang, "plan_buyer_credit")} v={String(plan.buyer_credit)} />
                  <KV k={cr(lang, "plan_seller")} v={String(plan.seller_reclaim)} />
                  <KV k={cr(lang, "plan_absorbed")} v={String(plan.platform_absorbed)} />
                </>
              )}
            </Card>
          ) : plan?.error ? <Card><Body>{plan.error}</Body></Card> : null}

          {["pending", "under_review", "approved"].includes(r.status) ? (
            <Card>
              {r.status === "pending" ? <Btn kind="ghost" label={cr(lang, "adm_review")} disabled={busy} onPress={() => act(() => consumerApi.admin.review(r.refund_id))} testID="admin-review" /> : null}
              {r.status !== "approved" || ["idle", "failed_retryable", "failed_definitive"].includes(r.processing_state) ? (
                <>
                  <Input label={cr(lang, "adm_reasoning")} value={reasoning} onChangeText={setReasoning} multiline maxLength={2000} testID="admin-reasoning" />
                  <Check checked={override} onToggle={() => setOverride(v => !v)} label={cr(lang, "adm_override")} testID="admin-override" />
                  <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
                    <View style={{ flex: 1, minWidth: 140 }}><Btn label={cr(lang, "adm_approve")} disabled={busy || reasoning.trim().length < 3} onPress={() => act(() => consumerApi.admin.decide(r.refund_id, "approve", reasoning, override))} testID="admin-approve" /></View>
                    {r.status !== "approved" ? <View style={{ flex: 1, minWidth: 140 }}><Btn kind="danger" label={cr(lang, "adm_reject")} disabled={busy || reasoning.trim().length < 3} onPress={() => act(() => consumerApi.admin.decide(r.refund_id, "reject", reasoning))} testID="admin-reject" /></View> : null}
                  </View>
                </>
              ) : null}
              {r.status === "approved" && ["idle", "failed_retryable"].includes(r.processing_state) ? (
                <Btn label={cr(lang, "adm_execute")} disabled={busy} onPress={() => confirmAction(cr(lang, "confirm_action"), () => act(() => consumerApi.admin.execute(r.refund_id)))} testID="admin-execute" />
              ) : null}
              {r.status === "approved" && r.processing_state === "awaiting_store_refund" ? (
                <>
                  <Body dim>{cr(lang, "ps_awaiting_store_refund")}</Body>
                  <Input label={cr(lang, "adm_ext_ref")} value={ext} onChangeText={setExt} testID="admin-ext-ref" />
                  <Btn label={cr(lang, "adm_confirm_ext")} disabled={busy || ext.trim().length < 3} onPress={() => act(() => consumerApi.admin.confirmExternal(r.refund_id, ext.trim()))} testID="admin-confirm-ext" />
                </>
              ) : null}
            </Card>
          ) : null}
          {msg ? <Card><Body>{msg}</Body></Card> : null}
          {r.admin_response ? <Card><H>{cr(lang, "admin_response")}</H><Body>{r.admin_response}</Body></Card> : null}
          <Card>
            <H>{cr(lang, "adm_audit")}</H>
            {d.audit.map((a: any) => <Body key={a.event_id} dim>{`${fmt(a.ts)} · ${a.event_type} · ${a.actor_id || "-"}`}</Body>)}
          </Card>
        </>
      ) : null}
    </Screen>
  );
}
