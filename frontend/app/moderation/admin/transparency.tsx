import React, { useState } from "react";
import { Text } from "react-native";
import { useI18n } from "@/src/i18n";
import { modErr, ms } from "@/src/i18n/modStrings";
import { dsaApi } from "@/src/api/dsa";
import { Body, Btn, Card, H, Input, KV, Screen } from "@/src/components/modUi";
import { colors } from "@/src/theme";

// Previzualizare + publicare raport Art. 15 (previzualizarea: personal; publicarea: doar admin, verificat pe server). Perioada: an calendaristic.
export default function TransparencyAdmin() {
  const { lang } = useI18n();
  const [year, setYear] = useState(String(new Date().getFullYear() - 1));
  const [rep, setRep] = useState<any>(null); const [applic, setApplic] = useState<any>(null);
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const y = parseInt(year, 10);
  const run = async (fn: () => Promise<void>) => { setBusy(true); setMsg(null); try { await fn(); } catch (e: any) { setMsg({ ok: false, text: modErr(lang, e) }); } finally { setBusy(false); } };
  return (
    <Screen title={ms(lang, "trp_title")} testID="transparency-admin">
      <Card>
        <Input testID="trp-year" label={ms(lang, "trp_year")} value={year} onChangeText={setYear} keyboardType="numeric" maxLength={4} />
        <Btn kind="ghost" testID="trp-preview" label={ms(lang, "trp_preview")} disabled={busy || !(y >= 2024)}
          onPress={() => run(async () => { setRep(await dsaApi.transparencyPreview(y)); setApplic((await dsaApi.applicability()).obligations); })} />
        <Btn testID="trp-publish" label={ms(lang, "trp_publish")} disabled={busy || !(y >= 2024) || !rep}
          onPress={() => run(async () => { const r = await dsaApi.transparencyPublish(`${y}-01-01T00:00:00Z`, `${y + 1}-01-01T00:00:00Z`); setMsg({ ok: true, text: `${ms(lang, "trp_published")}: v${r.version}` }); })} />
        {msg ? <Text style={{ color: msg.ok ? colors.success : colors.error }}>{msg.text}</Text> : null}
      </Card>
      {applic ? (
        <Card>
          <H>{ms(lang, "trp_applic")}</H>
          {["art_9", "art_10", "art_15", "art_16", "art_17", "art_20", "art_24_5"].map(k => <KV key={k} k={k.replace("_", ". ").replace("_", "(") + (k === "art_24_5" ? ")" : "")} v={applic[k].status} />)}
        </Card>
      ) : null}
      {rep ? <Card><H>{ms(lang, "trp_json")}</H><Text selectable style={{ color: colors.onSurface, fontSize: 11 }}>{JSON.stringify(rep, null, 1)}</Text></Card> : <Body dim>{ms(lang, "tr_note")}</Body>}
    </Screen>
  );
}
