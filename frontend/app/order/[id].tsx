import React from "react";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Badge, Body, Btn, Card, H, KV, LoadState, Screen, fmt, useLoad } from "@/src/components/modUi";
import { RefundPolicyLink } from "@/src/components/ConsumerDocModal";
import { useI18n } from "@/src/i18n";
import { cr } from "@/src/i18n/consumerStrings";
import { consumerApi } from "@/src/api/consumer";

// Confirmarea comenzii (art. 8 alin. 7): numar tranzactie, produs, pret, data, confirmarile inregistrate pe server, link catre politica.
export default function OrderScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { lang } = useI18n();
  const router = useRouter();
  const { data: o, error, loading, reload } = useLoad(() => consumerApi.order(String(id)), [id]);
  const trader = o?.trader;
  return (
    <Screen title={cr(lang, "order_title")} testID="order-screen">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {o ? (
        <>
          <Card>
            <KV k={cr(lang, "order_no")} v={o.order_id} />
            <KV k={cr(lang, "tx_no")} v={o.tx_id} />
            <KV k={cr(lang, "product")} v={o.product_name} />
            <KV k={cr(lang, "price_paid")} v={`${o.price_display} ${o.currency}`} />
            <KV k={cr(lang, "date")} v={fmt(o.created_at)} />
            <KV k={cr(lang, "delivery")} v={lang === "ro" ? "Imediată" : "Immediate"} />
            <KV k={cr(lang, "trader")} v={[trader?.name, trader?.address, trader?.email, trader?.phone].filter(Boolean).join("\n")} />
          </Card>
          <Card>
            <H>{cr(lang, "withdrawal")}</H>
            <Badge label={o.withdrawal_right.status === "waived" ? cr(lang, "withdrawal_waived") : cr(lang, "preserved")} tone={o.withdrawal_right.status === "waived" ? "warn" : "ok"} />
            <View style={{ height: 8 }} />
            <Body>{o.withdrawal_right.status === "waived" ? cr(lang, "waived") : cr(lang, "preserved")}</Body>
            {o.consents?.length ? (
              <View style={{ marginTop: 10 }}>
                <H>{cr(lang, "your_confirmations")}</H>
                {o.consents.map(c => <Body key={c.kind} dim>{`${fmt(c.created_at)} — ${c.text}`}</Body>)}
              </View>
            ) : null}
          </Card>
          <Card>
            <Body dim>{o.email.status === "sent" ? cr(lang, "email_sent") : o.email.status === "failed" ? cr(lang, "email_failed") : cr(lang, "email_none")}</Body>
            <View style={{ height: 8 }} />
            <RefundPolicyLink testID="order-refund-policy" />
            <View style={{ height: 12 }} />
            <Btn kind="ghost" label={cr(lang, "request_refund")} onPress={() => router.push("/refunds/new")} testID="order-request-refund" />
          </Card>
          <Btn label={cr(lang, "done")} onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} testID="order-done" />
        </>
      ) : null}
    </Screen>
  );
}
