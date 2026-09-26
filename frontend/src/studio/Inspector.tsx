// frontend/src/studio/Inspector.tsx
// Panoul din editor pentru obiectul selectat: Move / Rotate / Scale, culoare, Visible/Solid,
// duplicare, stergere. Cand e activ multi-select (mai multe obiecte), doar actiunile care au
// sens pe grup (Solid, Delete) sunt disponibile - vezi MultiSelectBar mai jos, folosit separat
// din studio/create.tsx.
import React, { useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, ScrollView, StyleSheet, Platform } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius } from "@/src/theme";
import { SceneObj, PALETTE, iconFor } from "./sceneShared";

type Tool = "move" | "rotate" | "scale";

const TOOLS: { key: Tool; label: string; icon: string }[] = [
  { key: "move", label: "Move", icon: "cursor-move" },
  { key: "rotate", label: "Rotate", icon: "rotate-3d-variant" },
  { key: "scale", label: "Scale", icon: "resize" },
];

const STEP_OPTIONS: Record<Tool, number[]> = {
  move: [0.1, 0.5, 1],
  rotate: [5, 15, 45],
  scale: [0.1, 0.25, 0.5],
};

const AXES = ["x", "y", "z"] as const;
const ROT_KEYS = ["rx", "ry", "rz"] as const;
const STRETCH_KEYS = ["sx", "sy", "sz"] as const;

type NumFieldProps = {
  label: string;
  value: number;
  step: number;
  min: number;
  max: number;
  decimals: number;
  onChange: (v: number) => void;
  testID?: string;
};

// Camp numeric: butoane -/+ si posibilitatea de a scrie valoarea direct
function NumField({ label, value, step, min, max, decimals, onChange, testID }: NumFieldProps) {
  const [text, setText] = useState(value.toFixed(decimals));
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setText(value.toFixed(decimals));
  }, [value, decimals, focused]);

  const clamp = (v: number) => Math.round(Math.max(min, Math.min(max, v)) * 1000) / 1000;

  const bump = (dir: 1 | -1) => {
    const next = clamp(value + dir * step);
    setText(next.toFixed(decimals));
    onChange(next);
  };

  const commit = () => {
    const n = parseFloat(text.replace(",", "."));
    if (!isNaN(n)) onChange(clamp(n));
    else setText(value.toFixed(decimals));
    setFocused(false);
  };

  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <View style={styles.fieldRow}>
        <Pressable onPress={() => bump(-1)} style={styles.stepBtn} hitSlop={6}>
          <Text style={styles.stepBtnText}>-</Text>
        </Pressable>
        <TextInput
          testID={testID}
          value={text}
          onChangeText={setText}
          onFocus={() => setFocused(true)}
          onBlur={commit}
          onSubmitEditing={commit}
          keyboardType={Platform.OS === "ios" ? "numbers-and-punctuation" : "numeric"}
          selectTextOnFocus
          style={styles.fieldInput}
        />
        <Pressable onPress={() => bump(1)} style={styles.stepBtn} hitSlop={6}>
          <Text style={styles.stepBtnText}>+</Text>
        </Pressable>
      </View>
    </View>
  );
}

type Props = {
  obj: SceneObj;
  onChange: (patch: Partial<SceneObj>) => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onFocus: () => void;
  onClose: () => void;
};

