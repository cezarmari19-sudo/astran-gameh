import React from "react";
import { Badge, Body, KV } from "@/src/components/modUi";
import { useI18n } from "@/src/i18n";
import { cs, type CsKey } from "@/src/i18n/complianceStrings";
import type { SellerInfo } from "@/src/api/compliance";

// DSA Art. 30(7) + informarea consumatorilor pe piete online: statutul vanzatorului si, DOAR pentru comercianti, datele lor.
// Continutul vine exclusiv de la server (merchants.public_seller_info); vanzatorii care nu sunt comercianti nu au date personale aici.
export default function SellerInfoCard({ seller }: { seller?: SellerInfo | null }) {
  const { lang } = useI18n();
  if (!seller) return null;
  if (seller.seller_status !== "trader" || !seller.trader) {
    return <Body dim>{seller.seller_status === "non_trader" ? cs(lang, "si_non_trader") : cs(lang, "si_undeclared")}</Body>;
  }
  const t = seller.trader;
  return (
    <>
      <Body bold>{cs(lang, "si_trader")}: {t.name}</Body>
      <KV k={cs(lang, "si_address")} v={t.address} />
      <KV k={cs(lang, "si_phone")} v={t.phone} />
      <KV k={cs(lang, "si_email")} v={t.email} />
      {t.registry ? <KV k={cs(lang, "si_registry")} v={`${t.registry.name} · ${t.registry.number}`} /> : null}
      <Body dim>{cs(lang, "si_statement")}</Body>
      <Badge label={`${cs(lang, "si_verification")}: ${cs(lang, `vs_${seller.verification_state ?? "unverified"}` as CsKey)}`} tone={seller.verification_state === "verified" ? "ok" : "warn"} />
    </>
  );
}
