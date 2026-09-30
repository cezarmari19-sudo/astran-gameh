// frontend/src/studio/projectTree.ts
// Utilitare PURE (fara React/RN) pentru modelul de proiect real: foldere + subfoldere +
// fisiere, adancime nelimitata - vezi si backend/astran_sandbox/routes.py, care persista
// exact acelasi model ({files: [{path, source}], folders: [path...]}).
//
// Folderele NU sunt entitati cu ID propriu - sunt DERIVATE din path-urile fisierelor
// (exact ca intr-un repo Git), plus lista explicita `folders` pentru folderele goale
// (altfel un folder fara niciun fisier in el ar disparea din proiect).

export type ProjectFile = { path: string; source: string };
export type ProjectState = { files: ProjectFile[]; folders: string[] };

export type TreeNode =
  | { kind: "file"; path: string; name: string }
  | { kind: "folder"; path: string; name: string; children: TreeNode[] };

// ---------- path-uri ----------

export function nameOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? path : path.slice(i + 1);
}

// Folderul parinte al unui path, sau null daca e la radacina.
// "a/b/c.lua" -> "a/b" ; "c.lua" -> null
export function parentPath(path: string): string | null {
  const i = path.lastIndexOf("/");
  return i === -1 ? null : path.slice(0, i);
}

export function joinPath(parent: string | null, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

export function extensionOf(path: string): string {
  const base = nameOf(path);
  const i = base.lastIndexOf(".");
  // un fisier fara punct, sau care incepe cu punct fara alta extensie (".gitignore"), nu are extensie
  return i > 0 ? base.slice(i + 1).toLowerCase() : "";
}

// Toti stramosii unui path, de la radacina spre parinte direct.
// "a/b/c.lua" -> ["a", "a/b"] ; "c.lua" -> []
export function ancestorsOf(path: string): string[] {
  const parts = path.split("/").slice(0, -1);
  const out: string[] = [];
  for (let i = 1; i <= parts.length; i++) out.push(parts.slice(0, i).join("/"));
  return out;
}

// E `path` egal cu `folder`, sau aflat undeva in interiorul lui?
export function isSameOrInside(path: string, folder: string): boolean {
  return path === folder || path.startsWith(folder + "/");
}

// Muta un path dintr-un folder (sau de la radacina) in altul, pastrand numele.
// Folosit pentru fisiere individuale la Move.
export function movedInto(path: string, destFolder: string | null): string {
  return joinPath(destFolder, nameOf(path));
}

// Rescrie un path care incepea cu `oldPrefix` (folder redenumit/mutat) sub `newPrefix`.
// "a/b/c.lua" cu oldPrefix "a/b", newPrefix "x/y" -> "x/y/c.lua"
export function rewritePrefix(path: string, oldPrefix: string, newPrefix: string): string {
  if (path === oldPrefix) return newPrefix;
  if (path.startsWith(oldPrefix + "/")) return newPrefix + path.slice(oldPrefix.length);
  return path;
}

// ---------- validare nume (un singur segment, nu un path intreg - vezi si backend) ----------

const SEGMENT_RE = /^[A-Za-z0-9_.-]{1,64}$/;

export function isValidSegmentName(name: string): boolean {
  return SEGMENT_RE.test(name) && name !== "." && name !== "..";
}

// ---------- arborele pentru File Explorer ----------

// Construieste arborele (foldere + fisiere), sortat: foldere inaintea fisierelor,
// alfabetic in fiecare nivel - exact cum arata un file explorer real.
export function buildTree(project: ProjectState): TreeNode[] {
  type MutFolder = { kind: "folder"; path: string; name: string; children: (TreeNode | MutFolder)[] };
  const root: MutFolder = { kind: "folder", path: "", name: "", children: [] };
  const folderByPath = new Map<string, MutFolder>();
  folderByPath.set("", root);

  function ensureFolder(path: string): MutFolder {
    const existing = folderByPath.get(path);
    if (existing) return existing;
    const parent = ensureFolder(parentPath(path) ?? "");
    const node: MutFolder = { kind: "folder", path, name: nameOf(path), children: [] };
    parent.children.push(node);
    folderByPath.set(path, node);
    return node;
  }

  // Foldere explicite (inclusiv cele goale) - se creeaza chiar daca nu au niciun fisier.
  for (const f of project.folders) ensureFolder(f);

  for (const file of project.files) {
    const parent = ensureFolder(parentPath(file.path) ?? "");
    parent.children.push({ kind: "file", path: file.path, name: nameOf(file.path) });
  }

  function sortChildren(node: MutFolder) {
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const c of node.children) if (c.kind === "folder") sortChildren(c as MutFolder);
  }
  sortChildren(root);

  return root.children as TreeNode[];
}

// Toate folderele cunoscute din proiect (explicite + derivate din fisiere + toti stramosii),
// ca set unic de path-uri - folosit de picker-ul de "Move" si de validari.
export function allFolderPaths(project: ProjectState): string[] {
  const set = new Set<string>();
  for (const f of project.folders) {
    set.add(f);
    for (const a of ancestorsOf(f + "/_")) set.add(a);
  }
  for (const file of project.files) {
    for (const a of ancestorsOf(file.path)) set.add(a);
  }
  return Array.from(set).sort();
}

// ---------- limbaj dupa extensie (pentru highlighting in CodeEditor) ----------
// "unde este posibil" (cerinta explicita): daca o extensie nu are highlighting dedicat,
// fisierul tot se editeaza normal, doar fara colorare de sintaxa.
export type LangId =
  | "lua" | "python" | "javascript" | "typescript" | "jsx" | "tsx"
  | "cpp" | "csharp" | "kotlin" | "json" | "markdown" | "html" | "css" | "yaml" | "xml" | "plain";

const EXT_LANG: Record<string, LangId> = {
  lua: "lua", luau: "lua",
  py: "python", pyw: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript",
  jsx: "jsx",
  ts: "typescript", mts: "typescript",
  tsx: "tsx",
  cpp: "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp", h: "cpp", c: "cpp",
  cs: "csharp",
  kt: "kotlin", kts: "kotlin",
  json: "json",
  md: "markdown", markdown: "markdown",
  html: "html", htm: "html",
  css: "css",
  yml: "yaml", yaml: "yaml",
  xml: "xml",
};

export function languageFor(path: string): LangId {
  return EXT_LANG[extensionOf(path)] ?? "plain";
}

// Fisierele care chiar RULEAZA in sandbox-ul Luau al jocului (vezi backend routes.py
// luau_bundle) - restul sunt organizare de proiect, editabile dar nu executabile aici.
export function isRunnable(path: string): boolean {
  const ext = extensionOf(path);
  return ext === "lua" || ext === "luau";
}