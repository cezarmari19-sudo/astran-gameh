import React, { useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Badge, Body, Btn, Card, Check, Chips, H, Input, KV, Screen, fmt, label } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const CATS = ["illegal_content", "child_safety", "harassment", "hate_speech", "violence", "sexual_content", "self_harm", "scam_fraud", "ip_infringement", "spam", "other"];

// Notificare de continut presupus ilegal (Art. 16) - FUNCTIONEAZA SI FARA CONT (api() nu cere token; serverul aplica limite de rata,
// deduplicare si plafon zilnic). Nimic nu e clasificat ca ilegal automat: un moderator decide. Mesajele private nu se pot raporta de aici.
export default function ReportIllegal() {
  const { lang } = useI18n();
  const [target, setTarget] = useState(""); const [account, setAccount] = useState("");
  const [category, setCategory] = useState("illegal_content");
  const [explanation, setExplanation] = useState(""); const [basis, setBasis] = useState("");
  const [name, setName] = useState(""); const [email, setEmail] = useState("");
  const [exempt, setExempt] = useState(false); const [good, setGood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  const [sent, setSent] = useState<{ id: string; token: string } | null>(null);
  const [cRef, setCRef] = useState(""); const [cCode, setCCode] = useState("");
  const [status, setStatus] = useState<any>(null); const [cErr, setCErr] = useState<string | null>(null);

  const canExempt = category === "child_safety";
  const needsContact = !(canExempt && exempt);
  const ready = (target.trim() || account.trim()) && explanation.trim().length >= 20 && good && (!needsContact || (name.trim() && email.trim()));

  const submit = async () => {
    setBusy(true); setRes(null);
    try {
      const out = await dsaApi.publicNotice({
        content_url: target.trim() || undefined, account_username: target.trim() ? undefined : account.trim() || undefined, category,
        explanation, alleged_legal_basis: basis || undefined, reporter_name: name || undefined, reporter_email: email || undefined,
        identity_exempt: canExempt && exempt, good_faith_declaration: good,
      });
      setSent({ id: out.report_id, token: out.tracking_token });
      setCRef(out.report_id); setCCode(out.tracking_token);
      setExplanation(""); setBasis(""); setTarget(""); setAccount("");
    } catch (e: any) { setRes({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };
  const check = async () => {
    setCErr(null); setStatus(null);
    try { setStatus(await dsaApi.publicNoticeStatus(cRef.trim(), cCode.trim())); } catch (e: any) { setCErr(modErr(lang, e)); }
  };

  return (
    <Screen title={ms(lang, "pub_title")} testID="report-illegal-screen">
      <Body dim>{ms(lang, "pub_intro")}</Body>
      <Body dim>{ms(lang, "pub_private")}</Body>
      <Card>
        <Input testID="pub-target" label={ms(lang, "pub_target")} value={target} onChangeText={setTarget} maxLength={600} placeholder="https://… / astran://game/…" />
        <Input testID="pub-account" label={ms(lang, "pub_account")} value={account} onChangeText={setAccount} maxLength={64} />
        <Chips label={ms(lang, "rep_category")} value={category} onChange={setCategory}
          options={CATS.map(v => ({ value: v, label: ms(lang, ("cat_" + v) as any) }))} />
        <Input testID="pub-explanation" label={ms(lang, "rep_explanation")} value={explanation} onChangeText={setExplanation} multiline maxLength={4000} />
        <Input testID="pub-basis" label={ms(lang, "rep_legal_basis")} value={basis} onChangeText={setBasis} maxLength={500} />
        {canExempt ? <Check testID="pub-exempt" checked={exempt} onToggle={() => setExempt(!exempt)} label={ms(lang, "rep_identity_exempt")} /> : null}
        {needsContact ? (
          <>
            <Body dim>{ms(lang, "pub_no_account")}</Body>
            <Input testID="pub-name" label={ms(lang, "rep_name")} value={name} onChangeText={setName} maxLength={120} />
            <Input testID="pub-email" label={ms(lang, "rep_email")} value={email} onChangeText={setEmail} keyboardType="email-address" maxLength={320} />
          </>
        ) : null}
        <Check testID="pub-good-faith" checked={good} onToggle={() => setGood(!good)} label={ms(lang, "rep_good_faith")} />
        {res ? <Text style={{ color: res.ok ? colors.success : colors.error, marginBottom: 8 }} testID="pub-result">{res.text}</Text> : null}
        <Btn testID="pub-submit" label={ms(lang, "rep_submit")} onPress={submit} disabled={busy || !ready} />
      </Card>
      {sent ? (
        <Card>
          <KV k={ms(lang, "pub_sent")} v={sent.id} />
          <KV k={ms(lang, "pub_token")} v={sent.token} />
        </Card>
      ) : null}

      <H>{ms(lang, "pub_check_title")}</H>
      <Card>
        <Input testID="pub-check-ref" label={ms(lang, "pub_ref")} value={cRef} onChangeText={setCRef} maxLength={64} />
        <Input testID="pub-check-code" label={ms(lang, "pub_code")} value={cCode} onChangeText={setCCode} maxLength={200} />
        {cErr ? <Text style={{ color: colors.error, marginBottom: 8 }}>{cErr}</Text> : null}
        <Btn testID="pub-check" kind="ghost" label={ms(lang, "pub_check")} onPress={check} disabled={cRef.trim().length < 3 || cCode.trim().length < 10} />
      </Card>
      {status ? (
        <Card>
          <KV k={ms(lang, "pub_status")} v={<Badge label={label(lang, "status_", status.status)} />} />
          <KV k={ms(lang, "rep_received_at")} v={fmt(status.created_at)} />
          {(status.outcomes ?? []).length === 0 ? <Body dim>{ms(lang, "pub_no_outcome")}</Body> : null}
          {(status.outcomes ?? []).map((o: any, i: number) => (
            <KV key={i} k={ms(lang, "pub_outcome")} v={`${ms(lang, ("pub_outcome_" + o.outcome) as any)}${o.reasoning ? ` — ${o.reasoning}` : ""}`} />
          ))}
        </Card>
      ) : null}
    </Screen>
  );
}
