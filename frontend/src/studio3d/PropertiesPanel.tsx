// frontend/src/studio3d/PropertiesPanel.tsx
// Proprietatile obiectelor selectate: nume, vizibilitate, Position / Rotation / Scale / Size,
// culoare, material, transparenta.
import React, { useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, ScrollView, StyleSheet } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius } from "@/src/theme";
import NumField from "./NumField";
import { Part, MATERIALS, MaterialKind, PALETTE, BASE_SIZE } from "./modelTypes";

type Section = "pos" | "rot" | "scale" | "size";

const STEP_OPTIONS: Record<Section, number[]> = {
  pos: [0.1, 0.5, 1],
  rot: [5, 15, 45],
  scale: [0.1, 0.25, 0.5],
  size: [0.1, 0.5, 1],
};

const POS_KEYS = ["x", "y", "z"] as const;
const ROT_KEYS = ["rx", "ry", "rz"] as const;
const SCALE_KEYS = ["sx", "sy", "sz"] as const;
const SIZE_LABELS = ["WIDTH", "HEIGHT", "DEPTH"];
const QUICK_SCALE: [string, number][] = [["½×", 0.5], ["-10%", 0.9], ["+10%", 1.1], ["2×", 2]];

type Props = {
  selected: Part[]; // ultimul element e cel "principal" (cel afisat)
  onPatch: (patch: Partial<Part>) => void; // se aplica tuturor celor selectate
};

