import React from "react";
import { Screen, Card } from "@/src/components/modUi";
import { ConsumerDocBody } from "@/src/components/ConsumerDocModal";
import { useI18n } from "@/src/i18n";
import { cr } from "@/src/i18n/consumerStrings";

// Pagina Politicii de rambursare (accesibila direct si din Portofel, Magazin, Setari, checkout si confirmarea comenzii).
export default function RefundPolicyScreen() {
  const { lang } = useI18n();
  return <Screen title={cr(lang, "policy_title")} testID="refund-policy-screen"><Card><ConsumerDocBody /></Card></Screen>;
}
