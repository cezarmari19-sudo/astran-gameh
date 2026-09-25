// frontend/src/avatar/templateGenerator.ts
// Deseneaza liniile ghid (regiuni + etichete) ale template-ului UV, direct pe un SkCanvas.
// Folosit atat pentru fundalul editorului de textura, cat si pentru exportul PNG "Download template".
import { Skia, SkCanvas, PaintStyle } from "@shopify/react-native-skia";
import { TEMPLATE_WIDTH, TEMPLATE_HEIGHT, regionsFor, Region } from "./uvTemplate";

export { TEMPLATE_WIDTH, TEMPLATE_HEIGHT };

let cachedFont: any = null;
function labelFont() {
  if (!cachedFont) {
    const typeface = Skia.Typeface.MakeDefault();
    cachedFont = Skia.Font(typeface, 13);
  }
  return cachedFont;
}

export function drawTemplateGuides(canvas: SkCanvas, kind: "shirt" | "pants") {
  const bgPaint = Skia.Paint();
  bgPaint.setColor(Skia.Color("#F4F4F4"));
  canvas.drawRect(Skia.XYWHRect(0, 0, TEMPLATE_WIDTH, TEMPLATE_HEIGHT), bgPaint);

  regionsFor(kind).forEach(r => drawRegionGuide(canvas, r));
}

function drawRegionGuide(canvas: SkCanvas, r: Region) {
  const linePaint = Skia.Paint();
  linePaint.setColor(Skia.Color("#B0B0B0"));
  linePaint.setStyle(PaintStyle.Stroke);
  linePaint.setStrokeWidth(2);
  canvas.drawRect(Skia.XYWHRect(r.x, r.y, r.w, r.h), linePaint);

  const font = labelFont();
  const text = r.label;
  const width = font.getTextWidth(text);
  const padX = 6, padY = 4, boxH = 18 + padY;
  const bx = r.x + r.w / 2 - (width + padX * 2) / 2;
  const by = r.y + 6;

  const boxPaint = Skia.Paint();
  boxPaint.setColor(Skia.Color("rgba(0,0,0,0.55)"));
  canvas.drawRect(Skia.XYWHRect(bx, by, width + padX * 2, boxH), boxPaint);

  const textPaint = Skia.Paint();
  textPaint.setColor(Skia.Color("#FFFFFF"));
  canvas.drawText(text, bx + padX, by + boxH / 2 + 4, textPaint, font);
}

// PNG doar cu liniile ghid (fara pictura userului) - pentru butonul "Download template".
export function renderTemplateGuidesPng(kind: "shirt" | "pants"): string {
  const surface = Skia.Surface.MakeOffscreen(TEMPLATE_WIDTH, TEMPLATE_HEIGHT)!;
  const canvas = surface.getCanvas();
  drawTemplateGuides(canvas, kind);
  surface.flush();
  const image = surface.makeImageSnapshot();
  const bytes = image.encodeToBytes();
  return `data:image/png;base64,${Skia.Data.fromBytes(bytes).base64()}`;
}