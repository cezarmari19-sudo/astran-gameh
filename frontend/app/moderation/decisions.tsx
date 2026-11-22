import React from "react";
import { Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, LoadState, Row, Screen, fmt, label, useLoad } from "@/src/components/modUi";

export default function Decisions() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => modApi.myDecisions());
  const list = data?.decisions ?? [];
  return (
    <Screen title={ms(lang, "mod_decisions")} testID="my-decisions">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {!loading && !error && list.length === 0 ? <Body dim>{ms(lang, "dec_empty")}</Body> : null}
      {list.map((d: any) => (
        <Row key={d.decision_id} onPress={() => router.push({ pathname: "/moderation/decision/[id]", params: { id: d.decision_id } })} testID={`decision-${d.decision_id}`}>
          <View style={{ flexDirection: "row", gap: 8, marginBottom: 6 }}>
            <Badge label={label(lang, "dec_", d.status)} tone={d.status === "reversed" ? "ok" : "warn"} />
          </View>
          <Text style={{ color: "#fff", fontWeight: "700" }}>{label(lang, "act_", d.action)}</Text>
          <Body dim>{label(lang, "type_", d.target_type)} · {fmt(d.created_at)}</Body>
        </Row>
      ))}
    </Screen>
  );
}
