import { api } from "./client";

// DSA: moderare. Toate verificarile (rol, proprietate, termene) sunt pe server (backend/astran_sandbox/moderation_*.py);
// clientul doar trimite si afiseaza. `role` e folosit DOAR ca sa arate/ascunda un meniu - rutele /admin refuza oricum fara rol.
const j = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });
const qs = (o: Record<string, any>) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return p.length ? `?${p.join("&")}` : "";
};

export type Receipt = {
  report_id: string; received_at: string; kind: string; is_dsa_notice: boolean; target_type: string; target_id: string;
  reported_content: { location: string; summary: string | null }; status: string; received: boolean; track: { path: string };
};
export type NoticeInput = {
  target_type: "user" | "game" | "message"; target_id: string; category: string; explanation: string; alleged_legal_basis?: string;
  content_location?: string; reporter_name?: string; reporter_email?: string; identity_exempt?: boolean; good_faith_declaration: boolean;
};

export const modApi = {
  // utilizator
  report: (body: { target_type: string; target_id: string; reason: string; details?: string; category?: string }) =>
    api<{ report_id: string; receipt: Receipt }>("/reports", j(body)),
  notice: (body: NoticeInput) => api<{ report_id: string; receipt: Receipt }>("/moderation/notices", j(body)),
  myReports: () => api<{ reports: any[] }>("/moderation/me/reports"),
  myReport: (id: string) => api<any>(`/moderation/me/reports/${encodeURIComponent(id)}`),
  notifications: () => api<{ notifications: any[] }>("/moderation/me/notifications"),
  readNotification: (id: string) => api(`/moderation/me/notifications/${encodeURIComponent(id)}/read`, { method: "POST" }),
  myDecisions: () => api<{ decisions: any[] }>("/moderation/me/decisions"),
  myDecision: (id: string) => api<any>(`/moderation/me/decisions/${encodeURIComponent(id)}`),
  appeal: (decisionId: string, body: { grounds: string; evidence_text?: string; evidence_urls?: string[] }) =>
    api<any>(`/moderation/me/decisions/${encodeURIComponent(decisionId)}/appeals`, j(body)),
  myAppeals: () => api<{ appeals: any[] }>("/moderation/me/appeals"),
  role: () => api<{ role: "admin" | "moderator" | null }>("/moderation/me/role"),
  // contact (public)
  contactInfo: () => api<any>("/contact/info"),
  contactSend: (body: { channel: string; name?: string; email: string; subject: string; message: string }) =>
    api<{ message_id: string; received_at: string }>("/contact/messages", j(body)),
  // personal
  adminReports: (f: Record<string, any>) => api<{ items: any[]; total: number; page: number; page_size: number }>(`/moderation/admin/reports${qs(f)}`),
  adminReport: (id: string) => api<any>(`/moderation/admin/reports/${encodeURIComponent(id)}`),
  assign: (id: string, assignee_id: string) => api(`/moderation/admin/reports/${encodeURIComponent(id)}/assign`, j({ assignee_id })),
  setStatus: (id: string, status: string, reasoning?: string) => api(`/moderation/admin/reports/${encodeURIComponent(id)}/status`, j({ status, reasoning })),
  addNote: (id: string, text: string) => api(`/moderation/admin/reports/${encodeURIComponent(id)}/notes`, j({ text })),
  assess: (id: string, state: "sufficient" | "insufficient", reasoning: string) => api(`/moderation/admin/reports/${encodeURIComponent(id)}/assessment`, j({ state, reasoning })),
  decide: (id: string, body: Record<string, any>) => api<any>(`/moderation/admin/reports/${encodeURIComponent(id)}/decisions`, j(body)),
  correct: (decisionId: string, text: string) => api(`/moderation/admin/decisions/${encodeURIComponent(decisionId)}/corrections`, j({ text })),
  audit: (f: { report_id?: string; decision_id?: string }) => api<{ events: any[] }>(`/moderation/admin/audit${qs(f)}`),
  auditVerify: () => api<{ ok: boolean; checked: number; broken_at_seq: number | null }>("/moderation/admin/audit/verify"),
  adminAppeals: (status?: string) => api<{ appeals: any[] }>(`/moderation/admin/appeals${qs({ status })}`),
  adminAppeal: (id: string) => api<any>(`/moderation/admin/appeals/${encodeURIComponent(id)}`),
  startReview: (id: string) => api(`/moderation/admin/appeals/${encodeURIComponent(id)}/start-review`, { method: "POST" }),
  resolveAppeal: (id: string, outcome: "maintain" | "reverse", reasoning: string) => api(`/moderation/admin/appeals/${encodeURIComponent(id)}/resolve`, j({ outcome, reasoning })),
  contactMessages: () => api<{ messages: any[] }>("/moderation/admin/contact-messages"),
};
