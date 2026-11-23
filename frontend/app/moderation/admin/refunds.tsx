import React, { useState } from "react";
import { useRouter } from "expo-router";
import { Badge, Body, Card, Chips, LoadState, Row, Screen, fmt, useLoad } from "@/src/components/modUi";
import { useI18n } from "@/src/i18n";
import { cr, type CrKey } from "@/src/i18n/consumerStrings";
import { consumerApi, refundTone } from "@/src/api/consumer";

// Lista cererilor de rambursare. Accesul e verificat pe SERVER (is_platform_admin citit din DB); ecranul doar afiseaza ce permite backend-ul.
export default function AdminRefunds() {
  const { lang } = useI18n();
  const router = useRouter();
  const [status, setStatus] = useState("pending");
  const { data, error, loading, reload } = useLoad(() => consumerApi.admin.list(status === "all" ? undefined : status), [status]);
  const opts = ["pending", "under_review", "approved", "rejected", "refunded", "all"].map(v => ({ value: v, label: v === "all" ? cr(lang, "adm_all") : cr(lang, `st_${v}` as CrKey) }));
  return (
    <Screen title={cr(lang, "adm_title")} testID="admin-refunds-screen">
      <Chips value={status} onChange={setStatus} options={opts} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && data.requests.length === 0 ? <Card><Body dim>{cr(lang, "no_requests")}</Body></Card> : null}
      {data?.requests.map(r => (
        <Row key={r.refund_id} onPress={() => router.push(`/moderation/admin/refund/${r.refund_id}`)} testID={`admin-refund-${r.refund_id}`}>
          <Body bold>{r.product_name || r.tx_id}</Body>
          <Body dim>{`${cr(lang, `reason_${r.reason}` as CrKey)} · ${r.user_id} · ${fmt(r.created_at)}`}</Body>
          <Badge label={cr(lang, `st_${r.status}` as CrKey)} tone={refundTone(r.status)} />
        </Row>
      ))}
    </Screen>
  );
}
