import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  Pressable,
  ScrollView,
  TextInput,
  Modal,
  ActivityIndicator,
  FlatList,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";

type Kind = "model" | "script";

type ShopItem = {
  item_id: string;
  kind: Kind;
  owner_id: string;
  owner_username: string;
  name: string;
  description: string;
  price: number;
  is_public: boolean;
  downloads: number;
  owned: boolean;
  preview: any; // {type,color,scale} pentru model, {file_count} pentru script
};

type ShopItemDetail = ShopItem & {
  object?: { type: string; color: string; scale: number };
  files?: { name: string; source: string }[];
};

const SHAPE_ICON: Record<string, string> = {
  cube: "cube-outline",
  sphere: "circle-outline",
  cylinder: "cylinder",
  cone: "triangle-outline",
  pyramid: "triangle-outline",
};

function priceLabel(price: number): string {
  return price === 0 ? "Free" : `${price} Astrans`;
}

export default function ShopScreen() {
  const router = useRouter();
  const [kind, setKind] = useState<Kind>("model");
  const [q, setQ] = useState("");
  const [mineOnly, setMineOnly] = useState(false);
  const [items, setItems] = useState<ShopItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showPublish, setShowPublish] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const path = kind === "model" ? "/shop/models" : "/shop/scripts";
      const params = new URLSearchParams();
      if (q.trim()) params.set("q", q.trim());
      if (mineOnly) params.set("mine", "true");
      const qs = params.toString();
      const r = await api(`${path}${qs ? `?${qs}` : ""}`);
      setItems(Array.isArray(r?.items) ? r.items : []);
    } catch (e: any) {
      setErr(e?.message || "Could not load the shop");
    } finally {
      setLoading(false);
    }
  }, [kind, q, mineOnly]);

  useEffect(() => {
    const t = setTimeout(load, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <View style={styles.header}>
        <Pressable testID="shop-back" onPress={() => router.back()} style={styles.iconBtn}>
          <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Shop</Text>
        <Pressable testID="shop-publish" onPress={() => setShowPublish(true)} style={styles.iconBtn}>
          <MaterialCommunityIcons name="plus" size={24} color={colors.brand} />
        </Pressable>
      </View>

      <View style={styles.tabs}>
        <Pressable testID="shop-tab-models" onPress={() => setKind("model")} style={[styles.tab, kind === "model" && styles.tabActive]}>
          <MaterialCommunityIcons name="cube-outline" size={16} color={kind === "model" ? colors.brand : colors.onSurface3} />
          <Text style={[styles.tabText, kind === "model" && { color: colors.brand }]}>Models</Text>
        </Pressable>
        <Pressable testID="shop-tab-scripts" onPress={() => setKind("script")} style={[styles.tab, kind === "script" && styles.tabActive]}>
          <MaterialCommunityIcons name="code-braces" size={16} color={kind === "script" ? colors.brand : colors.onSurface3} />
          <Text style={[styles.tabText, kind === "script" && { color: colors.brand }]}>Scripts</Text>
        </Pressable>
      </View>

      <View style={styles.searchRow}>
        <View style={styles.searchBox}>
          <MaterialCommunityIcons name="magnify" size={18} color={colors.onSurface3} />
          <TextInput
            testID="shop-search"
            value={q}
            onChangeText={setQ}
            placeholder="Search by name or id"
            placeholderTextColor={colors.onSurface3}
            autoCapitalize="none"
            style={styles.searchInput}
          />
        </View>
        <Pressable testID="shop-mine-toggle" onPress={() => setMineOnly(v => !v)} style={[styles.mineBtn, mineOnly && styles.mineBtnActive]}>
          <Text style={[styles.mineBtnText, mineOnly && { color: colors.brand }]}>Mine</Text>
        </Pressable>
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.brand} />
        </View>
      ) : err ? (
        <View style={styles.center}>
          <Text style={styles.errText}>{err}</Text>
        </View>
      ) : items.length === 0 ? (
        <View style={styles.center}>
          <MaterialCommunityIcons name={kind === "model" ? "cube-off-outline" : "code-tags"} size={40} color={colors.onSurface3} />
          <Text style={styles.emptyText}>{mineOnly ? "You haven't published anything yet" : "Nothing here yet"}</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={i => i.item_id}
          numColumns={kind === "model" ? 2 : 1}
          key={kind} // forces re-layout when column count changes
          contentContainerStyle={{ padding: spacing.md, gap: spacing.sm }}
          columnWrapperStyle={kind === "model" ? { gap: spacing.sm } : undefined}
          renderItem={({ item }) =>
            kind === "model" ? (
              <ModelCard item={item} onPress={() => setSelected(item.item_id)} />
            ) : (
              <ScriptRow item={item} onPress={() => setSelected(item.item_id)} />
            )
          }
        />
      )}

      <ItemDetailModal
        itemId={selected}
        onClose={() => setSelected(null)}
        onChanged={load}
      />

      <PublishModal
        visible={showPublish}
        kind={kind}
        onClose={() => setShowPublish(false)}
        onPublished={() => { setShowPublish(false); load(); }}
      />
    </SafeAreaView>
  );
}

