import React from "react";
import { Pressable, StyleSheet, Text, View, ViewStyle } from "react-native";
import { Image } from "expo-image";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing, type } from "../theme";

export function AstransPill({ balance, onPress }: { balance: number; onPress?: () => void }) {
  return (
    <Pressable testID="astrans-balance-pill" onPress={onPress} style={styles.pill}>
      <MaterialCommunityIcons name="hexagon-slice-6" size={16} color={colors.brand} />
      <Text style={styles.pillText}>{balance.toLocaleString()}</Text>
    </Pressable>
  );
}

export function PrimaryButton({ label, onPress, disabled, style, testID, icon }:
  { label: string; onPress?: () => void; disabled?: boolean; style?: ViewStyle; testID?: string; icon?: string }) {
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.primaryBtn,
        style,
        pressed && { opacity: 0.85 },
        disabled && { opacity: 0.4 },
      ]}
    >
      {icon ? <MaterialCommunityIcons name={icon as any} size={20} color={colors.onBrand} /> : null}
      <Text style={styles.primaryBtnText}>{label}</Text>
    </Pressable>
  );
}

export function SecondaryButton({ label, onPress, testID, icon }: { label: string; onPress?: () => void; testID?: string; icon?: string }) {
  return (
    <Pressable testID={testID} onPress={onPress} style={({ pressed }) => [styles.secondaryBtn, pressed && { opacity: 0.7 }]}>
      {icon ? <MaterialCommunityIcons name={icon as any} size={18} color={colors.onSurface} /> : null}
      <Text style={styles.secondaryBtnText}>{label}</Text>
    </Pressable>
  );
}

export function Chip({ label, active, onPress, testID }: { label: string; active?: boolean; onPress?: () => void; testID?: string }) {
  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      style={[styles.chip, active ? styles.chipActive : null]}
    >
      <Text style={[styles.chipText, active ? styles.chipTextActive : null]}>{label}</Text>
    </Pressable>
  );
}

export function GameCard({ game, onPress, wide, testID }:
  { game: any; onPress?: () => void; wide?: boolean; testID?: string }) {
  const w = wide ? { width: "100%" as const, height: 200 } : { width: 180, height: 220 };
  return (
    <Pressable testID={testID} onPress={onPress} style={[styles.card, w]}>
      {game.thumbnail_url ? (
        <Image
          source={{ uri: game.thumbnail_url }}
          style={StyleSheet.absoluteFillObject}
          contentFit="cover"
          transition={200}
        />
      ) : (
        <View style={[StyleSheet.absoluteFillObject, { backgroundColor: colors.surface3 }]} />
      )}
      <LinearGradient
        colors={["transparent", "rgba(0,0,0,0.5)", "rgba(0,0,0,0.9)"]}
        style={StyleSheet.absoluteFillObject}
      />
      {game.age_category === "adult_18" ? (
        <View style={styles.ageTag}>
          <Text style={styles.ageTagText}>18+</Text>
        </View>
      ) : null}
      <View style={styles.cardBottom}>
        <Text numberOfLines={1} style={styles.cardTitle}>{game.title}</Text>
        <View style={styles.cardMeta}>
          <MaterialCommunityIcons name="account-multiple" size={12} color={colors.onSurface3} />
          <Text style={styles.cardMetaText}>{(game.player_count ?? 0).toLocaleString()}</Text>
          <MaterialCommunityIcons name="play-circle" size={12} color={colors.onSurface3} style={{ marginLeft: 8 }} />
          <Text style={styles.cardMetaText}>{(game.total_plays ?? 0).toLocaleString()}</Text>
        </View>
      </View>
    </Pressable>
  );
}

export function SectionTitle({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) {
  return (
    <View style={styles.sectionRow}>
      <Text style={styles.sectionTitle}>{title.toUpperCase()}</Text>
      {action ? (
        <Pressable onPress={onAction}>
          <Text style={styles.sectionAction}>{action}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.surface2,
    borderRadius: radius.pill,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: colors.border,
  },
  pillText: { color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  primaryBtn: {
    backgroundColor: colors.brand,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.xl,
    paddingVertical: 14,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
    minHeight: 48,
  },
  primaryBtnText: { color: colors.onBrand, fontWeight: "800", fontSize: 15, letterSpacing: 0.3 },
  secondaryBtn: {
    borderRadius: radius.pill,
    paddingHorizontal: spacing.xl,
    paddingVertical: 12,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.borderStrong,
    flexDirection: "row",
    gap: 8,
    minHeight: 44,
  },
  secondaryBtnText: { color: colors.onSurface, fontWeight: "700", fontSize: 14 },
  chip: {
    flexShrink: 0,
    height: 36,
    paddingHorizontal: 14,
    borderRadius: radius.pill,
    backgroundColor: colors.surface2,
    borderWidth: 1,
    borderColor: colors.border,
    justifyContent: "center",
  },
  chipActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  chipText: { color: colors.onSurface2, fontWeight: "600", fontSize: 12, letterSpacing: 0.4 },
  chipTextActive: { color: colors.brand },
  card: {
    borderRadius: radius.lg,
    overflow: "hidden",
    backgroundColor: colors.surface2,
    marginRight: 12,
  },
  cardBottom: { position: "absolute", left: 12, right: 12, bottom: 12 },
  cardTitle: { color: colors.onSurface, fontSize: 15, fontWeight: "800", letterSpacing: 0.2 },
  cardMeta: { flexDirection: "row", alignItems: "center", gap: 4, marginTop: 4 },
  cardMetaText: { color: colors.onSurface3, fontSize: 11, fontWeight: "600" },
  ageTag: {
    position: "absolute", top: 10, right: 10,
    backgroundColor: colors.error, paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: radius.sm,
  },
  ageTagText: { color: "#fff", fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  sectionRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: spacing.lg,
    marginTop: spacing.xl,
    marginBottom: spacing.md,
  },
  sectionTitle: { color: colors.onSurface, fontSize: 15, fontWeight: "800", letterSpacing: 1 },
  sectionAction: { color: colors.brand, fontSize: 12, fontWeight: "700" },
});