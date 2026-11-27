import React, { useEffect, useState } from "react";
import { Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { useAuth } from "@/src/context/AuthContext";
import { cr } from "@/src/i18n/consumerStrings";
import { cs } from "@/src/i18n/complianceStrings";
import { Badge, Body, Btn, Card, Chips, Input, LoadState, Row, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";

const CATS = ["", "illegal_content", "child_safety", "harassment", "hate_speech", "violence", "sexual_content", "self_harm", "scam_fraud", "ip_infringement", "spam", "other"];
const isoDay = (s: string, end?: boolean) => (/^\d{4}-\d{2}-\d{2}$/.test(s) ? `${s}T${end ? "23:59:59" : "00:00:00"}Z` : "");

// Panou de moderare. Ascunderea pentru non-staff e doar UX: serverul raspunde 403 la orice ruta /admin fara rol.
export default function AdminDashboard() {
  const { lang } = useI18n();
  const { user } = useAuth();   // doar pentru a afisa link-ul; autorizarea reala e pe backend (/consumer/admin/*)
  const router = useRouter();
  const [role, setRole] = useState<string | null | undefined>(undefined);
  const [orders, setOrders] = useState(false);
  const [status, setStatus] = useState(""); const [kind, setKind] = useState(""); const [category, setCategory] = useState("");
  const [type, setType] = useState(""); const [q, setQ] = useState(""); const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => { modApi.role().then(r => { setRole(r.role); setOrders(!!r.orders); }).catch(() => setRole(null)); }, []);
  const { data, error, loading, reload } = useLoad(() => modApi.adminReports({
    status, kind, category, target_type: type, q, date_from: isoDay(from), date_to: isoDay(to, true), page, page_size: 20,
  }), [status, kind, category, type, q, from, to, page, role]);
  if (role === null) return <Screen title={ms(lang, "adm_title")}><Body>{ms(lang, "adm_no_access")}</Body></Screen>;
  const opt = (arr: string[], prefix: string, allKey: any) => arr.map(v => ({ value: v, label: v ? ms(lang, (prefix + v) as any) : ms(lang, allKey) }));
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const f = (setter: (v: string) => void) => (v: string) => { setPage(1); setter(v); };
  return (
    <Screen title={ms(lang, "adm_title")} testID="admin-dashboard">
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
        <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_appeals")} onPress={() => router.push("/moderation/admin/appeals")} testID="admin-appeals-link" /></View>
        <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_audit")} onPress={() => router.push("/moderation/admin/audit")} testID="admin-audit-link" /></View>
        {user?.is_platform_admin ? <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={cr(lang, "adm_title")} onPress={() => router.push("/moderation/admin/refunds")} testID="admin-refunds-link" /></View> : null}
      </View>
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
        <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_deliveries")} onPress={() => router.push("/moderation/admin/deliveries")} testID="admin-deliveries-link" /></View>
        <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_own")} onPress={() => router.push("/moderation/admin/own-initiative")} testID="admin-own-link" /></View>
        <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_transparency")} onPress={() => router.push("/moderation/admin/transparency")} testID="admin-transparency-link" /></View>
        {orders || user?.is_platform_admin ? <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_orders")} onPress={() => router.push("/moderation/admin/orders")} testID="admin-orders-link" /></View> : null}
        {user?.is_platform_admin || (user as any)?.is_legal_officer || (user as any)?.is_security_officer ? <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={cs(lang, "inc_title")} onPress={() => router.push("/moderation/admin/incidents")} testID="admin-incidents-link" /></View> : null}
        {user?.is_platform_admin || (user as any)?.is_legal_officer ? <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={cs(lang, "mer_title")} onPress={() => router.push("/moderation/admin/merchants")} testID="admin-merchants-link" /></View> : null}
        {user?.is_platform_admin ? <View style={{ flex: 1, minWidth: 140 }}><Btn kind="ghost" label={ms(lang, "adm_tdb")} onPress={() => router.push("/moderation/admin/tdb")} testID="admin-tdb-link" /></View> : null}
      </View>
      <Card>
        <Input testID="admin-search" value={q} onChangeText={f(setQ)} placeholder={ms(lang, "adm_search")} />
        <Chips label={ms(lang, "mod_title")} value={status} onChange={f(setStatus)}
          options={[{ value: "", label: ms(lang, "status_all") }, ...(["pending", "under_review", "resolved", "rejected"] as const).map(v => ({ value: v as string, label: ms(lang, ("status_" + v) as any) }))]} />
        <Chips value={kind} onChange={f(setKind)} options={[{ value: "", label: ms(lang, "status_all") },
          { value: "rule_violation", label: ms(lang, "kind_rule_violation") }, { value: "illegal_content_notice", label: ms(lang, "kind_illegal_content_notice") },
          { value: "own_initiative", label: ms(lang, "kind_own_initiative") }, { value: "authority_order", label: ms(lang, "kind_authority_order") }]} />
        <Chips value={type} onChange={f(setType)} options={[{ value: "", label: ms(lang, "status_all") }, ...(["game", "user", "message"] as const).map(v => ({ value: v as string, label: ms(lang, ("type_" + v) as any) }))]} />
        <Chips label={ms(lang, "rep_category")} value={category} onChange={f(setCategory)} options={opt(CATS, "cat_", "status_all")} />
        <View style={{ flexDirection: "row", gap: 8 }}>
          <View style={{ flex: 1 }}><Input testID="admin-date-from" value={from} onChangeText={f(setFrom)} placeholder={ms(lang, "adm_date_from")} /></View>
          <View style={{ flex: 1 }}><Input testID="admin-date-to" value={to} onChangeText={f(setTo)} placeholder={ms(lang, "adm_date_to")} /></View>
        </View>
      </Card>
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data ? <Body dim>{ms(lang, "adm_total")}: {data.total}</Body> : null}
      {data && data.items.length === 0 ? <Body dim>{ms(lang, "empty")}</Body> : null}
      {(data?.items ?? []).map((r: any) => (
        <Row key={r.report_id} onPress={() => router.push({ pathname: "/moderation/admin/report/[id]", params: { id: r.report_id } })} testID={`admin-report-${r.report_id}`}>
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
            <Badge label={label(lang, "status_", r.status)} tone={tone(r.status)} />
            <Badge label={label(lang, "kind_", r.kind)} tone={r.kind === "illegal_content_notice" ? "bad" : "info"} />
          </View>
          <Text style={{ color: "#fff", fontWeight: "700" }}>{label(lang, "type_", r.target_type)} · {label(lang, "cat_", r.category)}</Text>
          <Body dim>{fmt(r.created_at)}{r.assigned_to ? ` · ${ms(lang, "adm_assigned")} ${r.assigned_to}` : ""}</Body>
        </Row>
      ))}
      {data ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View style={{ flex: 1 }}><Btn kind="ghost" label={ms(lang, "adm_prev")} onPress={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} /></View>
          <Body>{ms(lang, "adm_page")} {page}/{pages}</Body>
          <View style={{ flex: 1 }}><Btn kind="ghost" label={ms(lang, "adm_next")} onPress={() => setPage(p => p + 1)} disabled={page >= pages} /></View>
        </View>
      ) : null}
    </Screen>
  );
}