function ModelCard({ item, onPress }: { item: ShopItem; onPress: () => void }) {
  const p = item.preview || {};
  return (
    <Pressable testID={`shop-item-${item.item_id}`} onPress={onPress} style={styles.modelCard}>
      <View style={[styles.modelSwatch, { backgroundColor: p.color || "#666" }]}>
        <MaterialCommunityIcons name={(SHAPE_ICON[p.type] || "cube-outline") as any} size={28} color="rgba(0,0,0,0.35)" />
      </View>
      <Text style={styles.cardName} numberOfLines={1}>{item.name}</Text>
      <View style={styles.cardMetaRow}>
        <Text style={[styles.cardPrice, item.price === 0 && { color: colors.brand }]}>{priceLabel(item.price)}</Text>
        {item.owned ? <MaterialCommunityIcons name="check-circle" size={14} color={colors.brand} /> : null}
      </View>
      <Text style={styles.cardAuthor} numberOfLines={1}>by {item.owner_username}</Text>
    </Pressable>
  );
}

function ScriptRow({ item, onPress }: { item: ShopItem; onPress: () => void }) {
  const fileCount = item.preview?.file_count ?? 0;
  return (
    <Pressable testID={`shop-item-${item.item_id}`} onPress={onPress} style={styles.scriptRow}>
      <View style={styles.scriptIcon}>
        <MaterialCommunityIcons name="code-braces" size={20} color={colors.brand} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.cardName} numberOfLines={1}>{item.name}</Text>
        <Text style={styles.cardAuthor} numberOfLines={1}>
          by {item.owner_username} · {fileCount} file{fileCount === 1 ? "" : "s"}
        </Text>
      </View>
      <View style={{ alignItems: "flex-end", gap: 4 }}>
        <Text style={[styles.cardPrice, item.price === 0 && { color: colors.brand }]}>{priceLabel(item.price)}</Text>
        {item.owned ? <MaterialCommunityIcons name="check-circle" size={14} color={colors.brand} /> : null}
      </View>
    </Pressable>
  );
}

