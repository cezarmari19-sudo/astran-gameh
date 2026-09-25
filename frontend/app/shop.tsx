import React, { useCallback, useEffect, useRef, useState } from "react";
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
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Image } from "expo-image";
import * as ImagePicker from "expo-image-picker";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";
import ScriptEditor, { ScriptFile } from "@/src/components/ScriptEditor";

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
  thumbnail_url?: string | null;
  slot?: string | null; // doar modele: daca setat, itemul e echipabil in Avatar Editor
  preview: any; // model: {type,color,scale,part_count}  ·  script: {file_count}
};

type ShopItemDetail = ShopItem & {
  object?: { type: string; color: string; scale: number };
  files?: ScriptFile[];
  parts?: any[];
};

type StudioModel = { model_id: string; name: string; part_count: number };
type SlotDef = { key: string; label: string };

const SHAPE_ICON: Record<string, string> = {
  cube: "cube-outline",
  sphere: "circle-outline",
  cylinder: "cylinder",
  cone: "triangle-outline",
  pyramid: "triangle-outline",
};

const MAX_SOURCE_CHARS = 20000; // limita din backend, per fisier
const FEE_PERCENT = 5;

function priceLabel(price: number): string {
  return price === 0 ? "Free" : `${price} Astrans`;
}

export default function ShopScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ publish?: string }>();
  const [kind, setKind] = useState<Kind>("model");
  const [q, setQ] = useState("");
  const [mineOnly, setMineOnly] = useState(false);
  const [items, setItems] = useState<ShopItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [myId, setMyId] = useState<string | null>(null);

  const [showPublish, setShowPublish] = useState(false);
  const [presetModel, setPresetModel] = useState<string | null>(null);
  const [editItem, setEditItem] = useState<ShopItemDetail | null>(null);
  const handledParam = useRef<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const path = kind === "model" ? "/shop/models" : "/shop/scripts";
      const qp = new URLSearchParams();
      if (q.trim()) qp.set("q", q.trim());
      if (mineOnly) qp.set("mine", "true");
      const qs = qp.toString();
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

  // cine sunt eu (ca sa stiu care iteme sunt ale mele)
  useEffect(() => {
    api("/auth/me").then(r => setMyId(r?.user?.user_id ?? null)).catch(() => {});
  }, []);

  // venim din Studio cu butonul "Publica": deschidem direct publicarea modelului ales
  useEffect(() => {
    const id = params.publish;
    if (id && handledParam.current !== id) {
      handledParam.current = id;
      setKind("model");
      setPresetModel(id);
      setShowPublish(true);
    }
  }, [params.publish]);

  function closePublish() {
    setShowPublish(false);
    setEditItem(null);
    setPresetModel(null);
    handledParam.current = null;
    if (params.publish) router.setParams({ publish: "" } as any);
  }

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
        myId={myId}
        onClose={() => setSelected(null)}
        onChanged={load}
        onEdit={it => {
          setSelected(null);
          setTimeout(() => setEditItem(it), 350); // lasam modalul de detalii sa se inchida
        }}
      />

      <PublishModal
        visible={showPublish || editItem !== null}
        kind={kind}
        editItem={editItem}
        presetModelId={presetModel}
        onClose={closePublish}
        onDone={() => { closePublish(); load(); }}
      />
    </SafeAreaView>
  );
}

