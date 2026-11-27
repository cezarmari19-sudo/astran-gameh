import React, { useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { cs, csErr, type CsKey } from "@/src/i18n/complianceStrings";
import { incidentApi } from "@/src/api/compliance";
import { Badge, Body, Btn, Card, Check, Chips, H, Input, KV, LoadState, Row, Screen, fmt, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const TYPES = ["unauthorized_access", "data_exfiltration", "credential_compromise", "malware_ransomware", "accidental_disclosure", "loss_or_theft_of_device", "vendor_processor_breach", "service_unavailability", "other"];
const CATS = ["account_identifiers", "email_addresses", "password_hashes", "date_of_birth_age", "child_or_parental_data", "payment_or_transaction_data", "user_content_messages", "ip_or_device_data", "moderation_or_legal_records", "special_category_data", "other"];
const SEV = ["low", "medium", "high", "critical"]; const RISK = ["unlikely", "risk", "high_risk"]; const CH = ["email", "in_app", "public_notice", "other"];
const dlTone = (s: string) => (s === "overdue" || s === "late" ? "bad" : s === "due_soon" ? "warn" : s === "met" || s === "not_required" ? "ok" : "info") as any;
const nowIso = () => new Date().toISOString().slice(0, 19) + "Z";

// GDPR Art. 33-34. Ecran DOAR pentru personal autorizat (autorizarea reala e pe backend: orice ruta /admin/incidents raspunde 403 altora).
// Sistemul pregateste si documenteaza; NU trimite nimic automat catre autoritati sau utilizatori.
export default function Incidents() {
  const { lang } = useI18n();
  const list = useLoad(() => incidentApi.list(), []);
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  if (list.error && /403|autoriz|authoriz|forbidden/i.test(list.error)) return <Screen title={cs(lang, "inc_title")}><Body>{cs(lang, "inc_staff_only")}</Body></Screen>;
  if (sel) return <Detail id={sel} onBack={() => { setSel(null); list.reload(); }} />;
  return (
    <Screen title={cs(lang, "inc_title")} testID="admin-incidents">
      <Body dim>{cs(lang, "inc_no_autosend")}</Body>
      {creating ? <CreateForm onDone={(id) => { setCreating(false); list.reload(); if (id) setSel(id); }} /> : <Btn label={cs(lang, "inc_new")} onPress={() => setCreating(true)} testID="inc-new" />}
      <LoadState loading={list.loading} error={list.error} onRetry={list.reload} />
      {!list.loading && !list.error && !(list.data?.incidents ?? []).length ? <Body dim>{cs(lang, "inc_empty")}</Body> : null}
      {(list.data?.incidents ?? []).map((i: any) => (
        <Row key={i.incident_id} onPress={() => setSel(i.incident_id)} testID={`inc-${i.incident_id}`}>
          <Body bold>{i.reference} · {i.title}</Body>
          <Body dim>{fmt(i.discovered_at)} · {i.status}</Body>
          <Badge label={cs(lang, `dl_${i.deadline.state}` as CsKey)} tone={dlTone(i.deadline.state)} />
        </Row>
      ))}
    </Screen>
  );
}

function CreateForm({ onDone }: { onDone: (id?: string) => void }) {
  const { lang } = useI18n();
  const [f, setF] = useState({ title: "", description: "", incident_type: "unauthorized_access", discovered_at: nowIso(), users: "", unknown: false });
  const [cats, setCats] = useState<string[]>([]); const [err, setErr] = useState("");
  const save = async () => {
    setErr("");
    try {
      const r = await incidentApi.create({ title: f.title, description: f.description, incident_type: f.incident_type, discovered_at: f.discovered_at, data_categories: cats,
        ...(f.unknown ? { affected_users_unknown: true } : { affected_users_estimate: Number(f.users) }) });
      onDone(r.incident.incident_id);
    } catch (e: any) { setErr(csErr(lang, e)); }
  };
  return (
    <Card>
      <Input label={cs(lang, "inc_f_title")} value={f.title} onChangeText={v => setF({ ...f, title: v })} testID="inc-title" />
      <Input label={cs(lang, "inc_f_desc")} value={f.description} onChangeText={v => setF({ ...f, description: v })} multiline testID="inc-desc" />
      <Chips label={cs(lang, "inc_f_type")} value={f.incident_type} onChange={v => setF({ ...f, incident_type: v })} options={TYPES.map(t => ({ value: t, label: t }))} />
      <Input label={cs(lang, "inc_f_discovered")} value={f.discovered_at} onChangeText={v => setF({ ...f, discovered_at: v })} testID="inc-discovered" />
      <Body dim>{cs(lang, "inc_f_categories")}</Body>
      {CATS.map(c => <Check key={c} checked={cats.includes(c)} onToggle={() => setCats(x => (x.includes(c) ? x.filter(y => y !== c) : [...x, c]))} label={c} />)}
      <Input label={cs(lang, "inc_f_users")} value={f.users} onChangeText={v => setF({ ...f, users: v.replace(/\D/g, "") })} keyboardType="number-pad" testID="inc-users" />
      <Check checked={f.unknown} onToggle={() => setF({ ...f, unknown: !f.unknown })} label={cs(lang, "inc_f_unknown")} />
      {err ? <Text style={{ color: colors.error }} testID="inc-error">{err}</Text> : null}
      <Btn label={cs(lang, "inc_create")} onPress={save} disabled={f.title.length < 5 || f.description.length < 10 || (!f.unknown && f.users === "")} testID="inc-create" />
      <Btn kind="ghost" label={cs(lang, "inc_back")} onPress={() => onDone()} />
    </Card>
  );
}

function Detail({ id, onBack }: { id: string; onBack: () => void }) {
  const { lang } = useI18n();
  const { data, error, loading, reload, setData } = useLoad(() => incidentApi.get(id), [id]);
  const audit = useLoad(() => incidentApi.audit(id), [id, data?.incident?.version]);
  const [err, setErr] = useState("");
  const [x, setX] = useState<Record<string, string>>({ severity: "high", risk: "risk", submitted_at: nowIso(), communicated_at: nowIso(), channel: "email" });
  const set = (k: string) => (v: string) => setX(p => ({ ...p, [k]: v }));
  const act = async (fn: () => Promise<any>) => { setErr(""); try { const r = await fn(); if (r?.incident) setData({ incident: r.incident }); else await reload(); } catch (e: any) { setErr(csErr(lang, e)); } };
  const inc = data?.incident;
  const closed = inc?.status === "closed";
  return (
    <Screen title={inc ? inc.reference : cs(lang, "inc_title")} testID="admin-incident-detail">
      <Btn kind="ghost" label={cs(lang, "inc_back")} onPress={onBack} />
      <LoadState loading={loading} error={error} onRetry={reload} />
      {inc ? (
        <>
          <Card>
            <Body bold>{inc.title}</Body><Body>{inc.description}</Body>
            <KV k={cs(lang, "inc_status")} v={inc.status} />
            <KV k={cs(lang, "inc_deadline")} v={<Badge label={`${cs(lang, `dl_${inc.deadline.state}` as CsKey)}${inc.deadline.hours_remaining != null && inc.deadline.hours_remaining >= 0 ? ` · ${cs(lang, "inc_hours_left", { h: inc.deadline.hours_remaining })}` : ""} · ${fmt(inc.deadline.deadline_at)}`} tone={dlTone(inc.deadline.state)} />} />
            <KV k={cs(lang, "inc_f_categories")} v={(inc.data_categories ?? []).join(", ") || "—"} />
            <KV k={cs(lang, "inc_f_users")} v={inc.affected_users_unknown ? "?" : String(inc.affected_users_estimate)} />
            {(inc.measures ?? []).map((m: any, i: number) => <Body key={i} dim>• {m.text}</Body>)}
            {!closed ? (<><Input label={cs(lang, "inc_measure")} value={x.measure ?? ""} onChangeText={set("measure")} />
              <Btn kind="ghost" label={cs(lang, "inc_add")} disabled={(x.measure ?? "").length < 5} onPress={() => act(async () => { const r = await incidentApi.patch(id, { add_measure: x.measure }); setX(p => ({ ...p, measure: "" })); return r; })} /></>) : null}
          </Card>

          <Card>
            <H>{cs(lang, "inc_severity")} / {cs(lang, "inc_risk")}</H>
            {inc.risk_assessment ? <Body>{cs(lang, `sv_${inc.severity}` as CsKey)} · {cs(lang, `rk_${inc.risk_assessment.level}` as CsKey)}</Body> : null}
            {!closed ? (<>
              <Chips value={x.severity} onChange={set("severity")} options={SEV.map(v => ({ value: v, label: cs(lang, `sv_${v}` as CsKey) }))} />
              <Chips value={x.risk} onChange={set("risk")} options={RISK.map(v => ({ value: v, label: cs(lang, `rk_${v}` as CsKey) }))} />
              <Input label={cs(lang, "inc_reasoning")} value={x.reasoning ?? ""} onChangeText={set("reasoning")} multiline testID="inc-reasoning" />
              <Input label={cs(lang, "inc_consequences")} value={x.cons ?? ""} onChangeText={set("cons")} multiline />
              <Btn label={cs(lang, "inc_assess")} onPress={() => act(() => incidentApi.assess(id, { severity: x.severity, risk_level: x.risk, reasoning: x.reasoning ?? "", likely_consequences: x.cons || undefined }))} testID="inc-assess" /></>) : null}
          </Card>

          <Card>
            <H>{cs(lang, "inc_authority")}</H>
            {inc.authority_decision ? <Body>{inc.authority_decision.decision} · {inc.authority_decision.approved_by ? "✔" : "…"} · {inc.authority_decision.reasons}</Body> : null}
            {!closed && !inc.authority_notification?.submitted_at ? (<>
              <Input label={cs(lang, "inc_reasons")} value={x.reasons ?? ""} onChangeText={set("reasons")} multiline testID="inc-reasons" />
              <Btn kind="ghost" label={cs(lang, "inc_decide_notify")} onPress={() => act(() => incidentApi.decide(id, { decision: "notify", reasons: x.reasons ?? "" }))} testID="inc-decide-notify" />
              <Btn kind="ghost" label={cs(lang, "inc_decide_not_notify")} onPress={() => act(() => incidentApi.decide(id, { decision: "not_notify", reasons: x.reasons ?? "" }))} />
              {inc.authority_decision && !inc.authority_decision.approved_by ? <Btn label={cs(lang, "inc_approve")} onPress={() => act(() => incidentApi.approve(id))} testID="inc-approve" /> : null}
            </>) : null}
            {inc.authority_decision?.decision === "notify" && !closed ? (<>
              <Input label={cs(lang, "inc_dpo")} value={x.dpo ?? ""} onChangeText={set("dpo")} />
              <Btn kind="ghost" label={cs(lang, "inc_prepare")} onPress={() => act(() => incidentApi.draft(id, { dpo_contact: x.dpo ?? "", likely_consequences: x.cons || undefined }))} testID="inc-draft" />
              {inc.authority_notification?.draft_text ? <Card><Body dim>{cs(lang, "inc_draft")}</Body><Body>{inc.authority_notification.draft_text}</Body></Card> : null}
              {inc.authority_notification?.draft_text && !inc.authority_notification.submitted_at ? (<>
                <Input label={cs(lang, "inc_submitted_at")} value={x.submitted_at} onChangeText={set("submitted_at")} />
                <Input label={cs(lang, "inc_auth_ref")} value={x.ref ?? ""} onChangeText={set("ref")} />
                <Input label={cs(lang, "inc_late_reason")} value={x.late ?? ""} onChangeText={set("late")} multiline />
                <Btn label={cs(lang, "inc_record_sub")} onPress={() => act(() => incidentApi.submission(id, { submitted_at: x.submitted_at, authority_reference: x.ref || undefined, late_reason: x.late || undefined }))} testID="inc-submission" />
              </>) : null}
            </>) : null}
            {inc.authority_notification?.submitted_at ? <Body>{fmt(inc.authority_notification.submitted_at)}{inc.authority_notification.late ? ` · ${cs(lang, "dl_late")}` : ""}</Body> : null}
          </Card>

          <Card>
            <H>{cs(lang, "inc_subjects")}</H>
            {inc.data_subjects ? <Body>{inc.data_subjects.required ? cs(lang, "inc_subjects_required") : cs(lang, "inc_subjects_not_required")} · {inc.data_subjects.reasons}</Body> : null}
            {inc.data_subjects?.draft ? <Card><Body>{inc.data_subjects.draft[lang === "ro" ? "ro" : "en"]}</Body></Card> : null}
            {!closed && !inc.data_subjects?.communicated_at ? (<>
              <Input label={cs(lang, "inc_reasons")} value={x.sreasons ?? ""} onChangeText={set("sreasons")} multiline />
              <Input label={cs(lang, "inc_dpo")} value={x.scontact ?? ""} onChangeText={set("scontact")} />
              <Input label={cs(lang, "inc_advice")} value={x.advice ?? ""} onChangeText={set("advice")} multiline />
              <Btn kind="ghost" label={cs(lang, "inc_subjects_required")} onPress={() => act(() => incidentApi.subjectsDecision(id, { required: true, reasons: x.sreasons ?? "", contact: x.scontact, advice: x.advice, likely_consequences: x.cons || undefined }))} testID="inc-subjects-required" />
              <Input label={cs(lang, "inc_exemption")} value={x.exemption ?? ""} onChangeText={set("exemption")} />
              <Btn kind="ghost" label={cs(lang, "inc_subjects_not_required")} onPress={() => act(() => incidentApi.subjectsDecision(id, { required: false, reasons: x.sreasons ?? "", exemption: x.exemption || undefined }))} />
              {inc.data_subjects?.required && inc.data_subjects?.draft ? (<>
                <Chips label={cs(lang, "inc_channel")} value={x.channel} onChange={set("channel")} options={CH.map(c => ({ value: c, label: c }))} />
                <Input label={cs(lang, "inc_recipients")} value={x.recipients ?? ""} onChangeText={v => set("recipients")(v.replace(/\D/g, ""))} keyboardType="number-pad" />
                <Btn label={cs(lang, "inc_record_comm")} onPress={() => act(() => incidentApi.subjectsRecord(id, { communicated_at: x.communicated_at, channel: x.channel, recipients_count: Number(x.recipients || 0) }))} testID="inc-record-comm" />
              </>) : null}
            </>) : null}
          </Card>

          {!closed ? (
            <Card>
              <Input label={cs(lang, "inc_lessons")} value={x.lessons ?? ""} onChangeText={set("lessons")} multiline />
              <Btn kind="danger" label={cs(lang, "inc_close")} onPress={() => act(() => incidentApi.close(id, { lessons_learned: x.lessons ?? "" }))} testID="inc-close" />
            </Card>
          ) : null}
          {err ? <Text style={{ color: colors.error }} testID="inc-error">{err}</Text> : null}

          <Card>
            <H>{cs(lang, "inc_audit")}</H>
            {audit.data ? <Text style={{ color: audit.data.chain.ok ? colors.success : colors.error }} testID="inc-chain">{audit.data.chain.ok ? cs(lang, "inc_chain_ok") : cs(lang, "inc_chain_broken")}</Text> : null}
            {(audit.data?.events ?? []).map((e: any) => <Body key={e.event_id} dim>#{e.seq} {fmt(e.ts)} · {e.action} · {e.actor_id}</Body>)}
          </Card>
        </>
      ) : null}
    </Screen>
  );
}
