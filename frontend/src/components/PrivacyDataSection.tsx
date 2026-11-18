import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, Modal, Pressable, TextInput, ActivityIndicator, Platform, ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import { PrimaryButton, SecondaryButton } from "@/src/components/ui";
import { privacyApi } from "@/src/api/client";
import { accountExportApi, downloadArchive, type ExportInfo, type ExportView } from "@/src/api/dataExport";
import { useAuth } from "@/src/context/AuthContext";
import { useI18n } from "@/src/i18n";

// GDPR: "Privacy & Security > Your Data" - Download Your Data (Art. 15/20, export ZIP asincron) + Delete My Account (Art. 17).
// Toate verificarile reale (re-autentificare, confirmare, apartenenta contului, expirarea link-ului) se fac pe
// server (backend/astran_sandbox/privacy_routes.py); aici doar colectam datele si aratam starea.

type PrivacyStatus = {
  has_password: boolean;
  email_masked: string | null;
  email_service_available: boolean;
  confirmation_text: string;
  export_ttl_seconds: number;
  retention: { financial_records_days: number; moderation_records_days: number; security_log_days: number };
  can_delete: boolean;
};

// SecondaryButton (ui.tsx) nu accepta `style`: il impachetam ca sa ocupe jumatate din rand
function SecBtn({ flex, ...p }: { label: string; onPress?: () => void; testID?: string; flex?: boolean }) {
  return (
    <View style={flex ? { flex: 1 } : undefined}>
      <SecondaryButton {...p} />
    </View>
  );
}

type Mode = null | "export" | "delete";
type DeleteStage = "warning" | "confirm" | "progress";

function errText(e: any, fallback: string): string {
  return e?.message || fallback;
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "-" : d.toLocaleString();
}

