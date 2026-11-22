import React, { useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { modApi } from "@/src/api/moderation";
import { Body, Btn, Card, Chips, H, Input, KV, LoadState, Screen, useLoad } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Pagina de contact (Art. 11/12). Datele vin DOAR din configurarea serverului; ce lipseste e aratat ca "nesetat", nu inventat.
export default function Contact() {
  const { lang } = useI18n();
  const { data, error, loading, reload } = useLoad(() => modApi.contactInfo());
  const [channel, setChannel] = useState("user");
  const [email, setEmail] = useState(""); const [name, setName] = useState("");
  const [subject, setSubject] = useState(""); const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  const missing = ms(lang, "con_missing");
  const send = async () => {
    setBusy(true); setRes(null);
    try { await modApi.contactSend({ channel, name: name || undefined, email, subject, message }); setRes({ ok: true, text: ms(lang, "con_sent") }); setSubject(""); setMessage(""); }
    catch (e: any) { setRes({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); }
  };
  const c = data;
  const channels = (["user", "authority", "commission_or_dsc", "trusted_flagger", "other"] as const)
    .map(v => ({ value: v, label: ms(lang, ("con_ch_" + v) as any) }));
  return (
    <Screen title={ms(lang, "con_title")} testID="contact-screen">
      <LoadState loading={loading} error={error} onRetry={reload} />
      {c ? (
        <>
          {!c.configured ? <Card><Text style={{ color: colors.warning }} testID="contact-missing">{ms(lang, "con_missing_banner")}</Text></Card> : null}
          <Card>
            <KV k={ms(lang, "con_entity")} v={c.legal_entity_name || missing} />
            <KV k={ms(lang, "con_address")} v={c.postal_address || missing} />
            {c.eu_legal_representative ? <KV k={ms(lang, "con_eu_rep")} v={c.eu_legal_representative} /> : null}
          </Card>
          <H>{ms(lang, "con_authorities")}</H>
          <Card>
            <KV k={ms(lang, "con_email")} v={c.authorities_email || missing} />
            <KV k={ms(lang, "con_languages")} v={c.authorities_languages || missing} />
          </Card>
          <H>{ms(lang, "con_users")}</H>
          <Card>
            <KV k={ms(lang, "con_email")} v={c.users_email || missing} />
            <KV k={ms(lang, "con_languages")} v={c.users_languages || missing} />
          </Card>
          <H>{ms(lang, "con_trusted")}</H>
          <Card><KV k={ms(lang, "con_email")} v={c.trusted_flaggers_email || missing} /></Card>
          <H>{ms(lang, "con_ods")}</H>
          <Card><Body>{c.out_of_court_bodies || missing}</Body></Card>
        </>
      ) : null}
      <H>{ms(lang, "con_form")}</H>
      <Card>
        <Chips label={ms(lang, "con_channel")} options={channels} value={channel as any} onChange={setChannel} />
        <Input testID="contact-name" label={ms(lang, "rep_name")} value={name} onChangeText={setName} maxLength={120} />
        <Input testID="contact-email" label={ms(lang, "con_your_email")} value={email} onChangeText={setEmail} keyboardType="email-address" maxLength={320} />
        <Input testID="contact-subject" label={ms(lang, "con_subject")} value={subject} onChangeText={setSubject} maxLength={200} />
        <Input testID="contact-message" label={ms(lang, "con_message")} value={message} onChangeText={setMessage} multiline maxLength={4000} />
        {res ? <Text style={{ color: res.ok ? colors.success : colors.error, marginBottom: 8 }} testID="contact-result">{res.text}</Text> : null}
        <Btn testID="contact-send" label={ms(lang, "send")} onPress={send} disabled={busy || !email || subject.trim().length < 3 || message.trim().length < 10} />
      </Card>
    </Screen>
  );
}