export default function Inspector({ obj, onChange, onDelete, onDuplicate, onFocus, onClose }: Props) {
  const [tool, setTool] = useState<Tool>("move");
  const [steps, setSteps] = useState<Record<Tool, number>>({ move: 0.5, rotate: 15, scale: 0.1 });
  const step = steps[tool];
  const isSpawn = obj.type === "spawn";
  const visible = obj.visible !== false;
  const solid = obj.solid !== false;

  const resetTool = () => {
    if (tool === "move") onChange({ x: 0, y: 0, z: 0 });
    else if (tool === "rotate") onChange({ rx: 0, ry: 0, rz: 0 });
    else onChange({ scale: 1, sx: 1, sy: 1, sz: 1 });
  };

  return (
    <View style={styles.wrap}>
      <View style={styles.head}>
        <MaterialCommunityIcons name={iconFor(obj.type) as any} size={18} color={colors.brand} />
        <Text style={styles.title}>{isSpawn ? "Spawn Point" : obj.type}</Text>
        <View style={{ flex: 1 }} />
        <Pressable testID="insp-focus" onPress={onFocus} hitSlop={8} style={styles.headBtn}>
          <MaterialCommunityIcons name="crosshairs-gps" size={20} color={colors.onSurface2} />
        </Pressable>
        <Pressable testID="insp-reset" onPress={resetTool} hitSlop={8} style={styles.headBtn}>
          <MaterialCommunityIcons name="restore" size={20} color={colors.onSurface2} />
        </Pressable>
        <Pressable testID="insp-duplicate" onPress={onDuplicate} hitSlop={8} style={styles.headBtn}>
          <MaterialCommunityIcons name="content-copy" size={19} color={colors.onSurface2} />
        </Pressable>
        <Pressable testID="editor-delete-obj" onPress={onDelete} hitSlop={8} style={styles.headBtn}>
          <MaterialCommunityIcons name="trash-can-outline" size={20} color={colors.error} />
        </Pressable>
        <Pressable testID="insp-close" onPress={onClose} hitSlop={8} style={styles.headBtn}>
          <MaterialCommunityIcons name="close" size={20} color={colors.onSurface2} />
        </Pressable>
      </View>

      {/* Visible / Solid - independente una de alta, controleaza cum se comporta obiectul in Play */}
      <View style={styles.flagsRow}>
        <Pressable testID="insp-toggle-visible" onPress={() => onChange({ visible: !visible })} style={[styles.flagBtn, visible && styles.flagBtnActive]}>
          <MaterialCommunityIcons name={visible ? "eye-outline" : "eye-off-outline"} size={16} color={visible ? colors.brand : colors.onSurface3} />
          <Text style={[styles.flagText, visible && { color: colors.brand }]}>Visible</Text>
        </Pressable>
        <Pressable testID="insp-toggle-solid" onPress={() => onChange({ solid: !solid })} style={[styles.flagBtn, solid && styles.flagBtnActive]}>
          <MaterialCommunityIcons name={solid ? "wall" : "walk"} size={16} color={solid ? colors.brand : colors.onSurface3} />
          <Text style={[styles.flagText, solid && { color: colors.brand }]}>Solid</Text>
        </Pressable>
      </View>

      <View style={styles.tabsRow}>
        {TOOLS.map(tl => (
          <Pressable
            key={tl.key}
            testID={`insp-tool-${tl.key}`}
            onPress={() => setTool(tl.key)}
            style={[styles.tab, tool === tl.key && styles.tabActive]}
          >
            <MaterialCommunityIcons name={tl.icon as any} size={15} color={tool === tl.key ? colors.brand : colors.onSurface2} />
            <Text style={[styles.tabText, tool === tl.key && { color: colors.brand }]}>{tl.label}</Text>
          </Pressable>
        ))}
        <View style={{ flex: 1 }} />
        {STEP_OPTIONS[tool].map(s => (
          <Pressable
            key={s}
            onPress={() => setSteps(prev => ({ ...prev, [tool]: s }))}
            style={[styles.chip, step === s && styles.chipActive]}
          >
            <Text style={[styles.chipText, step === s && { color: colors.brand }]}>{s}</Text>
          </Pressable>
        ))}
      </View>

      <View style={styles.fieldsRow}>
        {tool === "move" &&
          AXES.map(a => (
            <NumField
              key={a}
              testID={`insp-pos-${a}`}
              label={a.toUpperCase()}
              value={obj[a]}
              step={step}
              min={-100}
              max={100}
              decimals={2}
              onChange={v => onChange({ [a]: v } as Partial<SceneObj>)}
            />
          ))}

        {tool === "rotate" &&
          ROT_KEYS.map(k => (
            <NumField
              key={k}
              testID={`insp-rot-${k}`}
              label={k.slice(1).toUpperCase() + "°"}
              value={obj[k] ?? 0}
              step={step}
              min={-360}
              max={360}
              decimals={0}
              onChange={v => onChange({ [k]: v } as Partial<SceneObj>)}
            />
          ))}

        {tool === "scale" && (
          <>
            <NumField
              testID="insp-scale-all"
              label="ALL"
              value={obj.scale}
              step={step}
              min={0.1}
              max={10}
              decimals={2}
              onChange={v => onChange({ scale: v })}
            />
            {STRETCH_KEYS.map(k => (
              <NumField
                key={k}
                testID={`insp-stretch-${k}`}
                label={k.slice(1).toUpperCase()}
                value={obj[k] ?? 1}
                step={step}
                min={0.1}
                max={10}
                decimals={2}
                onChange={v => onChange({ [k]: v } as Partial<SceneObj>)}
              />
            ))}
          </>
        )}
      </View>

      {!isSpawn ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 8 }}>
          {PALETTE.map(c => (
            <Pressable
              key={c}
              testID={`editor-color-${c}`}
              onPress={() => onChange({ color: c })}
              style={[styles.swatch, { backgroundColor: c }, obj.color === c && styles.swatchSel]}
            />
          ))}
        </ScrollView>
      ) : null}
    </View>
  );
}

