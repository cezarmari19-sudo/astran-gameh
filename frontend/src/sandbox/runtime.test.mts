// Teste pentru runtime-ul de scripturi client. Ruleaza codul REAL: runtime.ts, pagina-gazda,
// worker-ul si Luau compilat in WASM (fara mock-uri pentru VM).
//   cd frontend && node --experimental-strip-types --import ./src/sandbox/register-alias.mjs --test src/sandbox/runtime.test.mts
import test from "node:test";
import assert from "node:assert/strict";
import { createHostPage, FakeWorker, runMessage } from "./testHarness.mts";
import { parseSandboxOutput, sanitizeOps, validateFiles, buildBundle } from "./runtime.ts";

let nextId = 1;
async function runViaPage(page: ReturnType<typeof createHostPage>, src: string | Record<string, string>, extra: any = {}) {
  const id = nextId++;
  page.deliver({ ...runMessage(src, extra), id });
  const res = await page.waitFor((m) => m.type === "result" && m.id === id);
  return { res, parsed: parseSandboxOutput(res.output, res.fatal) };
}

test("executa un script real si produce ops (create/set/world) - incl. viteza/saritura libere", async () => {
  const page = createHostPage();
  const { parsed } = await runViaPage(page, `
    print("hi")
    local p = Instance.new("Part"); p.Name = "Cub"; p.Position = Vector3.new(1,2,3); p.Parent = workspace
    workspace.JumpSpeed = 90; workspace.MoveSpeed = 55; workspace.Gravity = -3
    task.wait(0.5)
    p.Position = Vector3.new(9,9,9)
  `);
  assert.equal(parsed.ok, true, JSON.stringify(parsed.errors));
  assert.deepEqual(parsed.output.map((o) => o.msg), ["hi"]);
  const world = parsed.ops.filter((o) => o.op === "world");
  assert.ok(world.some((o) => o.jump_speed === 90));
  assert.ok(world.some((o) => o.move_speed === 55), "fara plafon de gameplay pe viteza");
  assert.ok(parsed.ops.some((o) => o.op === "set" && o.t === 0.5 && o.x === 9));
});

test("eroare in script nu blocheaza: ok=false, mesaj cu linie, rularea urmatoare merge", async () => {
  const page = createHostPage();
  const a = await runViaPage(page, `error("boom")`);
  assert.equal(a.parsed.ok, false);
  assert.match(a.parsed.errors[0], /main:1: boom/);
  const b = await runViaPage(page, `print("dupa")`);
  assert.equal(b.parsed.ok, true);
});

test("bucla infinita e oprita (timeout) si pagina ramane utilizabila", async () => {
  const page = createHostPage();
  const a = await runViaPage(page, `while true do end`, { timeoutMs: 300 });
  assert.equal(a.parsed.ok, false);
  assert.match(a.parsed.errors.join(" "), /timed out/);
  const b = await runViaPage(page, `print("viu")`);
  assert.equal(b.parsed.ok, true);
});

test("worker blocat (nu raspunde deloc) e terminat de pagina-gazda", async () => {
  class HungWorker extends FakeWorker { postMessage() { /* nu raspunde niciodata */ } }
  const page = createHostPage({ workerFactory: (s) => new HungWorker(s) });
  const { res, parsed } = await runViaPage(page, `print(1)`, { timeoutMs: 50 });
  assert.equal(res.fatal, true);
  assert.match(parsed.errors.join(" "), /timed out/);
  assert.ok(FakeWorker.all.at(-1)!.terminated, "worker-ul trebuie terminate()");
});

test("izolare: lockdown scoate API-urile de retea/stocare din worker dupa initializarea WASM", async () => {
  const page = createHostPage();
  await runViaPage(page, `print(1)`);
  const w = FakeWorker.all.at(-1)!;
  for (const n of ["fetch", "XMLHttpRequest", "WebSocket", "importScripts", "indexedDB", "caches", "Worker"]) {
    assert.equal(w.typeofGlobal(n), "undefined", n + " trebuie sa fie blocat");
  }
});

