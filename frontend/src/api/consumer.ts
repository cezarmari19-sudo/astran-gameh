import { api } from "./client";

// Oglindeste backend/astran_sandbox/consumer_routes.py. Pretul, textele confirmarilor si starea vin DOAR de la server.
export type ProductType = "astran_package" | "shop_item" | "clothes_item";
export type Checkout = {
  checkout_id: string; status: string; expires_at: string;
  product: { type: ProductType; id: string; name: string; description: string; price: string; currency: string; unit: string; additional_fees: string; delivery: string; seller?: { username?: string } | null };
  delivery_note: string; needs_consent: boolean;
  consent_texts: { A: { text: string; sha256: string }; B: { text: string; sha256: string } } | null;
  withdrawal: { days: number; info: string };
  terms: { terms_of_service_version: string; refund_policy_version: string };
  trader: { name?: string; address?: string; email?: string; phone?: string; registration_no?: string; vat_id?: string };
};
export type Order = {
  order_id: string; tx_id: string; product_name: string; price_display: string; currency: string; delivery: string; delivered_at: string; created_at: string;
  withdrawal_right: { status: "waived" | "preserved" }; terms_version: string; refund_policy_version: string;
  trader: Checkout["trader"]; email: { status: string }; consents?: { kind: string; text: string; created_at: string }[];
};
export type RefundRequest = {
  refund_id: string; tx_id: string; product_name?: string; tx_type: string; reason: string; explanation?: string | null; status: string; processing_state?: string;
  admin_response?: string | null; created_at: string; updated_at: string; history: { status: string; at: string }[];
  paid: { unit: string; amount_minor: number; currency: string }; refund?: { money_minor?: number; currency: string; astrans_returned_to_you?: number; astrans_reclaimed?: number } | null;
  user_id?: string;
};
export type RefundableTx = { tx_id: string; type: string; reference?: string; timestamp: string; amount: number; price?: number; currency: string; withdrawal_right_applies: boolean; withdrawal_deadline: string; waiver_valid: boolean; active_request?: { refund_id: string; status: string } | null };

const post = (path: string, body?: any) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const consumerApi = {
  createCheckout: (product_type: ProductType, product_id: string, language?: string) => post("/consumer/checkout", { product_type, product_id, language }) as Promise<Checkout>,
  confirmCheckout: (c: Checkout) => post(`/consumer/checkout/${c.checkout_id}/confirm`, {
    accept_immediate_performance: true, acknowledge_loss_of_withdrawal: true,
    presented_sha256: { A: c.consent_texts!.A.sha256, B: c.consent_texts!.B.sha256 },
  }) as Promise<Checkout>,
  document: (doc: string, lang: string) => api<{ text: string; version: string; sha256: string }>(`/consumer/documents/${doc}?lang=${lang}`),
  order: (id: string) => api<Order>(`/consumer/orders/${id}`),
  refundableTx: () => api<{ transactions: RefundableTx[] }>("/consumer/refunds/transactions"),
  createRefund: (tx_id: string, reason: string, explanation?: string) => post("/consumer/refunds", { tx_id, reason, explanation: explanation || null }) as Promise<RefundRequest>,
  myRefunds: () => api<{ requests: RefundRequest[] }>("/consumer/refunds"),
  myRefund: (id: string) => api<RefundRequest>(`/consumer/refunds/${id}`),
  admin: {
    list: (status?: string) => api<{ requests: RefundRequest[] }>(`/consumer/admin/refunds${status ? `?status=${status}` : ""}`),
    detail: (id: string) => api<any>(`/consumer/admin/refunds/${id}`),
    review: (id: string) => post(`/consumer/admin/refunds/${id}/review`),
    decide: (id: string, decision: "approve" | "reject", reasoning: string, override = false) => post(`/consumer/admin/refunds/${id}/decision`, { decision, reasoning, override }),
    execute: (id: string) => post(`/consumer/admin/refunds/${id}/execute`),
    confirmExternal: (id: string, external_ref: string) => post(`/consumer/admin/refunds/${id}/confirm-external`, { external_ref }),
  },
};

export const refundTone = (s: string): "ok" | "warn" | "bad" | "info" => (s === "refunded" || s === "approved" ? "ok" : s === "rejected" ? "bad" : s === "pending" ? "warn" : "info");