// Bara pentru multi-selectie (Box Select): actiuni care se aplica pe toate obiectele selectate deodata.
export function MultiSelectBar({ count, onSolid, onUnsolid, onDelete, onClear }: {
  count: number; onSolid: () => void; onUnsolid: () => void; onDelete: () => void; onClear: () => void;
}) {
  return (
    <View style={styles.multiWrap}>
      <Text style={styles.multiTitle}>{count} selected</Text>
      <View style={{ flex: 1 }} />
      <Pressable testID="multi-solid-on" onPress={onSolid} style={styles.multiBtn}>
        <MaterialCommunityIcons name="wall" size={16} color={colors.brand} />
        <Text style={styles.multiBtnText}>Solid ON</Text>
      </Pressable>
      <Pressable testID="multi-solid-off" onPress={onUnsolid} style={styles.multiBtn}>
        <MaterialCommunityIcons name="walk" size={16} color={colors.onSurface2} />
        <Text style={styles.multiBtnText}>Solid OFF</Text>
      </Pressable>
      <Pressable testID="multi-delete" onPress={onDelete} style={[styles.multiBtn, { borderColor: colors.error }]}>
        <MaterialCommunityIcons name="trash-can-outline" size={16} color={colors.error} />
      </Pressable>
      <Pressable testID="multi-clear" onPress={onClear} style={styles.multiBtn}>
        <MaterialCommunityIcons name="close" size={16} color={colors.onSurface2} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 2 },
  head: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  title: { color: colors.onSurface, fontWeight: "800", fontSize: 14, textTransform: "capitalize" },
  headBtn: { paddingHorizontal: 4, paddingVertical: 2 },
  flagsRow: { flexDirection: "row", gap: 8, marginBottom: 8 },
  flagBtn: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, paddingVertical: 7, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  flagBtnActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  flagText: { color: colors.onSurface3, fontSize: 11, fontWeight: "700" },
  tabsRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 8 },
  tab: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  tabActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  tabText: { color: colors.onSurface2, fontSize: 11, fontWeight: "700" },
  chip: { paddingHorizontal: 7, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipActive: { borderColor: colors.brand },
  chipText: { color: colors.onSurface3, fontSize: 10, fontWeight: "800" },
  fieldsRow: { flexDirection: "row", gap: 6 },
  field: { flex: 1, padding: 6, backgroundColor: colors.surface3, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border },
  fieldLabel: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", marginBottom: 4 },
  fieldRow: { flexDirection: "row", alignItems: "center", gap: 2 },
  stepBtn: { width: 22, height: 26, borderRadius: 13, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  stepBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 14, lineHeight: 16 },
  fieldInput: { flex: 1, minWidth: 0, color: colors.onSurface, fontWeight: "700", fontSize: 12, textAlign: "center", paddingVertical: 2, paddingHorizontal: 0 },
  swatch: { width: 30, height: 30, borderRadius: 15, borderWidth: 2, borderColor: colors.border },
  swatchSel: { borderColor: colors.onSurface },
  multiWrap: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface2, borderTopWidth: 1, borderColor: colors.border, paddingHorizontal: 12, paddingVertical: 12 },
  multiTitle: { color: colors.onSurface, fontWeight: "800", fontSize: 13 },
  multiBtn: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 10, paddingVertical: 8, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  multiBtnText: { color: colors.onSurface, fontSize: 11, fontWeight: "700" },
});