test("izolare: Luau nu are io/loadstring/os.execute si bibliotecile sunt read-only", async () => {
  const page = createHostPage();
  const { parsed } = await runViaPage(page, `
    local function try(f) local ok = pcall(f) return ok end
    print(tostring(io), tostring(loadstring), tostring(load), tostring(dofile), tostring(os.execute))
    print(try(function() os.time = nil end), try(function() string.sub = nil end), try(function() workspace.X = 1 end))
    print(try(function() return require("../etc/passwd") end), typeof(js), typeof(window), typeof(fetch))
  `);
  assert.equal(parsed.output[0].msg, "nil\tnil\tnil\tnil\tnil");
  assert.equal(parsed.output[1].msg, "false\tfalse\tfalse");
  assert.equal(parsed.output[2].msg, "false\tnil\tnil\tnil");
});

test("izolare: scriptul nu poate umbri preludiul (task/error/pcall suprascrise nu rup runtime-ul)", async () => {
  const page = createHostPage();
  const { parsed } = await runViaPage(page, `
    pcall(function() coroutine.resume = nil end)
    pcall(function() _G.error = function() end end)
    getfenv().pcall = nil
    task.wait(0.1)
    print("ok")
  `);
  assert.deepEqual(parsed.output.map((o) => o.msg), ["ok"]);
});

test("sanitizeOps: respinge tipuri/id-uri/culori invalide, taie numere uriase, fara limite de gameplay", () => {
  const ops = sanitizeOps([
    { op: "evil", id: "a" },
    { op: "create", id: "bad id!" },
    { op: "create", id: "ok1", x: 1e30, color: "red", type: "torus" },
    { op: "world", id: "world", jump_speed: 500, move_speed: 200, gravity: -1 },
    { op: "set", id: "ok1", x: NaN, y: 3 },
  ]);
  assert.equal(ops.length, 3);
  assert.equal(ops[0].x, 1e9);
  assert.equal(ops[0].color, undefined);
  assert.equal(ops[0].type, undefined);
  assert.equal(ops[1].jump_speed, 500);
  assert.equal(ops[2].x, undefined);
});

test("validateFiles/buildBundle: nume rezervate, duplicate, depasiri", () => {
  assert.throws(() => validateFiles([{ name: "__scene", source: "" }]), /reserved/);
  assert.throws(() => validateFiles([{ name: "a", source: "" }, { name: "a", source: "" }]), /Duplicate/);
  assert.throws(() => validateFiles([{ name: "../x", source: "" }]), /Invalid script name/);
  assert.match(buildBundle([{ name: "main", source: "é" }]), /^@@FILE main 2\n/);
});

test("require intre fisiere + scena Studio expusa ca piese", async () => {
  const page = createHostPage();
  const { parsed } = await runViaPage(page, {
    main: `local u = require("utils") print(u.hi(), workspace.Cube1 ~= nil)`,
    utils: `return { hi = function() return "salut" end }`,
  }, { scene: { objects: [{ id: "o1", type: "cube", x: 1, y: 2, z: 3 }] } });
  assert.equal(parsed.ok, true, JSON.stringify(parsed.errors));
  assert.equal(parsed.output[0].msg, "salut\ttrue");
});

// ===================== canalul RN <-> pagina-gazda (HostChannel) =====================
import { HostChannel } from "@/src/sandbox/hostChannel";
import { mergeScriptOps, clientFilesFromProject, luauNameFor } from "@/src/sandbox/clientRun";
import { pickEntry } from "./runtime.ts";

type Rig = { channel: HostChannel; page: ReturnType<typeof createHostPage>; restarts: number; posted: number };
function makeRig(o: { deferFirstLoad?: boolean; neverLoad?: boolean; loadTimeoutMs?: number; watchdogGraceMs?: number; dropPosts?: boolean; workerFactory?: (s: string) => any } = {}): Rig {
  const rig: any = { restarts: 0, posted: 0 };
  const mk = (defer: boolean) => createHostPage({
    deferLoad: defer, workerFactory: o.workerFactory,
    onSend: (raw) => rig.channel.onHostMessage(raw),
  });
  rig.channel = new HostChannel({
    post: (j) => { rig.posted++; if (!o.dropPosts) rig.page.deliver(j); },
    restart: () => { rig.restarts++; rig.page = mk(!!o.neverLoad); },
    loadTimeoutMs: o.loadTimeoutMs, watchdogGraceMs: o.watchdogGraceMs,
  });
  rig.page = mk(!!o.deferFirstLoad || !!o.neverLoad);
  return rig;
}

