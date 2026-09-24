// frontend/src/studio3d/NumField.tsx
// Camp numeric pentru telefon: butoane -/+ mari si posibilitatea de a scrie valoarea direct.
import React, { useEffect, useState } from "react";
import { View, Text, TextInput, Pressable, StyleSheet, Platform } from "react-native";
import { colors, radius } from "@/src/theme";

type Props = {
  label: string;
  value: number;
  step: number;
  min: number;
  max: number;
  decimals: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  testID?: string;
};

export default function NumField({ label, value, step, min, max, decimals, onChange, disabled, testID }: Props) {
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
    <View style={[styles.field, disabled && { opacity: 0.4 }]}>
      <Text style={styles.label}>{label}</Text>
      <View style={styles.row}>
        <Pressable onPress={() => bump(-1)} disabled={disabled} style={styles.btn} hitSlop={4}>
          <Text style={styles.btnText}>-</Text>
        </Pressable>
        <TextInput
          testID={testID}
          value={text}
          editable={!disabled}
          onChangeText={setText}
          onFocus={() => setFocused(true)}
          onBlur={commit}
          onSubmitEditing={commit}
          keyboardType={Platform.OS === "ios" ? "numbers-and-punctuation" : "numeric"}
          selectTextOnFocus
          style={styles.input}
        />
        <Pressable onPress={() => bump(1)} disabled={disabled} style={styles.btn} hitSlop={4}>
          <Text style={styles.btnText}>+</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  field: { flex: 1, padding: 6, backgroundColor: colors.surface3, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.border },
  label: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", marginBottom: 4 },
  row: { flexDirection: "row", alignItems: "center", gap: 2 },
  btn: { width: 30, height: 34, borderRadius: 15, backgroundColor: colors.brand, alignItems: "center", justifyContent: "center" },
  btnText: { color: colors.onBrand, fontWeight: "900", fontSize: 16, lineHeight: 18 },
  input: { flex: 1, minWidth: 0, color: colors.onSurface, fontWeight: "700", fontSize: 12, textAlign: "center", paddingVertical: 4, paddingHorizontal: 0 },
});