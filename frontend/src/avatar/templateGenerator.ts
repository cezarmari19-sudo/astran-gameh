// frontend/src/avatar/templateGenerator.ts
// Genereaza template-ul UV gol (585x559) ca data URL PNG: fundal alb, regiuni delimitate
// cu linii si etichete, exact ca ghidul clasic Roblox de tricou/pantalon. Userul il descarca,
// picteaza peste el intr-un editor extern (sau foloseste tool-ul de vopsit din aplicatie),
// apoi il incarca inapoi.
//
// Foloseste un <canvas> HTML (functioneaza si in React Native web si, prin react-native-canvas
// / expo, poate fi portat; aici presupunem executie in contextul WebGL/Canvas din editorul de
// textura, care ruleaza pe un <canvas> DOM-like oferit de expo-gl / skia - vezi textureEditor.ts).
import { TEMPLATE_WIDTH, TEMPLATE_HEIGHT, regionsFor, Region } from "./uvTemplate";

const BG = "#F4F4F4";
const LINE = "#B0B0B0";
const LABEL_BG = "rgba(0,0,0,0.55)";
const LABEL_TEXT = "#FFFFFF";

// Deseneaza template-ul gol pe un context 2D deja creat (canvas 585x559).
// Functie pura de desen, ca sa poata fi refolosita atat pentru generarea PNG-ului de descarcat
// cat si ca fundal in editorul de vopsit (unde userul picteaza peste liniile ghid).
export function drawTemplate(ctx: any, kind: "shirt" | "pants") {
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, TEMPLATE_WIDTH, TEMPLATE_HEIGHT);

  const regions = regionsFor(kind);
  regions.forEach(r => drawRegion(ctx, r));
}

function drawRegion(ctx: any, r: Region) {
  ctx.strokeStyle = LINE;
  ctx.lineWidth = 2;
  ctx.strokeRect(r.x, r.y, r.w, r.h);

  // eticheta: fundal semi-transparent + text alb, centrat sus in regiune
  const text = r.label;
  ctx.font = "600 13px sans-serif";
  const metrics = ctx.measureText(text);
  const padX = 6, padY = 4;
  const boxW = metrics.width + padX * 2;
  const boxH = 18 + padY;
  const bx = r.x + r.w / 2 - boxW / 2;
  const by = r.y + 6;

  ctx.fillStyle = LABEL_BG;
  ctx.fillRect(bx, by, boxW, boxH);
  ctx.fillStyle = LABEL_TEXT;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillText(text, r.x + r.w / 2, by + boxH / 2);
}

// Genereaza data URL-ul PNG folosind un canvas offscreen din DOM (functioneaza pe web si in
// React Native prin react-native-webview/expo cand e nevoie; pentru Expo Go native folosim
// react-native-view-shot pe un <Canvas> - vezi TextureEditor.tsx pentru implementarea reala
// de randare pe ecran). Aceasta functie e utilizata direct doar in contextul web/testare.
export function generateTemplateDataUrl(kind: "shirt" | "pants"): string | null {
  if (typeof document === "undefined") return null; // pe native, generarea se face in TextureEditor via Skia canvas
  const canvas = document.createElement("canvas");
  canvas.width = TEMPLATE_WIDTH;
  canvas.height = TEMPLATE_HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  drawTemplate(ctx, kind);
  return canvas.toDataURL("image/png");
}