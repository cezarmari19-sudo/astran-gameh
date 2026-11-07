// Teste pentru dmStore.ts. Rulare (Node >= 22.6, fara dependente):
//   node --experimental-strip-types --test src/dm/dmStore.test.mts
import test from "node:test";
import assert from "node:assert/strict";
import * as S from "./dmStore.ts";

const msg = (id: string, ts: number, conv = "c1", mine = false, text = "hi"): S.DmMsg =>
  ({ id, conversation_id: conv, sender_id: mine ? "me" : "friend", ts, text, mine });
const conv = (id: string, updated: number, unread = 0, last: S.DmMsg | null = null): S.DmConversation => ({
  conversation_id: id, friend: { user_id: "f" + id, username: "f" + id }, last_message: last, unread, can_send: true, updated_at: updated,
});

test("mesajele sunt ordonate cele mai noi primele, dupa (ts,id), chiar daca sosesc invers", () => {
  let l = S.mergeMessages([], [msg("b", 20), msg("a", 10)]);
  l = S.mergeMessages(l, [msg("c", 20)]);
  assert.deepEqual(l.map((m) => m.id), ["c", "b", "a"]);
});

test("duplicatele (istoric + live + raspunsul trimiterii) sunt ignorate", () => {
  let l = S.mergeMessages([], [msg("a", 1), msg("b", 2)]);
  l = S.mergeMessages(l, [msg("b", 2)]);
  l = S.mergeMessages(l, [msg("a", 1), msg("b", 2), msg("c", 3)]);
  assert.deepEqual(l.map((m) => m.id), ["c", "b", "a"]);
});

test("paginarea adauga mesaje vechi la capat fara sa le piarda pe cele noi", () => {
  let l = S.mergeMessages([], [msg("m5", 5), msg("m4", 4)]);
  l = S.mergeMessages(l, [msg("m6", 6)]); // live intre timp
  l = S.mergeMessages(l, [msg("m3", 3), msg("m2", 2)]); // pagina veche
  assert.deepEqual(l.map((m) => m.id), ["m6", "m5", "m4", "m3", "m2"]);
});

test("mesaj primit: ultimul mesaj, necitite +1, conversatia urca in capul listei", () => {
  const list = [conv("c1", 100, 0, msg("x", 100)), conv("c2", 50, 3, msg("y", 50, "c2"))];
  const r = S.applyIncoming(list, msg("z", 200, "c2"), null);
  assert.equal(r.needsRefresh, false);
  assert.deepEqual(r.list.map((c) => c.conversation_id), ["c2", "c1"]);
  assert.equal(r.list[0].unread, 4);
  assert.equal(r.list[0].last_message?.id, "z");
  assert.equal(r.list[0].updated_at, 200);
});

test("mesaj in conversatia deschisa sau trimis de mine NU creste necititele", () => {
  const list = [conv("c1", 100, 0)];
  assert.equal(S.applyIncoming(list, msg("a", 200, "c1"), "c1").list[0].unread, 0);
  assert.equal(S.applyIncoming(list, msg("b", 200, "c1", true), null).list[0].unread, 0);
  assert.equal(S.applyIncoming(list, msg("c", 200, "c1"), "altaConv").list[0].unread, 1);
});

test("acelasi mesaj livrat de doua ori nu dubleaza necititele", () => {
  let r = S.applyIncoming([conv("c1", 100)], msg("a", 200, "c1"), null);
  r = S.applyIncoming(r.list, msg("a", 200, "c1"), null);
  assert.equal(r.list[0].unread, 1);
});

test("conversatie necunoscuta (primul mesaj cu un prieten) cere reincarcarea listei de la server", () => {
  const r = S.applyIncoming([conv("c1", 100)], msg("a", 200, "nou"), null);
  assert.equal(r.needsRefresh, true);
  assert.equal(r.list.length, 1);
});

test("markConversationRead si totalUnread", () => {
  let l = [conv("c1", 1, 2), conv("c2", 2, 5)];
  assert.equal(S.totalUnread(l), 7);
  l = S.markConversationRead(l, "c2");
  assert.equal(S.totalUnread(l), 2);
  assert.deepEqual(S.markConversationRead(l, "nu-exista"), l); // id necunoscut: nimic nu se schimba
});

test("bule in curs de trimitere: esuat apoi rezolvat; retrimiterea cu acelasi cid nu dubleaza", () => {
  let p = S.addPending([], { cid: "k1", text: "salut", status: "sending" });
  p = S.failPending(p, "k1", "rate_limited");
  assert.equal(p[0].status, "failed");
  assert.equal(p[0].error, "rate_limited");
  p = S.addPending(p, { cid: "k1", text: "salut", status: "sending" });
  assert.equal(p.length, 1);
  assert.deepEqual(S.resolvePending(p, "k1"), []);
});

test("codurile de eroare ale serverului se mapeaza pe chei i18n; retea cazuta = offline", () => {
  assert.equal(S.errorKey("too_long"), "dm_err_too_long");
  assert.equal(S.errorKey("rate_limited"), "dm_err_rate_limited");
  assert.equal(S.errorKey("not_found"), "dm_err_not_allowed");
  assert.equal(S.errorKey(undefined, 0), "dm_err_offline");
  assert.equal(S.errorKey("offline"), "dm_err_offline");
  assert.equal(S.errorKey(undefined, 500), "dm_err_unavailable");
  assert.equal(S.isPermanentError("too_long"), true);
  assert.equal(S.isPermanentError("rate_limited"), false);
});

test("formatTime: azi=ora, ieri=eticheta, altfel data", () => {
  const now = new Date(2026, 10, 7, 15, 0, 0).getTime();
  assert.match(S.formatTime(new Date(2026, 10, 7, 9, 5).getTime(), now, { yesterday: "Ieri" }, "en-GB"), /^09:05$/);
  assert.equal(S.formatTime(new Date(2026, 10, 6, 23, 59).getTime(), now, { yesterday: "Ieri" }), "Ieri");
  assert.match(S.formatTime(new Date(2026, 10, 1, 12).getTime(), now, { yesterday: "Ieri" }, "en-GB"), /^01\/11$/);
});

test("newClientId e unic si are cel mult 40 de caractere", () => {
  const ids = new Set(Array.from({ length: 500 }, () => S.newClientId()));
  assert.equal(ids.size, 500);
  for (const id of ids) assert.ok(id.length <= 40);
});