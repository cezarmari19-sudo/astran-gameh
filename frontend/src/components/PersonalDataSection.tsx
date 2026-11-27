import React, { useState } from "react";
import { Text } from "react-native";
import { useRouter } from "expo-router";
import { useI18n } from "@/src/i18n";
import { cs, csErr } from "@/src/i18n/complianceStrings";
import { personalApi, type Reauth } from "@/src/api/compliance";
import { Badge, Body, Btn, Card, Chips, H, Input, KV, LoadState, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

const REASONS = ["typo", "wrong_year_or_day", "entered_by_someone_else", "other"] as const;
const DOB_RE = /^\d{4}-\d{2}-\d{2}$/;

// GDPR Art. 16: corectarea e-mailului si a datei nasterii. Toata logica (reautentificare, limite, reguli de varsta, linkuri) e pe server;
// aici doar colectam datele si aratam rezultatul. NU se cer si NU se incarca documente de identitate.
export default function PersonalDataSection() {
  const { lang } = useI18n();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad(() => personalApi.overview(), []);
  const [password, setPassword] = useState(""); const [code, setCode] = useState(""); const [codeSent, setCodeSent] = useState(false);
  const [newEmail, setNewEmail] = useState(""); const [dob, setDob] = useState("");
  const [reason, setReason] = useState<(typeof REASONS)[number]>("typo"); const [reasonText, setReasonText] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null); const [busy, setBusy] = useState(false);

  const reauth = (): Reauth => (data?.reauth_method === "password" ? { password } : { reauth_code: code.trim() });
  const run = async (fn: () => Promise<any>, okText?: (r: any) => string) => {
    setBusy(true); setMsg(null);
    try { const r = await fn(); if (okText) setMsg({ ok: true, text: okText(r) }); setPassword(""); setCode(""); await reload(); }
    catch (e: any) { setMsg({ ok: false, text: csErr(lang, e) }); }
    finally { setBusy(false); }
  };
  const sendCode = () => run(async () => { await personalApi.sendReauthCode(); setCodeSent(true); }, () => cs(lang, "pd_code"));
  const corr = data?.date_of_birth.correction;
  const reauthReady = data?.reauth_method === "password" ? password.length > 0 : code.trim().length > 0;

  const reauthFields = () => data ? (
    <>
      <Body dim>{cs(lang, "pd_reauth_note")}</Body>
      {data.reauth_method === "password"
        ? <Input label={cs(lang, "pd_password")} value={password} onChangeText={setPassword} secure testID="pd-password" />
        : <>
            <Btn kind="ghost" label={cs(lang, "pd_send_code")} onPress={sendCode} disabled={busy} testID="pd-send-code" />
            {codeSent ? <Input label={cs(lang, "pd_code")} value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={8} testID="pd-code" /> : null}
          </>}
    </>
  ) : null;

  return (
    <Card>
      <H>{cs(lang, "pd_title")}</H>
      <LoadState loading={loading} error={error} onRetry={reload} />
      {data ? (
        <>
          <KV k={cs(lang, "pd_email")} v={<Body>{data.email} <Badge label={cs(lang, data.email_verified ? "pd_verified" : "pd_unverified")} tone={data.email_verified ? "ok" : "warn"} /></Body>} />
          {data.pending_email_change ? (
            <>
              <Body dim>{cs(lang, "pd_pending", { email: data.pending_email_change.new_email_masked })}</Body>
              <Btn kind="ghost" label={cs(lang, "pd_cancel")} onPress={() => run(() => personalApi.cancelEmailChange())} testID="pd-cancel-email" />
            </>
          ) : data.email_service_available ? (
            <>
              <Input label={cs(lang, "pd_new_email")} value={newEmail} onChangeText={setNewEmail} placeholder="name@example.com" testID="pd-new-email" />
              {reauthFields()}
              <Btn label={cs(lang, "pd_request_link")} disabled={busy || !newEmail.includes("@") || !reauthReady} testID="pd-email-submit"
                onPress={() => run(() => personalApi.requestEmailChange(newEmail.trim(), reauth()), () => cs(lang, "pd_link_sent"))} />
            </>
          ) : <Body dim>{csErr(lang, { code: "email_not_configured" })}</Body>}

          <KV k={cs(lang, "pd_dob")} v={data.date_of_birth.set ? (data.date_of_birth.age_band ?? "—") : cs(lang, "pd_dob_not_set")} />
          {data.date_of_birth.set && corr ? (
            corr.pending ? (
              <>
                <Body>{cs(lang, "pd_awaiting", { age: corr.pending.required_threshold })}</Body>
                {!corr.pending.provider_available ? <Body dim>{cs(lang, "pd_provider_unavailable")}</Body> : null}
                <Btn label={cs(lang, "pd_verify_now")} onPress={() => router.push("/age-status")} disabled={!corr.pending.provider_available} testID="pd-verify-age" />
                <Btn kind="ghost" label={cs(lang, "pd_cancel_request")} onPress={() => run(() => personalApi.cancelDobCorrection())} testID="pd-cancel-dob" />
              </>
            ) : corr.can_request ? (
              <>
                <Input label={cs(lang, "pd_new_dob")} value={dob} onChangeText={setDob} placeholder="1999-04-23" maxLength={10} testID="pd-dob" />
                <Chips label={cs(lang, "pd_reason")} value={reason} onChange={setReason} options={REASONS.map(r => ({ value: r, label: cs(lang, `reason_${r}` as any) }))} />
                {reason === "other" ? <Input label={cs(lang, "pd_reason_text")} value={reasonText} onChangeText={setReasonText} multiline maxLength={500} testID="pd-reason-text" /> : null}
                {reauthFields()}
                <Body dim>{cs(lang, "pd_limits_note")} {cs(lang, "pd_no_docs")}</Body>
                <Btn label={cs(lang, "pd_submit")} testID="pd-dob-submit" disabled={busy || !DOB_RE.test(dob) || !reauthReady || (reason === "other" && reasonText.trim().length < 10)}
                  onPress={() => run(() => personalApi.requestDobCorrection({ date_of_birth: dob, reason_code: reason, reason_text: reasonText.trim() || undefined, ...reauth() }),
                    (r) => (r?.status === "applied" ? cs(lang, "pd_applied") : cs(lang, "pd_awaiting", { age: r?.required_threshold ?? 18 })))} />
              </>
            ) : (
              <Body dim>{corr.applied_count >= corr.max_applied ? cs(lang, "pd_limit_reached")
                : corr.next_request_at ? cs(lang, "pd_next_at", { date: new Date(corr.next_request_at).toLocaleDateString() }) : ""}</Body>
            )
          ) : null}
        </>
      ) : null}
      {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error, marginTop: 8 }} testID="pd-message">{msg.text}</Text> : null}
    </Card>
  );
}
