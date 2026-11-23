// Texte pentru drepturile consumatorilor (checkout, confirmare comanda, rambursari, panou admin).
// Aceeasi conventie ca consentStrings.ts / modStrings.ts: ro + en, orice alta limba cade pe engleza.
// Textele confirmarilor A/B si documentul politicii vin de la SERVER (versionate, cu amprenta), nu de aici.
const RO = {
  checkout_title: "Confirmă achiziția", product: "Produs", description: "Descriere", price: "Preț total", fees: "Taxe suplimentare",
  fees_none: "Nu percepem taxe suplimentare; prețul afișat este prețul total.", delivery: "Livrare", trader: "Comerciant",
  terms: "Termeni și condiții de utilizare", refund_policy: "Politica de rambursare și dreptul de retragere",
  withdrawal: "Dreptul de retragere", confirm_hint: "Bifează ambele confirmări pentru a continua. Sunt obligatorii pentru livrarea imediată.",
  buy_now: "Confirm și cumpăr", cancel: "Anulează", confirming: "Se înregistrează…", loading: "Se încarcă…", retry: "Reîncearcă",
  err_generic: "A apărut o eroare. Încearcă din nou.", err_consent: "Confirmările sunt obligatorii înainte de achiziție.",
  err_price_changed: "Prețul s-a schimbat. Reîncarcă achiziția și verifică noul preț.", err_expired: "Sesiunea de achiziție a expirat. Începe din nou.",
  err_trader: "Achizițiile sunt momentan indisponibile (datele comerciantului nu sunt configurate).", err_texts_changed: "Textele s-au schimbat. Reîncarcă și citește-le din nou.",
  order_title: "Confirmarea comenzii", order_no: "Număr comandă", tx_no: "Număr tranzacție", date: "Data și ora", price_paid: "Preț plătit",
  waived: "Ai cerut expres furnizarea imediată și ai confirmat pierderea dreptului de retragere (art. 16 lit. (m)). Confirmările sunt salvate pe server.",
  preserved: "Dreptul tău de retragere de 14 zile se păstrează pentru această achiziție.", your_confirmations: "Confirmările tale înregistrate",
  email_sent: "Confirmarea a fost trimisă pe e-mail.", email_failed: "Nu am putut trimite e-mailul; confirmarea rămâne disponibilă aici.",
  email_none: "Trimiterea pe e-mail nu este disponibilă acum; confirmarea rămâne disponibilă aici.", done: "Gata",
  request_refund: "Cere rambursare", refund_requests: "Cereri de rambursare", new_request: "Cerere nouă", my_requests: "Cererile mele",
  no_requests: "Nu ai cereri de rambursare.", select_tx: "Alege tranzacția", no_tx: "Nu ai tranzacții care pot fi rambursate prin acest sistem.",
  reason: "Motiv", explanation: "Explicație (opțional)", send: "Trimite cererea", sent_ok: "Cererea a fost trimisă.",
  status: "Stare", admin_response: "Răspunsul administratorului", no_response: "Încă nu există un răspuns.", history: "Istoric",
  refund_amount: "Sumă rambursată", reason_withdrawal: "Mă retrag din contract (14 zile)", reason_not_as_described: "Nu corespunde descrierii",
  reason_defective: "Defect / nu funcționează", reason_not_received: "Nu am primit conținutul", reason_unauthorized_purchase: "Achiziție neautorizată",
  reason_duplicate_charge: "Plată dublă", reason_other: "Alt motiv",
  st_pending: "În așteptare", st_under_review: "În analiză", st_approved: "Aprobată", st_rejected: "Respinsă", st_refunded: "Rambursată",
  ps_awaiting_store_refund: "Se așteaptă rambursarea de către magazinul de aplicații", ps_failed_retryable: "Procesare întreruptă, se reîncearcă",
  withdrawal_applies: "Dreptul de retragere se aplică până la", withdrawal_waived: "Dreptul de retragere a fost pierdut (confirmări valide)",
  withdrawal_expired: "Termenul de retragere de 14 zile a expirat", active_request: "Cerere activă",
  err_duplicate: "Există deja o cerere activă pentru această tranzacție.", err_not_refundable: "Acest tip de tranzacție nu poate fi rambursat prin acest sistem (ex.: Astrans primiți gratuit, câștigați sau transferați).",
  err_already_refunded: "Tranzacția a fost deja rambursată.", err_too_many: "Prea multe cereri pentru această tranzacție. Contactează comerciantul.",
  policy_title: "Politica de rambursare", policy_open: "Politica de rambursare", tos_open: "Termeni și condiții",
  adm_title: "Cereri de rambursare", adm_detail: "Cerere de rambursare", adm_all: "Toate", adm_tx: "Tranzacția originală", adm_order: "Comanda",
  adm_consents: "Confirmările utilizatorului", adm_usage: "Utilizare", adm_plan: "Plan de rambursare", adm_audit: "Jurnal de audit",
  adm_review: "Începe analiza", adm_approve: "Aprobă", adm_reject: "Respinge", adm_reasoning: "Motivul deciziei (obligatoriu)",
  adm_override: "Aprobă ca excepție (retragere / rambursare integrală) – cere motiv", adm_execute: "Inițiază rambursarea prin procesator",
  adm_ext_ref: "Referința rambursării din magazin", adm_confirm_ext: "Confirmă rambursarea făcută în magazin", adm_user_balance: "Sold utilizator",
  adm_content_accessed: "Conținut accesat de cumpărător", adm_not_accessed: "Conținut neaccesat", adm_done: "Acțiune efectuată.",
  plan_reclaim: "Astrans retrași", plan_money: "Sumă de rambursat (unități minore)", plan_unrecovered: "Astrans deja cheltuiți (nerambursați)",
  plan_buyer_credit: "Astrans returnați cumpărătorului", plan_seller: "Retras de la vânzător", plan_absorbed: "Suportat de platformă",
  confirm_action: "Confirmi acțiunea?", yes: "Da",
};
type K = keyof typeof RO;
const EN: Record<K, string> = {
  checkout_title: "Confirm purchase", product: "Product", description: "Description", price: "Total price", fees: "Additional fees",
  fees_none: "We charge no additional fees; the price shown is the total price.", delivery: "Delivery", trader: "Trader",
  terms: "Terms of use", refund_policy: "Refund policy and right of withdrawal",
  withdrawal: "Right of withdrawal", confirm_hint: "Tick both confirmations to continue. They are required for immediate delivery.",
  buy_now: "Confirm and buy", cancel: "Cancel", confirming: "Recording…", loading: "Loading…", retry: "Retry",
  err_generic: "Something went wrong. Please try again.", err_consent: "The confirmations are required before purchase.",
  err_price_changed: "The price changed. Reload the purchase and check the new price.", err_expired: "The purchase session expired. Start again.",
  err_trader: "Purchases are currently unavailable (merchant details are not configured).", err_texts_changed: "The texts changed. Reload and read them again.",
  order_title: "Order confirmation", order_no: "Order number", tx_no: "Transaction number", date: "Date and time", price_paid: "Price paid",
  waived: "You expressly requested immediate supply and acknowledged losing the right of withdrawal (Art. 16(m)). The confirmations are stored on the server.",
  preserved: "Your 14-day right of withdrawal is preserved for this purchase.", your_confirmations: "Your recorded confirmations",
  email_sent: "The confirmation was sent by email.", email_failed: "We could not send the email; the confirmation stays available here.",
  email_none: "Email delivery is not available right now; the confirmation stays available here.", done: "Done",
  request_refund: "Request refund", refund_requests: "Refund requests", new_request: "New request", my_requests: "My requests",
  no_requests: "You have no refund requests.", select_tx: "Select the transaction", no_tx: "You have no transactions that can be refunded through this system.",
  reason: "Reason", explanation: "Explanation (optional)", send: "Submit request", sent_ok: "Your request was submitted.",
  status: "Status", admin_response: "Administrator's response", no_response: "No response yet.", history: "History",
  refund_amount: "Refund amount", reason_withdrawal: "I withdraw from the contract (14 days)", reason_not_as_described: "Not as described",
  reason_defective: "Defective / does not work", reason_not_received: "I did not receive the content", reason_unauthorized_purchase: "Unauthorized purchase",
  reason_duplicate_charge: "Duplicate charge", reason_other: "Other",
  st_pending: "Pending", st_under_review: "Under Review", st_approved: "Approved", st_rejected: "Rejected", st_refunded: "Refunded",
  ps_awaiting_store_refund: "Waiting for the app store to process the refund", ps_failed_retryable: "Processing interrupted, will be retried",
  withdrawal_applies: "Right of withdrawal applies until", withdrawal_waived: "Right of withdrawal was lost (valid confirmations)",
  withdrawal_expired: "The 14-day withdrawal period has expired", active_request: "Active request",
  err_duplicate: "There is already an active request for this transaction.", err_not_refundable: "This kind of transaction cannot be refunded through this system (e.g. free, earned or transferred Astrans).",
  err_already_refunded: "This transaction was already refunded.", err_too_many: "Too many requests for this transaction. Contact the trader.",
  policy_title: "Refund policy", policy_open: "Refund policy", tos_open: "Terms of use",
  adm_title: "Refund requests", adm_detail: "Refund request", adm_all: "All", adm_tx: "Original transaction", adm_order: "Order",
  adm_consents: "User confirmations", adm_usage: "Usage", adm_plan: "Refund plan", adm_audit: "Audit log",
  adm_review: "Start review", adm_approve: "Approve", adm_reject: "Reject", adm_reasoning: "Reason for the decision (required)",
  adm_override: "Approve as exception (withdrawal / full refund) – requires reason", adm_execute: "Initiate refund through processor",
  adm_ext_ref: "Store refund reference", adm_confirm_ext: "Confirm refund done in the store", adm_user_balance: "User balance",
  adm_content_accessed: "Content accessed by buyer", adm_not_accessed: "Content not accessed", adm_done: "Done.",
  plan_reclaim: "Astrans reclaimed", plan_money: "Money to refund (minor units)", plan_unrecovered: "Astrans already spent (not refunded)",
  plan_buyer_credit: "Astrans returned to buyer", plan_seller: "Reclaimed from seller", plan_absorbed: "Absorbed by the platform",
  confirm_action: "Confirm this action?", yes: "Yes",
};
export type CrKey = K;
export const CR_KEYS: string[] = Object.keys(RO);
export function cr(lang: string, key: CrKey): string {
  return ((lang === "ro" ? RO : EN)[key]) ?? EN[key] ?? key;
}
export function crErr(lang: string, e: any): string {
  const code = e?.code ?? e?.detail?.code ?? "";
  const map: Record<string, CrKey> = {
    consumer_consent_required: "err_consent", confirmations_required: "err_consent", price_changed: "err_price_changed", checkout_expired: "err_expired",
    trader_info_missing: "err_trader", texts_changed: "err_texts_changed", duplicate_request: "err_duplicate", transaction_not_refundable: "err_not_refundable",
    already_refunded: "err_already_refunded", too_many_requests: "err_too_many",
  };
  return map[code] ? cr(lang, map[code]) : (e?.message && typeof e.message === "string" && !/^\{/.test(e.message) ? e.message : cr(lang, "err_generic"));
}
