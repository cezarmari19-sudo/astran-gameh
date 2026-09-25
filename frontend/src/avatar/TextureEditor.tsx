// frontend/src/avatar/TextureEditor.tsx
// Editorul de textura pentru tricou/pantaloni: canvas Skia peste template-ul UV, cu trei
// unelte care scriu toate pe acelasi strat pictat de utilizator:
//   - "fill"  : tap pe o regiune -> umple tot dreptunghiul cu culoarea aleasa
//   - "brush" : tragi degetul -> deseneaza o urma continua cu culoarea/grosimea aleasa
//   - "image" : alegi o poza din galerie -> o plasezi/scalezi/muti in interiorul canvasului
// Liniile ghid ale template-ului raman vizibile dedesubt (desenate primele), pictura utilizatorului
// deasupra. Exportul (PNG data URL) contine DOAR ce a desenat/plasat userul + liniile ghid, gata
// de trimis catre backend ca `texture_url` si de aplicat pe geometria UV a corpului (uvTemplate.ts).
import React, { useRef, useState, useCallback } from "react";
import { View, Text, StyleSheet, Pressable, ScrollView, Modal, ActivityIndicator } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";
import {
  Canvas, useCanvasRef, Skia, SkImage, Image as SkiaImage, Group, Rect,
  Path, SkPath, useImage, Fill,
} from "@shopify/react-native-skia";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { useSharedValue, runOnJS } from "react-native-reanimated";
import { colors, radius, spacing } from "@/src/theme";
import { TEMPLATE_WIDTH, TEMPLATE_HEIGHT, regionsFor, regionAt, Region } from "./uvTemplate";
import { drawTemplateGuides } from "./templateGenerator";

const COLORS = ["#FFFFFF", "#1A1A1A", "#E53935", "#1E88E5", "#43A047", "#FDD835", "#8E24AA", "#FB8C00", "#00ACC1", "#6D4C41"];
const BRUSH_SIZES = [4, 10, 20];

type Tool = "fill" | "brush" | "image";

type StrokePoint = { x: number; y: number };
type Stroke = { points: StrokePoint[]; color: string; size: number };
type FillOp = { region: Region; color: string };
type PlacedImage = { uri: string; x: number; y: number; w: number; h: number; image: SkImage };

