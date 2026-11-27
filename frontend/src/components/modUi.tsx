import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import { useI18n } from "@/src/i18n";
import { modErr, ms, type MsKey } from "@/src/i18n/modStrings";

// Piese comune ale ecranelor de moderare. Continutul e centrat cu latime maxima, ca sa arate bine si pe telefon si pe desktop/web.
export function Screen({ title, children, testID }: { title: string; children: React.ReactNode; testID?: string }) {
  const router = useRouter();
  return (
    <SafeAreaView style={s.root} edges={["top"]} testID={testID}>
      <View style={s.header}>
        <Pressable onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} hitSlop={10} testID="mod-back">
          <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={s.title} numberOfLines={1}>{title}</Text>
        <View style={{ width: 26 }} />
      </View>
      <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
        <View style={s.column}>{children}</View>
      </ScrollView>
    </SafeAreaView>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: any }) {
  return <View style={[s.card, style]}>{children}</View>;
}
export function H({ children }: { children: React.ReactNode }) {
  return <Text style={s.h}>{String(children).toUpperCase()}</Text>;
}
export function Body({ children, dim, bold }: { children: React.ReactNode; dim?: boolean; bold?: boolean }) {
  return <Text style={[s.body, dim && { color: colors.onSurface3 }, bold && { fontWeight: "700", color: colors.onSurface }]} selectable>{children}</Text>;
}
export function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <View style={{ marginBottom: 8 }}>
      <Text style={s.k}>{k}</Text>
      {typeof v === "string" || typeof v === "number" ? <Text style={s.body} selectable>{v}</Text> : v}
    </View>
  );
}
export function Badge({ label, tone }: { label: string; tone?: "ok" | "warn" | "bad" | "info" }) {
  const c = tone === "ok" ? colors.success : tone === "bad" ? colors.error : tone === "warn" ? colors.warning : colors.info;
  return <View style={[s.badge, { borderColor: c }]}><Text style={[s.badgeText, { color: c }]}>{label}</Text></View>;
}
export function Btn({ label, onPress, disabled, kind, testID }: { label: string; onPress: () => void; disabled?: boolean; kind?: "primary" | "ghost" | "danger"; testID?: string }) {
  const bg = kind === "ghost" ? colors.surface2 : kind === "danger" ? colors.error : colors.brand;
  const fg = kind === "ghost" ? colors.onSurface : kind === "danger" ? "#fff" : colors.onBrand;
  return (
    <Pressable testID={testID} onPress={onPress} disabled={disabled}
      style={({ pressed }) => [s.btn, { backgroundColor: bg, borderColor: kind === "ghost" ? colors.border : bg }, pressed && { opacity: 0.85 }, disabled && { opacity: 0.4 }]}>
      <Text style={[s.btnText, { color: fg }]}>{label}</Text>
    </Pressable>
  );
}
export function Input({ label, value, onChangeText, multiline, placeholder, testID, keyboardType, maxLength, secure }:
  { label?: string; value: string; onChangeText: (v: string) => void; multiline?: boolean; placeholder?: string; testID?: string; keyboardType?: any; maxLength?: number; secure?: boolean }) {
  return (
    <View style={{ marginBottom: 12 }}>
      {label ? <Text style={s.k}>{label}</Text> : null}
      <TextInput testID={testID} value={value} onChangeText={onChangeText} multiline={multiline} placeholder={placeholder} maxLength={maxLength}
        placeholderTextColor={colors.onSurface3} keyboardType={keyboardType} autoCapitalize="none" secureTextEntry={secure} autoCorrect={secure ? false : undefined}
        style={[s.input, multiline && { minHeight: 96, textAlignVertical: "top" }]} />
    </View>
  );
}
export function Chips<T extends string>({ options, value, onChange, label }: { options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label?: string }) {
  return (
    <View style={{ marginBottom: 12 }}>
      {label ? <Text style={s.k}>{label}</Text> : null}
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
        {options.map(o => (
          <Pressable key={o.value} onPress={() => onChange(o.value)} testID={`chip-${o.value}`} style={[s.chip, value === o.value && s.chipOn]}>
            <Text style={[s.chipText, value === o.value && { color: colors.onBrand }]}>{o.label}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}
export function Check({ checked, onToggle, label, testID }: { checked: boolean; onToggle: () => void; label: string; testID?: string }) {
  return (
    <Pressable onPress={onToggle} testID={testID} style={{ flexDirection: "row", gap: 10, alignItems: "flex-start", marginBottom: 12 }}>
      <MaterialCommunityIcons name={checked ? "checkbox-marked" : "checkbox-blank-outline"} size={22} color={checked ? colors.brand : colors.onSurface3} />
      <Text style={[s.body, { flex: 1 }]}>{label}</Text>
    </Pressable>
  );
}
export function Row({ children, onPress, testID }: { children: React.ReactNode; onPress?: () => void; testID?: string }) {
  return (
    <Pressable onPress={onPress} testID={testID} style={({ pressed }) => [s.row, pressed && { opacity: 0.8 }]}>
      <View style={{ flex: 1 }}>{children}</View>
      {onPress ? <MaterialCommunityIcons name="chevron-right" size={20} color={colors.onSurface3} /> : null}
    </Pressable>
  );
}

/** Incarca date, cu stari de loading/eroare reale (fara date inventate). */
export function useLoad<T>(fn: () => Promise<T>, deps: any[] = []) {
  const { lang } = useI18n();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true); setError(null);
    try { setData(await fn()); } catch (e: any) { setError(modErr(lang, e)); } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}
export function LoadState({ loading, error, onRetry }: { loading: boolean; error: string | null; onRetry: () => void }) {
  const { lang } = useI18n();
  if (loading) return <ActivityIndicator color={colors.brand} style={{ marginVertical: 24 }} />;
  if (error) return <Card><Text style={{ color: colors.error, marginBottom: 8 }} testID="mod-error">{error}</Text><Btn kind="ghost" label={ms(lang, "retry")} onPress={onRetry} /></Card>;
  return null;
}
export const fmt = (d: any) => { try { return d ? new Date(d).toLocaleString() : "—"; } catch { return String(d); } };
export const tone = (status: string): "ok" | "warn" | "bad" | "info" =>
  status === "resolved" || status === "decision_reversed" ? "ok" : status === "rejected" || status === "failed" ? "bad" : status === "pending" || status === "received" ? "warn" : "info";
export const label = (lang: string, prefix: string, v: string) => ms(lang, (prefix + v) as MsKey);

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.lg },
  title: { color: colors.onSurface, fontSize: 18, fontWeight: "800", flex: 1, textAlign: "center", marginHorizontal: 8 },
  content: { padding: spacing.lg, paddingBottom: 60 },
  column: { width: "100%", maxWidth: 760, alignSelf: "center" },
  card: { backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: 14, marginBottom: 12 },
  h: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 2, marginTop: 14, marginBottom: 8 },
  body: { color: colors.onSurface2, fontSize: 14, lineHeight: 20 },
  k: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", marginBottom: 4, letterSpacing: 0.5 },
  badge: { borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 3, alignSelf: "flex-start" },
  badgeText: { fontSize: 11, fontWeight: "800" },
  btn: { borderRadius: radius.md, borderWidth: 1, paddingVertical: 13, paddingHorizontal: 16, alignItems: "center", marginBottom: 10 },
  btnText: { fontWeight: "800", fontSize: 14 },
  input: { backgroundColor: colors.surface, color: colors.onSurface, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, padding: 12, fontSize: 14 },
  chip: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipOn: { backgroundColor: colors.brand, borderColor: colors.brand },
  chipText: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  row: { flexDirection: "row", alignItems: "center", gap: 12, padding: 14, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, marginBottom: 10 },
});
