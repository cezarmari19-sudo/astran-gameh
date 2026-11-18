import { Platform, Share } from "react-native";
import { api, authedRequestInfo } from "@/src/api/client";

// GDPR Art. 15 / 20: export ZIP asincron. Oglindeste backend/astran_sandbox/account_export_routes.py.
// Toate verificarile (re-autentificare, apartenenta, expirare, limite) se fac pe server; nu exista niciun id in URL.

export type ExportStatus = "processing" | "ready" | "expired" | "failed";

export type ExportView = {
  export_id: string;
  status: ExportStatus;
  requested_at: string | null;
  generated_at: string | null;
  expires_at: string | null;
  size_bytes: number | null;
  downloads_left: number | null;
  error_code: string | null;
  legal_deadline_at: string | null;
};

export type ExportInfo = {
  export: ExportView | null;
  reauth: { has_password: boolean; email_masked: string | null; email_service_available: boolean };
  info: {
    archive_name: string;
    archive_ttl_seconds: number;
    max_downloads: number;
    files: string[];
    legal_response_days: number;
    legal_extension_months: number;
    contact_email: string | null;
  };
};

export const accountExportApi = {
  status: () => api<ExportInfo>("/account/data-export"),
  request: (body: { password?: string; code?: string }) =>
    api<{ created: boolean; export: ExportView }>("/account/data-export", {
      method: "POST",
      body: JSON.stringify({ ...body, confirm: true }),
    }),
};

function fail(status: number, message: string): never {
  const err = new Error(message);
  (err as any).status = status;
  throw err;
}

/** Descarca arhiva ZIP REALA a utilizatorului curent.
 *  - Web: fisier prin <a download>  -> "saved".
 *  - Android: utilizatorul alege un folder (Storage Access Framework), ZIP-ul se scrie acolo -> "saved"; daca anuleaza
 *    selectul de folder, NU se cere nimic de la server -> "cancelled" (nu se consuma o descarcare).
 *  - iOS: foaia de partajare / "Save to Files" -> "shared".
 *  Nu are nevoie de pachete native noi: expo-file-system e deja dependenta lui `expo` (SDK 54, in yarn.lock). */
export async function downloadArchive(
  filename: string, unavailableMsg: string, saveFailedMsg: string,
): Promise<"saved" | "shared" | "cancelled"> {
  const { url, headers } = await authedRequestInfo("/account/data-export/download");
  const g: any = globalThis as any;

  if (Platform.OS === "web" && g.document && g.Blob && g.URL) {
    const res = await fetch(url, { headers });
    if (!res.ok) fail(res.status, res.status === 404 ? unavailableMsg : saveFailedMsg);
    const blob = await res.blob();
    const href = g.URL.createObjectURL(blob);
    const a = g.document.createElement("a");
    a.href = href;
    a.download = filename;
    g.document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => g.URL.revokeObjectURL(href), 1500);
    return "saved";
  }

  let FS: any;
  try {
    FS = require("expo-file-system/legacy");
  } catch {
    fail(0, saveFailedMsg);
  }
  const dir: string | null = FS.cacheDirectory;
  if (!dir) fail(0, saveFailedMsg);

  // Android: intai alegem folderul (daca anuleaza, nu atingem serverul)
  let safDir: string | null = null;
  if (Platform.OS === "android") {
    const perm = await FS.StorageAccessFramework.requestDirectoryPermissionsAsync();
    if (!perm?.granted) return "cancelled";
    safDir = perm.directoryUri;
  }

  const target = `${dir}${filename}`;
  const cleanup = async () => { try { await FS.deleteAsync(target, { idempotent: true }); } catch {} };
  try {
    const res = await FS.downloadAsync(url, target, { headers });
    if (res.status !== 200) fail(res.status, res.status === 404 ? unavailableMsg : saveFailedMsg);

    if (safDir) {
      const b64: string = await FS.readAsStringAsync(res.uri, { encoding: FS.EncodingType.Base64 });
      const dest: string = await FS.StorageAccessFramework.createFileAsync(safDir, filename.replace(/\.zip$/i, ""), "application/zip");
      await FS.writeAsStringAsync(dest, b64, { encoding: FS.EncodingType.Base64 });
      return "saved";
    }
    // iOS: foaia de partajare accepta un URL de fisier local
    await Share.share({ url: res.uri });
    return "shared";
  } finally {
    await cleanup();     // arhiva contine date personale: nu ramane in cache
  }
}