test("BUG vechi: mesaj trimis inainte ca pagina sa se incarce se pierdea; acum e tinut in coada pana la ready", async () => {
  const rig = makeRig({ deferFirstLoad: true });
  // dovada ca problema era reala: livrat direct unei pagini neincarcate, mesajul dispare
  rig.page.deliver({ ...runMessage(`print("x")`), id: 99 });
  assert.equal(rig.page.sent.length, 0);

  const p = rig.channel.run(runMessage(`print("dupa load")`));
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(rig.posted, 0, "nimic nu se posteaza inainte de ready");
  rig.page.load(); // pagina se incarca tarziu -> trimite ready -> canalul posteaza rularea
  const res = await p;
  assert.equal(res.ok, true, JSON.stringify(res.errors));
  assert.deepEqual(res.output.map((o) => o.msg), ["dupa load"]);
});

test("pagina care nu se incarca niciodata: eroare clara dupa loadTimeout, WebView recreat", async () => {
  const rig = makeRig({ neverLoad: true, loadTimeoutMs: 80 });
  const res = await rig.channel.run(runMessage(`print(1)`));
  assert.equal(res.ok, false);
  assert.match(res.errors[0], /failed to load/);
  assert.equal(rig.restarts, 1);
});

test("watchdog: pagina care nu raspunde niciodata => eroare + restart, apoi urmatoarea rulare merge", async () => {
  const rig = makeRig({ dropPosts: true, watchdogGraceMs: 80 });
  await new Promise((r) => setTimeout(r, 20));
  const res = await rig.channel.run(runMessage(`print(1)`, { timeoutMs: 50 }));
  assert.match(res.errors[0], /watchdog/);
  assert.equal(rig.restarts, 1);
});

test("stop(): rularea in curs se incheie cu 'Stopped', worker-ul e terminat, urmatoarea rulare merge", async () => {
  class HungWorker extends FakeWorker { postMessage() {} }
  const rig = makeRig({ workerFactory: (s) => new HungWorker(s) });
  const p = rig.channel.run(runMessage(`while true do end`, { timeoutMs: 5000 }));
  await new Promise((r) => setTimeout(r, 30));
  rig.channel.stop();
  const res = await p;
  assert.deepEqual(res.errors, ["Stopped"]);
  assert.equal(rig.restarts, 1);
  assert.equal(rig.channel.isReady(), true, "pagina noua a anuntat ready");
});

test("procesul WebView a murit (Android onRenderProcessGone): rularea esueaza imediat, nu dupa watchdog", async () => {
  class HungWorker extends FakeWorker { postMessage() {} }
  const rig = makeRig({ workerFactory: (s) => new HungWorker(s) });
  const p = rig.channel.run(runMessage(`print(1)`, { timeoutMs: 5000 }));
  await new Promise((r) => setTimeout(r, 30));
  const t0 = Date.now();
  rig.channel.onHostLost("Sandbox process was terminated by the system");
  const res = await p;
  assert.ok(Date.now() - t0 < 500);
  assert.match(res.errors[0], /terminated/);
  assert.equal(rig.restarts, 1);
});

test("o rulare noua o inlocuieste pe cea veche (cea veche primeste 'Superseded')", async () => {
  class HungWorker extends FakeWorker { postMessage() {} }
  let n = 0;
  const rig = makeRig({ workerFactory: (s) => (n++ === 0 ? new HungWorker(s) : new FakeWorker(s)) });
  const a = rig.channel.run(runMessage(`print("a")`, { timeoutMs: 5000 }));
  await new Promise((r) => setTimeout(r, 30));
  const b = rig.channel.run(runMessage(`print("b")`));
  assert.match((await a).errors[0], /Superseded/);
  assert.deepEqual((await b).output.map((o) => o.msg), ["b"]);
});

test("raspuns cu id gresit / fara rulare in curs e ignorat (un worker compromis nu poate falsifica rezultate)", async () => {
  class HungWorker extends FakeWorker { postMessage() {} }
  const rig = makeRig({ workerFactory: (s) => new HungWorker(s) });
  const p = rig.channel.run(runMessage(`print(1)`, { timeoutMs: 120 }));
  await new Promise((r) => setTimeout(r, 30));
  const forged = JSON.stringify({ type: "result", id: 12345, output: '{"type":"ops","data":{"ops":[{"op":"create","id":"evil"}]}}\n' });
  rig.channel.onHostMessage(forged);
  rig.channel.onHostMessage("not json");
  rig.channel.onHostMessage("x".repeat(2_500_000));
  const res = await p; // ajunge la timeout-ul paginii, nu la rezultatul fals
  assert.equal(res.ops.length, 0);
  assert.equal(res.ok, false);
});

