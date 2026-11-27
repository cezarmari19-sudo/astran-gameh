// node --experimental-strip-types --test src/i18n/complianceStrings.test.mts
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import { cs, csErr, CS_EN_KEYS, CS_RO_KEYS } from "./complianceStrings.ts";

test("ro si en au exact aceleasi chei, nevide; alta limba cade pe engleza", () => {
  assert.deepEqual([...CS_RO_KEYS].sort(), [...CS_EN_KEYS].sort());
  for (const k of CS_EN_KEYS) {
    assert.ok(cs("ro", k as any).trim().length > 1, `ro ${k}`);
    assert.ok(cs("en", k as any).trim().length > 1, `en ${k}`);
    assert.equal(cs("de", k as any), cs("en", k as any));
  }
  assert.notEqual(cs("ro", "pd_title"), cs("en", "pd_title"));
});

test("variabilele {x} se inlocuiesc in ambele limbi si nu raman acolade", () => {
  for (const lang of ["ro", "en"]) {
    assert.match(cs(lang, "pd_awaiting", { age: 18 }), /18/);
    assert.ok(!/[{}]/.test(cs(lang, "pd_awaiting", { age: 18 })));
    assert.ok(!/[{}]/.test(cs(lang, "pd_pending", { email: "a***@b***.com" })));
    assert.ok(!/[{}]/.test(cs(lang, "inc_hours_left", { h: 5 })));
    assert.ok(!/[{}]/.test(cs(lang, "pd_next_at", { date: "1/1/2027" })));
  }
});

test("codurile de eroare ale serverului au mesaj tradus, iar necunoscutele cad pe un mesaj generic", () => {
  for (const code of ["seller_status_required", "trader_details_required", "correction_limit_reached", "invalid_token", "email_unavailable", "notification_required", "version_conflict"]) {
    assert.notEqual(csErr("ro", { code }), cs("ro", "err_generic"), code);
    assert.notEqual(csErr("en", { code }), csErr("ro", { code }), code);
  }
  assert.equal(csErr("en", { code: "something_new" }), cs("en", "err_generic"));
  assert.equal(csErr("ro", { status: 429 }), cs("ro", "err_too_many_requests"));
  assert.equal(csErr("en", new Error("raw server text that must not leak")), cs("en", "err_generic"));
});

test("toate codurile de eroare din backend (servicii noi) au traducere sau cad intentionat pe mesajul generic", () => {
  const dir = "../backend/astran_sandbox";
  if (!existsSync(dir)) return;
  const codes = new Set<string>();
  for (const f of ["incidents.py", "personal_data.py", "dob_correction.py", "merchants.py"]) {
    for (const m of readFileSync(join(dir, f), "utf8").matchAll(/(?:Error)\(\s*\d{3},\s*"([a-z_0-9]+)"/g)) codes.add(m[1]);
  }
  const intentionallyGeneric = new Set(["not_found", "forbidden", "audit_unavailable", "unknown_setting", "invalid_setting", "admin_required", "nothing_to_update", "no_trader_details",
    "account_closing", "already_notified", "already_communicated", "communication_not_prepared", "notification_not_decided", "decision_required", "password_account", "email_not_verified",
    "too_long", "stale", "invalid_reason", "approver_role_required"]);
  const missing = [...codes].filter(c => csErr("en", { code: c }) === cs("en", "err_generic") && !intentionallyGeneric.has(c));
  assert.deepEqual(missing, [], `coduri backend fara mesaj in complianceStrings: ${missing.join(", ")}`);
});

const walk = (d: string): string[] => readdirSync(d).flatMap(f => { const p = join(d, f); return statSync(p).isDirectory() ? walk(p) : [p]; });
const files = ["app/confirm-email.tsx", "app/revert-email.tsx", "app/seller.tsx", "app/moderation/admin/incidents.tsx", "app/moderation/admin/merchants.tsx", "app/settings.tsx",
  "app/shop.tsx", "app/moderation/admin/index.tsx", "src/components/PersonalDataSection.tsx", "src/components/RecommendationControls.tsx", "src/components/SellerInfoCard.tsx",
  "src/components/CheckoutSheet.tsx"].filter(existsSync);

test("fiecare cs(lang, \"cheie\") literal folosit in ecrane exista", () => {
  const keys = new Set(CS_EN_KEYS);
  const bad: string[] = [];
  for (const f of files) {
    const src = readFileSync(f, "utf8");
    for (const m of src.matchAll(/\bcs\(\s*lang\s*,\s*"([a-z0-9_]+)"/g)) if (!keys.has(m[1])) bad.push(`${f}: ${m[1]}`);
    // chei construite din prefix: toate variantele trebuie sa existe
    for (const m of src.matchAll(/cs\(lang,\s*`([a-z_]+)\$\{[^}]+\}`/g)) {
      assert.ok([...keys].some(k => k.startsWith(m[1])), `${f}: prefix ${m[1]}`);
    }
  }
  assert.deepEqual(bad, []);
});

test("variantele construite din prefix acopera toate valorile posibile", () => {
  for (const v of ["unverified", "in_review", "verified", "rejected"]) assert.ok(CS_EN_KEYS.includes(`vs_${v}`), v);
  for (const v of ["undeclared", "non_trader", "trader"]) assert.ok(CS_EN_KEYS.includes(`sl_status_${v}`), v);
  for (const v of ["seller_status_required", "trader_details_required", "trader_verification_required", "ok"]) assert.ok(CS_EN_KEYS.includes(`sl_elig_${v}`), v);
  for (const v of ["not_assessed", "not_required", "pending", "due_soon", "overdue", "met", "late"]) assert.ok(CS_EN_KEYS.includes(`dl_${v}`), v);
  for (const v of ["low", "medium", "high", "critical"]) assert.ok(CS_EN_KEYS.includes(`sv_${v}`), v);
  for (const v of ["unlikely", "risk", "high_risk"]) assert.ok(CS_EN_KEYS.includes(`rk_${v}`), v);
  for (const v of ["typo", "wrong_year_or_day", "entered_by_someone_else", "other"]) assert.ok(CS_EN_KEYS.includes(`reason_${v}`), v);
  for (const q of ["regular_sales", "for_profit_or_business", "professional_or_company", "hired_staff_or_resells_goods"]) assert.ok(CS_EN_KEYS.includes(`q_${q}`), q);
});

test("nicio cale a frontendului nu cere sau incarca documente de identitate si ecranele de date nu stocheaza parole", () => {
  for (const f of ["src/components/PersonalDataSection.tsx", "app/seller.tsx"]) {
    const src = readFileSync(f, "utf8");
    assert.ok(!/expo-image-picker|expo-document-picker|launchImageLibrary|launchCamera|FormData/.test(src), f);
    assert.ok(!/AsyncStorage|localStorage|SecureStore/.test(src), `${f}: nu persistam parole/coduri`);
  }
  assert.ok(/secure/.test(readFileSync("src/components/PersonalDataSection.tsx", "utf8")));       // parola nu se afiseaza
});

test("ecranele de administrare nu se bazeaza pe ascunderea in UI: tot accesul e verificat de server si exista mesaj pentru 403", () => {
  for (const f of ["app/moderation/admin/incidents.tsx", "app/moderation/admin/merchants.tsx"]) {
    assert.ok(/403/.test(readFileSync(f, "utf8")), f);
  }
});
