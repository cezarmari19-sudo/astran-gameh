import React, { useState } from "react";
import { Platform, Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Badge, Body, Btn, Card, Check, H, Input, KV, LoadState, Screen, fmt, label, tone, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Pick de fisier DOAR pe web (nu exista un selector de documente nativ in dependentele aplicatiei). Continutul se trimite base64;
// serverul valideaza tipul, marimea si continutul si il stocheaza intr-un director privat (niciodata public).
function pickWebFile(): Promise<{ name: string; b64: string } | null> {
  return new Promise(resolve => {
    const doc = (globalThis as any).document;
    if (Platform.OS !== "web" || !doc) return resolve(null);
    const inp = doc.createElement("input"); inp.type = "file"; inp.accept = ".pdf,.xml,.json,.txt";
    inp.onchange = () => {
      const file = inp.files?.[0]; if (!file) return resolve(null);
      const r = new (globalThis as any).FileReader();
      r.onload = () => resolve({ name: file.name, b64: String(r.result).split(",")[1] || "" });
      r.readAsDataURL(file);
    };
    inp.click();
  });
}

export default function OrderDetail() {
  const { lang } = useI18n();
  const { id } = useLocalSearchParams<{ id: string }>();
  const oid = String(id);
  const { data: o, error, loading, reload } = useLoad(() => dsaApi.order(oid), [oid]);
  const [method, setMethod] = useState(""); const [reasoning, setReasoning] = useState(""); const [note, setNote] = useState("");
  const [ndConfirm, setNdConfirm] = useState(false);
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ ok: true, text: ms(lang, "save") }); await reload(); } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };
  if (!o) return <Screen title={ms(lang, "ord_title")}><LoadState loading={loading} error={error} onRetry={reload} /></Screen>;
  const st = o.status as string;
  const art9 = o.order_type === "art9_illegal_content";
  const reviewable = st === "pending_legal_review" || st === "incomplete";
  const done = new Set((o.responses ?? []).map((r: any) => r.kind));
  const attach = async () => {
    const f = await pickWebFile(); if (!f) return;
    await act(() => dsaApi.attach(oid, f.name, f.b64));
  };
  return (
    <Screen title={o.reference} testID="order-detail">
      <Card>
        <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
          <Badge label={label(lang, "ord_status_", st)} tone={tone(st)} />
          <Badge label={ms(lang, ("ord_type_" + o.order_type) as any)} tone="info" />
        </View>
        <KV k={ms(lang, "ord_authority")} v={`${o.authority.name ?? "—"} (${o.authority.member_state ?? "—"})`} />
        <KV k={ms(lang, "ord_their_ref")} v={o.authority.their_reference ?? "—"} />
        <KV k={ms(lang, "rep_received_at")} v={fmt(o.received_at)} />
        <KV k={ms(lang, "ord_legal_basis")} v={o.legal_basis ?? "—"} />
        <KV k={ms(lang, "ord_reasons")} v={o.statement_of_reasons ?? "—"} />
        {art9 ? <KV k={ms(lang, "ord_scope")} v={o.territorial_scope ?? "—"} /> : null}
        <Body dim>{o.response_deadline ? `${fmt(o.response_deadline)}${o.deadline_status === "overdue" ? ` — ${ms(lang, "ord_overdue")}` : ""}` : ms(lang, "ord_no_deadline")}</Body>
        {o.non_disclosure?.requested ? <Body bold>{ms(lang, "ord_nd")}</Body> : null}
      </Card>
      <Card>
        <H>{ms(lang, "ord_validation")}</H>
        {o.validation?.state === "complete" ? <Body>{ms(lang, "ord_complete")}</Body> : <Body>{ms(lang, "ord_missing")}: {(o.validation?.missing ?? []).join(", ")}</Body>}
        <H>{ms(lang, "ord_authenticity")}</H>
        <Badge label={ms(lang, o.authenticity?.state === "verified" ? "ord_auth_verified" : "ord_auth_unverified")} tone={o.authenticity?.state === "verified" ? "ok" : "warn"} />
        {reviewable ? (
          <>
            <Input testID="ord-method" label={ms(lang, "ord_method")} value={method} onChangeText={setMethod} multiline maxLength={500} />
            <Btn kind="ghost" testID="ord-verify" label={ms(lang, "ord_mark_verified")} disabled={busy || method.trim().length < 10} onPress={() => act(() => dsaApi.authenticity(oid, true, method))} />
          </>
        ) : null}
      </Card>
      {reviewable ? (
        <Card>
          <H>{ms(lang, "ord_review")}</H>
          <Body dim>{ms(lang, "ord_four_eyes")}</Body>
          <Input testID="ord-reasoning" label={ms(lang, "ord_reasoning")} value={reasoning} onChangeText={setReasoning} multiline maxLength={4000} />
          {o.non_disclosure?.requested ? <Check checked={ndConfirm} onToggle={() => setNdConfirm(!ndConfirm)} label={ms(lang, "ord_nd_confirm")} /> : null}
          <Btn testID="ord-approve" label={ms(lang, "ord_approve")} disabled={busy || reasoning.trim().length < 20} onPress={() => act(() => dsaApi.legalReview(oid, "approve", reasoning, ndConfirm))} />
          <Btn kind="ghost" label={ms(lang, "ord_request_info")} disabled={busy || reasoning.trim().length < 20} onPress={() => act(() => dsaApi.legalReview(oid, "request_information", reasoning))} />
          <Btn kind="danger" testID="ord-reject" label={ms(lang, "ord_reject")} disabled={busy || reasoning.trim().length < 20} onPress={() => act(() => dsaApi.legalReview(oid, "reject", reasoning))} />
        </Card>
      ) : null}
      {(st === "approved" || st === "execution_failed") && art9 ? <Btn testID="ord-execute" label={ms(lang, "ord_execute")} disabled={busy || !o.can_execute} onPress={() => act(() => dsaApi.executeOrder(oid))} /> : null}
      {st === "approved" && !art9 ? <Btn testID="ord-prepare" label={ms(lang, "ord_prepare")} disabled={busy} onPress={() => act(() => dsaApi.prepareInformation(oid))} /> : null}
      {st === "information_prepared" && !o.information_package?.manual_delivery ? (
        <Card>
          <Input testID="ord-note" label={ms(lang, "ord_note")} value={note} onChangeText={setNote} multiline maxLength={500} />
          <Btn label={ms(lang, "ord_package_delivered")} disabled={busy || note.trim().length < 10} onPress={() => act(() => dsaApi.packageDelivered(oid, note))} />
        </Card>
      ) : null}
      {o.recipient_notice?.status === "withheld_by_order" ? (
        <Card><Input label={ms(lang, "ord_reasoning")} value={note} onChangeText={setNote} multiline maxLength={500} />
          <Btn kind="ghost" label={ms(lang, "ord_release")} disabled={busy || note.trim().length < 10} onPress={() => act(() => dsaApi.releaseNotice(oid, note))} /></Card>
      ) : null}
      <Card>
        <H>{ms(lang, "ord_respond")}</H>
        {!done.has("receipt") ? <Btn kind="ghost" testID="ord-r-receipt" label={ms(lang, "ord_r_receipt")} disabled={busy} onPress={() => act(() => dsaApi.respond(oid, "receipt"))} /> : null}
        {(st === "executed" || (st === "information_prepared" && o.information_package?.manual_delivery)) && !done.has("effect")
          ? <Btn kind="ghost" label={ms(lang, "ord_r_effect")} disabled={busy} onPress={() => act(() => dsaApi.respond(oid, "effect"))} /> : null}
        {st === "rejected" && !done.has("rejection") ? <Btn kind="ghost" label={ms(lang, "ord_r_rejection")} disabled={busy} onPress={() => act(() => dsaApi.respond(oid, "rejection"))} /> : null}
        {(o.responses ?? []).length ? <H>{ms(lang, "ord_responses")}</H> : null}
        {(o.responses ?? []).map((r: any) => (
          <KV key={r.response_id} k={`${label(lang, "ord_r_", r.kind === "effect" ? "effect" : r.kind)} · ${fmt(r.created_at)}`}
            v={r.delivery ? ms(lang, ("del_" + (r.delivery.status === "sent" ? "sent" : r.delivery.status)) as any) : "—"} />
        ))}
        {["executed", "information_prepared", "rejected"].includes(st) ? (
          <>
            <Input label={ms(lang, "ord_note")} value={note} onChangeText={setNote} multiline maxLength={500} />
            <Btn testID="ord-close" kind="ghost" label={ms(lang, "ord_close")} disabled={busy || note.trim().length < 10} onPress={() => act(() => dsaApi.closeOrder(oid, note))} />
          </>
        ) : null}
      </Card>
      <Card>
        <H>{ms(lang, "ord_attachments")}</H>
        {o.storage?.note ? <Text style={{ color: colors.warning, marginBottom: 6 }}>{ms(lang, "ord_storage_warn")}</Text> : null}
        {(o.attachments ?? []).map((a: any) => <KV key={a.attachment_id} k={a.filename} v={`${Math.ceil(a.size / 1024)} KB · ${a.sha256.slice(0, 12)}…`} />)}
        {Platform.OS === "web" ? <Btn kind="ghost" testID="ord-attach" label={ms(lang, "ord_attach_web")} disabled={busy} onPress={attach} /> : <Body dim>{ms(lang, "ord_attach_native")}</Body>}
      </Card>
      {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error, marginBottom: 8 }} testID="ord-action-msg">{msg.text}</Text> : null}
      <H>{ms(lang, "ord_history")}</H>
      {(o.history ?? []).map((h: any) => (
        <Body key={h.seq} dim>{fmt(h.ts)} · {h.event_type}{h.new_status ? ` → ${h.new_status}` : ""}</Body>
      ))}
    </Screen>
  );
}
