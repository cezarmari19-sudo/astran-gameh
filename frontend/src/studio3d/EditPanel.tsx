// frontend/src/studio3d/EditPanel.tsx
// Panoul de unelte din Edit Mode, gandit pentru ecran tactil: butoane mari, slidere cu +/-,
// derulare orizontala, si se poate strange ca sa lase viewport-ul liber.
import React, { useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, PanResponder, LayoutChangeEvent } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius } from "@/src/theme";
import { SelMode } from "./editSelection";
import { TOOLS, ToolDef, ToolId, ToolParam } from "./editOps";

export type ActiveOpView = { def: ToolDef; params: Record<string, number> };

const SEL_MODES: { key: SelMode; icon: string; label: string }[] = [
  { key: "vertex", icon: "dots-hexagon", label: "Vertex" },
  { key: "edge", icon: "vector-line", label: "Edge" },
  { key: "face", icon: "square-medium", label: "Face" },
];

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }
function snapTo(v: number, p: ToolParam) {
  const s = Math.round((v - p.min) / p.step) * p.step + p.min;
  return clamp(p.int ? Math.round(s) : Math.round(s * 10000) / 10000, p.min, p.max);
}
function fmt(v: number, p: ToolParam) {
  if (p.int) return String(Math.round(v));
  return Math.abs(p.step) >= 0.1 ? v.toFixed(1) : p.step >= 0.01 ? v.toFixed(2) : v.toFixed(3);
}

// Slider facut cu PanResponder (nu depinde de alta biblioteca); atingerea oriunde pe bara seteaza valoarea
function TouchSlider({ param, value, onChange }: { param: ToolParam; value: number; onChange: (v: number) => void }) {
  const width = useRef(1);
  const left = useRef(0);
  const latest = useRef({ param, onChange });
  latest.current = { param, onChange };
  const [, force] = useState(0);
  const setFromX = (x: number) => {
    const { param: p, onChange: cb } = latest.current;
    const t = clamp(x / Math.max(1, width.current), 0, 1);
    cb(snapTo(p.min + t * (p.max - p.min), p));
  };
  const pan = useRef(PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: () => true,
    onPanResponderTerminationRequest: () => false,
    onPanResponderGrant: e => setFromX(e.nativeEvent.locationX),
    onPanResponderMove: (e, g) => setFromX(g.moveX - left.current),
  })).current;
  const t = (value - param.min) / (param.max - param.min || 1);
  return (
    <View
      style={styles.sliderHit}
      {...pan.panHandlers}
      onLayout={(e: LayoutChangeEvent) => {
        width.current = e.nativeEvent.layout.width;
        (e.target as any)?.measure?.((_x: number, _y: number, _w: number, _h: number, px: number) => { left.current = px; force(n => n + 1); });
      }}
    >
      <View style={styles.sliderTrack} pointerEvents="none">
        <View style={[styles.sliderFill, { width: `${clamp(t, 0, 1) * 100}%` }]} />
      </View>
      <View style={[styles.sliderKnob, { left: `${clamp(t, 0, 1) * 100}%` }]} pointerEvents="none" />
    </View>
  );
}

function ParamRow({ param, value, onChange }: { param: ToolParam; value: number; onChange: (v: number) => void }) {
  if (param.toggle) {
    const on = value >= 0.5;
    return (
      <Pressable onPress={() => onChange(on ? 0 : 1)} style={styles.paramRow}>
        <Text style={styles.paramLabel}>{param.label}</Text>
        <View style={[styles.toggle, on && styles.toggleOn]}>
          <Text style={[styles.toggleText, on && { color: colors.brand }]}>{on ? "ON" : "OFF"}</Text>
        </View>
      </Pressable>
    );
  }
  return (
    <View style={styles.paramRow}>
      <Text style={styles.paramLabel}>{param.label}</Text>
      <Pressable hitSlop={6} onPress={() => onChange(snapTo(value - param.step, param))} style={styles.stepBtn}>
        <MaterialCommunityIcons name="minus" size={18} color={colors.onSurface} />
      </Pressable>
      <TouchSlider param={param} value={value} onChange={onChange} />
      <Pressable hitSlop={6} onPress={() => onChange(snapTo(value + param.step, param))} style={styles.stepBtn}>
        <MaterialCommunityIcons name="plus" size={18} color={colors.onSurface} />
      </Pressable>
      <Text style={styles.paramValue}>{fmt(value, param)}</Text>
    </View>
  );
}

export type EditPanelProps = {
  selMode: SelMode;
  onSelMode: (m: SelMode) => void;
  multi: boolean; onMulti: () => void;
  xray: boolean; onXray: () => void;
  onAll: () => void; onNone: () => void; onInvert: () => void; onFocus: () => void;
  stats: string;
  op: ActiveOpView | null;
  onTool: (id: ToolId) => void;
  onParam: (key: string, v: number) => void;
  onApply: () => void; onCancel: () => void;
  message: string | null;
  open: boolean; onToggleOpen: () => void;
};

function Chip({ icon, label, active, onPress, testID }: { icon: string; label: string; active?: boolean; onPress: () => void; testID?: string }) {
  return (
    <Pressable testID={testID} onPress={onPress} style={[styles.chip, active && styles.chipActive]}>
      <MaterialCommunityIcons name={icon as any} size={18} color={active ? colors.brand : colors.onSurface} />
      <Text style={[styles.chipText, active && { color: colors.brand }]}>{label}</Text>
    </Pressable>
  );
}

