import React from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, LoadState, Row, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";

export default function MyAppeals() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => modApi.myAppeals());
  const list = data?.appeals ?? [];
  return (
    <Screen title={ms(lang, "mod_appeals")} testID="my-appeals">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {!loading && !error && list.length === 0 ? <Body dim>{ms(lang, "appeal_none")}</Body> : null}
      {list.map((a: any) => (
        <Row key={a.appeal_id} onPress={() => router.push({ pathname: "/moderation/decision/[id]", params: { id: a.decision_id } })} testID={`appeal-${a.appeal_id}`}>
          <Badge label={label(lang, "appeal_", a.status)} tone={tone(a.status)} />
          <Text style={{ color: "#fff", fontWeight: "700", marginTop: 6 }} numberOfLines={2}>{a.grounds}</Text>
          <Body dim>{fmt(a.created_at)}</Body>
          {a.outcome_reasoning ? <Body>{a.outcome_reasoning}</Body> : null}
        </Row>
      ))}
    </Screen>
  );
}
