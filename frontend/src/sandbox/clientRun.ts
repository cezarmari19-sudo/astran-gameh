// frontend/src/sandbox/clientRun.ts
// Pasii comuni pentru a rula scripturile target="client" pe dispozitiv: folositi de ecranul de
// Play, de Play din Studio si de butonul Run din CodeStudio (inainte, ultimele doua nu puteau
// rula scripturi client deloc, iar primele doua aveau codul copiat de doua ori).
import { PRELUDE_SOURCE } from "@/src/sandbox/preludeSource";
import type { SandboxFile, SandboxResult, SandboxRunOptions } from "@/src/sandbox/runtime";
import { isRunnable, targetOf } from "@/src/studio/projectTree";
import type { ProjectFile } from "@/src/studio/projectTree";

// Orice are metoda run (ScriptSandboxHandle din componenta) - tip minimal, ca modulul sa
// ramana testabil fara React.
export type SandboxRunner = { run: (opts: SandboxRunOptions) => Promise<SandboxResult> };

// Oglinda lui luau_name_for din backend/routes.py: "folder/a.lua" -> "folder/a".
export function luauNameFor(path: string): string {
  return path.replace(/\.(lua|luau)$/, "");
}

// Fisierele .lua/.luau marcate Client dintr-un proiect (starea din editor), in formatul
// {name, source} pe care il asteapta sandbox-ul.
export function clientFilesFromProject(files: ProjectFile[]): SandboxFile[] {
  return files
    .filter((f) => isRunnable(f.path) && targetOf(f) === "client")
    .map((f) => ({ name: luauNameFor(f.path), source: f.source }));
}

// Raspunsul GET /sandbox/games/{id}/client-bundle -> rulare locala. Intoarce null daca nu
// exista nimic de rulat (niciun fisier client sau sandbox indisponibil).
export async function runClientBundle(
  sandbox: SandboxRunner | null | undefined,
  bundle: any
): Promise<SandboxResult | null> {
  const files = Array.isArray(bundle?.files) ? bundle.files : [];
  if (files.length === 0 || !sandbox) return null;
  const assets = Array.isArray(bundle?.assets) ? bundle.assets : [];
  const result = await sandbox.run({
    files,
    preludeSource: PRELUDE_SOURCE,
    assets,
    scene: bundle?.scene ?? null,
    capabilities: assets.length > 0 ? ["assets"] : [],
  });
  // Pana acum print()/erorile scripturilor client se pierdeau: creatorul nu vedea nimic.
  for (const l of result.output) console.log(`[client script ${l.level}]`, l.msg);
  for (const e of result.errors) console.log("[client script error]", e);
  return result;
}

// Combina ops-urile de pe server cu cele de pe client intr-o singura lista PE TIMP.
// Bucla de redare din ecranele de Play consuma lista cu un cursor (`nextOp`) si se opreste la
// primul op cu t > timpul curent: daca op-urile client (t incepe de la 0) ar fi doar lipite la
// coada celor de server, ele ar astepta dupa ultimul op de server (pana la 60 s intarziere).
// `consumed` = cate ops au fost deja redate: acel prefix ramane NEATINS (indicii deja consumati
// nu au voie sa se mute), iar restul + noile ops se sorteaza stabil dupa t (la timp egal, ordinea
// existenta - serverul inaintea clientului - se pastreaza).
export function mergeScriptOps<T extends { t: number }>(existing: T[], incoming: T[], consumed: number): T[] {
  if (incoming.length === 0) return existing;
  const keep = Math.max(0, Math.min(consumed | 0, existing.length));
  const tail = existing.slice(keep).concat(incoming);
  return existing
    .slice(0, keep)
    .concat(tail.map((o, i) => ({ o, i })).sort((a, b) => (a.o.t - b.o.t) || (a.i - b.i)).map((x) => x.o));
}