export default function EditPanel(p: EditPanelProps) {
  return (
    <View style={styles.wrap}>
      {p.message ? <View style={styles.toast}><Text style={styles.toastText}>{p.message}</Text></View> : null}

      <View style={styles.topRow}>
        <View style={styles.seg}>
          {SEL_MODES.map(m => (
            <Pressable key={m.key} testID={`ms-sel-${m.key}`} onPress={() => p.onSelMode(m.key)} disabled={!!p.op} style={[styles.segBtn, p.selMode === m.key && styles.segBtnActive, p.op && { opacity: 0.5 }]}>
              <MaterialCommunityIcons name={m.icon as any} size={18} color={p.selMode === m.key ? colors.brand : colors.onSurface} />
              <Text style={[styles.segText, p.selMode === m.key && { color: colors.brand }]}>{m.label}</Text>
            </Pressable>
          ))}
        </View>
        <Text style={styles.stats} numberOfLines={1}>{p.stats}</Text>
        <Pressable testID="ms-edit-panel-toggle" onPress={p.onToggleOpen} hitSlop={8} style={styles.collapse}>
          <MaterialCommunityIcons name={p.open ? "chevron-down" : "chevron-up"} size={24} color={colors.onSurface} />
        </Pressable>
      </View>

      {p.open ? (
        p.op ? (
          <View style={styles.opBox}>
            <View style={styles.opHead}>
              <Text style={styles.opTitle}>{p.op.def.label}</Text>
              <View style={{ flex: 1 }} />
              <Pressable testID="ms-op-cancel" onPress={p.onCancel} style={[styles.opBtn, { borderColor: colors.error }]}>
                <MaterialCommunityIcons name="close" size={18} color={colors.error} />
                <Text style={[styles.opBtnText, { color: colors.error }]}>Cancel</Text>
              </Pressable>
              <Pressable testID="ms-op-apply" onPress={p.onApply} style={[styles.opBtn, { borderColor: colors.brand, backgroundColor: colors.brandTint }]}>
                <MaterialCommunityIcons name="check" size={18} color={colors.brand} />
                <Text style={[styles.opBtnText, { color: colors.brand }]}>Apply</Text>
              </Pressable>
            </View>
            {p.op.def.params.map(pr => (
              <ParamRow key={pr.key} param={pr} value={p.op!.params[pr.key] ?? pr.def} onChange={v => p.onParam(pr.key, v)} />
            ))}
          </View>
        ) : (
          <>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
              <Chip icon="checkbox-multiple-marked-outline" label="Multi" active={p.multi} onPress={p.onMulti} testID="ms-edit-multi" />
              <Chip icon="select-all" label="All" onPress={p.onAll} />
              <Chip icon="select-off" label="None" onPress={p.onNone} />
              <Chip icon="select-inverse" label="Invert" onPress={p.onInvert} />
              <Chip icon="eye-outline" label="X-ray" active={p.xray} onPress={p.onXray} testID="ms-edit-xray" />
              <Chip icon="crosshairs-gps" label="Focus" onPress={p.onFocus} />
            </ScrollView>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
              {TOOLS.map(t => (
                <Chip key={t.id} testID={`ms-tool-${t.id}`} icon={t.icon} label={t.label} onPress={() => p.onTool(t.id)} />
              ))}
            </ScrollView>
          </>
        )
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingBottom: 6 },
  toast: { backgroundColor: "rgba(255,149,0,0.16)", paddingVertical: 6, paddingHorizontal: 12 },
  toastText: { color: "#FFB44D", fontSize: 12, fontWeight: "700", textAlign: "center" },
  topRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 10, paddingTop: 8, paddingBottom: 4 },
  seg: { flexDirection: "row", backgroundColor: colors.surface3, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border, overflow: "hidden" },
  segBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 11, paddingVertical: 9 },
  segBtnActive: { backgroundColor: colors.brandTint },
  segText: { color: colors.onSurface, fontSize: 12, fontWeight: "800" },
  stats: { flex: 1, color: colors.onSurface3, fontSize: 10, fontWeight: "700", textAlign: "right" },
  collapse: { width: 34, height: 34, alignItems: "center", justifyContent: "center" },
  row: { gap: 8, paddingHorizontal: 10, paddingVertical: 4 },
  chip: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 13, paddingVertical: 10, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  chipText: { color: colors.onSurface, fontSize: 12, fontWeight: "700" },
  opBox: { paddingHorizontal: 12, paddingTop: 4, gap: 2 },
  opHead: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 },
  opTitle: { color: colors.brand, fontSize: 14, fontWeight: "900" },
  opBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.border },
  opBtnText: { fontSize: 12, fontWeight: "800" },
  paramRow: { flexDirection: "row", alignItems: "center", gap: 8, height: 44 },
  paramLabel: { width: 70, color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  stepBtn: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  paramValue: { width: 46, color: colors.onSurface, fontSize: 12, fontWeight: "800", textAlign: "right" },
  sliderHit: { flex: 1, height: 40, justifyContent: "center" },
  sliderTrack: { height: 6, borderRadius: 3, backgroundColor: colors.surface3, overflow: "hidden" },
  sliderFill: { height: 6, backgroundColor: colors.brand },
  sliderKnob: { position: "absolute", width: 22, height: 22, borderRadius: 11, marginLeft: -11, backgroundColor: colors.brand, borderWidth: 2, borderColor: "#000" },
  toggle: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  toggleOn: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  toggleText: { color: colors.onSurface2, fontSize: 12, fontWeight: "800" },
});