export default function TextureEditor({
  visible, kind, initialTextureUrl, onClose, onSave,
}: {
  visible: boolean;
  kind: "shirt" | "pants";
  initialTextureUrl?: string | null;
  onClose: () => void;
  onSave: (dataUrl: string) => void;
}) {
  const canvasRef = useCanvasRef();
  const [tool, setTool] = useState<Tool>("brush");
  const [color, setColor] = useState(COLORS[2]);
  const [brushSize, setBrushSize] = useState(BRUSH_SIZES[1]);
  const [fills, setFills] = useState<FillOp[]>([]);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [placedImages, setPlacedImages] = useState<PlacedImage[]>([]);
  const [draggingImageIdx, setDraggingImageIdx] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const baseImage = useImage(initialTextureUrl ?? undefined);
  const currentStroke = useRef<StrokePoint[]>([]);
  const [liveStroke, setLiveStroke] = useState<StrokePoint[]>([]);

  const scale = useRef(1);
  const [displaySize, setDisplaySize] = useState({ w: TEMPLATE_WIDTH, h: TEMPLATE_HEIGHT });

  const toCanvasCoords = useCallback((x: number, y: number) => ({
    x: x / scale.current,
    y: y / scale.current,
  }), []);

  // ---------- gesturi ----------

  const tapGesture = Gesture.Tap().onEnd(e => {
    if (tool !== "fill") return;
    const { x, y } = toCanvasCoords(e.x, e.y);
    const region = regionAt(kind, x, y);
    if (region) runOnJS(setFills)(prev => [...prev.filter(f => f.region.key !== region.key), { region, color }]);
  });

  const panGesture = Gesture.Pan()
    .onStart(e => {
      if (tool !== "brush") return;
      const { x, y } = toCanvasCoords(e.x, e.y);
      currentStroke.current = [{ x, y }];
      runOnJS(setLiveStroke)([{ x, y }]);
    })
    .onUpdate(e => {
      if (tool !== "brush") return;
      const { x, y } = toCanvasCoords(e.x, e.y);
      currentStroke.current = [...currentStroke.current, { x, y }];
      runOnJS(setLiveStroke)([...currentStroke.current]);
    })
    .onEnd(() => {
      if (tool !== "brush" || currentStroke.current.length === 0) return;
      const finished = currentStroke.current;
      runOnJS(setStrokes)(prev => [...prev, { points: finished, color, size: brushSize }]);
      currentStroke.current = [];
      runOnJS(setLiveStroke)([]);
    });

  const composedGesture = Gesture.Race(tapGesture, panGesture);

  // ---------- imagine plasata: drag simplu cu Pan cand tool === "image" si un item e selectat ----------

  const imagePanGesture = Gesture.Pan()
    .onUpdate(e => {
      if (draggingImageIdx === null) return;
      const { x, y } = toCanvasCoords(e.x, e.y);
      runOnJS(setPlacedImages)(prev => prev.map((p, i) => (i === draggingImageIdx ? { ...p, x: x - p.w / 2, y: y - p.h / 2 } : p)));
    });

  async function pickImage() {
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) return;
      const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.9 });
      if (res.canceled) return;
      const uri = res.assets[0].uri;
      const img = await Skia.Image.MakeImageFromEncoded(await uriToSkData(uri));
      if (!img) return;
      const w = Math.min(160, TEMPLATE_WIDTH * 0.4);
      const h = w * (img.height() / img.width());
      setPlacedImages(prev => [...prev, { uri, x: TEMPLATE_WIDTH / 2 - w / 2, y: TEMPLATE_HEIGHT / 2 - h / 2, w, h, image: img }]);
      setTool("image");
      setDraggingImageIdx(placedImages.length);
    } catch {
      // upload esuat - userul poate incerca din nou
    }
  }

  function removeSelectedImage() {
    if (draggingImageIdx === null) return;
    setPlacedImages(prev => prev.filter((_, i) => i !== draggingImageIdx));
    setDraggingImageIdx(null);
  }

  function resizeSelectedImage(delta: number) {
    if (draggingImageIdx === null) return;
    setPlacedImages(prev => prev.map((p, i) => {
      if (i !== draggingImageIdx) return p;
      const ratio = p.h / p.w;
      const w = Math.max(20, Math.min(TEMPLATE_WIDTH, p.w + delta));
      return { ...p, w, h: w * ratio };
    }));
  }

  function clearAll() {
    setFills([]);
    setStrokes([]);
    setPlacedImages([]);
    setDraggingImageIdx(null);
  }

  async function handleSave() {
    setBusy(true);
    try {
      const surface = Skia.Surface.MakeOffscreen(TEMPLATE_WIDTH, TEMPLATE_HEIGHT)!;
      const canvas = surface.getCanvas();

      drawTemplateGuides(canvas, kind);

      if (baseImage) {
        canvas.drawImageRect(
          baseImage,
          Skia.XYWHRect(0, 0, baseImage.width(), baseImage.height()),
          Skia.XYWHRect(0, 0, TEMPLATE_WIDTH, TEMPLATE_HEIGHT),
          Skia.Paint()
        );
      }

      fills.forEach(f => {
        const p = Skia.Paint();
        p.setColor(Skia.Color(f.color));
        canvas.drawRect(Skia.XYWHRect(f.region.x, f.region.y, f.region.w, f.region.h), p);
      });

      strokes.forEach(s => {
        if (s.points.length < 2) return;
        const path = Skia.Path.Make();
        path.moveTo(s.points[0].x, s.points[0].y);
        s.points.slice(1).forEach(pt => path.lineTo(pt.x, pt.y));
        const p = Skia.Paint();
        p.setColor(Skia.Color(s.color));
        p.setStyle(1); // stroke
        p.setStrokeWidth(s.size);
        p.setStrokeCap(1); // round
        p.setStrokeJoin(1); // round
        canvas.drawPath(path, p);
      });

      placedImages.forEach(pi => {
        canvas.drawImageRect(
          pi.image,
          Skia.XYWHRect(0, 0, pi.image.width(), pi.image.height()),
          Skia.XYWHRect(pi.x, pi.y, pi.w, pi.h),
          Skia.Paint()
        );
      });

      surface.flush();
      const snapshot = surface.makeImageSnapshot();
      const bytes = snapshot.encodeToBytes();
      const dataUrl = `data:image/png;base64,${Skia.Data.fromBytes(bytes).base64()}`;
      onSave(dataUrl);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={styles.root}>
        <View style={styles.header}>
          <Pressable onPress={onClose} testID="texture-editor-close">
            <MaterialCommunityIcons name="close" size={24} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.title}>{kind === "shirt" ? "Editor tricou" : "Editor pantaloni"}</Text>
          <Pressable onPress={handleSave} disabled={busy} testID="texture-editor-save" style={styles.saveBtn}>
            {busy ? <ActivityIndicator size="small" color={colors.onBrand} /> : <Text style={styles.saveBtnText}>Save</Text>}
          </Pressable>
        </View>

        <View style={styles.canvasWrap}>
          <GestureDetector gesture={tool === "image" ? imagePanGesture : composedGesture}>
            <View
              style={{ width: "100%", aspectRatio: TEMPLATE_WIDTH / TEMPLATE_HEIGHT, maxHeight: "100%" }}
              onLayout={e => {
                const { width } = e.nativeEvent.layout;
                scale.current = width / TEMPLATE_WIDTH;
                setDisplaySize({ w: width, h: width * (TEMPLATE_HEIGHT / TEMPLATE_WIDTH) });
              }}
            >
              <Canvas ref={canvasRef} style={{ flex: 1 }}>
                <Group transform={[{ scale: scale.current }]}>
                  <TemplateBackground kind={kind} />
                  {baseImage ? (
                    <SkiaImage image={baseImage} x={0} y={0} width={TEMPLATE_WIDTH} height={TEMPLATE_HEIGHT} fit="cover" />
                  ) : null}
                  {fills.map((f, i) => (
                    <Rect key={i} x={f.region.x} y={f.region.y} width={f.region.w} height={f.region.h} color={f.color} />
                  ))}
                  {strokes.map((s, i) => (
                    <StrokePath key={i} stroke={s} />
                  ))}
                  {liveStroke.length > 1 ? <StrokePath stroke={{ points: liveStroke, color, size: brushSize }} /> : null}
                  {placedImages.map((pi, i) => (
                    <SkiaImage key={i} image={pi.image} x={pi.x} y={pi.y} width={pi.w} height={pi.h} fit="fill" />
                  ))}
                </Group>
              </Canvas>
            </View>
          </GestureDetector>
        </View>

        <View style={styles.toolbar}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12 }}>
            <ToolBtn icon="brush" label="Pensulă" active={tool === "brush"} onPress={() => setTool("brush")} testID="tool-brush" />
            <ToolBtn icon="format-color-fill" label="Umplere" active={tool === "fill"} onPress={() => setTool("fill")} testID="tool-fill" />
            <ToolBtn icon="image-plus" label="Imagine" active={tool === "image"} onPress={pickImage} testID="tool-image" />
            <ToolBtn icon="delete-outline" label="Șterge tot" active={false} onPress={clearAll} testID="tool-clear" />
          </ScrollView>

          {tool === "image" && draggingImageIdx !== null ? (
            <View style={styles.imageControls}>
              <Pressable onPress={() => resizeSelectedImage(-20)} style={styles.smallBtn}><MaterialCommunityIcons name="minus" size={18} color={colors.onSurface} /></Pressable>
              <Text style={styles.hintInline}>Trage pentru poziționare</Text>
              <Pressable onPress={() => resizeSelectedImage(20)} style={styles.smallBtn}><MaterialCommunityIcons name="plus" size={18} color={colors.onSurface} /></Pressable>
              <Pressable onPress={removeSelectedImage} style={styles.smallBtn}><MaterialCommunityIcons name="trash-can-outline" size={18} color={colors.error} /></Pressable>
            </View>
          ) : (
            <>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingHorizontal: 12, marginTop: 8 }}>
                {COLORS.map(c => (
                  <Pressable key={c} onPress={() => setColor(c)} style={[styles.swatch, { backgroundColor: c }, color === c && styles.swatchActive]} testID={`color-${c}`} />
                ))}
              </ScrollView>
              {tool === "brush" ? (
                <View style={styles.sizeRow}>
                  {BRUSH_SIZES.map(s => (
                    <Pressable key={s} onPress={() => setBrushSize(s)} style={[styles.sizeBtn, brushSize === s && styles.sizeBtnActive]} testID={`brush-size-${s}`}>
                      <View style={{ width: s, height: s, borderRadius: s / 2, backgroundColor: brushSize === s ? colors.brand : colors.onSurface3 }} />
                    </Pressable>
                  ))}
                </View>
              ) : null}
            </>
          )}
        </View>
      </View>
    </Modal>
  );
}

