import { api } from "./client";

// GDPR: consimtamant demonstrabil. Toata logica (versiuni, data UTC, validare) este pe server
// (backend/astran_sandbox/consent*.py). Clientul doar afiseaza textele servite de server si trimite ce a ales utilizatorul;
// NU trimite versiuni sau date ca dovada - `presented_versions` e doar comparat de server cu versiunea curenta.

export type LegalDocType = "terms_of_service" | "privacy_policy";

export type LegalDocInfo = {
  doc_type: LegalDocType;
  legal_basis: "contract" | "information_notice";
  current_version: string;
  effective_date: string | null;
  requires_reacceptance: boolean;
  sha256: string | null;
  language: string;
};

export type LegalDocText = {
  doc_type: LegalDocType;
  version: string;
  language: string;
  sha256: string;
  text: string;
  is_current: boolean;
};

export type ConsentDocument = {
  subject: LegalDocType;
  kind: "document";
  legal_basis: "contract" | "information_notice";
  current_version: string;
  accepted_version: string | null;
  accepted_at: string | null;
  status: "current" | "acceptance_required" | "update_notice";
  withdrawable: false;
  change_summary: string | null;
};

export type ConsentPurpose = {
  subject: string;
  kind: "purpose";
  legal_basis: "consent";
  version: string;
  title: string;
  description: string;
  consequences: string;
  state: "granted" | "withdrawn" | "never_granted";
  granted_version: string | null;
  granted_at: string | null;
  withdrawn_at: string | null;
  eligible: boolean;
  withdrawable: boolean;
  requires_confirmation: boolean;
};

export type ConsentOverview = {
  documents: ConsentDocument[];
  purposes: ConsentPurpose[];
  pending_acceptance: LegalDocType[];
  notices: { subject: LegalDocType; version: string; summary: string | null }[];
};

export type ConsentEvent = {
  event_id: string;
  subject: string;
  subject_type: "document" | "purpose";
  legal_basis: string;
  action: "accepted" | "withdrawn";
  version: string;
  document_id: string;
  document_sha256: string | null;
  document_language: string | null;
  created_at: string;
  source: string;
};

export const consentApi = {
  documents: (lang: string) => api<{ documents: LegalDocInfo[] }>(`/legal/documents?lang=${encodeURIComponent(lang)}`),
  document: (type: LegalDocType, lang: string, version?: string) =>
    api<LegalDocText>(`/legal/documents/${type}?lang=${encodeURIComponent(lang)}${version ? `&version=${encodeURIComponent(version)}` : ""}`),
  me: (lang: string) => api<ConsentOverview>(`/consent/me?lang=${encodeURIComponent(lang)}`),
  history: () => api<{ events: ConsentEvent[] }>("/consent/history"),
  acceptDocuments: (documents: LegalDocType[], presented_versions: Record<string, string>, language: string) =>
    api<ConsentOverview>("/consent/documents/accept", { method: "POST", body: JSON.stringify({ documents, presented_versions, language }) }),
  grant: (purpose: string, language: string) =>
    api<ConsentOverview>(`/consent/purposes/${encodeURIComponent(purpose)}/grant`, { method: "POST", body: JSON.stringify({ language }) }),
  withdraw: (purpose: string, confirm: boolean, language: string) =>
    api<ConsentOverview>(`/consent/purposes/${encodeURIComponent(purpose)}/withdraw`, { method: "POST", body: JSON.stringify({ confirm, language }) }),
};