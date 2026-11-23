import React from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Badge, Body, Btn, Card, LoadState, Row, Screen, fmt, useLoad } from "@/src/components/modUi";
import { RefundPolicyLink } from "@/src/components/ConsumerDocModal";
import { useI18n } from "@/src/i18n";
import { cr, type CrKey } from "@/src/i18n/consumerStrings";
import { consumerApi, refundTone } from "@/src/api/consumer";

export default function RefundsScreen() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => consumerApi.myRefunds(), []);
  return (
    <Screen title={cr(lang, "refund_requests")} testID="refunds-screen">
      <Btn label={cr(lang, "new_request")} onPress={() => router.push("/refunds/new")} testID="refunds-new" />
      <View style={{ height: 8 }}><RefundPolicyLink testID="refunds-policy" /></View>
      <View style={{ height: 12 }} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && data.requests.length === 0 ? <Body dim>{cr(lang, "no_requests")}</Body> : null}
      {data?.requests.map(r => (
        <Row key={r.refund_id} onPress={() => router.push(`/refunds/${r.refund_id}`)} testID={`refund-${r.refund_id}`}>
          <Body bold>{r.product_name || r.tx_id}</Body>
          <Body dim>{`${cr(lang, `reason_${r.reason}` as CrKey)} · ${fmt(r.created_at)}`}</Body>
          <Badge label={cr(lang, `st_${r.status}` as CrKey)} tone={refundTone(r.status)} />
        </Row>
      ))}
    </Screen>
  );
}
