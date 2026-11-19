// Texte UI pentru consimtamant (RO/EN). Textele juridice in sine vin de la server (/api/legal/documents/*).
type S = Record<string, string>;
const RO: S = {
  accept_terms_pre: "Accept ", terms_link: "Termenii și Condițiile",
  ack_privacy_pre: "Am luat la cunoștință ", privacy_link: "Politica de Confidențialitate",
  marketing_optional: "Vreau să primesc comunicări de marketing prin email (opțional). Pot refuza și folosi Astran în continuare.",
  docs_load_failed: "Nu am putut încărca documentele juridice. Încearcă din nou.",
  doc_changed: "Documentele juridice au fost actualizate. Citește versiunea curentă și bifează din nou.",
  retry: "Reîncearcă", close: "Închide", loading: "Se încarcă…",
  version: "Versiune", cm_title: "Gestionare consimțământ", cm_intro: "Aici vezi ce ai acceptat, ce versiune și când, și poți retrage consimțămintele opționale. Retragerea nu șterge contul.",
  cm_docs: "Documente", cm_optional: "Consimțăminte opționale", cm_history: "Istoric", cm_history_hide: "Ascunde istoricul", cm_history_show: "Arată istoricul",
  accepted_on: "Acceptat la", status_current: "La zi", status_accept: "Necesită acceptare", status_notice: "Actualizat (doar informare)",
  basis_contract: "Acord contractual", basis_notice: "Informare (nu este consimțământ)", not_withdrawable: "Nu este un consimțământ retractabil. Dacă nu mai vrei să respecți termenii, poți șterge contul mai jos.",
  state_granted: "Acordat", state_withdrawn: "Retras", state_never: "Neacordat", grant: "Acordă", withdraw: "Retrage",
  withdraw_title: "Retragi consimțământul?", withdraw_confirm: "Retrage consimțământul", cancel: "Anulează",
  withdrawn_ok: "Consimțământul a fost retras. Prelucrările viitoare bazate pe el se opresc.", granted_ok: "Consimțământul a fost înregistrat.",
  not_available: "Indisponibil pentru contul tău.", read: "Citește", action_accepted: "Acceptat", action_withdrawn: "Retras",
  col_doc: "Document", col_version: "Versiune", col_action: "Acțiune", col_date: "Data (UTC)",
  gate_title: "Acceptare necesară", gate_intro: "Pentru a continua, citește și acceptă versiunea curentă a documentelor de mai jos.",
  gate_accept: "Accept și continui", gate_notice_title: "Documente actualizate",
  google_consent_title: "Creezi un cont nou", google_consent_intro: "Pentru a crea contul cu Google trebuie să accepți documentele de mai jos.",
  continue: "Continuă",
};
const EN: S = {
  accept_terms_pre: "I accept the ", terms_link: "Terms of Service",
  ack_privacy_pre: "I have read the ", privacy_link: "Privacy Policy",
  marketing_optional: "I want to receive marketing emails (optional). I can decline and still use Astran.",
  docs_load_failed: "Could not load the legal documents. Please try again.",
  doc_changed: "The legal documents were updated. Read the current version and tick the boxes again.",
  retry: "Retry", close: "Close", loading: "Loading…",
  version: "Version", cm_title: "Consent Management", cm_intro: "See what you accepted, which version and when, and withdraw optional consents. Withdrawing does not delete your account.",
  cm_docs: "Documents", cm_optional: "Optional consents", cm_history: "History", cm_history_hide: "Hide history", cm_history_show: "Show history",
  accepted_on: "Accepted on", status_current: "Up to date", status_accept: "Acceptance required", status_notice: "Updated (notice only)",
  basis_contract: "Contractual acceptance", basis_notice: "Information notice (not a consent)", not_withdrawable: "Not a withdrawable consent. If you no longer want to be bound by the terms, you can delete your account below.",
  state_granted: "Granted", state_withdrawn: "Withdrawn", state_never: "Not granted", grant: "Grant", withdraw: "Withdraw",
  withdraw_title: "Withdraw consent?", withdraw_confirm: "Withdraw consent", cancel: "Cancel",
  withdrawn_ok: "Consent withdrawn. Future processing based on it stops.", granted_ok: "Consent recorded.",
  not_available: "Not available for your account.", read: "Read", action_accepted: "Accepted", action_withdrawn: "Withdrawn",
  col_doc: "Document", col_version: "Version", col_action: "Action", col_date: "Date (UTC)",
  gate_title: "Acceptance required", gate_intro: "To continue, read and accept the current version of the documents below.",
  gate_accept: "Accept and continue", gate_notice_title: "Documents updated",
  google_consent_title: "Creating a new account", google_consent_intro: "To create your account with Google you must accept the documents below.",
  continue: "Continue",
};
export function cs(lang: string, key: keyof typeof EN): string {
  return ((lang === "ro" ? RO : EN) as S)[key] ?? (EN as S)[key] ?? key;
}
export function docTitle(lang: string, doc: string): string {
  return doc === "terms_of_service" ? cs(lang, "terms_link") : cs(lang, "privacy_link");
}