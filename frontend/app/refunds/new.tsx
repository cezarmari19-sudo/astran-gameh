import React, { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Badge, Body, Btn, Card, Chips, Input, LoadState, Row, Screen, fmt, useLoad } from "@/src/components/modUi";
import { RefundPolicyLink } from "@/src/components/ConsumerDocModal";
import { useI18n } from "@/src/i18n";
import { cr, crErr, type CrKey } from "@/src/i18n/consumerStrings";
import { consumerApi } from "@/src/api/consumer";

const REASONS = ["withdrawal", "not_as_described", "defective", "not_received", "unauthorized_purchase", "duplicate_charge", "other"] as const;

export default function NewRefundScreen() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => consumerApi.refundableTx(), []);
  const [tx, setTx] = useState<string | null>(null);
  const [reason, setReason] = useState<(typeof REASONS)[number]>("defective");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!tx || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await consumerApi.createRefund(tx, reason, text.trim());
      router.replace(`/refunds/${r.refund_id}`);
    } catch (e: any) { setErr(crErr(lang, e)); } finally { setBusy(false); }
  }
  return (
    <Screen title={cr(lang, "new_request")} testID="refund-new-screen">
      <RefundPolicyLink testID="refund-new-policy" />
      <View style={{ height: 12 }} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && data.transactions.length === 0 ? <Body dim>{cr(lang, "no_tx")}</Body> : null}
      {data?.transactions.map(t => (
        <Row key={t.tx_id} onPress={() => !t.active_request && setTx(t.tx_id)} testID={`refund-tx-${t.tx_id}`}>
          <Body bold>{`${tx === t.tx_id ? "✔ " : ""}${t.reference || t.type}`}</Body>
          <Body dim>{`${t.type === "purchase" ? `${t.price} ${t.currency}` : `${t.amount} Astrans`} · ${fmt(t.timestamp)}`}</Body>
          {t.active_request ? <Badge label={cr(lang, "active_request")} tone="warn" /> : null}
          <Body dim>{t.waiver_valid ? cr(lang, "withdrawal_waived") : t.withdrawal_right_applies ? `${cr(lang, "withdrawal_applies")} ${fmt(t.withdrawal_deadline)}` : cr(lang, "withdrawal_expired")}</Body>
        </Row>
      ))}
      {tx ? (
        <Card>
          <Chips label={cr(lang, "reason")} value={reason} onChange={setReason} options={REASONS.map(r => ({ value: r, label: cr(lang, `reason_${r}` as CrKey) }))} />
          <Input label={cr(lang, "explanation")} value={text} onChangeText={setText} multiline maxLength={2000} testID="refund-explanation" />
          {err ? <Body>{err}</Body> : null}
          <Btn label={cr(lang, "send")} onPress={submit} disabled={busy} testID="refund-submit" />
        </Card>
      ) : null}
    </Screen>
  );
}