function TemplateBackground({ kind }: { kind: "shirt" | "pants" }) {
  // fundal alb + regiuni desenate ca dreptunghiuri simple (Skia declarativ), etichetele
  // se randeaza doar la export (drawTemplateGuides) pentru performanta in timpul desenului live
  const regions = regionsFor(kind);
  return (
    <Group>
      <Rect x={0} y={0} width={TEMPLATE_WIDTH} height={TEMPLATE_HEIGHT} color="#F4F4F4" />
      {regions.map(r => (
        <Rect key={r.key} x={r.x} y={r.y} width={r.w} height={r.h} color="transparent" style="stroke" strokeWidth={2} />
      ))}
    </Group>
  );
}

function StrokePath({ stroke }: { stroke: Stroke }) {
  const path = Skia.Path.Make();
  if (stroke.points.length > 0) {
    path.moveTo(stroke.points[0].x, stroke.points[0].y);
    stroke.points.slice(1).forEach(pt => path.lineTo(pt.x, pt.y));
  }
  return <Path path={path} style="stroke" strokeWidth={stroke.size} strokeCap="round" strokeJoin="round" color={stroke.color} />;
}

function ToolBtn({ icon, label, active, onPress, testID }: { icon: any; label: string; active: boolean; onPress: () => void; testID?: string }) {
  return (
    <Pressable testID={testID} onPress={onPress} style={[styles.toolBtn, active && styles.toolBtnActive]}>
      <MaterialCommunityIcons name={icon} size={20} color={active ? colors.brand : colors.onSurface2} />
      <Text style={[styles.toolBtnText, active && { color: colors.brand }]}>{label}</Text>
    </Pressable>
  );
}

