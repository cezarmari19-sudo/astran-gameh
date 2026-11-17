import { Platform } from "react-native";
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

/** Descarca arhiva ZIP REALA a utilizatorului curent. Web: fisier prin <a download>. Nativ (Android/iOS): se salveaza in
 *  cache si se deschide foaia de partajare/salvare. Intoarce "saved" (web) sau "shared" (nativ). */
export async function downloadArchive(filename: string, unavailableMsg: string, saveFailedMsg: string): Promise<"saved" | "shared"> {
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

  // Nativ: dependente incarcate la nevoie (pachetele sunt in package.json; modulul lipseste doar intr-un build vechi)
  let FS: any, Sharing: any;
  try {
    FS = require("expo-file-system/legacy");
    Sharing = require("expo-sharing");
  } catch {
    fail(0, saveFailedMsg);
  }
  const dir: string | null = FS.cacheDirectory;
  if (!dir) fail(0, saveFailedMsg);
  const target = `${dir}${filename}`;
  const res = await FS.downloadAsync(url, target, { headers });
  if (res.status !== 200) {
    try { await FS.deleteAsync(target, { idempotent: true }); } catch {}
    fail(res.status, res.status === 404 ? unavailableMsg : saveFailedMsg);
  }
  if (!(await Sharing.isAvailableAsync())) fail(0, saveFailedMsg);
  await Sharing.shareAsync(res.uri, { mimeType: "application/zip", UTI: "public.zip-archive", dialogTitle: filename });
  // arhiva contine date personale: nu ramane in cache dupa ce utilizatorul a ales unde o salveaza
  try { await FS.deleteAsync(res.uri, { idempotent: true }); } catch {}
  return "shared";
}