function ItemDetailModal({ itemId, onClose, onChanged }: { itemId: string | null; onClose: () => void; onChanged: () => void }) {
  const [item, setItem] = useState<ShopItemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!itemId) { setItem(null); return; }
    setLoading(true);
    setErr(null);
    api(`/shop/items/${itemId}`)
      .then(r => setItem(r.item))
      .catch(e => setErr(e?.message || "Could not load item"))
      .finally(() => setLoading(false));
  }, [itemId]);

  async function buy() {
    if (!item) return;
    setBusy(true);
    setErr(null);
    try {
      await api(`/shop/items/${item.item_id}/buy`, { method: "POST" });
      const r = await api(`/shop/items/${item.item_id}`);
      setItem(r.item);
      onChanged();
    } catch (e: any) {
      setErr(e?.message || "Purchase failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal visible={itemId !== null} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.detailBackdrop}>
        <Pressable style={StyleSheet.absoluteFillObject} onPress={onClose} />
        <View style={styles.detailBox}>
          {loading || !item ? (
            <View style={{ padding: spacing.xl }}>
              <ActivityIndicator color={colors.brand} />
            </View>
          ) : (
            <>
              <View style={styles.detailHeader}>
                {item.kind === "model" ? (
                  <View style={[styles.detailSwatch, { backgroundColor: item.object?.color || "#666" }]}>
                    <MaterialCommunityIcons name={(SHAPE_ICON[item.object?.type || ""] || "cube-outline") as any} size={36} color="rgba(0,0,0,0.35)" />
                  </View>
                ) : (
                  <View style={[styles.detailSwatch, { backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" }]}>
                    <MaterialCommunityIcons name="code-braces" size={36} color={colors.brand} />
                  </View>
                )}
                <Text style={styles.detailName}>{item.name}</Text>
                <Text style={styles.detailAuthor}>by {item.owner_username}</Text>
              </View>

              {item.description ? <Text style={styles.detailDesc}>{item.description}</Text> : null}

              <View style={styles.detailMetaRow}>
                <Text style={styles.detailMetaText}>{item.downloads} downloads</Text>
                <Text style={styles.detailMetaText}>·</Text>
                <Text style={styles.detailMetaText}>{item.is_public ? "Public" : "Private"}</Text>
                {item.kind === "script" && item.files ? (
                  <>
                    <Text style={styles.detailMetaText}>·</Text>
                    <Text style={styles.detailMetaText}>{item.files.length} files</Text>
                  </>
                ) : null}
              </View>

              <Text style={styles.detailId} selectable>{item.item_id}</Text>

              {err ? <Text style={styles.errText}>{err}</Text> : null}

              {item.owned ? (
                <View style={styles.ownedPill}>
                  <MaterialCommunityIcons name="check-circle" size={16} color={colors.brand} />
                  <Text style={styles.ownedText}>
                    {item.kind === "model" ? "You own this — use it from Studio or Assets.load(\"" + item.item_id + "\")" : "You own this script"}
                  </Text>
                </View>
              ) : (
                <Pressable testID="shop-buy-btn" onPress={buy} disabled={busy} style={styles.buyBtn}>
                  {busy ? <ActivityIndicator size="small" color={colors.onBrand} /> : (
                    <>
                      <MaterialCommunityIcons name="cart-outline" size={18} color={colors.onBrand} />
                      <Text style={styles.buyText}>{item.price === 0 ? "Get for free" : `Buy for ${item.price} Astrans`}</Text>
                    </>
                  )}
                </Pressable>
              )}
            </>
          )}
        </View>
      </View>
    </Modal>
  );
}

function PublishModal({ visible, kind, onClose, onPublished }: { visible: boolean; kind: Kind; onClose: () => void; onPublished: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState("0");
  const [isPublic, setIsPublic] = useState(true);
  const [shape, setShape] = useState<"cube" | "sphere" | "cylinder" | "cone" | "pyramid">("cube");
  const [color, setColor] = useState("#CCFF00");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (visible) { setName(""); setDescription(""); setPrice("0"); setIsPublic(true); setErr(null); }
  }, [visible]);

  async function publish() {
    if (!name || name.trim().length < 2) { setErr("Name too short"); return; }
    const priceNum = Math.max(0, Math.floor(Number(price) || 0));
    setBusy(true);
    setErr(null);
    try {
      if (kind === "model") {
        await api("/shop/models", {
          method: "POST",
          body: JSON.stringify({
            name: name.trim(), description, price: priceNum, is_public: isPublic,
            object: { type: shape, color, scale: 1 },
          }),
        });
      } else {
        await api("/shop/scripts", {
          method: "POST",
          body: JSON.stringify({
            name: name.trim(), description, price: priceNum, is_public: isPublic,
            files: [{ name: "main", source: "-- write your script here\n" }],
          }),
        });
      }
      onPublished();
    } catch (e: any) {
      setErr(e?.message || "Publish failed");
    } finally {
      setBusy(false);
    }
  }

  const PALETTE = ["#CCFF00", "#FF3366", "#00E5FF", "#FFD500", "#00FF66", "#FF9500", "#B266FF", "#FFFFFF", "#666666"];
  const SHAPES: typeof shape[] = ["cube", "sphere", "cylinder", "cone", "pyramid"];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: "flex-end" }}>
        <Pressable style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.6)" }} onPress={onClose} />
        <ScrollView style={styles.publishSheet} contentContainerStyle={{ paddingBottom: 30 }} keyboardShouldPersistTaps="handled">
          <Text style={styles.sheetTitle}>Publish {kind === "model" ? "a model" : "a script"}</Text>

          <Text style={styles.lab}>Name</Text>
          <TextInput testID="publish-name" value={name} onChangeText={setName} style={styles.input} placeholder="Oak Tree" placeholderTextColor={colors.onSurface3} />

          <Text style={styles.lab}>Description</Text>
          <TextInput testID="publish-desc" value={description} onChangeText={setDescription} multiline style={[styles.input, { height: 70 }]} placeholder="..." placeholderTextColor={colors.onSurface3} />

          <Text style={styles.lab}>Price (Astrans, 0 = free)</Text>
          <TextInput testID="publish-price" value={price} onChangeText={setPrice} keyboardType="number-pad" style={styles.input} placeholder="0" placeholderTextColor={colors.onSurface3} />

          {kind === "model" ? (
            <>
              <Text style={styles.lab}>Shape</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 4 }}>
                {SHAPES.map(s => (
                  <Pressable key={s} testID={`publish-shape-${s}`} onPress={() => setShape(s)} style={[styles.pill, shape === s && styles.pillActive]}>
                    <Text style={[styles.pillText, shape === s && { color: colors.brand }]}>{s}</Text>
                  </Pressable>
                ))}
              </ScrollView>
              <Text style={styles.lab}>Color</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 4 }}>
                {PALETTE.map(c => (
                  <Pressable key={c} testID={`publish-color-${c}`} onPress={() => setColor(c)} style={[styles.swatch, { backgroundColor: c }, color === c && styles.swatchSel]} />
                ))}
              </ScrollView>
            </>
          ) : (
            <Text style={styles.hintText}>A starter "main" script is created; edit it afterwards from Studio, using the same script editor.</Text>
          )}

          <Pressable onPress={() => setIsPublic(v => !v)} style={styles.toggle} testID="publish-toggle-public">
            <MaterialCommunityIcons name={isPublic ? "eye" : "eye-off"} size={20} color={isPublic ? colors.brand : colors.onSurface3} />
            <Text style={{ color: colors.onSurface, flex: 1, fontWeight: "700" }}>Public (visible in the Shop)</Text>
            <View style={[styles.switch, isPublic && styles.switchOn]}><View style={[styles.knob, isPublic && styles.knobOn]} /></View>
          </Pressable>

          {err ? <Text style={styles.errText}>{err}</Text> : null}

          <Pressable testID="publish-submit" onPress={publish} disabled={busy} style={styles.buyBtn}>
            {busy ? <ActivityIndicator size="small" color={colors.onBrand} /> : <Text style={styles.buyText}>Publish</Text>}
          </Pressable>
        </ScrollView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md, gap: 10 },
  iconBtn: { width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, textAlign: "center", color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  tabs: { flexDirection: "row", paddingHorizontal: spacing.md, gap: spacing.sm, marginBottom: spacing.sm },
  tab: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 14, paddingVertical: 9, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  tabActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  tabText: { color: colors.onSurface2, fontWeight: "700", fontSize: 13 },
  searchRow: { flexDirection: "row", gap: spacing.sm, paddingHorizontal: spacing.md, marginBottom: spacing.sm },
  searchBox: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, paddingHorizontal: 12 },
  searchInput: { flex: 1, color: colors.onSurface, paddingVertical: 10, fontSize: 14 },
  mineBtn: { paddingHorizontal: 14, justifyContent: "center", borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  mineBtnActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  mineBtnText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8, padding: spacing.xl },
  errText: { color: colors.error, fontSize: 13, textAlign: "center" },
  emptyText: { color: colors.onSurface3, fontSize: 13 },
  modelCard: { flex: 1, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.sm, gap: 4 },
  modelSwatch: { height: 90, borderRadius: radius.sm, alignItems: "center", justifyContent: "center", marginBottom: 4 },
  scriptRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.sm },
  scriptIcon: { width: 40, height: 40, borderRadius: radius.sm, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" },
  cardName: { color: colors.onSurface, fontWeight: "800", fontSize: 13 },
  cardMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cardPrice: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  cardAuthor: { color: colors.onSurface3, fontSize: 11 },
  detailBackdrop: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  detailBox: { width: "100%", maxWidth: 400, backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg, gap: spacing.sm },
  detailHeader: { alignItems: "center", gap: 4, marginBottom: 4 },
  detailSwatch: { width: 84, height: 84, borderRadius: radius.md, alignItems: "center", justifyContent: "center", marginBottom: 6 },
  detailName: { color: colors.onSurface, fontSize: 18, fontWeight: "900", textAlign: "center" },
  detailAuthor: { color: colors.onSurface3, fontSize: 12 },
  detailDesc: { color: colors.onSurface2, fontSize: 13, textAlign: "center" },
  detailMetaRow: { flexDirection: "row", justifyContent: "center", gap: 6 },
  detailMetaText: { color: colors.onSurface3, fontSize: 11 },
  detailId: { color: colors.onSurface3, fontSize: 10, textAlign: "center", fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  ownedPill: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: colors.surface2, borderRadius: radius.md, padding: 12, marginTop: 4 },
  ownedText: { color: colors.onSurface2, fontSize: 12, flex: 1 },
  buyBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, backgroundColor: colors.brand, borderRadius: radius.pill, paddingVertical: 13, marginTop: 8 },
  buyText: { color: colors.onBrand, fontWeight: "900", fontSize: 14 },
  publishSheet: { maxHeight: "88%", backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border },
  sheetTitle: { color: colors.onSurface, fontSize: 20, fontWeight: "900", marginBottom: 12 },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  hintText: { color: colors.onSurface3, fontSize: 12, marginTop: 8 },
  pill: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  pillActive: { backgroundColor: colors.brandTint, borderColor: colors.brand },
  pillText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12, textTransform: "capitalize" },
  swatch: { width: 32, height: 32, borderRadius: 16, borderWidth: 2, borderColor: colors.border },
  swatchSel: { borderColor: colors.onSurface },
  toggle: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  switch: { width: 44, height: 26, borderRadius: 13, backgroundColor: colors.surface3, padding: 3 },
  switchOn: { backgroundColor: colors.brand },
  knob: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.onSurface2 },
  knobOn: { backgroundColor: colors.onBrand, marginLeft: "auto" },
});