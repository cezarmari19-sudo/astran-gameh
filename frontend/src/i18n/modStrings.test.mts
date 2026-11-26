import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { MS_EN_KEYS, MS_RO_KEYS, ms, modErr } from "./modStrings.ts";

const walk = (d: string): string[] => readdirSync(d).flatMap(f => { const p = join(d, f); return statSync(p).isDirectory() ? walk(p) : [p]; });
const files = [...walk("app/moderation"), "src/components/ReportSheet.tsx", "app/settings.tsx"].filter(f => /\.tsx$/.test(f));

test("RO and EN dictionaries have identical keys", () => {
  assert.deepEqual([...MS_EN_KEYS].sort(), [...MS_RO_KEYS].sort());
});

test("every literal ms(lang, \"key\") used by moderation screens exists", () => {
  const missing: string[] = [];
  for (const f of files) {
    for (const m of readFileSync(f, "utf8").matchAll(/\bms\(lang,\s*"([a-z_0-9]+)"\)/g)) if (!MS_EN_KEYS.includes(m[1])) missing.push(`${f}: ${m[1]}`);
  }
  assert.deepEqual(missing, []);
});

test("dynamic key families used by the screens are complete", () => {
  const fam: Record<string, string[]> = {
    status_: ["pending", "under_review", "resolved", "rejected"],
    kind_: ["rule_violation", "illegal_content_notice", "own_initiative", "authority_order"],
    cat_: ["illegal_content", "child_safety", "harassment", "hate_speech", "violence", "sexual_content", "self_harm", "scam_fraud", "ip_infringement", "spam", "other"],
    type_: ["game", "user", "message"],
    act_: ["no_action", "close_resolved", "warn", "remove_content_illegal", "remove_content_policy", "restrict_content", "restrict_account_temporary", "suspend_account_permanent"],
    appeal_: ["received", "under_review", "decision_maintained", "decision_reversed", "decision_modified"],
    dec_: ["in_effect", "reversed", "applying", "failed", "modified"],
    ord_status_: ["incomplete", "pending_legal_review", "approved", "rejected", "executed", "execution_failed", "information_prepared", "closed"],
    tdb_st_: ["pending", "submitted", "submitted_manual", "failed", "abandoned", "rejected", "blocked_pii", "needs_review", "exported", "needs_reconciliation", "submitting"],
    del_: ["pending", "sending", "sent", "failed", "abandoned", "skipped_no_contact"],
    pub_outcome_: ["action_taken", "no_action", "close_resolved"],
    con_ch_: ["user", "authority", "commission_or_dsc", "trusted_flagger", "other"],
  };
  for (const [p, vs] of Object.entries(fam)) for (const v of vs) assert.ok(MS_EN_KEYS.includes(p + v), `missing ${p}${v}`);
});

test("server error codes map to localized text and unknown codes fall back to the raw message", () => {
  for (const code of ["complaint_period_expired", "reviewer_not_independent", "good_faith_required", "account_suspended"]) {
    assert.notEqual(modErr("ro", { code }), modErr("en", { code }));
  }
  assert.equal(modErr("en", { code: "zzz", message: "raw" }), "raw");
  assert.equal(ms("ro", "mod_terms"), "Termeni și condiții");
});

test("screens never claim legal compliance", () => {
  for (const k of MS_EN_KEYS) assert.ok(!/compliant|fully compliant|certified/i.test(ms("en", k as any)), k);
});
