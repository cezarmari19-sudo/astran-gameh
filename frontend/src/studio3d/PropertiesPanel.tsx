// frontend/src/studio3d/PropertiesPanel.tsx
// Proprietatile obiectelor selectate: nume, vizibilitate, pozitie/rotatie/scala, culoare, material, transparenta.
import React, { useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, ScrollView, StyleSheet } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius } from "@/src/theme";
import NumField from "./NumField";
import { Part, MATERIALS, MaterialKind, PALETTE } from "./modelTypes";

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

const POS_KEYS = ["x", "y", "z"] as const;
const ROT_KEYS = ["rx", "ry", "rz"] as const;
const SCALE_KEYS = ["sx", "sy", "sz"] as const;

type Props = {
  selected: Part[]; // ultimul element e cel "principal" (cel afisat)
  onPatch: (patch: Partial<Part>) => void; // se aplica tuturor celor selectate
};

export default function PropertiesPanel({ selected, onPatch }: Props) {
  const primary = selected[selected.length - 1];
  const multiple = selected.length > 1;
  const hasShape = selected.some(p => p.type !== "group");

  const [tool, setTool] = useState<Tool>("move");
  const [steps, setSteps] = useState<Record<Tool, number>>({ move: 0.5, rotate: 15, scale: 0.1 });
  const [linked, setLinked] = useState(true);
  const [nameText, setNameText] = useState(primary?.name ?? "");
  const [hexText, setHexText] = useState(primary?.color ?? "#cccccc");

  useEffect(() => { setNameText(primary?.name ?? ""); }, [primary?.id, primary?.name]);
  useEffect(() => { setHexText(primary?.color ?? "#cccccc"); }, [primary?.id, primary?.color]);

  if (!primary) return null;
  const step = steps[tool];

  const commitName = () => {
    const n = nameText.trim();
    if (n && n !== primary.name) onPatch({ name: n.slice(0, 48) });
    else setNameText(primary.name);
  };

  const commitHex = () => {
    const h = hexText.trim().replace(/^#/, "");
    if (/^[0-9a-fA-F]{6}$/.test(h)) onPatch({ color: "#" + h.toLowerCase() });
    else setHexText(primary.color);
  };

  const setScale = (k: (typeof SCALE_KEYS)[number], v: number) => {
    if (!linked || !primary[k]) {
      onPatch({ [k]: v } as Partial<Part>);
      return;
    }
    const ratio = v / primary[k];
    onPatch({ sx: primary.sx * ratio, sy: primary.sy * ratio, sz: primary.sz * ratio });
  };

  const pickMaterial = (m: MaterialKind) => {
    if (m === "glass" && primary.opacity >= 1) onPatch({ material: m, opacity: 0.4 });
    else onPatch({ material: m });
  };

  return (
    <View style={styles.wrap}>
      <View style={styles.nameRow}>
        {multiple ? (
          <Text style={styles.multiText}>{selected.length} objects selected</Text>
        ) : (
          <TextInput
            testID="prop-name"
            value={nameText}
            onChangeText={setNameText}
            onBlur={commitName}
            onSubmitEditing={commitName}
            maxLength={48}
            style={styles.nameInput}
            placeholder="Name"
            placeholderTextColor={colors.onSurface3}
          />
        )}
        <Pressable testID="prop-visible" onPress={() => onPatch({ visible: !primary.visible })} hitSlop={8} style={styles.eyeBtn}>
          <MaterialCommunityIcons name={primary.visible ? "eye-outline" : "eye-off-outline"} size={22} color={primary.visible ? colors.brand : colors.onSurface3} />
        </Pressable>
      </View>

      <View style={styles.tabsRow}>
        {TOOLS.map(tl => (
          <Pressable key={tl.key} onPress={() => setTool(tl.key)} style={[styles.tab, tool === tl.key && styles.tabActive]}>
            <MaterialCommunityIcons name={tl.icon as any} size={15} color={tool === tl.key ? colors.brand : colors.onSurface2} />
            <Text style={[styles.tabText, tool === tl.key && { color: colors.brand }]}>{tl.label}</Text>
          </Pressable>
        ))}
        <View style={{ flex: 1 }} />
        {STEP_OPTIONS[tool].map(s => (
          <Pressable key={s} onPress={() => setSteps(prev => ({ ...prev, [tool]: s }))} style={[styles.chip, step === s && styles.chipActive]}>
            <Text style={[styles.chipText, step === s && { color: colors.brand }]}>{s}</Text>
          </Pressable>
        ))}
      </View>

      <View style={styles.fieldsRow}>
        {tool === "move" && POS_KEYS.map(k => (
          <NumField key={k} testID={`prop-pos-${k}`} label={k.toUpperCase()} value={primary[k]} step={step} min={-1000} max={1000} decimals={2}
            onChange={v => onPatch({ [k]: v } as Partial<Part>)} />
        ))}
        {tool === "rotate" && ROT_KEYS.map(k => (
          <NumField key={k} testID={`prop-rot-${k}`} label={k.slice(1).toUpperCase() + "°"} value={primary[k]} step={step} min={-3600} max={3600} decimals={0}
            onChange={v => onPatch({ [k]: v } as Partial<Part>)} />
        ))}
        {tool === "scale" && (
          <>
            {SCALE_KEYS.map(k => (
              <NumField key={k} testID={`prop-scale-${k}`} label={k.slice(1).toUpperCase()} value={primary[k]} step={step} min={0.01} max={1000} decimals={2}
                onChange={v => setScale(k, v)} />
            ))}
            <Pressable onPress={() => setLinked(l => !l)} style={styles.linkBtn} hitSlop={6}>
              <MaterialCommunityIcons name={linked ? "link-variant" : "link-variant-off"} size={20} color={linked ? colors.brand : colors.onSurface3} />
            </Pressable>
          </>
        )}
      </View>

      {hasShape ? (
        <>
          <Text style={styles.lab}>COLOR</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 4, alignItems: "center" }}>
            {PALETTE.map(c => (
              <Pressable key={c} testID={`prop-color-${c}`} onPress={() => onPatch({ color: c })}
                style={[styles.swatch, { backgroundColor: c }, primary.color.toLowerCase() === c.toLowerCase() && styles.swatchSel]} />
            ))}
            <TextInput
              testID="prop-hex"
              value={hexText}
              onChangeText={setHexText}
              onBlur={commitHex}
              onSubmitEditing={commitHex}
              autoCapitalize="none"
              autoCorrect={false}
              maxLength={7}
              style={styles.hexInput}
            />
          </ScrollView>

          <Text style={styles.lab}>MATERIAL</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingVertical: 4 }}>
            {MATERIALS.map(m => (
              <Pressable key={m} testID={`prop-mat-${m}`} onPress={() => pickMaterial(m)} style={[styles.chipWide, primary.material === m && styles.chipActive]}>
                <Text style={[styles.chipText, { fontSize: 11 }, primary.material === m && { color: colors.brand }]}>{m}</Text>
              </Pressable>
            ))}
          </ScrollView>

          <View style={{ flexDirection: "row", marginTop: 6 }}>
            <NumField testID="prop-opacity" label="OPACITY" value={primary.opacity} step={0.1} min={0} max={1} decimals={2}
              onChange={v => onPatch({ opacity: v })} />
            <View style={{ flex: 2 }} />
          </View>
        </>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { padding: 12, paddingBottom: 20 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 },
  nameInput: { flex: 1, backgroundColor: colors.surface3, color: colors.onSurface, fontWeight: "800", fontSize: 14, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 10, paddingVertical: 8 },
  multiText: { flex: 1, color: colors.onSurface, fontWeight: "800", fontSize: 14 },
  eyeBtn: { paddingHorizontal: 4 },
  tabsRow: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 8 },
  tab: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  tabActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  tabText: { color: colors.onSurface2, fontSize: 11, fontWeight: "700" },
  chip: { paddingHorizontal: 7, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipWide: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipActive: { borderColor: colors.brand },
  chipText: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", textTransform: "capitalize" },
  fieldsRow: { flexDirection: "row", gap: 6, alignItems: "center" },
  linkBtn: { paddingHorizontal: 2 },
  lab: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 1.5, marginTop: 12 },
  swatch: { width: 30, height: 30, borderRadius: 15, borderWidth: 2, borderColor: colors.border },
  swatchSel: { borderColor: colors.onSurface },
  hexInput: { width: 84, backgroundColor: colors.surface3, color: colors.onSurface, fontWeight: "700", fontSize: 12, textAlign: "center", borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingVertical: 6 },
});