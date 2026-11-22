import React, { useState } from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, Chips, LoadState, Row, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";

export default function AdminAppeals() {
  const { lang } = useI18n();
  const router = useRouter();
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(() => modApi.adminAppeals(status), [status]);
  const opts = ["", "received", "under_review", "decision_maintained", "decision_reversed"].map(v => ({ value: v, label: v ? ms(lang, ("appeal_" + v) as any) : ms(lang, "status_all") }));
  return (
    <Screen title={ms(lang, "adm_appeals")} testID="admin-appeals">
      <Chips value={status} onChange={setStatus} options={opts} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && data.appeals.length === 0 ? <Body dim>{ms(lang, "empty")}</Body> : null}
      {(data?.appeals ?? []).map((a: any) => (
        <Row key={a.appeal_id} onPress={() => router.push({ pathname: "/moderation/admin/appeal/[id]", params: { id: a.appeal_id } })} testID={`admin-appeal-${a.appeal_id}`}>
          <Badge label={label(lang, "appeal_", a.status)} tone={tone(a.status)} />
          <Text style={{ color: "#fff", fontWeight: "700", marginTop: 6 }} numberOfLines={2}>{a.grounds}</Text>
          <Body dim>{fmt(a.created_at)} · {a.as_role}</Body>
        </Row>
      ))}
    </Screen>
  );
}
