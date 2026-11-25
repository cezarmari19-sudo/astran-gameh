import { api } from "./client";

// Oglindeste endpointurile din backend/astran_sandbox/age_routes.py. NICIUN rezultat de aici nu e "de incredere":
// serverul decide varsta, accesul 18+ si starea acordului parental la fiecare cerere.
export type ParentalState =
  | "PENDING_PARENTAL_CONSENT" | "PARENTAL_CONSENT_APPROVED" | "PARENTAL_CONSENT_REJECTED"
  | "PARENTAL_CONSENT_WITHDRAWN" | "PARENTAL_VERIFICATION_REQUIRED";

export type ParentalStatus = {
  consent_id: string; state: ParentalState; requested_at: string; expires_at?: string; approved_at?: string | null;
  parent_email_hint?: string; delivery?: "sent" | "failed" | "not_configured" | "not_attempted";
  verification_status?: string; verification_method?: string | null;
} | null;

export type AgeState = {
  age_band: "under_16" | "16_17" | "18_plus" | null;
  age_category: "under_18" | "adult_18";
  adult_content_allowed: boolean;
  account_state: "ACTIVE" | "AGE_INFO_REQUIRED" | "PARENTAL_CONSENT_REQUIRED" | "AGE_VERIFICATION_REQUIRED";
  restricted: boolean; needs_age_info: boolean;
  limited: boolean; consent_eligibility: "verified" | "parental" | "unverified"; consent_features_allowed: boolean; parental_option: boolean;
  parental_state: ParentalState | null;
  age_assurance: "none" | "pending" | "verified" | "failed";
  next_action: string | null;
  parental: ParentalStatus;
  age_assurance_available: boolean; parental_verification_available: boolean; email_delivery_available: boolean;
};

const post = (path: string, body: unknown) => api(path, { method: "POST", body: JSON.stringify(body) });

export const ageApi = {
  me: () => api<AgeState>("/age/me"),
  setDateOfBirth: (date_of_birth: string) => post("/age/date-of-birth", { date_of_birth }),
  requestParent: (parent_email: string, language: string) => post("/age/parental/request", { parent_email, language }),
  ownConsent: (language: string) => post("/age/own-consent", { language }),
  // `threshold` e doar o preferinta: serverul decide ce prag e permis (16-17 => 16, 18+ => 18) si il leaga de sesiune.
  startAssurance: (threshold?: 16 | 18) => post("/age/assurance/start", threshold ? { threshold } : {}) as Promise<{ status: string; redirect_url?: string | null; provider?: string; threshold?: number }>,
};

export type Acks = { read_notice: boolean; is_parent_or_guardian: boolean; understands_withdrawal: boolean };

export const parentalApi = {
  notice: (token: string, language?: string) => post("/parental/notice", { token, language }),
  decide: (token: string, decision: "approve" | "reject", acknowledgements: Acks) => post("/parental/decision", { token, decision, acknowledgements }),
  withdrawByToken: (token: string) => post("/parental/withdraw-by-token", { token }),
  children: () => api("/parental/children"),
  claim: (claim_token: string) => post("/parental/claim", { claim_token }),
  panelDecision: (id: string, decision: "approve" | "reject", acknowledgements: Acks) => post(`/parental/children/${encodeURIComponent(id)}/decision`, { decision, acknowledgements }),
  restartVerification: (id: string) => post(`/parental/children/${encodeURIComponent(id)}/verification/restart`, {}),
  withdraw: (id: string) => post(`/parental/children/${encodeURIComponent(id)}/withdraw`, {}),
  dataRequest: (id: string, kind: "access" | "erasure", confirm = false) => post(`/parental/children/${encodeURIComponent(id)}/data-request`, { kind, confirm }),
};
