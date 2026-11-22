import React, { useState } from "react";
import { Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Badge, Body, Btn, Card, Check, Chips, H, Input, KV, LoadState, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const ACTIONS = ["no_action", "close_resolved", "warn", "remove_content_illegal", "remove_content_policy", "restrict_content", "restrict_account_temporary", "suspend_account_permanent"];

export default function AdminReport() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const rid = String(id);
  const { lang } = useI18n();
  const { user } = useAuth();
  const { data, error, loading, reload } = useLoad(() => modApi.adminReport(rid), [rid]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(""); const [assignee, setAssignee] = useState(""); const [stReason, setStReason] = useState("");
  const [assess, setAssess] = useState<"sufficient" | "insufficient">("sufficient"); const [assessReason, setAssessReason] = useState("");
  const [action, setAction] = useState("warn"); const [reasoning, setReasoning] = useState(""); const [notifierReasoning, setNotifierReasoning] = useState("");
  const [legal, setLegal] = useState(""); const [tos, setTos] = useState(""); const [days, setDays] = useState("7"); const [scope, setScope] = useState<"chat" | "full">("full");
  const [auto, setAuto] = useState(false);
  const [corr, setCorr] = useState<Record<string, string>>({});

  const act = async (fn: () => Promise<any>, ok?: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ ok: true, text: ok ?? "OK" }); await reload(); } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };

  if (loading || error) return <Screen title={ms(lang, "adm_report")}><LoadState loading={loading} error={error} onRetry={reload} /></Screen>;
  const d = data!, r = d.report;
  const closed = r.status === "resolved" || r.status === "rejected";
  const notice = r.notice;
  const needLegal = action === "remove_content_illegal";
  const needTos = !["no_action", "close_resolved", "remove_content_illegal"].includes(action);
  const submitDecision = () => act(() => modApi.decide(rid, {
    action, reasoning, notifier_reasoning: notifierReasoning || undefined, legal_ground: legal || undefined, tos_provision: tos || undefined,
    ...(action === "restrict_account_temporary" ? { days: parseInt(days, 10), scope } : {}), automated_detection: auto,
  }), ms(lang, "adm_decision_done"));
  const card = (u: any) => !u ? <Body dim>—</Body> : (
    <>
      <Body bold>{u.username || u.user_id}</Body>
      <Body dim>{u.user_id}</Body>
      {u.missing ? null : <Body dim>{ms(lang, "adm_warnings")}: {u.warning_count} {u.suspended ? `· ${ms(lang, "adm_suspended")}` : ""}</Body>}
    </>
  );
  return (
    <Screen title={ms(lang, "adm_report")} testID="admin-report-detail">
      <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <Badge label={label(lang, "status_", r.status)} tone={tone(r.status)} />
        <Badge label={label(lang, "kind_", r.report_kind)} tone={r.report_kind === "illegal_content_notice" ? "bad" : "info"} />
        <Badge label={label(lang, "cat_", r.category || "other")} />
      </View>
      {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error, marginBottom: 8 }} testID="admin-msg">{msg.text}</Text> : null}
      <Card>
        <KV k={ms(lang, "rep_id")} v={r.report_id} />
        <KV k={ms(lang, "rep_received_at")} v={fmt(r.created_at)} />
        <KV k={ms(lang, "rep_reported_content")} v={`${label(lang, "type_", r.target_type)} · ${r.target_id}`} />
        {r.reason ? <KV k={ms(lang, "rep_reason")} v={r.reason} /> : null}
        {r.details ? <KV k={ms(lang, "rep_details")} v={r.details} /> : null}
        <KV k={ms(lang, "adm_assigned")} v={r.assigned_to || "—"} />
      </Card>
      {notice ? (
        <Card>
          <H>{ms(lang, "adm_notice")}</H>
          <KV k={ms(lang, "rep_explanation")} v={notice.explanation} />
          {notice.alleged_legal_basis ? <KV k={ms(lang, "rep_legal_basis")} v={notice.alleged_legal_basis} /> : null}
          {notice.content_location ? <KV k={ms(lang, "rep_location")} v={notice.content_location} /> : null}
          <KV k={ms(lang, "rep_name")} v={notice.reporter_name || "—"} />
          <KV k={ms(lang, "rep_email")} v={notice.reporter_email || "—"} />
          <KV k={ms(lang, "adm_assessment")} v={notice.assessment?.state === "not_assessed" ? "—" : `${notice.assessment?.state} · ${notice.assessment?.reasoning ?? ""}`} />
          {!closed ? (
            <>
              <Chips value={assess} onChange={setAssess} options={[{ value: "sufficient", label: ms(lang, "adm_sufficient") }, { value: "insufficient", label: ms(lang, "adm_insufficient") }]} />
              <Input testID="assess-reason" value={assessReason} onChangeText={setAssessReason} placeholder={ms(lang, "adm_reasoning")} multiline />
              <Btn kind="ghost" label={ms(lang, "save")} disabled={busy || assessReason.trim().length < 10} onPress={() => act(() => modApi.assess(rid, assess, assessReason))} />
            </>
          ) : null}
        </Card>
      ) : null}
      <Card>
        <H>{ms(lang, "adm_evidence")}</H>
        {Object.entries(d.evidence || {}).map(([k, v]) => <KV key={k} k={k} v={typeof v === "string" ? v : JSON.stringify(v)} />)}
        {d.evidence_sha256 ? <Body dim>sha256 {String(d.evidence_sha256).slice(0, 16)}…</Body> : null}
      </Card>
      <Card><H>{ms(lang, "adm_reporter")}</H>{card(d.reporter)}</Card>
      <Card><H>{ms(lang, "adm_reported")}</H>{card(d.reported_user)}<Body dim>{ms(lang, "adm_reports_against")}: {d.reports_against_user}</Body></Card>

      {!closed ? (
        <>
          <H>{ms(lang, "adm_assign")}</H>
          <Card>
            <Btn kind="ghost" label={ms(lang, "adm_assign_me")} disabled={busy} onPress={() => act(() => modApi.assign(rid, user!.user_id))} testID="assign-me" />
            <Input value={assignee} onChangeText={setAssignee} placeholder={ms(lang, "adm_assignee_id")} />
            <Btn kind="ghost" label={ms(lang, "adm_assign")} disabled={busy || !assignee.trim()} onPress={() => act(() => modApi.assign(rid, assignee.trim()))} />
          </Card>
        </>
      ) : null}
      <H>{ms(lang, "adm_set_status")}</H>
      <Card>
        <Input value={stReason} onChangeText={setStReason} placeholder={ms(lang, "adm_reasoning")} multiline />
        <View style={{ flexDirection: "row", gap: 8 }}>
          <View style={{ flex: 1 }}><Btn kind="ghost" label={ms(lang, "status_pending")} disabled={busy || r.status === "pending"} onPress={() => act(() => modApi.setStatus(rid, "pending", stReason))} testID="set-pending" /></View>
          <View style={{ flex: 1 }}><Btn kind="ghost" label={ms(lang, "status_under_review")} disabled={busy || r.status === "under_review"} onPress={() => act(() => modApi.setStatus(rid, "under_review", stReason))} testID="set-review" /></View>
        </View>
      </Card>

      {!closed ? (
        <>
          <H>{ms(lang, "adm_decide")}</H>
          <Card>
            <Chips value={action} onChange={setAction} options={ACTIONS.map(a => ({ value: a, label: ms(lang, ("act_" + a) as any) }))} />
            {action === "suspend_account_permanent" ? <Body dim>{ms(lang, "adm_admin_only")}</Body> : null}
            <Input testID="decision-reasoning" label={ms(lang, "adm_reasoning")} value={reasoning} onChangeText={setReasoning} multiline maxLength={4000} />
            {needLegal ? <Input testID="decision-legal" label={ms(lang, "dec_legal_ground")} value={legal} onChangeText={setLegal} maxLength={500} /> : null}
            {needTos ? <Input testID="decision-tos" label={ms(lang, "dec_tos")} value={tos} onChangeText={setTos} maxLength={300} /> : null}
            {action === "restrict_account_temporary" ? (
              <>
                <Input label={ms(lang, "adm_days")} value={days} onChangeText={setDays} keyboardType="number-pad" maxLength={3} />
                <Chips label={ms(lang, "adm_scope")} value={scope} onChange={setScope} options={[{ value: "chat", label: ms(lang, "adm_scope_chat") }, { value: "full", label: ms(lang, "adm_scope_full") }]} />
              </>
            ) : null}
            <Input label={ms(lang, "adm_notifier_reasoning")} value={notifierReasoning} onChangeText={setNotifierReasoning} multiline maxLength={2000} />
            <Check checked={auto} onToggle={() => setAuto(!auto)} label={ms(lang, "adm_auto_detect")} />
            <Btn testID="decision-submit" kind={action === "suspend_account_permanent" || action.startsWith("remove") ? "danger" : "primary"} label={ms(lang, "adm_decide")}
              disabled={busy || reasoning.trim().length < 10 || (needLegal && legal.trim().length < 3) || (needTos && tos.trim().length < 2)} onPress={submitDecision} />
          </Card>
        </>
      ) : <Body dim>{ms(lang, "adm_closed")}</Body>}

      <H>{ms(lang, "adm_decisions")}</H>
      {d.decisions.length === 0 ? <Body dim>{ms(lang, "empty")}</Body> : null}
      {d.decisions.map((x: any) => (
        <Card key={x.decision_id}>
          <Badge label={label(lang, "dec_", x.status)} tone={x.status === "reversed" ? "ok" : "warn"} />
          <Body bold>{label(lang, "act_", x.action)}</Body>
          <Body dim>{ms(lang, "adm_decision_by")} {x.moderator_id} · {fmt(x.created_at)}</Body>
          <Body>{x.reasoning}</Body>
          <Input value={corr[x.decision_id] ?? ""} onChangeText={v => setCorr({ ...corr, [x.decision_id]: v })} placeholder={ms(lang, "adm_correction")} multiline />
          <Btn kind="ghost" label={ms(lang, "adm_note_add")} disabled={busy || (corr[x.decision_id] ?? "").trim().length < 10}
            onPress={() => act(async () => { await modApi.correct(x.decision_id, corr[x.decision_id]); setCorr({ ...corr, [x.decision_id]: "" }); })} />
        </Card>
      ))}

      <H>{ms(lang, "adm_notes")}</H>
      {d.internal_notes.map((n: any) => <Card key={n.note_id}><Body dim>{n.author_id} · {fmt(n.created_at)}</Body><Body>{n.text}</Body></Card>)}
      <Card>
        <Input testID="note-text" value={note} onChangeText={setNote} multiline maxLength={4000} />
        <Btn testID="note-add" kind="ghost" label={ms(lang, "adm_note_add")} disabled={busy || !note.trim()} onPress={() => act(async () => { await modApi.addNote(rid, note); setNote(""); })} />
      </Card>

      <H>{ms(lang, "adm_history")}</H>
      {d.history.map((e: any) => (
        <Card key={e.event_id}>
          <Body bold>{e.event_type}{e.action ? ` · ${e.action}` : ""}</Body>
          <Body dim>{fmt(e.ts)} · {e.actor_id}{e.previous_status || e.new_status ? ` · ${e.previous_status ?? "—"} → ${e.new_status ?? "—"}` : ""}{e.outcome ? ` · ${e.outcome}` : ""}</Body>
          {e.reasoning ? <Body>{e.reasoning}</Body> : null}
        </Card>
      ))}
    </Screen>
  );
}
