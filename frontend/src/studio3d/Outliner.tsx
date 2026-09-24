// frontend/src/studio3d/Outliner.tsx
// Ierarhia modelului: Copac > Trunchi, Ramura_1, Frunze_1 ...
import React from "react";
import { View, Text, Pressable, StyleSheet } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius } from "@/src/theme";
import { Part, ROOT_ID, PART_ICON, flatten } from "./modelTypes";

type Props = {
  parts: Part[];
  selIds: string[];
  collapsed: Set<string>;
  onSelect: (id: string) => void;
  onToggleCollapse: (id: string) => void;
  onToggleVisible: (id: string) => void;
};

export default function Outliner({ parts, selIds, collapsed, onSelect, onToggleCollapse, onToggleVisible }: Props) {
  const rows = flatten(parts, collapsed);
  const selected = new Set(selIds);

  return (
    <View>
      {rows.map(({ part, depth }) => {
        const isGroup = part.type === "group";
        const isSel = selected.has(part.id);
        const isRoot = part.id === ROOT_ID;
        return (
          <Pressable
            key={part.id}
            testID={`outliner-${part.id}`}
            onPress={() => onSelect(part.id)}
            style={[styles.row, isSel && styles.rowSel, { paddingLeft: 6 + depth * 16 }]}
          >
            {isGroup ? (
              <Pressable onPress={() => onToggleCollapse(part.id)} hitSlop={8} style={styles.chev}>
                <MaterialCommunityIcons name={collapsed.has(part.id) ? "chevron-right" : "chevron-down"} size={18} color={colors.onSurface2} />
              </Pressable>
            ) : (
              <View style={styles.chev} />
            )}
            <MaterialCommunityIcons
              name={(isRoot ? "cube-scan" : PART_ICON[part.type]) as any}
              size={16}
              color={isSel ? colors.brand : part.visible ? colors.onSurface2 : colors.onSurface3}
            />
            <Text
              numberOfLines={1}
              style={[styles.name, isRoot && { fontWeight: "900" }, isSel && { color: colors.brand }, !part.visible && { opacity: 0.45 }]}
            >
              {part.name}
            </Text>
            <Pressable onPress={() => onToggleVisible(part.id)} hitSlop={8} style={styles.eye}>
              <MaterialCommunityIcons name={part.visible ? "eye-outline" : "eye-off-outline"} size={18} color={part.visible ? colors.onSurface2 : colors.onSurface3} />
            </Pressable>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 8, paddingRight: 10, borderRadius: radius.sm },
  rowSel: { backgroundColor: colors.brandTint },
  chev: { width: 18, alignItems: "center" },
  name: { flex: 1, color: colors.onSurface, fontSize: 13, fontWeight: "600" },
  eye: { paddingHorizontal: 4 },
});