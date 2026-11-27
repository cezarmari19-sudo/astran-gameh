import { api } from "./client";

// Oglindeste backend/astran_sandbox/{personal_data,merchant,incident}_routes.py si GET/DELETE din server.py (recomandari).
// NICIUN rezultat de aici nu e "de incredere": autorizarea, limitele si regulile de varsta se decid pe server.
const post = (path: string, body?: any) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });
const put = (path: string, body: any) => api(path, { method: "PUT", body: JSON.stringify(body) });

export type DobCorrectionStatus = {
  has_date_of_birth: boolean; applied_count: number; max_applied: number; can_request: boolean; next_request_at: string | null;
  pending: null | { request_id: string; required_threshold: 16 | 18; expires_at?: string; provider_available: boolean; next_action: string };
};
export type PersonalData = {
  email: string; email_verified: boolean; reauth_method: "password" | "email_code";
  pending_email_change: null | { new_email_masked: string; expires_at: string }; email_service_available: boolean;
  date_of_birth: { set: boolean; age_band: string | null; correction: DobCorrectionStatus };
};
export type Reauth = { password?: string; reauth_code?: string };

export const personalApi = {
  overview: () => api<PersonalData>("/account/personal-data"),
  sendReauthCode: () => post("/privacy/reauth/code"),     // serviciul de reautentificare EXISTENT (privacy_routes)
  requestEmailChange: (new_email: string, reauth: Reauth) => post("/account/email-change/request", { new_email, ...reauth }),
  cancelEmailChange: () => post("/account/email-change/cancel"),
  confirmEmail: (token: string) => post("/account/email-change/confirm", { token }),
  revertEmail: (token: string) => post("/account/email-change/revert", { token }),
  requestDobCorrection: (b: { date_of_birth: string; reason_code: string; reason_text?: string } & Reauth) => post("/account/dob-correction", b),
  cancelDobCorrection: () => post("/account/dob-correction/cancel"),
};

export type SellerInfo = {
  seller_status: "undeclared" | "non_trader" | "trader"; consumer_rights_notice: "non_trader" | null; verification_state?: string;
  trader: null | { name: string; trader_type: string; address: string; phone: string; email: string; registry: { name: string; number: string } | null; eu_compliance_statement: boolean };
};
export type TraderForm = {
  trader_type: "natural_person" | "company"; name: string; address: { line1: string; line2?: string; city: string; postal_code: string; country: string };
  phone: string; contact_email: string; registry: { register_name?: string; number?: string; not_applicable_reason?: string }; vat_id?: string; eu_compliance_statement: boolean;
};
export const sellerApi = {
  status: () => api("/seller/status"),
  declare: (b: { seller_status: "non_trader" | "trader"; assessment: Record<string, boolean>; trader?: TraderForm }) => put("/seller/status", b),
};

export const merchantAdminApi = {
  list: (state?: string) => api(`/admin/merchants${state ? `?state=${encodeURIComponent(state)}` : ""}`),
  get: (userId: string) => api(`/admin/merchants/${encodeURIComponent(userId)}`),
  review: (userId: string, decision: string, note?: string) => post(`/admin/merchants/${encodeURIComponent(userId)}/review`, { decision, note: note || undefined }),
  reclassify: (userId: string, reason: string) => post(`/admin/merchants/${encodeURIComponent(userId)}/reclassify`, { reason }),
};

const I = "/admin/incidents";
export const incidentApi = {
  me: () => api(`${I}/me`),
  summary: () => api(`${I}/summary`),
  list: (status?: string) => api(`${I}${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  create: (b: any) => post(I, b),
  get: (id: string) => api(`${I}/${id}`),
  patch: (id: string, b: any) => api(`${I}/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  assess: (id: string, b: any) => post(`${I}/${id}/assessment`, b),
  decide: (id: string, b: any) => post(`${I}/${id}/authority/decision`, b),
  approve: (id: string) => post(`${I}/${id}/authority/approve`),
  draft: (id: string, b: any) => post(`${I}/${id}/authority/draft`, b),
  submission: (id: string, b: any) => post(`${I}/${id}/authority/submission`, b),
  subjectsDecision: (id: string, b: any) => post(`${I}/${id}/data-subjects/decision`, b),
  subjectsRecord: (id: string, b: any) => post(`${I}/${id}/data-subjects/communication`, b),
  close: (id: string, b: any) => post(`${I}/${id}/close`, b),
  audit: (id: string) => api(`${I}/${id}/audit`),
};

export const recommendationApi = {
  info: () => api("/games/recommendation-info"),
  getSetting: async (): Promise<boolean> => !!(await api("/settings/me"))?.settings?.personalized_recommendations,
  setSetting: (on: boolean) => put("/settings/me", { settings: { personalized_recommendations: on } }),
  clearHistory: () => api("/games/recently-played", { method: "DELETE" }),
};
