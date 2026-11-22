import React from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, LoadState, Row, Screen, fmt, useLoad } from "@/src/components/modUi";

// Notificari de moderare in aplicatie: confirmari de primire (Art. 16), decizii, rezultate ale plangerilor.
export default function Notifications() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => modApi.notifications());
  const open = async (n: any) => {
    if (!n.read_at) { try { await modApi.readNotification(n.notification_id); } catch { /* ramane necitita */ } }
    const ref = n.ref || {};
    if (ref.decision_id) router.push({ pathname: "/moderation/decision/[id]", params: { id: ref.decision_id } });
    else if (ref.appeal_id) router.push("/moderation/appeals");
    else if (ref.report_id) router.push("/moderation/reports");
    reload();
  };
  const list = data?.notifications ?? [];
  return (
    <Screen title={ms(lang, "mod_notifications")} testID="mod-notifications-screen">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {!loading && !error && list.length === 0 ? <Body dim>{ms(lang, "notif_empty")}</Body> : null}
      {list.map((n: any) => (
        <Row key={n.notification_id} onPress={() => open(n)} testID={`notif-${n.notification_id}`}>
          {!n.read_at ? <Badge label="NEW" tone="warn" /> : null}
          <Text style={{ color: "#fff", fontWeight: n.read_at ? "500" : "800", marginTop: 4 }}>{n.message}</Text>
          <Body dim>{fmt(n.created_at)}</Body>
        </Row>
      ))}
    </Screen>
  );
}
