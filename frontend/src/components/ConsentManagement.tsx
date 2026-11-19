import React, { useCallback, useEffect, useState } from "react";
import { View, Text, StyleSheet, Pressable, Modal, ActivityIndicator } from "react-native";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { consentApi, type ConsentEvent, type ConsentOverview, type ConsentPurpose } from "@/src/api/consent";
import { useI18n } from "@/src/i18n";
import { cs, docTitle } from "@/src/i18n/consentStrings";
import LegalDocModal from "@/src/components/LegalDocModal";
import type { LegalDocType } from "@/src/api/consent";

// Settings > Privacy > "Consent Management". Totul vine de la server (/api/consent/*): documentele acceptate, versiunea,
// data (UTC, generata de server), starea fiecarui consimtamant si istoricul complet. Retragerea = eveniment nou in
// registrul adauga-doar; dovada acceptarii ramane. Retragerea unui consimtamant NU sterge contul.

function fmt(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "-" : d.toLocaleString();
}

export default function ConsentManagement() {
  const { lang } = useI18n();
  const [ov, setOv] = useState<ConsentOverview | null>(null);
  const [events, setEvents] = useState<ConsentEvent[] | null>(null);
  const [showHist, setShowHist] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ConsentPurpose | null>(null);
  const [reading, setReading] = useState<{ type: LegalDocType; version: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [o, h] = await Promise.all([consentApi.me(lang), consentApi.history()]);
      setOv(o); setEvents(h.events); setErr(null);
    } catch (e: any) { setErr(e?.message || cs(lang, "docs_load_failed")); }
  }, [lang]);
  useEffect(() => { load(); }, [load]);

  async function act(fn: () => Promise<ConsentOverview>, msg: string) {
    setBusy(true); setErr(null); setOk(null);
    try { setOv(await fn()); setOk(msg); setEvents((await consentApi.history()).events); }
    catch (e: any) { setErr(e?.message || "Error"); }
    finally { setBusy(false); setConfirm(null); }
  }

  if (!ov) return <View style={styles.box}>{err ? <Text style={styles.err}>{err}</Text> : <ActivityIndicator color={colors.brand} />}</View>;

  const statusLabel = (s: string) => s === "current" ? cs(lang, "status_current") : s === "acceptance_required" ? cs(lang, "status_accept") : cs(lang, "status_notice");
  const stateLabel = (s: string) => s === "granted" ? cs(lang, "state_granted") : s === "withdrawn" ? cs(lang, "state_withdrawn") : cs(lang, "state_never");

  return (
    <View style={styles.box} testID="consent-management">
      <Text style={styles.h}>{cs(lang, "cm_title")}</Text>
      <Text style={styles.p}>{cs(lang, "cm_intro")}</Text>
      {err ? <Text style={styles.err} testID="consent-error">{err}</Text> : null}
      {ok ? <Text style={styles.ok} testID="consent-ok">{ok}</Text> : null}

      <Text style={styles.sh}>{cs(lang, "cm_docs")}</Text>
      {ov.documents.map(d => (
        <View key={d.subject} style={styles.card} testID={`consent-doc-${d.subject}`}>
          <Text style={styles.t}>{docTitle(lang, d.subject)}</Text>
          <Text style={styles.p}>{d.legal_basis === "contract" ? cs(lang, "basis_contract") : cs(lang, "basis_notice")}</Text>
          <Text style={styles.p}>{cs(lang, "version")}: {d.accepted_version ?? "-"} · {cs(lang, "accepted_on")}: {fmt(d.accepted_at)}</Text>
          <Text style={[styles.p, d.status === "current" ? null : styles.warn]}>{statusLabel(d.status)}{d.change_summary ? ` — ${d.change_summary}` : ""}</Text>
          <Text style={styles.small}>{cs(lang, "not_withdrawable")}</Text>
          <Pressable onPress={() => setReading({ type: d.subject, version: d.accepted_version ?? d.current_version })} hitSlop={8}>
            <Text style={styles.link}>{cs(lang, "read")} ({cs(lang, "version")} {d.accepted_version ?? d.current_version})</Text>
          </Pressable>
        </View>
      ))}

      <Text style={styles.sh}>{cs(lang, "cm_optional")}</Text>
      {ov.purposes.map(p => (
        <View key={p.subject} style={styles.card} testID={`consent-purpose-${p.subject}`}>
          <Text style={styles.t}>{p.title}</Text>
          <Text style={styles.p}>{p.description}</Text>
          <Text style={styles.p}>{stateLabel(p.state)}{p.granted_at ? ` · ${fmt(p.granted_at)}` : p.withdrawn_at ? ` · ${fmt(p.withdrawn_at)}` : ""}{p.granted_version ? ` · ${cs(lang, "version")} ${p.granted_version}` : ""}</Text>
          {!p.eligible ? <Text style={styles.small}>{cs(lang, "not_available")}</Text>
            : p.state === "granted"
              ? <SecondaryButton testID={`consent-withdraw-${p.subject}`} label={cs(lang, "withdraw")} onPress={() => setConfirm(p)} />
              : <PrimaryButton testID={`consent-grant-${p.subject}`} label={cs(lang, "grant")} disabled={busy}
                  onPress={() => act(() => consentApi.grant(p.subject, lang), cs(lang, "granted_ok"))} />}
        </View>
      ))}

      <Pressable onPress={() => setShowHist(v => !v)} hitSlop={8} testID="consent-history-toggle">
        <Text style={styles.link}>{showHist ? cs(lang, "cm_history_hide") : cs(lang, "cm_history_show")}</Text>
      </Pressable>
      {showHist && events ? (
        <View style={styles.card} testID="consent-history">
          <View style={styles.hrow}>
            {(["col_doc", "col_version", "col_action", "col_date"] as const).map(k => <Text key={k} style={[styles.hc, styles.hh]}>{cs(lang, k)}</Text>)}
          </View>
          {[...events].reverse().map(e => (
            <View key={e.event_id} style={styles.hrow}>
              <Text style={styles.hc}>{e.subject === "terms_of_service" || e.subject === "privacy_policy" ? docTitle(lang, e.subject) : (ov.purposes.find(p => p.subject === e.subject)?.title ?? e.subject)}</Text>
              <Text style={styles.hc}>{e.version}</Text>
              <Text style={styles.hc}>{e.action === "accepted" ? cs(lang, "action_accepted") : cs(lang, "action_withdrawn")}</Text>
              <Text style={styles.hc}>{fmt(e.created_at)}</Text>
            </View>
          ))}
        </View>
      ) : null}

      <Modal visible={!!confirm} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
        <View style={styles.backdrop}>
          <View style={styles.dialog}>
            <Text style={styles.h}>{cs(lang, "withdraw_title")}</Text>
            <Text style={styles.p}>{confirm?.consequences}</Text>
            <View style={{ height: spacing.md }} />
            <PrimaryButton testID="consent-withdraw-confirm" label={cs(lang, "withdraw_confirm")} disabled={busy}
              onPress={() => confirm && act(() => consentApi.withdraw(confirm.subject, true, lang), cs(lang, "withdrawn_ok"))} />
            <View style={{ height: spacing.sm }} />
            <SecondaryButton label={cs(lang, "cancel")} onPress={() => setConfirm(null)} />
          </View>
        </View>
      </Modal>
      {reading ? <LegalDocModal visible docType={reading.type} lang={lang} version={reading.version} onClose={() => setReading(null)} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { gap: spacing.sm, marginBottom: spacing.xl },
  h: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  sh: { color: colors.onSurface, fontSize: 14, fontWeight: "800", marginTop: spacing.md },
  t: { color: colors.onSurface, fontSize: 14, fontWeight: "700" },
  p: { color: colors.onSurface2, fontSize: 12, lineHeight: 17 },
  small: { color: colors.onSurface3, fontSize: 11, lineHeight: 15 },
  warn: { color: colors.warning },
  err: { color: colors.error, fontSize: 12 },
  ok: { color: colors.success, fontSize: 12 },
  link: { color: colors.brand, fontWeight: "700", textDecorationLine: "underline", fontSize: 12 },
  card: { backgroundColor: colors.surface2, borderRadius: radius.md, padding: spacing.md, gap: 6, borderWidth: 1, borderColor: colors.border },
  hrow: { flexDirection: "row", gap: 4, paddingVertical: 3 },
  hc: { flex: 1, color: colors.onSurface2, fontSize: 10 },
  hh: { fontWeight: "800", color: colors.onSurface },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.lg },
  dialog: { backgroundColor: colors.surface2, borderRadius: radius.lg, padding: spacing.lg, width: "100%", maxWidth: 420 },
});