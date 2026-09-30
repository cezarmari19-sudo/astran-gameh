// frontend/src/components/FileExplorer.tsx
// File Explorer real (nu doar indentare vizuala): arata arborele de foldere/subfoldere/
// fisiere construit in projectTree.buildTree, cu expand/collapse, selectie, context menu
// (long-press) si un picker de "Move" (drag&drop e nesigur pe telefon - vezi cerinta).
import React, { useState } from "react";
import { View, Text, Pressable, ScrollView, StyleSheet, Alert, TextInput, Modal } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, radius, spacing } from "@/src/theme";
import {
  ProjectState, TreeNode, buildTree, allFolderPaths, isValidSegmentName,
  parentPath, joinPath, languageFor,
} from "@/src/studio/projectTree";

const LANG_ICON: Partial<Record<string, string>> = {
  lua: "script-text-outline",
  python: "language-python",
  javascript: "language-javascript",
  typescript: "language-typescript",
  jsx: "react", tsx: "react",
  cpp: "language-cpp",
  csharp: "language-csharp",
  kotlin: "language-kotlin",
  json: "code-json",
  markdown: "language-markdown-outline",
  html: "language-html5",
  css: "language-css3",
  yaml: "file-code-outline",
  xml: "xml",
  plain: "file-outline",
};

function fileIcon(path: string): string {
  return LANG_ICON[languageFor(path)] ?? "file-outline";
}

type DialogState =
  | { mode: "newFile" | "newFolder" | "rename"; targetPath: string | null; value: string }
  | null;

type Props = {
  project: ProjectState;
  openPath: string | null;
  dirtyPaths: Set<string>;
  onOpen: (path: string) => void;
  onCreateFile: (path: string) => void;
  onCreateFolder: (path: string) => void;
  onRename: (oldPath: string, newPath: string, isFolder: boolean) => void;
  onMove: (path: string, destFolder: string | null, isFolder: boolean) => void;
  onDelete: (path: string, isFolder: boolean) => void;
  onDuplicate: (path: string) => void;
};

