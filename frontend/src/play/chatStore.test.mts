// Teste pentru chatStore.ts. Rulare (Node >= 22.6, fara dependente):
//   node --experimental-strip-types --test src/play/chatStore.test.mts
import test from "node:test";
import assert from "node:assert/strict";
import * as S from "./chatStore.ts";

const msg = (id: string, ts: number, uid = "u1", text = "hi") => ({ id, ts, user_id: uid, username: uid, text });

test("mesajele sunt ordonate dupa (ts,id) chiar daca sosesc invers", () => {
  let s = S.initialChatState();
  s = S.applyMessage(s, msg("b", 20));
  s = S.applyMessage(s, msg("a", 10));
  s = S.applyMessage(s, msg("c", 20));
  assert.deepEqual(s.messages.map(m => m.id), ["a", "b", "c"]);
});

test("duplicatele (istoric + live) sunt ignorate", () => {
  let s = S.initialChatState();
  s = S.applyHistory(s, [msg("a", 1), msg("b", 2)]);
  s = S.applyMessage(s, msg("b", 2));
  s = S.applyHistory(s, [msg("a", 1), msg("b", 2), msg("c", 3)]);
  assert.deepEqual(s.messages.map(m => m.id), ["a", "b", "c"]);
  assert.equal(s.historyLoaded, true);
});

test("mesaje malformate de la server sunt ignorate", () => {
  let s = S.initialChatState();
  for (const bad of [null, 5, {}, { id: "x" }, { id: "x", ts: "1", user_id: "u", username: "u", text: "t" }, { id: "x", ts: NaN, user_id: "u", username: "u", text: "t" }, { id: "", ts: 1, user_id: "u", username: "u", text: "t" }]) {
    s = S.applyMessage(s, bad);
  }
  assert.equal(s.messages.length, 0);
  assert.equal(S.applyHistory(s, "nu-i lista").messages.length, 0);
});

test("ecoul cu cid inlocuieste mesajul pending", () => {
  let s = S.addPending(S.initialChatState(), "c1", "salut", 0);
  assert.equal(s.pending.length, 1);
  s = S.applyMessage(s, { ...msg("m1", 5, "me", "salut"), cid: "c1" });
  assert.equal(s.pending.length, 0);
  assert.equal(s.messages.length, 1);
});

test("eroare de la server cu cid marcheaza pending ca esuat; fara cid devine notificare", () => {
  let s = S.addPending(S.initialChatState(), "c1", "x", 0);
  s = S.applyServerError(s, "rate_limited", "c1", 1);
  assert.equal(s.pending[0].status, "failed");
  assert.equal(s.pending[0].error, "rate_limited");
  s = S.applyServerError(s, "unavailable", undefined, 2);
  assert.equal(s.notice?.code, "unavailable");
});

test("timeout: pending neconfirmat devine esuat dupa PENDING_TIMEOUT_MS, nu inainte", () => {
  let s = S.addPending(S.initialChatState(), "c1", "x", 1000);
  assert.equal(S.expirePending(s, 1000 + S.PENDING_TIMEOUT_MS - 1), s);
  s = S.expirePending(s, 1000 + S.PENDING_TIMEOUT_MS);
  assert.equal(s.pending[0].status, "failed");
  assert.equal(s.pending[0].error, "timeout");
});

test("pierderea conexiunii marcheaza mesajele in curs ca esuate", () => {
  let s = S.addPending(S.initialChatState(), "c1", "x", 0);
  s = S.failAllSending(S.setConnection(s, false), "offline");
  assert.equal(s.connected, false);
  assert.equal(s.pending[0].error, "offline");
});

test("plafon de mesaje pastrate", () => {
  let s = S.initialChatState();
  s = S.applyHistory(s, Array.from({ length: S.MAX_MESSAGES + 50 }, (_, i) => msg("m" + String(i).padStart(4, "0"), i)));
  assert.equal(s.messages.length, S.MAX_MESSAGES);
  assert.equal(s.messages[s.messages.length - 1].id, "m" + String(S.MAX_MESSAGES + 49).padStart(4, "0"));
});

test("blocarea locala ascunde imediat mesajele jucatorului", () => {
  let s = S.applyHistory(S.initialChatState(), [msg("a", 1, "x"), msg("b", 2, "y")]);
  s = S.blockLocal(s, "x");
  assert.deepEqual(S.visibleMessages(s).map(m => m.id), ["b"]);
});

test("normalizeInput: spatii, control, zero-width, bidi", () => {
  assert.equal(S.normalizeInput("  a\n\n b\t\u200bc\u202e  "), "a b c");
  assert.equal(S.normalizeInput("\u200b \n"), "");
});

test("setInfo: mod necunoscut -> null (informativ), lungime invalida -> implicit", () => {
  assert.equal(S.setInfo(S.initialChatState(), "adult_18", 150).mode, "adult_18");
  const s = S.setInfo(S.initialChatState(), "hacker", -5);
  assert.equal(s.mode, null);
  assert.equal(s.maxLen, S.DEFAULT_MAX_LEN);
});