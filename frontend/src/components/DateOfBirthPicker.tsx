import React, { useState } from "react";
import { Modal, Pressable, FlatList, Text, View, StyleSheet } from "react-native";
import { colors, radius, spacing, type } from "../theme";
import { as } from "../i18n/ageStrings";

export type Dob = { d: number | null; m: number | null; y: number | null };
export const EMPTY_DOB: Dob = { d: null, m: null, y: null };

const MONTHS_RO = ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"];
const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Doar pentru UX (mesaje rapide). Validarea care conteaza e facuta de server (age_policy.parse_dob). */
export function dobToIso(v: Dob): string | null {
  if (!v.d || !v.m || !v.y) return null;
  return `${String(v.y).padStart(4, "0")}-${String(v.m).padStart(2, "0")}-${String(v.d).padStart(2, "0")}`;
}
export function dobClientError(v: Dob, lang: string): string | null {
  if (!v.d || !v.m || !v.y) return as(lang, "err_dob_required");
  const dt = new Date(Date.UTC(v.y, v.m - 1, v.d));
  if (dt.getUTCFullYear() !== v.y || dt.getUTCMonth() !== v.m - 1 || dt.getUTCDate() !== v.d) return as(lang, "err_dob_invalid");
  if (dt.getTime() > Date.now()) return as(lang, "err_dob_future");
  return null;
}
/** Previzualizare a benzii DOAR pentru explicatii in formular; categoria reala o stabileste serverul. */
export function previewBand(v: Dob): "under_16" | "16_17" | "18_plus" | null {
  if (dobClientError(v, "en") || !v.d || !v.m || !v.y) return null;
  const now = new Date();
  let age = now.getUTCFullYear() - v.y - ((now.getUTCMonth() + 1 < v.m || (now.getUTCMonth() + 1 === v.m && now.getUTCDate() < v.d)) ? 1 : 0);
  return age < 16 ? "under_16" : age < 18 ? "16_17" : "18_plus";
}

function Field({ label, value, options, onPick, testID }: { label: string; value: string | null; options: { k: number; label: string }[]; onPick: (k: number) => void; testID: string }) {
  const [open, setOpen] = useState(false);
  return (
    <View style={{ flex: 1 }}>
      <Pressable testID={testID} onPress={() => setOpen(true)} style={styles.field} accessibilityRole="button" accessibilityLabel={label}>
        <Text style={styles.fieldLabel}>{label}</Text>
        <Text style={[styles.fieldValue, !value && { color: colors.onSurface3 }]}>{value ?? "—"}</Text>
      </Pressable>
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)}>
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>{label}</Text>
            <FlatList
              data={options} keyExtractor={(o) => String(o.k)} initialNumToRender={20}
              renderItem={({ item }) => (
                <Pressable testID={`${testID}-opt-${item.k}`} onPress={() => { onPick(item.k); setOpen(false); }} style={styles.opt}>
                  <Text style={styles.optText}>{item.label}</Text>
                </Pressable>
              )}
            />
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

export default function DateOfBirthPicker({ value, onChange, lang, testPrefix = "dob" }: { value: Dob; onChange: (v: Dob) => void; lang: string; testPrefix?: string }) {
  const months = lang === "ro" ? MONTHS_RO : MONTHS_EN;
  const thisYear = new Date().getUTCFullYear();
  const days = Array.from({ length: 31 }, (_, i) => ({ k: i + 1, label: String(i + 1) }));
  const ms = months.map((m, i) => ({ k: i + 1, label: `${i + 1} · ${m}` }));
  const ys = Array.from({ length: 121 }, (_, i) => ({ k: thisYear - i, label: String(thisYear - i) }));
  return (
    <View style={styles.row}>
      <Field testID={`${testPrefix}-day`} label={as(lang, "dob_day")} value={value.d ? String(value.d) : null} options={days} onPick={(k) => onChange({ ...value, d: k })} />
      <Field testID={`${testPrefix}-month`} label={as(lang, "dob_month")} value={value.m ? months[value.m - 1] : null} options={ms} onPick={(k) => onChange({ ...value, m: k })} />
      <Field testID={`${testPrefix}-year`} label={as(lang, "dob_year")} value={value.y ? String(value.y) : null} options={ys} onPick={(k) => onChange({ ...value, y: k })} />
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: spacing.sm },
  field: { backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingVertical: 10, paddingHorizontal: 12 },
  fieldLabel: { ...type.micro, color: colors.onSurface3, textTransform: "uppercase" },
  fieldValue: { ...type.h3, color: colors.onSurface, marginTop: 2 },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", justifyContent: "center", padding: spacing.xl },
  sheet: { backgroundColor: colors.surface2, borderRadius: radius.lg, maxHeight: "70%", padding: spacing.lg, borderWidth: 1, borderColor: colors.borderStrong },
  sheetTitle: { ...type.h3, color: colors.onSurface, marginBottom: spacing.sm },
  opt: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.divider },
  optText: { ...type.body, color: colors.onSurface },
});
