import React, { useEffect, useState } from "react";
import { Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { dsaApi } from "@/src/api/dsa";
import { Badge, Body, Btn, Card, Check, Chips, H, Input, LoadState, Row, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const isoDay = (s: string) => (/^\d{4}-\d{2}-\d{2}$/.test(s.trim()) ? `${s.trim()}T00:00:00Z` : undefined);

// Ordine ale autoritatilor (Art. 9/10). Ascunderea pentru non-juridic e doar UX: /api/dsa/orders/* refuza (403) fara is_legal_officer / admin.
export default function Orders() {
  const { lang } = useI18n();
  const router = useRouter();
  const [allowed, setAllowed] = useState<boolean | undefined>(undefined);
  useEffect(() => { modApi.role().then(r => setAllowed(!!(r.orders || r.role === "admin"))).catch(() => setAllowed(false)); }, []);
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(() => dsaApi.orders(status || undefined), [status, allowed]);
  const [type, setType] = useState<"art9_illegal_content" | "art10_information">("art9_illegal_content");
  const [f, setF] = useState({ name: "", ms: "", email: "", ref: "", basis: "", reasons: "", issued: "", scope: "", targets: "", items: "", deadline: "" });
  const [redress, setRedress] = useState(false); const [nd, setNd] = useState(false);
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (k: keyof typeof f) => (v: string) => setF(s => ({ ...s, [k]: v }));
  if (allowed === false) return <Screen title={ms(lang, "ord_title")}><Body>{ms(lang, "ord_no_access")}</Body></Screen>;
  const create = async () => {
    setBusy(true); setMsg(null);
    try {
      const lines = f.targets.split("\n").map(x => x.trim()).filter(Boolean);
      const o = await dsaApi.createOrder({
        order_type: type, authority: { name: f.name, member_state: f.ms.toUpperCase(), contact_email: f.email || undefined, their_reference: f.ref || undefined },
        legal_basis: f.basis || undefined, statement_of_reasons: f.reasons || undefined, issued_at: isoDay(f.issued), response_deadline: isoDay(f.deadline),
        territorial_scope: type === "art9_illegal_content" ? f.scope || undefined : undefined, redress_information: redress,
        non_disclosure: { requested: nd },
        targets: type === "art9_illegal_content" ? lines.map(url => ({ url })) : lines.map(account_username => ({ account_username })),
        requested_items: type === "art10_information" ? f.items.split(",").map(x => x.trim()).filter(Boolean) : [],
      });
      setMsg({ ok: true, text: o.reference }); reload();
      router.push({ pathname: "/moderation/admin/order/[id]", params: { id: o.order_id } });
    } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };
  return (
    <Screen title={ms(lang, "ord_title")} testID="orders-screen">
      <Chips value={status} onChange={setStatus} options={[{ value: "", label: ms(lang, "status_all") },
        ...(["incomplete", "pending_legal_review", "approved", "executed", "information_prepared", "rejected", "closed"] as const).map(v => ({ value: v as string, label: ms(lang, ("ord_status_" + v) as any) }))]} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data && data.items.length === 0 ? <Body dim>{ms(lang, "empty")}</Body> : null}
      {(data?.items ?? []).map((o: any) => (
        <Row key={o.order_id} onPress={() => router.push({ pathname: "/moderation/admin/order/[id]", params: { id: o.order_id } })} testID={`order-${o.reference}`}>
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
            <Badge label={label(lang, "ord_status_", o.status)} tone={tone(o.status)} />
            <Badge label={ms(lang, ("ord_type_" + o.order_type) as any)} tone="info" />
            {o.deadline_status === "overdue" ? <Badge label={ms(lang, "ord_overdue")} tone="bad" /> : null}
          </View>
          <Text style={{ color: "#fff", fontWeight: "700" }}>{o.reference} · {o.authority.name} ({o.authority.member_state})</Text>
          <Body dim>{fmt(o.received_at)}</Body>
        </Row>
      ))}
      <H>{ms(lang, "ord_new")}</H>
      <Card>
        <Chips value={type} onChange={setType} options={(["art9_illegal_content", "art10_information"] as const).map(v => ({ value: v, label: ms(lang, ("ord_type_" + v) as any) }))} />
        <Input testID="ord-authority" label={ms(lang, "ord_authority")} value={f.name} onChangeText={set("name")} maxLength={200} />
        <Input testID="ord-ms" label={ms(lang, "ord_member_state")} value={f.ms} onChangeText={set("ms")} maxLength={2} />
        <Input testID="ord-email" label={ms(lang, "ord_authority_email")} value={f.email} onChangeText={set("email")} keyboardType="email-address" maxLength={320} />
        <Input label={ms(lang, "ord_their_ref")} value={f.ref} onChangeText={set("ref")} maxLength={120} />
        <Input testID="ord-basis" label={ms(lang, "ord_legal_basis")} value={f.basis} onChangeText={set("basis")} multiline maxLength={1000} />
        <Input testID="ord-reasons" label={ms(lang, "ord_reasons")} value={f.reasons} onChangeText={set("reasons")} multiline maxLength={4000} />
        <Input testID="ord-issued" label={ms(lang, "ord_issued")} value={f.issued} onChangeText={set("issued")} maxLength={10} />
        <Input label={ms(lang, "ord_deadline")} value={f.deadline} onChangeText={set("deadline")} maxLength={10} />
        {type === "art9_illegal_content" ? <Input testID="ord-scope" label={ms(lang, "ord_scope")} value={f.scope} onChangeText={set("scope")} maxLength={300} /> : null}
        <Input testID="ord-targets" label={ms(lang, "ord_targets")} value={f.targets} onChangeText={set("targets")} multiline maxLength={4000} />
        {type === "art10_information" ? <Input testID="ord-items" label={ms(lang, "ord_items")} value={f.items} onChangeText={set("items")} maxLength={600} /> : null}
        <Check checked={redress} onToggle={() => setRedress(!redress)} label={ms(lang, "ord_redress")} />
        <Check checked={nd} onToggle={() => setNd(!nd)} label={ms(lang, "ord_nd")} />
        {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error, marginBottom: 8 }} testID="ord-msg">{msg.text}</Text> : null}
        <Btn testID="ord-create" label={ms(lang, "ord_create")} onPress={create} disabled={busy || !f.name.trim()} />
      </Card>
    </Screen>
  );
}