export default function FileExplorer({
  project, openPath, dirtyPaths, onOpen, onCreateFile, onCreateFolder, onRename, onMove, onDelete, onDuplicate,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(allFolderPaths(project)));
  // Folderul "curent" pentru New File/New Folder cand apesi + fara sa fi selectat nimic anume
  // in prealabil - vezi cerinta punctul 3: daca ai selectat un folder, se creeaza in el.
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [contextFor, setContextFor] = useState<{ path: string; isFolder: boolean } | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dialogErr, setDialogErr] = useState("");
  const [movePicker, setMovePicker] = useState<{ path: string; isFolder: boolean } | null>(null);

  const tree = buildTree(project);

  function toggle(path: string) {
    setExpanded(s => {
      const next = new Set(s);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function existsPath(path: string): boolean {
    return project.files.some(f => f.path === path) || allFolderPaths(project).includes(path) || project.folders.includes(path);
  }

  function openNewFile() {
    setDialogErr("");
    setDialog({ mode: "newFile", targetPath: selectedFolder, value: "" });
  }
  function openNewFolder() {
    setDialogErr("");
    setDialog({ mode: "newFolder", targetPath: selectedFolder, value: "" });
  }
  function openRename(path: string, isFolder: boolean) {
    setDialogErr("");
    setDialog({ mode: "rename", targetPath: path, value: isFolder ? path.split("/").pop()! : path.split("/").pop()! });
    setContextFor(null);
  }

  function submitDialog() {
    if (!dialog) return;
    const name = dialog.value.trim();
    if (!isValidSegmentName(name)) {
      setDialogErr("Use letters, numbers, _ - and . (max 64 characters)");
      return;
    }
    if (dialog.mode === "newFile") {
      const path = joinPath(dialog.targetPath, name);
      if (existsPath(path)) { setDialogErr("Already exists"); return; }
      onCreateFile(path);
      setExpanded(s => (dialog.targetPath ? new Set(s).add(dialog.targetPath!) : s));
    } else if (dialog.mode === "newFolder") {
      const path = joinPath(dialog.targetPath, name);
      if (existsPath(path)) { setDialogErr("Already exists"); return; }
      onCreateFolder(path);
      setExpanded(s => new Set(s).add(path).add(dialog.targetPath ?? ""));
    } else if (dialog.mode === "rename" && dialog.targetPath) {
      const parent = parentPath(dialog.targetPath);
      const newPath = joinPath(parent, name);
      if (newPath !== dialog.targetPath && existsPath(newPath)) { setDialogErr("Already exists"); return; }
      const isFolder = allFolderPaths(project).includes(dialog.targetPath) || project.folders.includes(dialog.targetPath);
      onRename(dialog.targetPath, newPath, isFolder);
    }
    setDialog(null);
    setDialogErr("");
  }

  function askDelete(path: string, isFolder: boolean) {
    setContextFor(null);
    const label = isFolder ? "folder" : "file";
    Alert.alert(
      `Delete ${label}`,
      isFolder ? `Delete "${path}" and everything inside it?` : `Delete "${path}"?`,
      [
        { text: "Cancel", style: "cancel" },
        { text: "Delete", style: "destructive", onPress: () => onDelete(path, isFolder) },
      ]
    );
  }

  function renderNode(node: TreeNode, depth: number) {
    if (node.kind === "folder") {
      const isOpen = expanded.has(node.path);
      const isSelected = selectedFolder === node.path;
      return (
        <View key={node.path}>
          <Pressable
            testID={`fe-folder-${node.path}`}
            onPress={() => { toggle(node.path); setSelectedFolder(node.path); }}
            onLongPress={() => setContextFor({ path: node.path, isFolder: true })}
            style={[styles.row, { paddingLeft: 10 + depth * 16 }, isSelected && styles.rowSelected]}
          >
            <MaterialCommunityIcons name={isOpen ? "chevron-down" : "chevron-right"} size={16} color={colors.onSurface3} />
            <MaterialCommunityIcons name={isOpen ? "folder-open" : "folder"} size={16} color={colors.brand} />
            <Text style={styles.rowText} numberOfLines={1}>{node.name}</Text>
          </Pressable>
          {isOpen ? node.children.map(c => renderNode(c, depth + 1)) : null}
        </View>
      );
    }
    const isActive = openPath === node.path;
    const isDirty = dirtyPaths.has(node.path);
    return (
      <Pressable
        key={node.path}
        testID={`fe-file-${node.path}`}
        onPress={() => onOpen(node.path)}
        onLongPress={() => setContextFor({ path: node.path, isFolder: false })}
        style={[styles.row, { paddingLeft: 26 + depth * 16 }, isActive && styles.rowActive]}
      >
        <MaterialCommunityIcons name={fileIcon(node.path) as any} size={15} color={isActive ? colors.brand : colors.onSurface3} />
        <Text style={[styles.rowText, isActive && { color: colors.brand, fontWeight: "700" }]} numberOfLines={1}>{node.name}</Text>
        {isDirty ? <View style={styles.dirtyDot} /> : null}
      </Pressable>
    );
  }

  const folderOptions = [null, ...allFolderPaths(project)]; // null = radacina proiectului

  return (
    <View style={styles.wrap}>
      <View style={styles.header}>
        <Pressable
          testID="fe-root"
          onPress={() => setSelectedFolder(null)}
          style={[styles.rootPill, selectedFolder === null && styles.rootPillActive]}
        >
          <MaterialCommunityIcons name="folder-home-outline" size={13} color={selectedFolder === null ? colors.brand : colors.onSurface3} />
          <Text style={[styles.rootPillText, selectedFolder === null && { color: colors.brand }]} numberOfLines={1}>
            {selectedFolder === null ? "root" : selectedFolder}
          </Text>
        </Pressable>
        <View style={{ flex: 1 }} />
        <Pressable testID="fe-new-file" onPress={openNewFile} hitSlop={8} style={styles.headerBtn}>
          <MaterialCommunityIcons name="file-plus-outline" size={18} color={colors.brand} />
        </Pressable>
        <Pressable testID="fe-new-folder" onPress={openNewFolder} hitSlop={8} style={styles.headerBtn}>
          <MaterialCommunityIcons name="folder-plus-outline" size={18} color={colors.brand} />
        </Pressable>
      </View>

      <ScrollView style={styles.tree} contentContainerStyle={{ paddingVertical: 6 }}>
        {tree.length === 0 ? (
          <Text style={styles.emptyText}>No files yet. Tap the file icon above to create one.</Text>
        ) : (
          tree.map(n => renderNode(n, 0))
        )}
      </ScrollView>

      {/* Context menu (long-press) */}
      <Modal visible={contextFor !== null} transparent animationType="fade" onRequestClose={() => setContextFor(null)}>
        <Pressable style={styles.sheetBackdrop} onPress={() => setContextFor(null)}>
          <Pressable style={styles.sheetBox} onPress={(e: any) => e.stopPropagation?.()}>
            <Text style={styles.sheetTitle} numberOfLines={1}>{contextFor?.path}</Text>
            {contextFor && !contextFor.isFolder ? (
              <Pressable testID="fe-ctx-open" style={styles.sheetRow} onPress={() => { onOpen(contextFor.path); setContextFor(null); }}>
                <MaterialCommunityIcons name="file-eye-outline" size={18} color={colors.onSurface} />
                <Text style={styles.sheetRowText}>Open</Text>
              </Pressable>
            ) : null}
            {contextFor?.isFolder ? (
              <>
                <Pressable testID="fe-ctx-new-file" style={styles.sheetRow} onPress={() => { setSelectedFolder(contextFor.path); setContextFor(null); setDialogErr(""); setDialog({ mode: "newFile", targetPath: contextFor.path, value: "" }); }}>
                  <MaterialCommunityIcons name="file-plus-outline" size={18} color={colors.onSurface} />
                  <Text style={styles.sheetRowText}>New File</Text>
                </Pressable>
                <Pressable testID="fe-ctx-new-folder" style={styles.sheetRow} onPress={() => { setSelectedFolder(contextFor.path); setContextFor(null); setDialogErr(""); setDialog({ mode: "newFolder", targetPath: contextFor.path, value: "" }); }}>
                  <MaterialCommunityIcons name="folder-plus-outline" size={18} color={colors.onSurface} />
                  <Text style={styles.sheetRowText}>New Folder</Text>
                </Pressable>
              </>
            ) : null}
            <Pressable testID="fe-ctx-rename" style={styles.sheetRow} onPress={() => contextFor && openRename(contextFor.path, contextFor.isFolder)}>
              <MaterialCommunityIcons name="rename-box" size={18} color={colors.onSurface} />
              <Text style={styles.sheetRowText}>Rename</Text>
            </Pressable>
            <Pressable testID="fe-ctx-move" style={styles.sheetRow} onPress={() => { if (contextFor) setMovePicker(contextFor); setContextFor(null); }}>
              <MaterialCommunityIcons name="folder-move-outline" size={18} color={colors.onSurface} />
              <Text style={styles.sheetRowText}>Move</Text>
            </Pressable>
            {contextFor && !contextFor.isFolder ? (
              <Pressable testID="fe-ctx-duplicate" style={styles.sheetRow} onPress={() => { onDuplicate(contextFor.path); setContextFor(null); }}>
                <MaterialCommunityIcons name="content-copy" size={18} color={colors.onSurface} />
                <Text style={styles.sheetRowText}>Duplicate</Text>
              </Pressable>
            ) : null}
            <Pressable testID="fe-ctx-delete" style={styles.sheetRow} onPress={() => contextFor && askDelete(contextFor.path, contextFor.isFolder)}>
              <MaterialCommunityIcons name="trash-can-outline" size={18} color={colors.error} />
              <Text style={[styles.sheetRowText, { color: colors.error }]}>Delete</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

      {/* New File / New Folder / Rename dialog */}
      <Modal visible={dialog !== null} transparent animationType="fade" onRequestClose={() => setDialog(null)}>
        <View style={styles.sheetBackdrop}>
          <View style={styles.sheetBox}>
            <Text style={styles.sheetTitle}>
              {dialog?.mode === "newFile" ? "New File" : dialog?.mode === "newFolder" ? "New Folder" : "Rename"}
            </Text>
            {dialog?.mode !== "rename" ? (
              <Text style={styles.hintSmall}>in {dialog?.targetPath ?? "root"}/</Text>
            ) : null}
            <TextInput
              testID="fe-dialog-input"
              autoFocus
              value={dialog?.value ?? ""}
              onChangeText={v => { setDialog(d => (d ? { ...d, value: v } : d)); setDialogErr(""); }}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder={dialog?.mode === "newFolder" ? "player" : "movement.lua"}
              placeholderTextColor={colors.onSurface3}
              style={styles.dialogInput}
            />
            {dialogErr ? <Text style={styles.dialogErr}>{dialogErr}</Text> : null}
            <View style={styles.dialogRow}>
              <Pressable testID="fe-dialog-cancel" onPress={() => setDialog(null)} style={styles.ghostBtn}>
                <Text style={styles.ghostBtnText}>Cancel</Text>
              </Pressable>
              <Pressable testID="fe-dialog-ok" onPress={submitDialog} style={styles.primaryBtn}>
                <Text style={styles.primaryBtnText}>OK</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* Move picker: alegi folderul destinatie dintr-o lista - alternativa sigura pe telefon
          la drag & drop (vezi cerinta punctul 4). */}
      <Modal visible={movePicker !== null} transparent animationType="fade" onRequestClose={() => setMovePicker(null)}>
        <Pressable style={styles.sheetBackdrop} onPress={() => setMovePicker(null)}>
          <Pressable style={styles.sheetBox} onPress={(e: any) => e.stopPropagation?.()}>
            <Text style={styles.sheetTitle} numberOfLines={1}>Move "{movePicker ? movePicker.path.split("/").pop() : ""}"</Text>
            <ScrollView style={{ maxHeight: 320 }}>
              {folderOptions
                .filter(dest => {
                  if (!movePicker) return true;
                  // nu poti muta un folder in el insusi sau intr-un subfolder al lui
                  if (movePicker.isFolder && dest !== null && (dest === movePicker.path || dest.startsWith(movePicker.path + "/"))) return false;
                  const currentParent = parentPath(movePicker.path);
                  return dest !== currentParent || (dest === null && currentParent !== null) || (dest !== null && dest !== currentParent);
                })
                .map(dest => (
                  <Pressable
                    key={dest ?? "__root__"}
                    testID={`fe-move-to-${dest ?? "root"}`}
                    style={styles.sheetRow}
                    onPress={() => {
                      if (movePicker) onMove(movePicker.path, dest, movePicker.isFolder);
                      setMovePicker(null);
                    }}
                  >
                    <MaterialCommunityIcons name="folder-outline" size={18} color={colors.brand} />
                    <Text style={styles.sheetRowText} numberOfLines={1}>{dest ?? "root"}</Text>
                  </Pressable>
                ))}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: colors.surface, borderRightWidth: 1, borderColor: colors.border },
  header: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10, paddingVertical: 8, borderBottomWidth: 1, borderColor: colors.border },
  rootPill: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: radius.pill, backgroundColor: colors.surface2, maxWidth: 140 },
  rootPillActive: { backgroundColor: colors.brandTint },
  rootPillText: { color: colors.onSurface3, fontSize: 10, fontWeight: "700" },
  headerBtn: { padding: 4 },
  tree: { flex: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: 4, paddingVertical: 6, paddingRight: 10 },
  rowSelected: { backgroundColor: colors.brandTint },
  rowActive: { backgroundColor: colors.surface2 },
  rowText: { color: colors.onSurface2, fontSize: 12.5, flexShrink: 1 },
  dirtyDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: colors.brand, marginLeft: 4 },
  emptyText: { color: colors.onSurface3, fontSize: 12, textAlign: "center", padding: spacing.lg },
  sheetBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  sheetBox: { width: "100%", maxWidth: 380, backgroundColor: colors.surface, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 1, borderColor: colors.borderStrong },
  sheetTitle: { color: colors.onSurface, fontSize: 15, fontWeight: "800", marginBottom: 10 },
  sheetRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 11 },
  sheetRowText: { color: colors.onSurface, fontSize: 14, fontWeight: "600" },
  hintSmall: { color: colors.onSurface3, fontSize: 11, marginBottom: 8, fontFamily: "monospace" },
  dialogInput: { backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  dialogErr: { color: colors.error, fontSize: 12, marginTop: 8 },
  dialogRow: { flexDirection: "row", justifyContent: "flex-end", gap: 8, marginTop: spacing.md },
  ghostBtn: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.borderStrong, alignItems: "center" },
  ghostBtnText: { color: colors.onSurface2, fontWeight: "700", fontSize: 13 },
  primaryBtn: { backgroundColor: colors.brand, paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, alignItems: "center" },
  primaryBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
});