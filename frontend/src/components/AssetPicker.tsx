import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  ScrollView,
  ActivityIndicator,
  Modal,
  FlatList,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";

type ShopModel = {
  item_id: string;
  name: string;
  owner_username: string;
  price: number;
  owned: boolean;
  preview: { type: string; color: string; scale: number };
};

type Props = {
  visible: boolean;
  assetIds: string[];
  onChange: (ids: string[]) => void;
  onClose: () => void;
};

const SHAPE_ICON: Record<string, string> = {
  cube: "cube-outline",
  sphere: "circle-outline",
  cylinder: "cylinder",
  cone: "triangle-outline",
  pyramid: "triangle-outline",
};

export default function AssetPicker({ visible, assetIds, onChange, onClose }: Props) {
  const router = useRouter();
  const [tab, setTab] = useState<"attached" | "browse">("attached");
  const [attached, setAttached] = useState<ShopModel[]>([]);
  const [loadingAttached, setLoadingAttached] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ShopModel[]>([]);
  const [loadingResults, setLoadingResults] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // incarca detalii pentru id-urile deja atasate (nume, pret) — separat de multimea de id-uri
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    (async () => {
      setLoadingAttached(true);
      const items: ShopModel[] = [];
      for (const id of assetIds) {
        try {
          const r = await api(`/shop/items/${id}`);
          if (r?.item) items.push(r.item);
        } catch {
          // itemul poate fi sters intre timp — il ignoram, ramane doar in assetIds
        }
      }
      if (!cancelled) { setAttached(items); setLoadingAttached(false); }
    })();
    return () => { cancelled = true; };
  }, [visible, assetIds]);

  useEffect(() => {
    if (!visible || tab !== "browse") return;
    const t = setTimeout(async () => {
      setLoadingResults(true);
      setErr(null);
      try {
        const params = new URLSearchParams();
        if (q.trim()) params.set("q", q.trim());
        const r = await api(`/shop/models?${params.toString()}`);
        setResults(Array.isArray(r?.items) ? r.items : []);
      } catch (e: any) {
        setErr(e?.message || "Search failed");
      } finally {
        setLoadingResults(false);
      }
    }, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [visible, tab, q]);

  function remove(id: string) {
    onChange(assetIds.filter(a => a !== id));
  }

  function add(item: ShopModel) {
    if (assetIds.includes(item.item_id)) return;
    onChange([...assetIds, item.item_id]);
    setAttached(prev => [...prev, item]);
    setTab("attached");
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.root} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable onPress={onClose} testID="assets-close">
            <MaterialCommunityIcons name="close" size={24} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.title}>Shop Models</Text>
          <View style={{ width: 24 }} />
        </View>

        <View style={styles.tabs}>
          <Pressable testID="assets-tab-attached" onPress={() => setTab("attached")} style={[styles.tab, tab === "attached" && styles.tabActive]}>
            <Text style={[styles.tabText, tab === "attached" && { color: colors.brand }]}>In this game ({assetIds.length})</Text>
          </Pressable>
          <Pressable testID="assets-tab-browse" onPress={() => setTab("browse")} style={[styles.tab, tab === "browse" && styles.tabActive]}>
            <Text style={[styles.tabText, tab === "browse" && { color: colors.brand }]}>Browse Shop</Text>
          </Pressable>
        </View>

        {tab === "attached" ? (
          loadingAttached ? (
            <View style={styles.center}><ActivityIndicator color={colors.brand} /></View>
          ) : attached.length === 0 ? (
            <View style={styles.center}>
              <MaterialCommunityIcons name="cube-off-outline" size={40} color={colors.onSurface3} />
              <Text style={styles.emptyText}>No models attached yet</Text>
              <Pressable testID="assets-go-browse" onPress={() => setTab("browse")} style={styles.primaryBtn}>
                <Text style={styles.primaryBtnText}>Browse the Shop</Text>
              </Pressable>
            </View>
          ) : (
            <FlatList
              data={attached}
              keyExtractor={i => i.item_id}
              contentContainerStyle={{ padding: spacing.md, gap: spacing.sm }}
              renderItem={({ item }) => (
                <View style={styles.row}>
                  <View style={[styles.swatch, { backgroundColor: item.preview?.color || "#666" }]}>
                    <MaterialCommunityIcons name={(SHAPE_ICON[item.preview?.type || ""] || "cube-outline") as any} size={18} color="rgba(0,0,0,0.35)" />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowName} numberOfLines={1}>{item.name}</Text>
                    <Text style={styles.rowId} numberOfLines={1} selectable>{item.item_id}</Text>
                  </View>
                  <Pressable testID={`assets-remove-${item.item_id}`} onPress={() => remove(item.item_id)} style={styles.removeBtn}>
                    <MaterialCommunityIcons name="close" size={16} color={colors.error} />
                  </Pressable>
                </View>
              )}
            />
          )
        ) : (
          <>
            <View style={styles.searchRow}>
              <MaterialCommunityIcons name="magnify" size={18} color={colors.onSurface3} />
              <TextInput
                testID="assets-search"
                value={q}
                onChangeText={setQ}
                placeholder="Search models by name"
                placeholderTextColor={colors.onSurface3}
                autoCapitalize="none"
                style={styles.searchInput}
              />
            </View>
            {loadingResults ? (
              <View style={styles.center}><ActivityIndicator color={colors.brand} /></View>
            ) : err ? (
              <View style={styles.center}><Text style={styles.errText}>{err}</Text></View>
            ) : (
              <FlatList
                data={results}
                keyExtractor={i => i.item_id}
                contentContainerStyle={{ padding: spacing.md, gap: spacing.sm }}
                renderItem={({ item }) => {
                  const already = assetIds.includes(item.item_id);
                  return (
                    <View style={styles.row}>
                      <View style={[styles.swatch, { backgroundColor: item.preview?.color || "#666" }]}>
                        <MaterialCommunityIcons name={(SHAPE_ICON[item.preview?.type || ""] || "cube-outline") as any} size={18} color="rgba(0,0,0,0.35)" />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.rowName} numberOfLines={1}>{item.name}</Text>
                        <Text style={styles.rowMeta} numberOfLines={1}>
                          by {item.owner_username} · {item.price === 0 ? "Free" : `${item.price} Astrans`}
                        </Text>
                      </View>
                      {already ? (
                        <View style={styles.addedPill}><MaterialCommunityIcons name="check" size={14} color={colors.brand} /></View>
                      ) : item.owned ? (
                        <Pressable testID={`assets-add-${item.item_id}`} onPress={() => add(item)} style={styles.addBtn}>
                          <Text style={styles.addBtnText}>Add</Text>
                        </Pressable>
                      ) : (
                        <Pressable
                          testID={`assets-buy-${item.item_id}`}
                          onPress={() => router.push("/shop" as any)}
                          style={styles.buyBtn}
                        >
                          <Text style={styles.buyBtnText}>{item.price === 0 ? "Get" : "Buy"}</Text>
                        </Pressable>
                      )}
                    </View>
                  );
                }}
                ListEmptyComponent={
                  <View style={styles.center}>
                    <Text style={styles.emptyText}>No models found</Text>
                  </View>
                }
              />
            )}
          </>
        )}

        <Text style={styles.hint}>
          Attached models can be used in scripts with Assets.load("id"). Buying an item happens in the Shop.
        </Text>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md },
  title: { color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  tabs: { flexDirection: "row", paddingHorizontal: spacing.md, gap: spacing.sm, marginBottom: spacing.sm },
  tab: { flex: 1, alignItems: "center", paddingVertical: 9, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  tabActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  tabText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8, padding: spacing.xl },
  emptyText: { color: colors.onSurface3, fontSize: 13 },
  errText: { color: colors.error, fontSize: 13, textAlign: "center" },
  searchRow: { flexDirection: "row", alignItems: "center", gap: 8, marginHorizontal: spacing.md, marginBottom: spacing.sm, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 12 },
  searchInput: { flex: 1, color: colors.onSurface, paddingVertical: 10, fontSize: 14 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.sm, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.sm },
  swatch: { width: 36, height: 36, borderRadius: radius.sm, alignItems: "center", justifyContent: "center" },
  rowName: { color: colors.onSurface, fontWeight: "700", fontSize: 13 },
  rowId: { color: colors.onSurface3, fontSize: 10 },
  rowMeta: { color: colors.onSurface3, fontSize: 11 },
  removeBtn: { width: 30, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface3 },
  addBtn: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, backgroundColor: colors.brand },
  addBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 12 },
  addedPill: { width: 28, height: 28, borderRadius: 14, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface3 },
  buyBtn: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.brand },
  buyBtnText: { color: colors.brand, fontWeight: "800", fontSize: 12 },
  primaryBtn: { backgroundColor: colors.brand, paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill },
  primaryBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
  hint: { color: colors.onSurface3, fontSize: 11, marginHorizontal: spacing.lg, marginBottom: spacing.md },
});