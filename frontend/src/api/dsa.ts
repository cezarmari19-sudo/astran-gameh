import { api, publicApiUrl } from "./client";

// DSA (Art. 9/10/15/16/24(5)). Toata logica si autorizarea sunt pe server (backend/astran_sandbox/dsa_*.py, authority_orders.py);
// clientul doar trimite si afiseaza. Rutele /dsa/* si /moderation/admin/* refuza fara rol, indiferent ce arata UI-ul.
const j = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });
const e = encodeURIComponent;
const qs = (o: Record<string, any>) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${e(k)}=${e(String(v))}`);
  return p.length ? `?${p.join("&")}` : "";
};

export type PublicNoticeInput = {
  content_url?: string; account_username?: string; category: string; explanation: string; alleged_legal_basis?: string;
  reporter_name?: string; reporter_email?: string; identity_exempt?: boolean; good_faith_declaration: boolean;
};
export type OrderInput = {
  order_type: "art9_illegal_content" | "art10_information";
  authority: { name: string; member_state: string; contact_email?: string; their_reference?: string };
  legal_basis?: string; statement_of_reasons?: string; issued_at?: string; response_deadline?: string; territorial_scope?: string;
  redress_information: boolean; non_disclosure?: { requested: boolean; basis?: string };
  targets: { url?: string; account_username?: string }[]; requested_items?: string[];
};

export const dsaApi = {
  // public (fara cont)
  publicNotice: (b: PublicNoticeInput) => api<{ report_id: string; tracking_token: string; receipt: any }>("/moderation/public/notices", j(b)),
  publicNoticeStatus: (report_id: string, token: string) => api<any>("/moderation/public/notices/status", j({ report_id, token })),
  transparencyList: () => api<{ reports: any[] }>("/transparency/reports"),
  transparencyGet: (id: string) => api<any>(`/transparency/reports/${e(id)}`),
  transparencyCsvUrl: (id: string) => publicApiUrl(`/transparency/reports/${e(id)}/csv`),
  transparencyJsonUrl: (id: string) => publicApiUrl(`/transparency/reports/${e(id)}`),
  // personal
  ownInitiative: (b: { target_type: string; target_id: string; category: string; summary: string; automated_detection: boolean }) =>
    api<{ report_id: string }>("/moderation/admin/own-initiative", j(b)),
  deliveries: (status?: string) => api<{ items: any[]; counts: Record<string, number> }>(`/moderation/admin/deliveries${qs({ status })}`),
  retryDelivery: (id: string) => api(`/moderation/admin/deliveries/${e(id)}/retry`, { method: "POST" }),
  applicability: () => api<any>("/dsa/applicability"),
  transparencyPreview: (year: number) => api<any>(`/dsa/transparency/preview${qs({ year })}`),
  // admin
  transparencyPublish: (start: string, end: string) => api<any>("/dsa/transparency/publish", j({ start, end })),
  tdb: (status?: string) => api<any>(`/dsa/tdb${qs({ status })}`),
  tdbExport: (sor_ids?: string[]) => api<any>("/dsa/tdb/export", j({ sor_ids: sor_ids ?? null })),
  tdbConfirm: (id: string, external_reference: string) => api<any>(`/dsa/tdb/${e(id)}/confirm-manual`, j({ external_reference })),
  tdbCategory: (id: string, category: string) => api<any>(`/dsa/tdb/${e(id)}/category`, j({ category })),
  tdbRetry: (id: string) => api<any>(`/dsa/tdb/${e(id)}/retry`, { method: "POST" }),
  // juridic: ordine Art. 9/10
  orders: (status?: string) => api<{ items: any[]; counts: Record<string, number> }>(`/dsa/orders${qs({ status })}`),
  createOrder: (b: OrderInput) => api<any>("/dsa/orders", j(b)),
  order: (id: string) => api<any>(`/dsa/orders/${e(id)}`),
  authenticity: (id: string, verified: boolean, method: string) => api(`/dsa/orders/${e(id)}/authenticity`, j({ verified, method })),
  legalReview: (id: string, decision: "approve" | "reject" | "request_information", reasoning: string, confirm_non_disclosure = false) =>
    api(`/dsa/orders/${e(id)}/legal-review`, j({ decision, reasoning, confirm_non_disclosure })),
  executeOrder: (id: string) => api<any>(`/dsa/orders/${e(id)}/execute`, { method: "POST" }),
  prepareInformation: (id: string) => api<any>(`/dsa/orders/${e(id)}/information`, { method: "POST" }),
  packageDelivered: (id: string, note: string) => api(`/dsa/orders/${e(id)}/package-delivered`, j({ note })),
  releaseNotice: (id: string, reason: string) => api(`/dsa/orders/${e(id)}/release-notice`, j({ reason })),
  respond: (id: string, kind: "receipt" | "effect" | "rejection") => api(`/dsa/orders/${e(id)}/responses`, j({ kind })),
  closeOrder: (id: string, note: string) => api(`/dsa/orders/${e(id)}/close`, j({ note })),
  attach: (id: string, filename: string, data_b64: string) => api(`/dsa/orders/${e(id)}/attachments`, j({ filename, data_b64, kind: "order_document" })),
};
