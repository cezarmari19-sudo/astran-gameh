import React, { useEffect, useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { cs, csErr, type CsKey } from "@/src/i18n/complianceStrings";
import { sellerApi, type TraderForm } from "@/src/api/compliance";
import { Badge, Body, Btn, Card, Check, Chips, H, Input, LoadState, Screen, useLoad } from "@/src/components/modUi";
import SellerInfoCard from "@/src/components/SellerInfoCard";
import { colors } from "@/src/theme";

const Q = ["regular_sales", "for_profit_or_business", "professional_or_company", "hired_staff_or_resells_goods"] as const;
const EMPTY: TraderForm = { trader_type: "company", name: "", address: { line1: "", line2: "", city: "", postal_code: "", country: "RO" }, phone: "", contact_email: "",
  registry: { register_name: "", number: "", not_applicable_reason: "" }, vat_id: "", eu_compliance_statement: false };

// DSA Art. 30-32: declaratia de statut a vanzatorului + datele comerciantului. Validarea reala, verificarea si restrictionarea
// vanzarilor sunt pe server; ecranul doar colecteaza si arata starea. Nu se incarca documente de identitate.
export default function SellerScreen() {
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => sellerApi.status(), []);
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState<TraderForm>(EMPTY);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null); const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!data?.profile) return;
    setAnswers({ ...Object.fromEntries(Q.map(q => [q, false])), ...(data.profile.assessment ?? {}) });
    const t = data.profile.trader;
    if (t) setForm({ ...EMPTY, ...t, address: { ...EMPTY.address, ...t.address }, registry: { ...EMPTY.registry, ...(t.registry ?? {}) }, eu_compliance_statement: !!t.eu_compliance_statement?.accepted });
  }, [data?.profile?.declared_at]);   // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (status: "non_trader" | "trader") => {
    setBusy(true); setMsg(null);
    try {
      const clean = (o: any) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== "" && v != null));
      const trader = status === "trader" ? { ...form, address: clean(form.address), registry: clean(form.registry), ...(form.vat_id ? {} : { vat_id: undefined }) } as TraderForm : undefined;
      await sellerApi.declare({ seller_status: status, assessment: Object.fromEntries(Q.map(q => [q, !!answers[q]])), trader });
      setMsg({ ok: true, text: cs(lang, "sl_saved") }); await reload();
    } catch (e: any) { setMsg({ ok: false, text: csErr(lang, e) }); }
    finally { setBusy(false); }
  };
  const set = (k: keyof TraderForm) => (v: string) => setForm(f => ({ ...f, [k]: v }));
  const setA = (k: string) => (v: string) => setForm(f => ({ ...f, address: { ...f.address, [k]: v } }));
  const setR = (k: string) => (v: string) => setForm(f => ({ ...f, registry: { ...f.registry, [k]: v } }));
  const prof = data?.profile; const elig = data?.eligibility;

  return (
    <Screen title={cs(lang, "sl_title")} testID="seller-screen">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data ? (
        <>
          <Card>
            <Body>{cs(lang, "sl_intro")}</Body>
            <Body bold>{cs(lang, `sl_status_${prof.seller_status}` as CsKey)}</Body>
            {prof.seller_status === "trader" ? <Badge label={cs(lang, `vs_${prof.verification?.state ?? "unverified"}` as CsKey)} tone={prof.verification?.state === "verified" ? "ok" : prof.verification?.state === "rejected" ? "bad" : "warn"} /> : null}
            <Text style={{ color: elig?.allowed ? colors.success : colors.warning, marginTop: 8 }} testID="seller-eligibility">
              {elig?.allowed ? cs(lang, "sl_elig_ok") : cs(lang, `sl_elig_${elig?.reason ?? "seller_status_required"}` as CsKey)}
            </Text>
            {!data.adult_verified ? <Body dim>{cs(lang, "sl_adult_only")}</Body> : null}
          </Card>
          {data.adult_verified ? (
            <>
              <Card>
                <H>{cs(lang, "sl_choose")}</H>
                {Q.map(q => <Check key={q} checked={!!answers[q]} onToggle={() => setAnswers(a => ({ ...a, [q]: !a[q] }))} label={cs(lang, `q_${q}` as CsKey)} testID={`seller-q-${q}`} />)}
                <Btn kind="ghost" label={cs(lang, "sl_declare_non_trader")} onPress={() => submit("non_trader")} disabled={busy || prof.forced_trader} testID="seller-non-trader" />
              </Card>
              <Card>
                <H>{cs(lang, "sl_trader_form")}</H>
                <Chips label={cs(lang, "f_trader_type")} value={form.trader_type} onChange={(v) => setForm(f => ({ ...f, trader_type: v }))}
                  options={[{ value: "company", label: cs(lang, "tt_company") }, { value: "natural_person", label: cs(lang, "tt_natural_person") }]} />
                <Input label={cs(lang, "f_name")} value={form.name} onChangeText={set("name")} testID="seller-name" />
                <Input label={cs(lang, "f_line1")} value={form.address.line1} onChangeText={setA("line1")} testID="seller-line1" />
                <Input label={cs(lang, "f_line2")} value={form.address.line2 ?? ""} onChangeText={setA("line2")} />
                <Input label={cs(lang, "f_city")} value={form.address.city} onChangeText={setA("city")} testID="seller-city" />
                <Input label={cs(lang, "f_postal")} value={form.address.postal_code} onChangeText={setA("postal_code")} testID="seller-postal" />
                <Input label={cs(lang, "f_country")} value={form.address.country} onChangeText={(v) => setA("country")(v.toUpperCase())} maxLength={2} testID="seller-country" />
                <Input label={cs(lang, "f_phone")} value={form.phone} onChangeText={set("phone")} keyboardType="phone-pad" testID="seller-phone" />
                <Input label={cs(lang, "f_email")} value={form.contact_email} onChangeText={set("contact_email")} keyboardType="email-address" testID="seller-email" />
                <Input label={cs(lang, "f_reg_name")} value={form.registry.register_name ?? ""} onChangeText={setR("register_name")} />
                <Input label={cs(lang, "f_reg_number")} value={form.registry.number ?? ""} onChangeText={setR("number")} testID="seller-reg-number" />
                {form.trader_type === "natural_person" ? <Input label={cs(lang, "f_reg_na")} value={form.registry.not_applicable_reason ?? ""} onChangeText={setR("not_applicable_reason")} multiline /> : null}
                <Input label={cs(lang, "f_vat")} value={form.vat_id ?? ""} onChangeText={set("vat_id")} />
                <Check checked={form.eu_compliance_statement} onToggle={() => setForm(f => ({ ...f, eu_compliance_statement: !f.eu_compliance_statement }))} label={cs(lang, "f_statement")} testID="seller-statement" />
                <Body dim>{cs(lang, "sl_no_docs")}</Body>
                <Btn label={cs(lang, "sl_declare_trader")} onPress={() => submit("trader")} disabled={busy || !form.eu_compliance_statement} testID="seller-trader" />
              </Card>
            </>
          ) : null}
          {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error }} testID="seller-message">{msg.text}</Text> : null}
          <Card><H>{cs(lang, "sl_preview")}</H><SellerInfoCard seller={data.public_preview} /></Card>
        </>
      ) : null}
    </Screen>
  );
}