async function uriToSkData(uri: string): Promise<any> {
  const resp = await fetch(uri);
  const buf = await resp.arrayBuffer();
  return Skia.Data.fromBytes(new Uint8Array(buf));
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: spacing.md, paddingTop: 50 },
  title: { color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  saveBtn: { backgroundColor: colors.brand, paddingHorizontal: 16, paddingVertical: 8, borderRadius: radius.pill },
  saveBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
  canvasWrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.md, backgroundColor: colors.surface2 },
  toolbar: { paddingVertical: spacing.md, borderTopWidth: 1, borderColor: colors.border },
  toolBtn: { alignItems: "center", gap: 2, paddingVertical: 8, paddingHorizontal: 14, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  toolBtnActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  toolBtnText: { color: colors.onSurface3, fontSize: 10, fontWeight: "700" },
  swatch: { width: 30, height: 30, borderRadius: 15, borderWidth: 2, borderColor: colors.border },
  swatchActive: { borderColor: colors.brand },
  sizeRow: { flexDirection: "row", gap: 12, paddingHorizontal: 12, marginTop: 10, alignItems: "center" },
  sizeBtn: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  sizeBtnActive: { borderColor: colors.brand },
  imageControls: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 12, marginTop: 8 },
  smallBtn: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  hintInline: { color: colors.onSurface3, fontSize: 11, flex: 1, textAlign: "center" },
});