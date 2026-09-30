// frontend/src/components/CodeStudio.tsx
// Code Editor / File Explorer real pentru Game Studio - inlocuieste vechiul ScriptEditor
// (lista plata de fisiere). Compune: FileExplorer (arbore de foldere/fisiere) + tab-uri
// pentru fisiere deschise simultan + breadcrumbs + CodeEditor (CodeMirror) + consola de Run.
//
// Detine STATE-UL REAL al proiectului ({files, folders}) si aplica toate operatiile
// (create/rename/move/delete/duplicate) direct pe aceasta structura - FileExplorer doar
// afiseaza si cere operatii, nu tine el insusi vreo copie a datelor.
import React, { useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, Pressable, Modal, ScrollView, ActivityIndicator, Platform, KeyboardAvoidingView } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import { api } from "@/src/api/client";
import FileExplorer from "./FileExplorer";
import CodeEditor from "./CodeEditor";
import {
  ProjectState, ProjectFile, nameOf, parentPath, joinPath, rewritePrefix,
  isSameOrInside, movedInto, ancestorsOf, isRunnable,
} from "@/src/studio/projectTree";

type RunLine = { kind: "log" | "error"; text: string };

type Props = {
  visible: boolean;
  project: ProjectState;
  onChange: (project: ProjectState) => void; // notifica parintele (create.tsx) - marcheaza dirty pentru Save general
  onClose: () => void;
  onSave: () => void; // Save-ul general al jocului (acelasi buton ca inainte, din create.tsx)
  saving: boolean;
  saved: boolean;
};

function starterContent(path: string): string {
  if (isRunnable(path)) {
    const isMain = path === "main.lua";
    return isMain
      ? "-- ruleaza primul, la intrarea in joc\n"
      : `-- ${nameOf(path)}\n`;
  }
  return "";
}