function Header({ title, options, value, onPick, children }: {
  title: string; options: number[]; value: number; onPick: (v: number) => void; children?: React.ReactNode;
}) {
  return (
    <View style={styles.headRow}>
      <Text style={styles.lab}>{title}</Text>
      <View style={{ flex: 1 }} />
      {children}
      {options.map(o => (
        <Pressable key={o} onPress={() => onPick(o)} hitSlop={4} style={[styles.chip, value === o && styles.chipActive]}>
          <Text style={[styles.chipText, value === o && { color: colors.brand }]}>{o}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export default function PropertiesPanel({ selected, onPatch }: Props) {
  const primary = selected[selected.length - 1];
  const multiple = selected.length > 1;
  const hasShape = selected.some(p => p.type !== "group");

  const [steps, setSteps] = useState<Record<Section, number>>({ pos: 0.5, rot: 15, scale: 0.1, size: 0.5 });
  const [linked, setLinked] = useState(false); // scalare uniforma (implicit oprita: X, Y, Z se modifica separat)
  const [nameText, setNameText] = useState(primary?.name ?? "");
  const [hexText, setHexText] = useState(primary?.color ?? "#cccccc");

  useEffect(() => { setNameText(primary?.name ?? ""); }, [primary?.id, primary?.name]);
  useEffect(() => { setHexText(primary?.color ?? "#cccccc"); }, [primary?.id, primary?.color]);

  if (!primary) return null;

  const setStep = (s: Section, v: number) => setSteps(prev => ({ ...prev, [s]: v }));

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

  // Schimba scala pe o axa; cu "link" activ, celelalte axe se schimba in acelasi raport
  const setScale = (k: (typeof SCALE_KEYS)[number], v: number) => {
    if (!linked || !primary[k]) {
      onPatch({ [k]: v } as Partial<Part>);
      return;
    }
    const ratio = v / primary[k];
    onPatch({ sx: primary.sx * ratio, sy: primary.sy * ratio, sz: primary.sz * ratio });
  };

  const scaleAll = (r: number) => onPatch({ sx: primary.sx * r, sy: primary.sy * r, sz: primary.sz * r });

  const pickMaterial = (m: MaterialKind) => {
    if (m === "glass" && primary.opacity >= 1) onPatch({ material: m, opacity: 0.4 });
    else onPatch({ material: m });
  };

  const showSize = primary.type !== "group";
  const base = BASE_SIZE[primary.type];

  const LinkButton = (
    <Pressable onPress={() => setLinked(l => !l)} hitSlop={6} style={[styles.linkBtn, linked && styles.linkBtnActive]}>
      <MaterialCommunityIcons name={linked ? "link-variant" : "link-variant-off"} size={16} color={linked ? colors.brand : colors.onSurface3} />
      <Text style={[styles.linkText, linked && { color: colors.brand }]}>Uniform</Text>
    </Pressable>
  );

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
          <MaterialCommunityIcons name={primary.visible ? "eye-outline" : "eye-off-outline"} size={24} color={primary.visible ? colors.brand : colors.onSurface3} />
        </Pressable>
      </View>

      {/* POSITION */}
      <Header title="POSITION" options={STEP_OPTIONS.pos} value={steps.pos} onPick={v => setStep("pos", v)}>
        <Pressable onPress={() => onPatch({ x: 0, y: 0, z: 0 })} hitSlop={6} style={styles.resetBtn}>
          <MaterialCommunityIcons name="restore" size={18} color={colors.onSurface3} />
        </Pressable>
      </Header>
      <View style={styles.fieldsRow}>
        {POS_KEYS.map(k => (
          <NumField key={k} testID={`prop-pos-${k}`} label={k.toUpperCase()} value={primary[k]} step={steps.pos} min={-1000} max={1000} decimals={2}
            onChange={v => onPatch({ [k]: v } as Partial<Part>)} />
        ))}
      </View>

      {/* ROTATION */}
      <Header title="ROTATION" options={STEP_OPTIONS.rot} value={steps.rot} onPick={v => setStep("rot", v)}>
        <Pressable onPress={() => onPatch({ rx: 0, ry: 0, rz: 0 })} hitSlop={6} style={styles.resetBtn}>
          <MaterialCommunityIcons name="restore" size={18} color={colors.onSurface3} />
        </Pressable>
      </Header>
      <View style={styles.fieldsRow}>
        {ROT_KEYS.map(k => (
          <NumField key={k} testID={`prop-rot-${k}`} label={k.slice(1).toUpperCase() + "°"} value={primary[k]} step={steps.rot} min={-3600} max={3600} decimals={0}
            onChange={v => onPatch({ [k]: v } as Partial<Part>)} />
        ))}
      </View>

      {/* SCALE */}
      <Header title="SCALE" options={STEP_OPTIONS.scale} value={steps.scale} onPick={v => setStep("scale", v)}>
        {LinkButton}
        <Pressable onPress={() => onPatch({ sx: 1, sy: 1, sz: 1 })} hitSlop={6} style={styles.resetBtn}>
          <MaterialCommunityIcons name="restore" size={18} color={colors.onSurface3} />
        </Pressable>
      </Header>
      <View style={styles.fieldsRow}>
        {SCALE_KEYS.map(k => (
          <NumField key={k} testID={`prop-scale-${k}`} label={k.slice(1).toUpperCase()} value={primary[k]} step={steps.scale} min={0.01} max={1000} decimals={2}
            onChange={v => setScale(k, v)} />
        ))}
      </View>
      <View style={styles.quickRow}>
        {QUICK_SCALE.map(([label, r]) => (
          <Pressable key={label} onPress={() => scaleAll(r)} style={styles.quickBtn}>
            <Text style={styles.quickText}>{label}</Text>
          </Pressable>
        ))}
      </View>

      {/* SIZE (dimensiunea reala = scala x dimensiunea formei) */}
      {showSize ? (
        <>
          <Header title="SIZE" options={STEP_OPTIONS.size} value={steps.size} onPick={v => setStep("size", v)}>
            {LinkButton}
          </Header>
          <View style={styles.fieldsRow}>
            {SCALE_KEYS.map((k, i) => (
              <NumField
                key={k}
                testID={`prop-size-${i}`}
                label={SIZE_LABELS[i]}
                value={primary[k] * base[i]}
                disabled={base[i] === 0}
                step={steps.size}
                min={0.01 * base[i]}
                max={1000 * base[i]}
                decimals={2}
                onChange={v => setScale(k, v / base[i])}
              />
            ))}
          </View>
        </>
      ) : null}

      {hasShape ? (
        <>
          <Text style={[styles.lab, { marginTop: 14 }]}>COLOR</Text>
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

          <Text style={[styles.lab, { marginTop: 10 }]}>MATERIAL</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingVertical: 4 }}>
            {MATERIALS.map(m => (
              <Pressable key={m} testID={`prop-mat-${m}`} onPress={() => pickMaterial(m)} style={[styles.chipWide, primary.material === m && styles.chipActive]}>
                <Text style={[styles.chipText, { fontSize: 11 }, primary.material === m && { color: colors.brand }]}>{m}</Text>
              </Pressable>
            ))}
          </ScrollView>

          <View style={{ flexDirection: "row", marginTop: 8 }}>
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
  wrap: { padding: 12, paddingBottom: 28 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 },
  nameInput: { flex: 1, backgroundColor: colors.surface3, color: colors.onSurface, fontWeight: "800", fontSize: 14, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 10, paddingVertical: 9 },
  multiText: { flex: 1, color: colors.onSurface, fontWeight: "800", fontSize: 14 },
  eyeBtn: { paddingHorizontal: 4 },
  headRow: { flexDirection: "row", alignItems: "center", gap: 6, marginTop: 12, marginBottom: 6 },
  lab: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 1.5 },
  chip: { paddingHorizontal: 9, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipWide: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  chipActive: { borderColor: colors.brand },
  chipText: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", textTransform: "capitalize" },
  fieldsRow: { flexDirection: "row", gap: 6 },
  resetBtn: { paddingHorizontal: 2 },
  linkBtn: { flexDirection: "row", alignItems: "center", gap: 3, paddingHorizontal: 8, paddingVertical: 5, borderRadius: radius.pill, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  linkBtnActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  linkText: { color: colors.onSurface3, fontSize: 10, fontWeight: "800" },
  quickRow: { flexDirection: "row", gap: 6, marginTop: 6 },
  quickBtn: { flex: 1, alignItems: "center", paddingVertical: 9, borderRadius: radius.sm, backgroundColor: colors.surface3, borderWidth: 1, borderColor: colors.border },
  quickText: { color: colors.onSurface, fontSize: 12, fontWeight: "800" },
  swatch: { width: 32, height: 32, borderRadius: 16, borderWidth: 2, borderColor: colors.border },
  swatchSel: { borderColor: colors.onSurface },
  hexInput: { width: 86, backgroundColor: colors.surface3, color: colors.onSurface, fontWeight: "700", fontSize: 12, textAlign: "center", borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border, paddingVertical: 8 },
});