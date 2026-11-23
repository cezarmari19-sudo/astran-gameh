import React from "react";
import { View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { Badge, Body, Card, H, KV, LoadState, Screen, fmt, useLoad } from "@/src/components/modUi";
import { RefundPolicyLink } from "@/src/components/ConsumerDocModal";
import { useI18n } from "@/src/i18n";
import { cr, type CrKey } from "@/src/i18n/consumerStrings";
import { consumerApi, refundTone } from "@/src/api/consumer";

export default function RefundDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { lang } = useI18n();
  const { data: r, error, loading, reload } = useLoad(() => consumerApi.myRefund(String(id)), [id]);
  return (
    <Screen title={cr(lang, "refund_requests")} testID="refund-detail-screen">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {r ? (
        <>
          <Card>
            <KV k={cr(lang, "product")} v={r.product_name || r.tx_id} />
            <KV k={cr(lang, "reason")} v={cr(lang, `reason_${r.reason}` as CrKey)} />
            {r.explanation ? <KV k={cr(lang, "explanation")} v={r.explanation} /> : null}
            <KV k={cr(lang, "status")} v={<Badge label={cr(lang, `st_${r.status}` as CrKey)} tone={refundTone(r.status)} />} />
            {r.processing_state === "awaiting_store_refund" || r.processing_state === "failed_retryable"
              ? <Body dim>{cr(lang, `ps_${r.processing_state}` as CrKey)}</Body> : null}
            {r.refund ? <KV k={cr(lang, "refund_amount")} v={[
              r.refund.money_minor != null ? `${(r.refund.money_minor / 100).toFixed(2)} ${r.refund.currency}` : null,
              r.refund.astrans_returned_to_you ? `${r.refund.astrans_returned_to_you} Astrans` : null].filter(Boolean).join(" · ")} /> : null}
          </Card>
          <Card>
            <H>{cr(lang, "admin_response")}</H>
            <Body>{r.admin_response || cr(lang, "no_response")}</Body>
          </Card>
          <Card>
            <H>{cr(lang, "history")}</H>
            {r.history.map((h, i) => <Body key={i} dim>{`${fmt(h.at)} — ${cr(lang, `st_${h.status}` as CrKey)}`}</Body>)}
          </Card>
          <View><RefundPolicyLink testID="refund-detail-policy" /></View>
        </>
      ) : null}
    </Screen>
  );
}