export default function CodeStudio({ visible, project, onChange, onClose, onSave, saving, saved }: Props) {
  const [openPaths, setOpenPaths] = useState<string[]>(() => (project.files[0] ? [project.files[0].path] : []));
  const [activePath, setActivePath] = useState<string | null>(() => project.files[0]?.path ?? null);
  const [dirtyPaths, setDirtyPaths] = useState<Set<string>>(new Set());
  const [findSignal, setFindSignal] = useState(0);
  const [showExplorer, setShowExplorer] = useState(true);
  const [running, setRunning] = useState(false);
  const [showConsole, setShowConsole] = useState(false);
  const [runLines, setRunLines] = useState<RunLine[]>([]);
  const [closeConfirm, setCloseConfirm] = useState<{ path: string } | null>(null);

  // Continut "de referinta" per path la momentul ultimului load in editor - folosit doar
  // ca sa stim ce sa trimitem catre CodeEditor cand se deschide un tab (vezi CodeEditor.tsx,
  // "initialSource" se citeste o singura data la schimbarea de path, nu la fiecare tasta).
  const fileByPath = useMemo(() => {
    const m = new Map<string, ProjectFile>();
    for (const f of project.files) m.set(f.path, f);
    return m;
  }, [project.files]);

  function updateProject(next: ProjectState) {
    onChange(next);
  }

  function openTab(path: string) {
    setOpenPaths(prev => (prev.includes(path) ? prev : [...prev, path]));
    setActivePath(path);
    setShowExplorer(Platform.OS !== "web" ? false : showExplorer); // pe telefon, deschiderea unui fisier ascunde explorer-ul ca sa incapa editorul
  }

  function closeTabForce(path: string) {
    setOpenPaths(prev => prev.filter(p => p !== path));
    setDirtyPaths(prev => { const n = new Set(prev); n.delete(path); return n; });
    setActivePath(prev => {
      if (prev !== path) return prev;
      const remaining = openPaths.filter(p => p !== path);
      return remaining.length > 0 ? remaining[remaining.length - 1] : null;
    });
  }

  function requestCloseTab(path: string) {
    if (dirtyPaths.has(path)) { setCloseConfirm({ path }); return; }
    closeTabForce(path);
  }

  function onEditorChange(path: string, source: string) {
    setDirtyPaths(prev => new Set(prev).add(path));
    const next: ProjectFile[] = project.files.map(f => (f.path === path ? { ...f, source } : f));
    updateProject({ ...project, files: next });
  }

  function markSaved(paths: string[]) {
    setDirtyPaths(prev => {
      const n = new Set(prev);
      for (const p of paths) n.delete(p);
      return n;
    });
  }

  // ---------- operatii din FileExplorer, aplicate pe structura reala ----------

  function handleCreateFile(path: string) {
    const file: ProjectFile = { path, source: starterContent(path) };
    updateProject({ ...project, files: [...project.files, file] });
    openTab(path);
  }

  function handleCreateFolder(path: string) {
    if (project.folders.includes(path)) return;
    updateProject({ ...project, folders: [...project.folders, path] });
  }

  function handleRename(oldPath: string, newPath: string, isFolder: boolean) {
    if (isFolder) {
      const files = project.files.map(f => (isSameOrInside(f.path, oldPath) ? { ...f, path: rewritePrefix(f.path, oldPath, newPath) } : f));
      const folders = project.folders.map(f => (isSameOrInside(f, oldPath) ? rewritePrefix(f, oldPath, newPath) : f));
      updateProject({ ...project, files, folders });
      setOpenPaths(prev => prev.map(p => (isSameOrInside(p, oldPath) ? rewritePrefix(p, oldPath, newPath) : p)));
      setActivePath(prev => (prev && isSameOrInside(prev, oldPath) ? rewritePrefix(prev, oldPath, newPath) : prev));
      setDirtyPaths(prev => new Set(Array.from(prev).map(p => (isSameOrInside(p, oldPath) ? rewritePrefix(p, oldPath, newPath) : p))));
    } else {
      const files = project.files.map(f => (f.path === oldPath ? { ...f, path: newPath } : f));
      updateProject({ ...project, files });
      setOpenPaths(prev => prev.map(p => (p === oldPath ? newPath : p)));
      setActivePath(prev => (prev === oldPath ? newPath : prev));
      setDirtyPaths(prev => {
        if (!prev.has(oldPath)) return prev;
        const n = new Set(prev); n.delete(oldPath); n.add(newPath); return n;
      });
    }
  }

  function handleMove(path: string, destFolder: string | null, isFolder: boolean) {
    if (isFolder) {
      const newPath = joinPath(destFolder, nameOf(path));
      handleRename(path, newPath, true);
    } else {
      const newPath = movedInto(path, destFolder);
      handleRename(path, newPath, false);
    }
  }

  function handleDelete(path: string, isFolder: boolean) {
    if (isFolder) {
      const files = project.files.filter(f => !isSameOrInside(f.path, path));
      const folders = project.folders.filter(f => !isSameOrInside(f, path));
      updateProject({ ...project, files, folders });
      const removedPaths = project.files.filter(f => isSameOrInside(f.path, path)).map(f => f.path);
      removedPaths.forEach(closeTabForce);
    } else {
      const files = project.files.filter(f => f.path !== path);
      updateProject({ ...project, files });
      closeTabForce(path);
    }
  }

  function handleDuplicate(path: string) {
    const src = project.files.find(f => f.path === path);
    if (!src) return;
    const parent = parentPath(path);
    const base = nameOf(path);
    const dot = base.lastIndexOf(".");
    const stem = dot > 0 ? base.slice(0, dot) : base;
    const ext = dot > 0 ? base.slice(dot) : "";
    let n = 1;
    let candidate = joinPath(parent, `${stem}_copy${ext}`);
    const existing = new Set(project.files.map(f => f.path));
    while (existing.has(candidate)) { n += 1; candidate = joinPath(parent, `${stem}_copy${n}${ext}`); }
    updateProject({ ...project, files: [...project.files, { path: candidate, source: src.source }] });
    openTab(candidate);
  }

  // ---------- Run: testeaza doar subsetul .lua/.luau din proiectul curent ----------

  async function runProject() {
    setRunning(true);
    setShowConsole(true);
    setRunLines([{ kind: "log", text: "Running..." }]);
    try {
      const r = await api("/sandbox/run", {
        method: "POST",
        body: JSON.stringify({ files: project.files.map(f => ({ path: f.path, source: f.source })) }),
      });
      const lines: RunLine[] = [];
      (r?.output ?? []).forEach((l: string) => lines.push({ kind: "log", text: l }));
      (r?.errors ?? []).forEach((l: string) => lines.push({ kind: "error", text: l }));
      if (lines.length === 0) lines.push({ kind: "log", text: r?.ok ? "No output (no runnable .lua/.luau files, or nothing printed)." : "Failed." });
      if (r?.truncated) lines.push({ kind: "log", text: "(output truncated)" });
      setRunLines(lines);
    } catch (e: any) {
      setRunLines([{ kind: "error", text: e.message || "Run failed" }]);
    } finally {
      setRunning(false);
    }
  }

  function saveAll() {
    markSaved(Array.from(dirtyPaths));
    onSave();
  }

  const activeFile = activePath ? fileByPath.get(activePath) : null;
  const breadcrumbParts = activePath ? activePath.split("/") : [];
  const anyDirty = dirtyPaths.size > 0;

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <View style={styles.root}>
        <View style={styles.topBar}>
          <Pressable testID="cs-toggle-explorer" onPress={() => setShowExplorer(v => !v)} hitSlop={8} style={styles.topBtn}>
            <MaterialCommunityIcons name="file-tree-outline" size={20} color={showExplorer ? colors.brand : colors.onSurface2} />
          </Pressable>
          <Text style={styles.topTitle}>Code {saved ? "· Saved ✓" : anyDirty ? "· Unsaved changes" : ""}</Text>
          <Pressable testID="cs-run" onPress={runProject} hitSlop={8} style={styles.topBtn} disabled={running}>
            {running ? <ActivityIndicator size="small" color={colors.brand} /> : <MaterialCommunityIcons name="play-circle-outline" size={22} color={colors.brand} />}
          </Pressable>
          <Pressable testID="cs-find" onPress={() => setFindSignal(s => s + 1)} hitSlop={8} style={styles.topBtn} disabled={!activePath}>
            <MaterialCommunityIcons name="magnify" size={20} color={activePath ? colors.onSurface2 : colors.onSurface3} />
          </Pressable>
          <Pressable testID="cs-save" onPress={saveAll} style={styles.saveBtn} disabled={saving}>
            <Text style={styles.saveBtnText}>{saving ? "..." : "Save"}</Text>
          </Pressable>
          <Pressable testID="cs-close" onPress={onClose} hitSlop={8} style={styles.topBtn}>
            <MaterialCommunityIcons name="close" size={22} color={colors.onSurface} />
          </Pressable>
        </View>

        <View style={styles.body}>
          {showExplorer ? (
            <View style={styles.explorerCol}>
              <FileExplorer
                project={project}
                openPath={activePath}
                dirtyPaths={dirtyPaths}
                onOpen={openTab}
                onCreateFile={handleCreateFile}
                onCreateFolder={handleCreateFolder}
                onRename={handleRename}
                onMove={handleMove}
                onDelete={handleDelete}
                onDuplicate={handleDuplicate}
              />
            </View>
          ) : null}

          <View style={styles.editorCol}>
            {openPaths.length > 0 ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.tabsBar} contentContainerStyle={{ alignItems: "center" }}>
                {openPaths.map(p => {
                  const isActive = p === activePath;
                  const isDirty = dirtyPaths.has(p);
                  return (
                    <Pressable
                      key={p}
                      testID={`cs-tab-${p}`}
                      onPress={() => setActivePath(p)}
                      style={[styles.tab, isActive && styles.tabActive]}
                    >
                      <Text style={[styles.tabText, isActive && { color: colors.onSurface }]} numberOfLines={1}>{nameOf(p)}</Text>
                      {isDirty ? <View style={styles.tabDirtyDot} /> : null}
                      <Pressable testID={`cs-tab-close-${p}`} onPress={() => requestCloseTab(p)} hitSlop={8} style={{ marginLeft: 4 }}>
                        <MaterialCommunityIcons name="close" size={13} color={colors.onSurface3} />
                      </Pressable>
                    </Pressable>
                  );
                })}
              </ScrollView>
            ) : null}

            {activePath ? (
              <View style={styles.breadcrumbBar}>
                <MaterialCommunityIcons name="folder-outline" size={12} color={colors.onSurface3} />
                {breadcrumbParts.map((part, i) => (
                  <React.Fragment key={i}>
                    {i > 0 ? <Text style={styles.breadcrumbSep}>/</Text> : null}
                    <Text style={[styles.breadcrumbText, i === breadcrumbParts.length - 1 && { color: colors.onSurface2, fontWeight: "700" }]} numberOfLines={1}>
                      {part}
                    </Text>
                  </React.Fragment>
                ))}
              </View>
            ) : null}

            {activeFile ? (
              <CodeEditor
                key={activePath /* fortam un CodeEditor "curat" per fisier - simplu si robust */}
                path={activeFile.path}
                initialSource={activeFile.source}
                onChange={onEditorChange}
                findSignal={findSignal}
              />
            ) : (
              <View style={styles.emptyState}>
                <MaterialCommunityIcons name="file-code-outline" size={40} color={colors.onSurface3} />
                <Text style={styles.emptyStateText}>No file open. Pick one from the explorer, or create a new one.</Text>
              </View>
            )}
          </View>
        </View>

        {showConsole ? (
          <View style={styles.console}>
            <View style={styles.consoleHeader}>
              <Text style={styles.consoleTitle}>Run output</Text>
              <Pressable testID="cs-console-close" onPress={() => setShowConsole(false)} hitSlop={8}>
                <MaterialCommunityIcons name="close" size={16} color={colors.onSurface3} />
              </Pressable>
            </View>
            <ScrollView style={{ maxHeight: 140 }}>
              {runLines.map((l, i) => (
                <Text key={i} style={[styles.consoleLine, l.kind === "error" && { color: colors.error }]}>{l.text}</Text>
              ))}
            </ScrollView>
          </View>
        ) : null}

        <Modal visible={closeConfirm !== null} transparent animationType="fade" onRequestClose={() => setCloseConfirm(null)}>
          <View style={styles.sheetBackdrop}>
            <View style={styles.sheetBox}>
              <Text style={styles.sheetTitle}>Unsaved changes</Text>
              <Text style={styles.hintSmall}>"{closeConfirm ? nameOf(closeConfirm.path) : ""}" has changes that aren't saved yet.</Text>
              <View style={styles.dialogRow}>
                <Pressable testID="cs-close-confirm-cancel" onPress={() => setCloseConfirm(null)} style={styles.ghostBtn}>
                  <Text style={styles.ghostBtnText}>Keep editing</Text>
                </Pressable>
                <Pressable
                  testID="cs-close-confirm-discard"
                  onPress={() => { if (closeConfirm) closeTabForce(closeConfirm.path); setCloseConfirm(null); }}
                  style={[styles.primaryBtn, { backgroundColor: colors.error }]}
                >
                  <Text style={styles.primaryBtnText}>Close without saving</Text>
                </Pressable>
              </View>
            </View>
          </View>
        </Modal>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: spacing.md, paddingVertical: 10, borderBottomWidth: 1, borderColor: colors.border, paddingTop: Platform.OS === "ios" ? 50 : 14 },
  topBtn: { padding: 6 },
  topTitle: { flex: 1, color: colors.onSurface2, fontSize: 12, fontWeight: "700", marginLeft: 4 },
  saveBtn: { backgroundColor: colors.brand, borderRadius: radius.pill, paddingHorizontal: 16, paddingVertical: 8, marginLeft: 4 },
  saveBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
  body: { flex: 1, flexDirection: "row" },
  explorerCol: { width: 220 },
  editorCol: { flex: 1 },
  tabsBar: { flexDirection: "row", borderBottomWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, maxHeight: 40 },
  tab: { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 10, borderRightWidth: 1, borderColor: colors.border, maxWidth: 160 },
  tabActive: { backgroundColor: colors.surface2, borderBottomWidth: 2, borderBottomColor: colors.brand },
  tabText: { color: colors.onSurface3, fontSize: 12, fontWeight: "600", flexShrink: 1 },
  tabDirtyDot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: colors.brand, marginLeft: 5 },
  breadcrumbBar: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 12, paddingVertical: 6, backgroundColor: colors.surface2, borderBottomWidth: 1, borderColor: colors.border },
  breadcrumbText: { color: colors.onSurface3, fontSize: 10.5, fontFamily: "monospace" },
  breadcrumbSep: { color: colors.onSurface3, fontSize: 10.5, marginHorizontal: 1 },
  emptyState: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10, padding: spacing.xl },
  emptyStateText: { color: colors.onSurface3, fontSize: 13, textAlign: "center" },
  console: { borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.surface2, padding: spacing.sm },
  consoleHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 4 },
  consoleTitle: { color: colors.onSurface3, fontSize: 10, fontWeight: "800", letterSpacing: 1, textTransform: "uppercase" },
  consoleLine: { color: colors.onSurface2, fontSize: 11, fontFamily: "monospace", paddingVertical: 1 },
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  sheetBox: { width: "100%", maxWidth: 380, backgroundColor: colors.surface, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 1, borderColor: colors.borderStrong },
  sheetTitle: { color: colors.onSurface, fontSize: 15, fontWeight: "800", marginBottom: 8 },
  hintSmall: { color: colors.onSurface3, fontSize: 12, marginBottom: 8 },
  dialogRow: { flexDirection: "row", justifyContent: "flex-end", gap: 8, marginTop: spacing.md },
  ghostBtn: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.borderStrong, alignItems: "center" },
  ghostBtnText: { color: colors.onSurface2, fontWeight: "700", fontSize: 13 },
  primaryBtn: { backgroundColor: colors.brand, paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, alignItems: "center" },
  primaryBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
});