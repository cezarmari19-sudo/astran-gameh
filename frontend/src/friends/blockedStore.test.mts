// Teste pentru blockedStore.ts + verificari statice ale ecranului Friends. Rulare (Node >= 22.6, fara dependente):
//   node --experimental-strip-types --test src/friends/blockedStore.test.mts
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as S from "./blockedStore.ts";

const u = (id: string, extra: object = {}) => ({ user_id: id, username: id, ...extra });

test("normalizeBlocked: pastreaza doar randuri valide, fara duplicate, si campurile necesare UI-ului", () => {
  const out = S.normalizeBlocked({ blocked: [u("a", { display_name: "A", avatar_url: "x", email: "a@x", password_hash: "s" }), u("a"), { user_id: 5 }, null, u("b")] });
  assert.deepEqual(out.map((x) => x.user_id), ["a", "b"]);
  assert.deepEqual(Object.keys(out[0]).sort(), ["avatar_url", "display_name", "user_id", "username"]); // nu propaga campuri straine
  assert.deepEqual(S.normalizeBlocked(null), []);
  assert.deepEqual(S.normalizeBlocked({}), []);
  assert.deepEqual(S.normalizeBlocked({ blocked: "nope" }), []);
});

test("removeBlocked: scoate doar utilizatorul deblocat, imutabil; id necunoscut => aceeasi lista", () => {
  const l = S.normalizeBlocked({ blocked: [u("a"), u("b"), u("c")] });
  const r = S.removeBlocked(l, "b");
  assert.deepEqual(r.map((x) => x.user_id), ["a", "c"]);
  assert.equal(l.length, 3);
  assert.equal(S.removeBlocked(l, "zzz"), l);
  assert.deepEqual(S.removeBlocked(S.removeBlocked(r, "a"), "c"), []); // lista goala dupa ultimul
});

test("not_blocked (404) e tratat ca deja deblocat; alte erori nu", () => {
  assert.equal(S.isAlreadyUnblocked("not_blocked", 404), true);
  assert.equal(S.isAlreadyUnblocked(undefined, 404), true);
  assert.equal(S.isAlreadyUnblocked(undefined, 429), false);
  assert.equal(S.isAlreadyUnblocked(undefined, 400), false);
  assert.equal(S.isAlreadyUnblocked(undefined, 500), false);
  assert.equal(S.isAlreadyUnblocked(undefined, 0), false);
});

test("unblockErrorKey: offline / rate limit / generic", () => {
  assert.equal(S.unblockErrorKey(undefined, 0), "dm_err_offline");
  assert.equal(S.unblockErrorKey(undefined, 429), "unblock_rate_limited");
  assert.equal(S.unblockErrorKey(undefined, 400), "unblock_failed");
  assert.equal(S.unblockErrorKey("x", 500), "unblock_failed");
});

// ---- i18n: ro + en (singurele limbi suportate de proiect: LangCode = "ro" | "en") --------------------------------
const i18n = readFileSync("src/i18n/index.ts", "utf8");
const roStart = i18n.indexOf("\n  ro: {"), enStart = i18n.indexOf("\n  en: {");
const dict = (from: number, to: number) => i18n.slice(from, to);
const RO = dict(roStart, enStart), EN = dict(enStart, i18n.indexOf("detectDeviceLang"));

test("limbile suportate sunt exact ro si en", () => {
  assert.match(i18n, /export type LangCode = "ro" \| "en";/);
});

test("fiecare cheie noua exista in ro si en, cu text nevid, iar ro != en", () => {
  for (const k of [...S.BLOCKED_I18N_KEYS, "dm_err_offline", "try_again", "cancel"]) {
    const re = new RegExp(`\\b${k}: "([^"]+)"`);
    const ro = RO.match(re)?.[1], en = EN.match(re)?.[1];
    assert.ok(ro && ro.length > 2, `ro ${k}`);
    assert.ok(en && en.length > 2, `en ${k}`);
    if (k.startsWith("unblock") || k.startsWith("blocked")) assert.notEqual(ro, en, k);
    assert.equal((i18n.match(new RegExp(`\\b${k}:`, "g")) || []).length, 2, `${k} definit o singura data per limba`);
  }
});

// ---- ecranul Friends: apeluri catre endpoint-urile reale + comportamentul cerut ------------------------------
const screen = readFileSync("app/(tabs)/friends.tsx", "utf8");

test("friends.tsx apeleaza endpoint-urile corecte (GET /friends/blocked, POST /friends/unblock cu target_user_id)", () => {
  assert.match(screen, /api\("\/friends\/blocked"\)/);
  assert.match(screen, /api\("\/friends\/unblock",\s*\{\s*method:\s*"POST",\s*body:\s*JSON\.stringify\(\{\s*target_user_id:\s*userId\s*\}\)\s*\}\)/);
  assert.doesNotMatch(screen, /unblock[^\n]*\/friends\/(request|accept)/); // deblocarea nu cheama cerere/acceptare
});

test("deblocarea cere confirmare, are loading/succes/eroare/lista goala si nu reincarca tot ecranul", () => {
  assert.match(screen, /setConfirmId\(item\.user_id\)/);            // 1) tap pe „Deblocheaza” doar cere confirmarea
  assert.match(screen, /testID=\{`unblock-confirm-\$\{item\.user_id\}`\}[^>]*onPress=\{\(\) => unblock\(item\.user_id\)\}/);   // 2) apelul real doar la confirmare
  assert.doesNotMatch(screen, /testID=\{`unblock-\$\{item\.user_id\}`\}[^>]*onPress=\{\(\) => unblock\(/);
  for (const k of ["unblock_success", "unblock_failed", "blocked_users_empty", "blocked_load_failed", "unblock_confirm_body"]) {
    assert.ok(screen.includes(`t("${k}")`) || screen.includes(`t(unblockErrorKey`) && k === "unblock_failed", k);
  }
  assert.match(screen, /blocked-loading/);                           // loading
  assert.match(screen, /removeBlocked\(l, userId\)/);                // lista se actualizeaza local
  const start = screen.indexOf("async function unblock(");
  const fn = screen.slice(start, screen.indexOf("\n  }\n", start));   // doar corpul functiei unblock
  assert.doesNotMatch(fn, /await load\(\)/);                         // fara reimprospatarea listelor de prieteni/cereri
  assert.match(fn, /if \(busyId\) return/);                          // anti dublu-tap
});

test("textele noi din ecran nu sunt hardcodate: folosesc t(...)", () => {
  const block = screen.slice(screen.indexOf('tab === "blocked" ? ('), screen.indexOf(") : item.contact_restricted"));
  assert.doesNotMatch(block, />\s*[A-Za-z][A-Za-z ]+\s*</);          // niciun text literal intre taguri
});