function ModelCard({ item, onPress }: { item: ShopItem; onPress: () => void }) {
  const p = item.preview || {};
  return (
    <Pressable testID={`shop-item-${item.item_id}`} onPress={onPress} style={styles.modelCard}>
      {item.thumbnail_url ? (
        <Image source={{ uri: item.thumbnail_url }} style={styles.modelThumb} contentFit="cover" />
      ) : (
        <View style={[styles.modelSwatch, { backgroundColor: p.color || "#666" }]}>
          <MaterialCommunityIcons name={(SHAPE_ICON[p.type] || "cube-outline") as any} size={28} color="rgba(0,0,0,0.35)" />
        </View>
      )}
      <Text style={styles.cardName} numberOfLines={1}>{item.name}</Text>
      <View style={styles.cardMetaRow}>
        <Text style={[styles.cardPrice, item.price === 0 && { color: colors.brand }]}>{priceLabel(item.price)}</Text>
        {item.owned ? <MaterialCommunityIcons name="check-circle" size={14} color={colors.brand} /> : null}
      </View>
      <Text style={styles.cardAuthor} numberOfLines={1}>
        by {item.owner_username}{p.part_count ? ` · ${p.part_count} objects` : ""}
      </Text>
      {item.slot ? (
        <View style={styles.slotTag}>
          <MaterialCommunityIcons name="account-outline" size={10} color={colors.brand} />
          <Text style={styles.slotTagText}>Avatar</Text>
        </View>
      ) : null}
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

function ItemDetailModal({ itemId, myId, onClose, onChanged, onEdit }: {
  itemId: string | null;
  myId: string | null;
  onClose: () => void;
  onChanged: () => void;
  onEdit: (item: ShopItemDetail) => void;
}) {
  const [item, setItem] = useState<ShopItemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [viewFiles, setViewFiles] = useState<ScriptFile[]>([]);

  useEffect(() => {
    if (!itemId) { setItem(null); setShowCode(false); return; }
    setLoading(true);
    setErr(null);
    api(`/shop/items/${itemId}`)
      .then(r => setItem(r.item))
      .catch(e => setErr(e?.message || "Could not load item"))
      .finally(() => setLoading(false));
  }, [itemId]);

  const isOwner = !!item && !!myId && item.owner_id === myId;

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

  function confirmDelete() {
    if (!item) return;
    Alert.alert("Delete from the Shop?", `"${item.name}" will be removed for everyone.`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          try {
            await api(`/shop/items/${item.item_id}`, { method: "DELETE" });
            onChanged();
            onClose();
          } catch (e: any) {
            setErr(e?.message || "Could not delete");
          }
        },
      },
    ]);
  }

  return (
    <Modal visible={itemId !== null} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.detailBackdrop}>
        <Pressable style={StyleSheet.absoluteFillObject} onPress={onClose} />
        <View style={styles.detailBox}>
          {loading || !item ? (
            <View style={{ padding: spacing.xl }}>
              {err ? <Text style={styles.errText}>{err}</Text> : <ActivityIndicator color={colors.brand} />}
            </View>
          ) : (
            <ScrollView contentContainerStyle={{ gap: spacing.sm }} keyboardShouldPersistTaps="handled">
              <View style={styles.detailHeader}>
                {item.kind === "model" ? (
                  item.thumbnail_url ? (
                    <Image source={{ uri: item.thumbnail_url }} style={styles.detailThumb} contentFit="cover" />
                  ) : (
                    <View style={[styles.detailSwatch, { backgroundColor: item.preview?.color || item.object?.color || "#666" }]}>
                      <MaterialCommunityIcons name={(SHAPE_ICON[item.preview?.type || item.object?.type || ""] || "cube-outline") as any} size={36} color="rgba(0,0,0,0.35)" />
                    </View>
                  )
                ) : (
                  <View style={[styles.detailSwatch, { backgroundColor: colors.surface3 }]}>
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
                {item.kind === "script" ? (
                  <>
                    <Text style={styles.detailMetaText}>·</Text>
                    <Text style={styles.detailMetaText}>{item.files?.length ?? item.preview?.file_count ?? 0} files</Text>
                  </>
                ) : item.preview?.part_count ? (
                  <>
                    <Text style={styles.detailMetaText}>·</Text>
                    <Text style={styles.detailMetaText}>{item.preview.part_count} objects</Text>
                  </>
                ) : null}
                {item.slot ? (
                  <>
                    <Text style={styles.detailMetaText}>·</Text>
                    <Text style={styles.detailMetaText}>Avatar item</Text>
                  </>
                ) : null}
              </View>

              <Text style={styles.detailId} selectable>{item.item_id}</Text>

              {err ? <Text style={styles.errText}>{err}</Text> : null}

              {item.owned ? (
                <View style={styles.ownedPill}>
                  <MaterialCommunityIcons name="check-circle" size={16} color={colors.brand} />
                  <Text style={styles.ownedText}>
                    {item.kind === "model"
                      ? "You own this - use it from Studio or Assets.load(\"" + item.item_id + "\")" + (item.slot ? " or equip it in Avatar" : "")
                      : "You own this script"}
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

              {item.kind === "script" && item.owned && item.files && item.files.length > 0 ? (
                <Pressable
                  testID="shop-view-code"
                  onPress={() => { setViewFiles(item.files || []); setShowCode(true); }}
                  style={styles.outlineBtn}
                >
                  <MaterialCommunityIcons name="code-braces" size={18} color={colors.brand} />
                  <Text style={styles.outlineBtnText}>{isOwner ? "View code" : "View code (copy it into your game)"}</Text>
                </Pressable>
              ) : null}

              {isOwner ? (
                <View style={styles.actionRow}>
                  <Pressable testID="shop-edit-btn" onPress={() => onEdit(item)} style={[styles.outlineBtn, { flex: 1 }]}>
                    <MaterialCommunityIcons name="pencil-outline" size={18} color={colors.brand} />
                    <Text style={styles.outlineBtnText}>Edit</Text>
                  </Pressable>
                  <Pressable testID="shop-delete-btn" onPress={confirmDelete} style={[styles.outlineBtn, { flex: 1, borderColor: colors.error }]}>
                    <MaterialCommunityIcons name="trash-can-outline" size={18} color={colors.error} />
                    <Text style={[styles.outlineBtnText, { color: colors.error }]}>Delete</Text>
                  </Pressable>
                </View>
              ) : null}
            </ScrollView>
          )}
        </View>

        {/* cod doar de citit/copiat (modificarile nu se salveaza nicaieri) */}
        <ScriptEditor
          visible={showCode}
          files={viewFiles}
          onChange={setViewFiles}
          onClose={() => setShowCode(false)}
        />
      </View>
    </Modal>
  );
}

function PublishModal({ visible, kind: kindProp, editItem, presetModelId, onClose, onDone }: {
  visible: boolean;
  kind: Kind;
  editItem: ShopItemDetail | null;
  presetModelId: string | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const router = useRouter();
  const editing = !!editItem;
  const kind: Kind = editItem ? editItem.kind : kindProp;

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState("0");
  const [isPublic, setIsPublic] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // script
  const [files, setFiles] = useState<ScriptFile[]>([]);
  const [showEditor, setShowEditor] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [games, setGames] = useState<any[] | null>(null);
  const [importBusy, setImportBusy] = useState(false);

  // model
  const [models, setModels] = useState<StudioModel[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [thumbnail, setThumbnail] = useState<string | null | undefined>(undefined); // undefined = neschimbata, null = stearsa
  const [slot, setSlot] = useState<string | null>(null); // null = nu e item de Avatar
  const [slots, setSlots] = useState<SlotDef[]>([]);

  useEffect(() => {
    if (!visible) return;
    setErr(null);
    setShowEditor(false);
    setShowImport(false);
    setThumbnail(undefined);
    if (editItem) {
      setName(editItem.name);
      setDescription(editItem.description || "");
      setPrice(String(editItem.price));
      setIsPublic(editItem.is_public);
      setFiles(editItem.files ?? []);
      setModelId(null);
      setSlot(editItem.slot ?? null);
    } else {
      setName("");
      setDescription("");
      setPrice("0");
      setIsPublic(true);
      setFiles([]);
      setModelId(presetModelId ?? null);
      setSlot(null);
    }
  }, [visible]);

  // modelele mele din Studio
  useEffect(() => {
    if (!visible || kind !== "model") return;
    api("/studio/models")
      .then(r => {
        const list: StudioModel[] = Array.isArray(r?.models) ? r.models : [];
        setModels(list);
        if (presetModelId && !editItem) {
          const m = list.find(x => x.model_id === presetModelId);
          if (m) setName(prev => prev || m.name);
        }
      })
      .catch(() => setModels([]));
  }, [visible, kind]);

  // categoriile disponibile pentru Avatar Editor (extensibile din backend)
  useEffect(() => {
    if (!visible || kind !== "model") return;
    api("/avatar/slots")
      .then(r => setSlots(Array.isArray(r?.slots) ? r.slots : []))
      .catch(() => setSlots([]));
  }, [visible, kind]);

  function pickModel(m: StudioModel) {
    if (modelId === m.model_id && editing) { setModelId(null); return; }
    setModelId(m.model_id);
    setName(prev => prev.trim() ? prev : m.name);
  }

  async function pickThumb() {
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) { setErr("Photo permission denied"); return; }
      const res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        quality: 0.3, base64: true, allowsEditing: true, aspect: [1, 1],
      });
      if (res.canceled) return;
      const a = res.assets[0];
      if (a.base64) setThumbnail(`data:image/jpeg;base64,${a.base64}`);
    } catch (e: any) {
      setErr(e?.message || "Could not pick the image");
    }
  }

  async function toggleImport() {
    const next = !showImport;
    setShowImport(next);
    if (next && games === null) {
      try {
        const r = await api("/games/mine");
        setGames(Array.isArray(r?.games) ? r.games : []);
      } catch {
        setGames([]);
      }
    }
  }

  async function importFrom(game: any) {
    const doImport = async () => {
      setImportBusy(true);
      try {
        const r = await api(`/sandbox/games/${game.game_id}/files`);
        const f: ScriptFile[] = Array.isArray(r?.files) ? r.files : [];
        if (f.length === 0) setErr(`"${game.title}" has no scripts yet`);
        else { setFiles(f); setShowImport(false); setErr(null); }
      } catch (e: any) {
        setErr(e?.message || "Could not load the scripts");
      } finally {
        setImportBusy(false);
      }
    };
    if (files.some(f => f.source.trim())) {
      Alert.alert("Replace your code?", `The code here will be replaced with the scripts from "${game.title}".`, [
        { text: "Cancel", style: "cancel" },
        { text: "Replace", style: "destructive", onPress: doImport },
      ]);
    } else {
      await doImport();
    }
  }

  async function submit() {
    const nm = name.trim();
    if (nm.length < 2) { setErr("Name too short"); return; }
    const priceNum = Math.max(0, Math.min(100000, Math.floor(Number(price) || 0)));

    if (kind === "script") {
      const main = files.find(f => f.name === "main");
      if (!main) { setErr('Write your code first - a script named "main" is required (it runs first)'); return; }
      if (!main.source.trim()) { setErr('Your "main" script is empty'); return; }
      const tooLong = files.find(f => f.source.length > MAX_SOURCE_CHARS);
      if (tooLong) { setErr(`"${tooLong.name}" is too long (max ${MAX_SOURCE_CHARS} characters per script)`); return; }
    } else if (!editing && !modelId) {
      setErr("Pick a model from your Studio to publish");
      return;
    }

    setBusy(true);
    setErr(null);
    try {
      if (editing && editItem) {
        const body: any = { name: nm, description, price: priceNum, is_public: isPublic };
        if (kind === "script") body.files = files;
        else {
          if (modelId) body.model_id = modelId;
          if (thumbnail === null) body.thumbnail_url = "";
          else if (typeof thumbnail === "string") body.thumbnail_url = thumbnail;
          body.slot = slot ?? ""; // "" scoate din Avatar Editor daca a fost debifat
        }
        await api(`/shop/items/${editItem.item_id}`, { method: "PATCH", body: JSON.stringify(body) });
      } else if (kind === "script") {
        await api("/shop/scripts", {
          method: "POST",
          body: JSON.stringify({ name: nm, description, price: priceNum, is_public: isPublic, files }),
        });
      } else {
        const body: any = { model_id: modelId, name: nm, description, price: priceNum, is_public: isPublic };
        if (typeof thumbnail === "string") body.thumbnail_url = thumbnail;
        if (slot) body.slot = slot;
        await api("/shop/models", { method: "POST", body: JSON.stringify(body) });
      }
      onDone();
    } catch (e: any) {
      setErr(e?.message || "Publish failed");
    } finally {
      setBusy(false);
    }
  }

  const priceNum = Math.max(0, Math.floor(Number(price) || 0));
  const fee = Math.floor((priceNum * FEE_PERCENT) / 100);
  const mainFile = files.find(f => f.name === "main");
  const codePreview = (mainFile?.source || "").split("\n").slice(0, 6).join("\n");
  const shownThumb = thumbnail === undefined ? editItem?.thumbnail_url ?? null : thumbnail;

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={{ flex: 1, justifyContent: "flex-end" }}>
        <Pressable style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.6)" }} onPress={onClose} />
        <ScrollView style={styles.publishSheet} contentContainerStyle={{ paddingBottom: 30 }} keyboardShouldPersistTaps="handled">
          <Text style={styles.sheetTitle}>
            {editing ? "Edit" : "Publish"} {kind === "model" ? "a model" : "a script"}
          </Text>

          <Text style={styles.lab}>Name</Text>
          <TextInput testID="publish-name" value={name} onChangeText={setName} maxLength={48} style={styles.input}
            placeholder={kind === "model" ? "Oak Tree" : "Double jump"} placeholderTextColor={colors.onSurface3} />

          <Text style={styles.lab}>Description</Text>
          <TextInput testID="publish-desc" value={description} onChangeText={setDescription} multiline maxLength={500}
            style={[styles.input, { height: 70 }]} placeholder="..." placeholderTextColor={colors.onSurface3} />

          {kind === "script" ? (
            <>
              <Text style={styles.lab}>Code</Text>
              <View style={styles.codeBox}>
                {mainFile && mainFile.source.trim() ? (
                  <Text style={styles.codeText} numberOfLines={6}>{codePreview}</Text>
                ) : (
                  <Text style={[styles.codeText, { color: colors.onSurface3 }]}>{"-- no code yet\n-- tap \"Write / edit code\" below"}</Text>
                )}
                <Text style={styles.codeMeta}>{files.length} file{files.length === 1 ? "" : "s"}</Text>
              </View>
              <View style={styles.actionRow}>
                <Pressable testID="publish-write-code" onPress={() => setShowEditor(true)} style={[styles.outlineBtn, { flex: 1, marginTop: 8 }]}>
                  <MaterialCommunityIcons name="code-braces" size={18} color={colors.brand} />
                  <Text style={styles.outlineBtnText}>Write / edit code</Text>
                </Pressable>
                <Pressable testID="publish-import-code" onPress={toggleImport} style={[styles.outlineBtn, { flex: 1, marginTop: 8 }]}>
                  <MaterialCommunityIcons name="download-outline" size={18} color={colors.brand} />
                  <Text style={styles.outlineBtnText}>From my game</Text>
                </Pressable>
              </View>

              {showImport ? (
                <View style={styles.importBox}>
                  {games === null || importBusy ? (
                    <ActivityIndicator color={colors.brand} />
                  ) : games.length === 0 ? (
                    <Text style={styles.hintText}>You have no games yet.</Text>
                  ) : (
                    games.map(g => (
                      <Pressable key={g.game_id} onPress={() => importFrom(g)} style={styles.gameRow}>
                        <MaterialCommunityIcons name="gamepad-variant-outline" size={18} color={colors.brand} />
                        <Text style={styles.gameRowText} numberOfLines={1}>{g.title}</Text>
                      </Pressable>
                    ))
                  )}
                </View>
              ) : null}
            </>
          ) : (
            <>
              <Text style={styles.lab}>{editing ? "Update content from Studio (optional)" : "Model from your Studio"}</Text>
              {models.length === 0 ? (
                <View style={styles.importBox}>
                  <Text style={styles.hintText}>You have no models in Studio yet. Build one first, then publish it here.</Text>
                  <Pressable onPress={() => { onClose(); router.push("/model-studio/new" as any); }} style={[styles.outlineBtn, { marginTop: 8 }]}>
                    <MaterialCommunityIcons name="cube-scan" size={18} color={colors.brand} />
                    <Text style={styles.outlineBtnText}>Open Studio</Text>
                  </Pressable>
                </View>
              ) : (
                <View style={{ gap: 6, marginTop: 6 }}>
                  {models.map(m => (
                    <Pressable key={m.model_id} testID={`publish-model-${m.model_id}`} onPress={() => pickModel(m)}
                      style={[styles.modelPick, modelId === m.model_id && styles.modelPickActive]}>
                      <MaterialCommunityIcons name="cube-scan" size={20} color={modelId === m.model_id ? colors.brand : colors.onSurface3} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.modelPickName} numberOfLines={1}>{m.name}</Text>
                        <Text style={styles.cardAuthor}>{m.part_count} object{m.part_count === 1 ? "" : "s"}</Text>
                      </View>
                      {modelId === m.model_id ? <MaterialCommunityIcons name="check-circle" size={18} color={colors.brand} /> : null}
                    </Pressable>
                  ))}
                </View>
              )}

              <Text style={styles.lab}>Avatar Editor category (optional)</Text>
              <Text style={styles.hintText}>Set this if the model is wearable — it'll show up in the Avatar Editor for anyone who owns it.</Text>
              <View style={styles.slotGrid}>
                <Pressable
                  testID="publish-slot-none"
                  onPress={() => setSlot(null)}
                  style={[styles.slotChip, slot === null && styles.slotChipActive]}
                >
                  <Text style={[styles.slotChipText, slot === null && { color: colors.brand }]}>None</Text>
                </Pressable>
                {slots.map(s => (
                  <Pressable
                    key={s.key}
                    testID={`publish-slot-${s.key}`}
                    onPress={() => setSlot(s.key)}
                    style={[styles.slotChip, slot === s.key && styles.slotChipActive]}
                  >
                    <Text style={[styles.slotChipText, slot === s.key && { color: colors.brand }]}>{s.label}</Text>
                  </Pressable>
                ))}
              </View>

              <Text style={styles.lab}>Thumbnail</Text>
              <View style={styles.thumbRow}>
                {shownThumb ? (
                  <Image source={{ uri: shownThumb }} style={styles.thumbPreview} contentFit="cover" />
                ) : (
                  <View style={[styles.thumbPreview, { backgroundColor: colors.surface2, alignItems: "center", justifyContent: "center" }]}>
                    <MaterialCommunityIcons name="image-outline" size={26} color={colors.onSurface3} />
                  </View>
                )}
                <View style={{ flex: 1, gap: 6 }}>
                  <Pressable testID="publish-pick-thumb" onPress={pickThumb} style={styles.outlineBtn}>
                    <MaterialCommunityIcons name="image-plus" size={18} color={colors.brand} />
                    <Text style={styles.outlineBtnText}>{shownThumb ? "Change image" : "Pick an image"}</Text>
                  </Pressable>
                  {shownThumb ? (
                    <Pressable onPress={() => setThumbnail(null)} style={styles.outlineBtn}>
                      <MaterialCommunityIcons name="close" size={18} color={colors.onSurface3} />
                      <Text style={[styles.outlineBtnText, { color: colors.onSurface3 }]}>Remove</Text>
                    </Pressable>
                  ) : null}
                </View>
              </View>
            </>
          )}

          <Text style={styles.lab}>Price (Astrans, 0 = free)</Text>
          <TextInput testID="publish-price" value={price} onChangeText={setPrice} keyboardType="number-pad" style={styles.input}
            placeholder="0" placeholderTextColor={colors.onSurface3} />
          {priceNum > 0 ? (
            <Text style={styles.hintText}>Shop fee {FEE_PERCENT}% - you receive {priceNum - fee} Astrans per sale.</Text>
          ) : null}

          <Pressable onPress={() => setIsPublic(v => !v)} style={styles.toggle} testID="publish-toggle-public">
            <MaterialCommunityIcons name={isPublic ? "eye" : "eye-off"} size={20} color={isPublic ? colors.brand : colors.onSurface3} />
            <Text style={{ color: colors.onSurface, flex: 1, fontWeight: "700" }}>Public (visible in the Shop)</Text>
            <View style={[styles.switch, isPublic && styles.switchOn]}><View style={[styles.knob, isPublic && styles.knobOn]} /></View>
          </Pressable>

          {err ? <Text style={[styles.errText, { marginTop: 10 }]}>{err}</Text> : null}

          <Pressable testID="publish-submit" onPress={submit} disabled={busy} style={styles.buyBtn}>
            {busy ? <ActivityIndicator size="small" color={colors.onBrand} /> : <Text style={styles.buyText}>{editing ? "Save changes" : "Publish"}</Text>}
          </Pressable>
        </ScrollView>

        {/* editorul de cod (acelasi ca in Studio): "Save" doar se intoarce la formular */}
        <ScriptEditor
          visible={showEditor}
          files={files}
          onChange={setFiles}
          onClose={() => setShowEditor(false)}
          onSave={() => setShowEditor(false)}
        />
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
  modelThumb: { height: 90, borderRadius: radius.sm, marginBottom: 4, backgroundColor: colors.surface3 },
  scriptRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.sm },
  scriptIcon: { width: 40, height: 40, borderRadius: radius.sm, backgroundColor: colors.surface3, alignItems: "center", justifyContent: "center" },
  cardName: { color: colors.onSurface, fontWeight: "800", fontSize: 13 },
  cardMetaRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cardPrice: { color: colors.onSurface2, fontSize: 12, fontWeight: "700" },
  cardAuthor: { color: colors.onSurface3, fontSize: 11 },
  slotTag: { flexDirection: "row", alignItems: "center", gap: 3, alignSelf: "flex-start", backgroundColor: colors.brandTint, borderRadius: radius.sm, paddingHorizontal: 6, paddingVertical: 2, marginTop: 2 },
  slotTagText: { color: colors.brand, fontSize: 9, fontWeight: "800", letterSpacing: 0.3 },
  detailBackdrop: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  detailBox: { width: "100%", maxWidth: 400, maxHeight: "88%", backgroundColor: colors.surface, borderRadius: radius.lg, borderWidth: 1, borderColor: colors.border, padding: spacing.lg },
  detailHeader: { alignItems: "center", gap: 4, marginBottom: 4 },
  detailSwatch: { width: 84, height: 84, borderRadius: radius.md, alignItems: "center", justifyContent: "center", marginBottom: 6 },
  detailThumb: { width: 84, height: 84, borderRadius: radius.md, marginBottom: 6, backgroundColor: colors.surface3 },
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
  outlineBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderWidth: 1, borderColor: colors.brand, borderRadius: radius.pill, paddingVertical: 11, paddingHorizontal: 12 },
  outlineBtnText: { color: colors.brand, fontWeight: "800", fontSize: 13 },
  actionRow: { flexDirection: "row", gap: 8 },
  publishSheet: { maxHeight: "90%", backgroundColor: colors.surface, padding: spacing.lg, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, borderTopWidth: 1, borderColor: colors.border },
  sheetTitle: { color: colors.onSurface, fontSize: 20, fontWeight: "900", marginBottom: 12 },
  lab: { color: colors.onSurface3, fontSize: 11, fontWeight: "700", letterSpacing: 1, textTransform: "uppercase", marginTop: 14 },
  input: { marginTop: 6, backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  hintText: { color: colors.onSurface3, fontSize: 12, marginTop: 8 },
  codeBox: { marginTop: 6, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: 12 },
  codeText: { color: colors.onSurface2, fontSize: 12, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  codeMeta: { color: colors.onSurface3, fontSize: 11, marginTop: 8, fontWeight: "700" },
  importBox: { marginTop: 8, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, padding: 10, gap: 4 },
  gameRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 10 },
  gameRowText: { color: colors.onSurface, fontSize: 14, fontWeight: "600", flex: 1 },
  modelPick: { flexDirection: "row", alignItems: "center", gap: 10, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  modelPickActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  modelPickName: { color: colors.onSurface, fontSize: 14, fontWeight: "700" },
  slotGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  slotChip: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  slotChipActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  slotChipText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12 },
  thumbRow: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 6 },
  thumbPreview: { width: 84, height: 84, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  toggle: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14, padding: 12, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  switch: { width: 44, height: 26, borderRadius: 13, backgroundColor: colors.surface3, padding: 3 },
  switchOn: { backgroundColor: colors.brand },
  knob: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.onSurface2 },
  knobOn: { backgroundColor: colors.onBrand, marginLeft: "auto" },
});