test("pagina anunta 'ready' a doua oara in timpul unei rulari (reincarcare): rularea se incheie cu eroare", async () => {
  class HungWorker extends FakeWorker { postMessage() {} }
  const rig = makeRig({ workerFactory: (s) => new HungWorker(s) });
  const p = rig.channel.run(runMessage(`print(1)`, { timeoutMs: 5000 }));
  await new Promise((r) => setTimeout(r, 30));
  rig.channel.onHostMessage(JSON.stringify({ type: "ready" }));
  assert.match((await p).errors[0], /reloaded/);
});

test("dispose/attach (React StrictMode): canalul ramane folosibil dupa cleanup + remount", async () => {
  const rig = makeRig();
  rig.channel.dispose();
  assert.match((await rig.channel.run(runMessage(`print(1)`))).errors[0], /unmounted/);
  rig.channel.attach();
  // pagina e deja 'ready' de la inceput (canalul a primit ready cat era atasat); dupa dispose ready ramane
  assert.equal((await rig.channel.run(runMessage(`print(2)`))).ok, true);
});

// ===================== punct de intrare client =====================
test("entry: `main` marcat Client SAU `client/main` (server si client pot avea fiecare intrarea lui)", async () => {
  assert.equal(pickEntry([{ name: "util" }, { name: "client/main" }]), "client/main");
  assert.equal(pickEntry([{ name: "main" }, { name: "client/main" }]), "main");
  assert.equal(pickEntry([{ name: "util" }]), null);
  assert.throws(() => runMessage({ util: "return 1" }), /entry script/);

  const page = createHostPage();
  const { parsed } = await runViaPage(page, {
    "client/main": `local u = require("client/util") print("client entry", u.v)`,
    "client/util": `return { v = 42 }`,
  });
  assert.equal(parsed.ok, true, JSON.stringify(parsed.errors));
  assert.equal(parsed.output[0].msg, "client entry\t42");
});

test("clientFilesFromProject: doar .lua/.luau marcate Client, cu numele ca pe server (fara extensie)", () => {
  const files = clientFilesFromProject([
    { path: "main.lua", source: "s", target: "server" },
    { path: "client/main.lua", source: "c1", target: "client" },
    { path: "client/ui.luau", source: "c2", target: "client" },
    { path: "README.md", source: "x", target: "client" },
    { path: "old.lua", source: "y" },
  ]);
  assert.deepEqual(files, [{ name: "client/main", source: "c1" }, { name: "client/ui", source: "c2" }]);
  assert.equal(luauNameFor("a/b.lua"), "a/b");
});

// ===================== merge server + client ops =====================
test("BUG vechi: ops-urile client lipite la coada serverului asteptau dupa ultimul op de server", () => {
  const server = [{ t: 0, id: "s" }, { t: 30, id: "s30" }];
  const client = [{ t: 0, id: "c0" }, { t: 1, id: "c1" }];
  const merged = mergeScriptOps(server, client, 0);
  assert.deepEqual(merged.map((o) => o.id), ["s", "c0", "c1", "s30"]);
  // cursorul de redare (se opreste la primul t > elapsed): la t=2 s toate cele <=2 s sunt aplicate
  let cursor = 0; const elapsed = 2;
  while (cursor < merged.length && merged[cursor].t <= elapsed) cursor++;
  assert.equal(cursor, 3);
  const naive = [...server, ...client]; cursor = 0;
  while (cursor < naive.length && naive[cursor].t <= elapsed) cursor++;
  assert.equal(cursor, 1, "varianta veche aplica doar 1 op");
});

test("mergeScriptOps pastreaza prefixul deja redat (indicii consumati nu se muta)", () => {
  const existing = [{ t: 0, id: "a" }, { t: 5, id: "b" }, { t: 9, id: "c" }];
  const merged = mergeScriptOps(existing, [{ t: 1, id: "x" }], 2);
  assert.deepEqual(merged.map((o) => o.id), ["a", "b", "x", "c"]);
  assert.equal(mergeScriptOps(existing, [], 1), existing);
  assert.deepEqual(mergeScriptOps(existing, [{ t: 0, id: "z" }], 99).map((o) => o.id), ["a", "b", "c", "z"]);
});