function fmtBytes(n: number | null | undefined): string {
  if (typeof n !== "number" || n < 0) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const POLL_MS = 3000;

export default function PrivacyDataSection() {
  const { t } = useI18n();
  const router = useRouter();
  const { logout } = useAuth();
  const [mode, setMode] = useState<Mode>(null);
  const [status, setStatus] = useState<PrivacyStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);

  // re-autentificare (comuna)
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // export ZIP (stare reala, venita de pe server; generarea ruleaza in fundal, UI-ul nu se blocheaza)
  const [exportInfo, setExportInfo] = useState<ExportInfo | null>(null);
  const [exportLoadErr, setExportLoadErr] = useState(false);
  const [dlBusy, setDlBusy] = useState(false);
  const [dlMsg, setDlMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const exportPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);
  const exp: ExportView | null = exportInfo?.export ?? null;

  // stergere
  const [stage, setStage] = useState<DeleteStage>("warning");
  const [ack, setAck] = useState(false);
  const [typed, setTyped] = useState("");
  const [progress, setProgress] = useState<"working" | "completed" | "background">("working");
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const finishedRef = useRef(false);

  const confirmWord = status?.confirmation_text || "DELETE";
  const hasPassword = status?.has_password !== false;

  const resetAll = useCallback(() => {
    setPassword(""); setShowPw(false); setCode(""); setCodeSent(false); setBusy(false); setErr(null);
    setStage("warning"); setAck(false); setTyped(""); setProgress("working");
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => () => {
    aliveRef.current = false;
    if (pollRef.current) clearTimeout(pollRef.current);
    if (exportPollRef.current) clearTimeout(exportPollRef.current);
  }, []);

  const loadExport = useCallback(async () => {
    try {
      const info = await accountExportApi.status();
      if (!aliveRef.current) return;
      setExportInfo(info);
      setExportLoadErr(false);
    } catch {
      if (aliveRef.current) setExportLoadErr(true);
    }
  }, []);

  useEffect(() => { loadExport(); }, [loadExport]);

  // cat timp arhiva se genereaza, intrebam periodic serverul (nu blocheaza interfata)
  useEffect(() => {
    if (exportPollRef.current) { clearTimeout(exportPollRef.current); exportPollRef.current = null; }
    if (exp?.status === "processing") {
      exportPollRef.current = setTimeout(loadExport, POLL_MS);
    }
    return () => { if (exportPollRef.current) clearTimeout(exportPollRef.current); };
  }, [exp?.status, exportInfo, loadExport]);

  async function open(m: Exclude<Mode, null>) {
    resetAll();
    finishedRef.current = false;
    setMode(m);
    setLoadingStatus(true);
    try {
      setStatus(await privacyApi.status());
    } catch (e: any) {
      setErr(errText(e, t("privacy_error_generic")));
    } finally {
      setLoadingStatus(false);
    }
  }

  function close() {
    // in timpul stergerii nu se poate inchide accidental ecranul de progres
    if (mode === "delete" && stage === "progress" && progress === "working") return;
    setMode(null);
    resetAll();
  }

  async function sendCode() {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await privacyApi.sendReauthCode();
      setCodeSent(true);
    } catch (e: any) {
      setErr(errText(e, t("privacy_error_generic")));
    } finally {
      setBusy(false);
    }
  }

  const credentials = () => (hasPassword ? { password } : code.trim() ? { code: code.trim() } : {});
  const credsReady = hasPassword ? password.length > 0 : code.trim().length >= 4 || status?.email_service_available === false;

  // ---------------------------------------------------------------- export
  async function doExport() {
    setBusy(true); setErr(null);
    try {
      const res = await accountExportApi.request(credentials());
      setExportInfo(prev => (prev ? { ...prev, export: res.export } : prev));
      setDlMsg({ ok: true, text: t("dx_requested_ok") });
      setMode(null);
      resetAll();
      loadExport();
    } catch (e: any) {
      setErr(errText(e, t("privacy_error_generic")));
    } finally {
      setBusy(false);
    }
  }

  async function doDownload() {
    if (dlBusy) return;
    setDlBusy(true); setDlMsg(null);
    try {
      const how = await downloadArchive(exportInfo?.info.archive_name || "astran_game_data_export.zip",
        t("dx_download_unavailable"), t("dx_download_failed"));
      if (how !== "cancelled") setDlMsg({ ok: true, text: how === "saved" ? t("dx_downloaded_saved") : t("dx_downloaded_shared") });
    } catch (e: any) {
      setDlMsg({ ok: false, text: errText(e, t("dx_download_failed")) });
    } finally {
      setDlBusy(false);
      loadExport();                         // descarcarile ramase / starea (poate fi acum expirat)
    }
  }

  // ---------------------------------------------------------------- stergere
  async function finishDeletion() {
    if (finishedRef.current) return;
    finishedRef.current = true;
    setMode(null);
    resetAll();
    await logout();                       // sterge tokenul local + starea utilizatorului
    router.replace("/(auth)/login");
  }

  function pollStatus(deletionId: string, attempt: number) {
    pollRef.current = setTimeout(async () => {
      try {
        const st = await privacyApi.deletionStatus(deletionId);
        if (st?.status === "completed") { setProgress("completed"); return; }
      } catch {}
      if (attempt >= 20) { setProgress("background"); return; }   // ~60s; stergerea continua pe server
      pollStatus(deletionId, attempt + 1);
    }, 3000);
  }

  async function doDelete() {
    setBusy(true); setErr(null);
    try {
      const res = await privacyApi.deleteAccount({ ...credentials(), confirm: true, confirmation_text: typed.trim() });
      setStage("progress");
      setPassword(""); setCode("");
      if (res?.status === "completed") setProgress("completed");
      else { setProgress("working"); pollStatus(res.deletion_id, 0); }
    } catch (e: any) {
      setErr(errText(e, t("privacy_error_generic")));
    } finally {
      setBusy(false);
    }
  }

  const canExport = ack && credsReady && !busy && !!status;
  const typedOk = typed.trim().toUpperCase() === confirmWord.toUpperCase();
  const canDelete = ack && typedOk && credsReady && !busy;

  // ---------------------------------------------------------------- UI
  const reauthBlock = (
    <>
      {hasPassword ? (
        <>
          <Text style={styles.label}>{t("privacy_reauth_password")}</Text>
          <View style={styles.pwRow}>
            <TextInput
              testID="privacy-password"
              value={password}
              onChangeText={setPassword}
              secureTextEntry={!showPw}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="••••••••"
              placeholderTextColor={colors.onSurface3}
              style={styles.input}
            />
            <Pressable onPress={() => setShowPw(v => !v)} hitSlop={10} style={styles.eyeBtn}>
              <MaterialCommunityIcons name={showPw ? "eye-off-outline" : "eye-outline"} size={20} color={colors.onSurface3} />
            </Pressable>
          </View>
        </>
      ) : (
        <>
          <Text style={styles.body}>{t("privacy_google_note").replace("{email}", status?.email_masked || "")}</Text>
          {status?.email_service_available === false ? null : (
            <>
              <SecBtn testID="privacy-send-code" label={codeSent ? t("privacy_code_resend") : t("privacy_send_code")} onPress={sendCode} />
              {codeSent ? <Text style={styles.okText}>{t("privacy_code_sent")}</Text> : null}
              <Text style={styles.label}>{t("privacy_code_label")}</Text>
              <TextInput
                testID="privacy-code"
                value={code}
                onChangeText={setCode}
                keyboardType="number-pad"
                maxLength={8}
                placeholder="123456"
                placeholderTextColor={colors.onSurface3}
                style={[styles.input, { flex: 0 }]}
              />
            </>
          )}
        </>
      )}
    </>
  );

  return (
    <View>
      <Text style={styles.section}>{t("privacy_title").toUpperCase()}</Text>

      <Text style={styles.subSection}>{t("privacy_your_data").toUpperCase()}</Text>
      <View style={styles.exportCard} testID="settings-export-data">
        <View style={styles.exportHead}>
          <MaterialCommunityIcons name="download-outline" size={20} color={colors.brand} />
          <Text style={styles.rowText}>{t("dx_title")}</Text>
        </View>
        <Text style={styles.rowSub}>{t("dx_desc")}</Text>

        {exportInfo === null && !exportLoadErr ? <ActivityIndicator color={colors.brand} style={{ marginTop: 8 }} /> : null}
        {exportLoadErr && exportInfo === null ? (
          <>
            <Text style={styles.err}>{t("server_unreachable_title")}</Text>
            <SecBtn testID="privacy-export-reload" label={t("dx_retry_btn")} onPress={loadExport} />
          </>
        ) : null}

        {exportInfo !== null ? (
          <View style={{ gap: 8, marginTop: 8 }}>
            <View style={styles.statusPill} testID="export-status">
              {exp?.status === "processing" ? <ActivityIndicator size="small" color={colors.brand} /> : (
                <MaterialCommunityIcons
                  name={exp?.status === "ready" ? "check-circle-outline" : exp?.status === "failed" ? "alert-circle-outline" : exp?.status === "expired" ? "clock-alert-outline" : "information-outline"}
                  size={18}
                  color={exp?.status === "ready" ? colors.brand : exp?.status === "failed" ? colors.error : colors.onSurface3} />
              )}
              <Text style={styles.statusText}>
                {!exp ? t("dx_st_none") : exp.status === "processing" ? t("dx_st_processing") : exp.status === "ready" ? t("dx_st_ready")
                  : exp.status === "expired" ? t("dx_st_expired") : t("dx_st_failed")}
              </Text>
            </View>

            {exp ? (
              <View style={styles.kvBox}>
                <View style={styles.kv}><Text style={styles.kvK}>{t("dx_requested_at")}</Text><Text style={styles.kvV}>{fmtDate(exp.requested_at)}</Text></View>
                {exp.status === "ready" || exp.status === "expired" ? (
                  <View style={styles.kv}><Text style={styles.kvK}>{t("dx_generated_at")}</Text><Text style={styles.kvV}>{fmtDate(exp.generated_at)}</Text></View>
                ) : null}
                {exp.status === "ready" || exp.status === "expired" ? (
                  <View style={styles.kv}><Text style={styles.kvK}>{t("dx_expires_at")}</Text><Text style={styles.kvV}>{fmtDate(exp.expires_at)}</Text></View>
                ) : null}
                {exp.status === "ready" ? (
                  <>
                    <View style={styles.kv}><Text style={styles.kvK}>{t("dx_size")}</Text><Text style={styles.kvV}>{fmtBytes(exp.size_bytes)}</Text></View>
                    <View style={styles.kv}><Text style={styles.kvK}>{t("dx_downloads_left")}</Text><Text style={styles.kvV}>{exp.downloads_left ?? "-"}</Text></View>
                  </>
                ) : null}
              </View>
            ) : null}

            {exp?.status === "failed" ? (
              <Text style={styles.err} testID="export-error">
                {(exp.error_code === "TOO_LARGE" ? t("dx_err_too_large") : t("dx_err_generic"))
                  .replace("{email}", exportInfo.info.contact_email ? (exp.error_code === "TOO_LARGE" ? exportInfo.info.contact_email : ` (${exportInfo.info.contact_email})`) : "")}
              </Text>
            ) : null}

            {dlMsg ? <Text style={dlMsg.ok ? styles.okText : styles.err} testID="export-msg">{dlMsg.text}</Text> : null}

            {exp?.status === "ready" ? (
              dlBusy ? <ActivityIndicator color={colors.brand} /> : (
                <PrimaryButton testID="privacy-export-download" label={t("dx_download_btn")} onPress={doDownload} />
              )
            ) : null}
            {exp?.status !== "processing" && exp?.status !== "ready" ? (
              <PrimaryButton testID="privacy-export-open"
                label={exp?.status === "failed" ? t("dx_retry_btn") : t("dx_request_btn")} onPress={() => open("export")} />
            ) : null}

            <Text style={styles.legal}>{t("dx_legal")}</Text>
          </View>
        ) : null}
      </View>

      <Pressable onPress={() => open("delete")} style={[styles.row, { marginTop: 10, borderColor: "rgba(255,51,102,0.35)" }]} testID="settings-delete-account">
        <MaterialCommunityIcons name="account-remove-outline" size={20} color={colors.error} />
        <View style={{ flex: 1 }}>
          <Text style={[styles.rowText, { color: colors.error }]}>{t("privacy_delete_row")}</Text>
          <Text style={styles.rowSub}>{t("privacy_delete_desc")}</Text>
        </View>
        <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} />
      </Pressable>

      <Modal visible={mode !== null} transparent animationType="fade" onRequestClose={close}>
        <View style={styles.overlay}>
          <View style={styles.card}>
            <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ gap: spacing.sm }}>
              {loadingStatus ? <ActivityIndicator color={colors.brand} /> : null}

              {/* ------------------------------- EXPORT ------------------------------- */}
              {mode === "export" && !loadingStatus ? (
                <>
                  <View style={[styles.iconWrap, { backgroundColor: colors.brandTint }]}>
                    <MaterialCommunityIcons name="download-outline" size={28} color={colors.brand} />
                  </View>
                  <Text style={styles.title}>{t("dx_confirm_title")}</Text>
                  <Text style={styles.body}>{t("dx_confirm_body")}</Text>
                  <Text style={styles.body}>{t("privacy_export_excluded")}</Text>
                  {status ? reauthBlock : null}
                  <Pressable onPress={() => setAck(v => !v)} style={styles.ackRow} testID="privacy-export-ack">
                    <MaterialCommunityIcons name={ack ? "checkbox-marked" : "checkbox-blank-outline"} size={22} color={ack ? colors.brand : colors.onSurface3} />
                    <Text style={styles.ackText}>{t("dx_confirm_ack")}</Text>
                  </Pressable>
                  {err ? <Text style={styles.err} testID="privacy-error">{err}</Text> : null}
                  <View style={styles.row2}>
                    <SecBtn testID="privacy-export-cancel" label={t("privacy_cancel")} onPress={close} flex />
                    {busy ? <ActivityIndicator color={colors.brand} style={{ flex: 1 }} /> : (
                      <PrimaryButton testID="privacy-export-go" label={t("dx_request_btn")} onPress={doExport} disabled={!canExport} style={{ flex: 1 }} />
                    )}
                  </View>
                </>
              ) : null}

              {/* ------------------------------- DELETE ------------------------------- */}
              {mode === "delete" && !loadingStatus && stage === "warning" ? (
                <>
                  <View style={styles.iconWrap}>
                    <MaterialCommunityIcons name="alert-outline" size={28} color={colors.error} />
                  </View>
                  <Text style={styles.title}>{t("privacy_delete_warn_title")}</Text>
                  <Text style={styles.body}>{t("privacy_delete_irreversible")}</Text>
                  <Text style={styles.listHead}>{t("privacy_lose_title")}</Text>
                  {["privacy_lose_1", "privacy_lose_2", "privacy_lose_3", "privacy_lose_4", "privacy_lose_5", "privacy_lose_6"].map(k => (
                    <View key={k} style={styles.bulletRow}>
                      <Text style={styles.bullet}>•</Text>
                      <Text style={styles.bulletText}>{t(k)}</Text>
                    </View>
                  ))}
                  <Text style={styles.listHead}>{t("privacy_keep_title")}</Text>
                  <Text style={styles.bulletText}>
                    {t("privacy_keep_body")
                      .replace("{fin}", String(Math.round((status?.retention.financial_records_days ?? 1825) / 365)))
                      .replace("{mod}", String(Math.round((status?.retention.moderation_records_days ?? 365) / 30)))}
                  </Text>
                  <Text style={styles.body}>{t("privacy_export_first")}</Text>
                  {status && !status.can_delete ? <Text style={styles.err}>{t("privacy_owner_blocked")}</Text> : null}
                  {err ? <Text style={styles.err} testID="privacy-error">{err}</Text> : null}
                  <View style={styles.row2}>
                    <SecBtn testID="privacy-delete-cancel" label={t("privacy_cancel")} onPress={close} flex />
                    <PrimaryButton testID="privacy-delete-continue" label={t("privacy_continue")} onPress={() => setStage("confirm")}
                      disabled={!status || !status.can_delete} style={{ flex: 1, backgroundColor: colors.error }} />
                  </View>
                </>
              ) : null}

              {mode === "delete" && stage === "confirm" ? (
                <>
                  <Text style={styles.title}>{t("privacy_confirm_title")}</Text>
                  {reauthBlock}
                  <Pressable onPress={() => setAck(v => !v)} style={styles.ackRow} testID="privacy-ack">
                    <MaterialCommunityIcons name={ack ? "checkbox-marked" : "checkbox-blank-outline"} size={22} color={ack ? colors.error : colors.onSurface3} />
                    <Text style={styles.ackText}>{t("privacy_ack")}</Text>
                  </Pressable>
                  <Text style={styles.label}>{t("privacy_type_to_confirm").replace("{word}", confirmWord)}</Text>
                  <TextInput
                    testID="privacy-confirm-text"
                    value={typed}
                    onChangeText={setTyped}
                    autoCapitalize="characters"
                    autoCorrect={false}
                    placeholder={confirmWord}
                    placeholderTextColor={colors.onSurface3}
                    style={[styles.input, { flex: 0 }]}
                  />
                  {err ? <Text style={styles.err} testID="privacy-error">{err}</Text> : null}
                  <View style={styles.row2}>
                    <SecBtn testID="privacy-delete-back" label={t("privacy_back")} onPress={() => { setErr(null); setStage("warning"); }} flex />
                    {busy ? <ActivityIndicator color={colors.error} style={{ flex: 1 }} /> : (
                      <PrimaryButton testID="privacy-delete-go" label={t("privacy_delete_btn")} onPress={doDelete} disabled={!canDelete}
                        style={{ flex: 1, backgroundColor: colors.error }} />
                    )}
                  </View>
                </>
              ) : null}

              {mode === "delete" && stage === "progress" ? (
                <>
                  <View style={[styles.iconWrap, progress === "completed" ? { backgroundColor: colors.brandTint } : null]}>
                    {progress === "working" ? <ActivityIndicator color={colors.error} /> : (
                      <MaterialCommunityIcons name={progress === "completed" ? "check-circle-outline" : "clock-outline"} size={28}
                        color={progress === "completed" ? colors.brand : colors.warning} />
                    )}
                  </View>
                  <Text style={styles.title} testID="privacy-delete-status">
                    {progress === "working" ? t("privacy_deleting") : progress === "completed" ? t("privacy_deleted") : t("privacy_processing_bg")}
                  </Text>
                  <Text style={styles.body}>
                    {progress === "working" ? t("privacy_deleting_body") : progress === "completed" ? t("privacy_deleted_body") : t("privacy_processing_bg_body")}
                  </Text>
                  {progress !== "working" ? (
                    <PrimaryButton testID="privacy-delete-finish" label={t("privacy_to_login")} onPress={finishDeletion} />
                  ) : null}
                </>
              ) : null}

              {err && !status && !loadingStatus ? (
                <>
                  <Text style={styles.err}>{err}</Text>
                  <SecBtn label={t("privacy_close")} onPress={close} />
                </>
              ) : null}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 2, marginTop: 22, marginBottom: 10 },
  subSection: { color: colors.onSurface2, fontSize: 11, fontWeight: "800", letterSpacing: 1.5, marginBottom: 8 },
  exportCard: { gap: 4, padding: 14, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  exportHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  statusPill: { flexDirection: "row", alignItems: "center", gap: 8 },
  statusText: { flex: 1, color: colors.onSurface2, fontSize: 12.5, lineHeight: 18 },
  kvBox: { gap: 4, padding: 10, backgroundColor: colors.surface3, borderRadius: radius.sm },
  kv: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between", gap: 8 },
  kvK: { color: colors.onSurface3, fontSize: 12 },
  kvV: { color: colors.onSurface, fontSize: 12, fontWeight: "600", flexShrink: 1, textAlign: "right" },
  legal: { color: colors.onSurface3, fontSize: 11, lineHeight: 16, marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  rowText: { color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  rowSub: { color: colors.onSurface3, fontSize: 11, marginTop: 2 },
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.lg },
  card: { width: "100%", maxWidth: 420, maxHeight: "92%", backgroundColor: colors.surface2, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  iconWrap: { alignSelf: "center", width: 56, height: 56, borderRadius: 28, backgroundColor: "rgba(255,51,102,0.12)", alignItems: "center", justifyContent: "center", marginBottom: 4 },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800", textAlign: "center" },
  body: { color: colors.onSurface3, fontSize: 13, textAlign: "center", lineHeight: 19 },
  listHead: { color: colors.onSurface2, fontSize: 12, fontWeight: "800", marginTop: spacing.xs },
  bulletRow: { flexDirection: "row", gap: 8 },
  bullet: { color: colors.error, fontSize: 13, lineHeight: 19 },
  bulletText: { flex: 1, color: colors.onSurface2, fontSize: 12.5, lineHeight: 18 },
  label: { color: colors.onSurface2, fontSize: 12, fontWeight: "700", marginTop: spacing.xs },
  input: { flex: 1, backgroundColor: colors.surface3, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 12, paddingVertical: 10, color: colors.onSurface, fontSize: 14 },
  pwRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  eyeBtn: { padding: 8 },
  row2: { flexDirection: "row", gap: spacing.sm, marginTop: spacing.sm },
  err: { color: colors.error, fontSize: 12, textAlign: "center" },
  okText: { color: colors.brand, fontSize: 12, textAlign: "center" },
  ackRow: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: spacing.xs },
  ackText: { flex: 1, color: colors.onSurface2, fontSize: 12.5, lineHeight: 18 },
});