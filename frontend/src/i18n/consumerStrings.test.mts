// node --experimental-strip-types --test src/i18n/consumerStrings.test.mts
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { cr, crErr, CR_KEYS } from "./consumerStrings.ts";

test("ro si en au text pentru aceleasi chei; alta limba cade pe engleza", () => {
  const keys = ["checkout_title", "buy_now", "waived", "preserved", "st_pending", "st_under_review", "st_approved", "st_rejected", "st_refunded",
    "reason_withdrawal", "reason_defective", "adm_title", "policy_title", "err_consent"] as const;
  for (const k of keys) {
    assert.ok(cr("ro", k).length > 3, `ro ${k}`);
    assert.ok(cr("en", k).length > 3, `en ${k}`);
    assert.equal(cr("de", k), cr("en", k), `fallback ${k}`);
  }
  assert.notEqual(cr("ro", "buy_now"), cr("en", "buy_now"));
});

test("codurile de eroare ale serverului au mesaj tradus", () => {
  assert.equal(crErr("ro", { code: "consumer_consent_required" }), cr("ro", "err_consent"));
  assert.equal(crErr("en", { code: "duplicate_request" }), cr("en", "err_duplicate"));
  assert.equal(crErr("en", { code: "price_changed" }), cr("en", "err_price_changed"));
  assert.equal(crErr("en", {}), cr("en", "err_generic"));
});

const walk = (d: string): string[] => readdirSync(d).flatMap(f => { const p = join(d, f); return statSync(p).isDirectory() ? walk(p) : [p]; });
const files = [...walk("app/refunds"), ...walk("app/order"), "app/moderation/admin/refunds.tsx", ...walk("app/moderation/admin/refund"), "app/refund-policy.tsx",
  "app/wallet.tsx", "app/settings.tsx", "app/moderation/admin/index.tsx", "src/components/CheckoutSheet.tsx", "src/components/ConsumerDocModal.tsx"];

test("fiecare cr(lang, \"cheie\") literal folosit in ecrane exista", () => {
  const missing: string[] = [];
  for (const f of files) for (const m of readFileSync(f, "utf8").matchAll(/\bcr\(lang,\s*"([a-z_0-9]+)"\)/g)) if (!CR_KEYS.includes(m[1])) missing.push(`${f}: ${m[1]}`);
  assert.deepEqual(missing, []);
});

test("familiile dinamice (reason_*, st_*, ps_*) sunt complete", () => {
  for (const r of ["withdrawal", "not_as_described", "defective", "not_received", "unauthorized_purchase", "duplicate_charge", "other"]) assert.ok(CR_KEYS.includes(`reason_${r}`), r);
  for (const st of ["pending", "under_review", "approved", "rejected", "refunded"]) assert.ok(CR_KEYS.includes(`st_${st}`), st);
  for (const ps of ["awaiting_store_refund", "failed_retryable"]) assert.ok(CR_KEYS.includes(`ps_${ps}`), ps);
});
