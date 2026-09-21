import React, { useState } from "react";
import {
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";

export type ScriptFile = { name: string; source: string };

type RunResult = {
  ok: boolean;
  output: { level: string; msg: string }[];
  errors: string[];
  ops: any[];
  duration: number;
  truncated: boolean;
};

type Props = {
  visible: boolean;
  files: ScriptFile[];
  onChange: (files: ScriptFile[]) => void;
  onClose: () => void;
};

const ENTRY = "main";
const MAX_FILES = 32;
const NAME_RE = /^[A-Za-z0-9_-]+(\/[A-Za-z0-9_-]+)*$/;

const EXAMPLE_MAIN = `-- "main" ruleaza primul. Celelalte fisiere se incarca cu require("nume").
local Utils = require("utils")

local part = Instance.new("Part")
part.Shape = "Ball"
part.Color = Color3.fromRGB(255, 80, 80)
part.Position = Vector3.new(0, 6, 0)
part.Parent = workspace

print(Utils.greet("Astran"))

for i = 1, 20 do
  task.wait(0.1)
  part.Position = part.Position - Vector3.new(0, 0.25, 0)
end
`;

const EXAMPLE_UTILS = `-- Fisier modul: intoarce un tabel pe care main il primeste cu require("utils")
local Utils = {}

function Utils.greet(name)
  return "Bine ai venit, " .. name .. "!"
end

return Utils
`;

export default function ScriptEditor({ visible, files, onChange, onClose }: Props) {
  const [active, setActive] = useState(0);
  const [dialog, setDialog] = useState<null | { mode: "new" | "rename"; value: string }>(null);
  const [dialogErr, setDialogErr] = useState("");
  const [runBusy, setRunBusy] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);

  const activeIdx = Math.min(active, Math.max(0, files.length - 1));
  const file: ScriptFile | undefined = files[activeIdx];
  const hasMain = files.some(f => f.name === ENTRY);

  function updateSource(value: string) {
    onChange(files.map((f, i) => (i === activeIdx ? { ...f, source: value } : f)));
  }

  function openNew() {
    setDialogErr("");
    setDialog({ mode: "new", value: hasMain ? "" : ENTRY });
  }

  function openRename() {
    if (!file) return;
    setDialogErr("");
    setDialog({ mode: "rename", value: file.name });
  }

  function submitDialog() {
    if (!dialog) return;
    const name = dialog.value.trim();
    if (!NAME_RE.test(name) || name.length > 64) {
      setDialogErr("Use letters, numbers, _ - and / (max 64 characters)");
      return;
    }
    const taken = files.some((f, i) => f.name === name && !(dialog.mode === "rename" && i === activeIdx));
    if (taken) {
      setDialogErr("A script with this name already exists");
      return;
    }
    if (dialog.mode === "new") {
      if (files.length >= MAX_FILES) {
        setDialogErr(`Maximum ${MAX_FILES} scripts`);
        return;
      }
      onChange([...files, { name, source: "" }]);
      setActive(files.length);
    } else {
      onChange(files.map((f, i) => (i === activeIdx ? { ...f, name } : f)));
    }
    setDialog(null);
    setDialogErr("");
  }

  function askDelete() {
    if (!file || file.name === ENTRY) return;
    Alert.alert("Delete script", `Delete "${file.name}"?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: () => {
          onChange(files.filter((_, i) => i !== activeIdx));
          setActive(0);
        },
      },
    ]);
  }

  function insertExample() {
    onChange([
      { name: ENTRY, source: EXAMPLE_MAIN },
      { name: "utils", source: EXAMPLE_UTILS },
    ]);
    setActive(0);
  }

  async function run() {
    setRunBusy(true);
    setResult(null);
    try {
      const r = await api("/sandbox/run", { method: "POST", body: JSON.stringify({ files }) });
      setResult(r);
    } catch (e: any) {
      setResult({ ok: false, output: [], errors: [e?.message || "Run failed"], ops: [], duration: 0, truncated: false });
    } finally {
      setRunBusy(false);
    }
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.root} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable onPress={onClose} testID="script-close">
            <MaterialCommunityIcons name="chevron-left" size={26} color={colors.onSurface} />
          </Pressable>
          <Text style={styles.title} numberOfLines={1}>Scripts</Text>
          <Pressable onPress={run} disabled={runBusy || files.length === 0} testID="script-run" style={[styles.runBtn, files.length === 0 && { opacity: 0.4 }]}>
            {runBusy ? (
              <ActivityIndicator size="small" color={colors.brand} />
            ) : (
              <MaterialCommunityIcons name="play" size={18} color={colors.brand} />
            )}
            <Text style={styles.runText}>Run</Text>
          </Pressable>
        </View>

        <View style={styles.tabsWrap}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabsRow}>
            {files.map((f, i) => (
              <Pressable
                key={f.name}
                testID={`script-tab-${f.name}`}
                onPress={() => setActive(i)}
                style={[styles.tab, i === activeIdx && styles.tabActive]}
              >
                <MaterialCommunityIcons name="file-code-outline" size={14} color={i === activeIdx ? colors.brand : colors.onSurface3} />
                <Text style={[styles.tabText, i === activeIdx && { color: colors.brand }]}>{f.name}</Text>
              </Pressable>
            ))}
            <Pressable testID="script-new" onPress={openNew} style={styles.tabAdd}>
              <MaterialCommunityIcons name="plus" size={18} color={colors.brand} />
            </Pressable>
          </ScrollView>
        </View>

        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
          {!file ? (
            <View style={styles.empty}>
              <MaterialCommunityIcons name="code-braces" size={48} color={colors.brand} />
              <Text style={styles.emptyTitle}>No scripts yet</Text>
              <Text style={styles.emptyText}>
                Every file is a script. "main" runs first, and it can load the others with require("name").
              </Text>
              <Pressable testID="script-example" onPress={insertExample} style={styles.primaryBtn}>
                <Text style={styles.primaryBtnText}>Start with an example (main + utils)</Text>
              </Pressable>
              <Pressable testID="script-create-main" onPress={() => onChange([{ name: ENTRY, source: "" }])} style={styles.ghostBtn}>
                <Text style={styles.ghostBtnText}>Empty "main" script</Text>
              </Pressable>
            </View>
          ) : (
            <>
              <View style={styles.fileBar}>
                <Text style={styles.fileName} numberOfLines={1}>{file.name}.luau</Text>
                <View style={{ flex: 1 }} />
                {file.name === ENTRY ? (
                  <Text style={styles.entryBadge}>runs first</Text>
                ) : (
                  <>
                    <Pressable testID="script-rename" onPress={openRename} style={styles.smallBtn}>
                      <Text style={styles.smallBtnText}>Rename</Text>
                    </Pressable>
                    <Pressable testID="script-delete" onPress={askDelete} style={[styles.smallBtn, { borderColor: colors.error }]}>
                      <Text style={[styles.smallBtnText, { color: colors.error }]}>Delete</Text>
                    </Pressable>
                  </>
                )}
              </View>

              <TextInput
                testID="script-input"
                value={file.source}
                onChangeText={updateSource}
                multiline
                autoCapitalize="none"
                autoCorrect={false}
                spellCheck={false}
                textAlignVertical="top"
                placeholder="-- write Luau here"
                placeholderTextColor={colors.onSurface3}
                style={styles.codeInput}
              />

              {!hasMain ? (
                <Text style={[styles.hint, { color: colors.warning }]}>
                  There is no "main" script. Tap + and create one; it is the file that runs first.
                </Text>
              ) : (
                <Text style={styles.hint}>
                  Load another file with require("name"). Scripts are saved with the game: close this and tap the check button.
                </Text>
              )}

              {result ? (
                <ScrollView style={styles.consoleBox} contentContainerStyle={{ padding: spacing.md, gap: 4 }}>
                  {result.output.map((o, i) => (
                    <Text key={`o${i}`} style={[styles.consoleLine, o.level === "warn" && { color: colors.warning }]}>{o.msg}</Text>
                  ))}
                  {result.errors.map((e, i) => (
                    <Text key={`e${i}`} style={[styles.consoleLine, { color: colors.error }]}>{e}</Text>
                  ))}
                  <Text style={styles.consoleMeta}>
                    {result.ok ? "OK" : "Failed"} · {result.ops.length} scene changes · {result.duration.toFixed(1)}s
                    {result.truncated ? " · stopped at the limit" : ""}
                  </Text>
                </ScrollView>
              ) : null}
            </>
          )}
        </KeyboardAvoidingView>

        <Modal visible={dialog !== null} transparent animationType="fade" onRequestClose={() => setDialog(null)}>
          <View style={styles.dialogBackdrop}>
            <View style={styles.dialogBox}>
              <Text style={styles.dialogTitle}>{dialog?.mode === "rename" ? "Rename script" : "New script"}</Text>
              <TextInput
                testID="script-name-input"
                autoFocus
                value={dialog?.value ?? ""}
                onChangeText={v => {
                  setDialog(d => (d ? { ...d, value: v } : d));
                  setDialogErr("");
                }}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="utils"
                placeholderTextColor={colors.onSurface3}
                style={styles.dialogInput}
              />
              {dialogErr ? <Text style={styles.dialogErr}>{dialogErr}</Text> : null}
              <View style={styles.dialogRow}>
                <Pressable testID="script-name-cancel" onPress={() => setDialog(null)} style={styles.ghostBtn}>
                  <Text style={styles.ghostBtnText}>Cancel</Text>
                </Pressable>
                <Pressable testID="script-name-ok" onPress={submitDialog} style={styles.primaryBtn}>
                  <Text style={styles.primaryBtnText}>OK</Text>
                </Pressable>
              </View>
            </View>
          </View>
        </Modal>
      </SafeAreaView>
    </Modal>
  );
}

const mono = Platform.OS === "ios" ? "Menlo" : "monospace";

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  header: { flexDirection: "row", alignItems: "center", padding: spacing.md, gap: 10 },
  title: { flex: 1, color: colors.onSurface, fontSize: 16, fontWeight: "800" },
  runBtn: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.brand },
  runText: { color: colors.brand, fontWeight: "800", fontSize: 13 },
  tabsWrap: { borderBottomWidth: 1, borderColor: colors.border },
  tabsRow: { paddingHorizontal: spacing.md, paddingBottom: spacing.sm, gap: 8, alignItems: "center" },
  tab: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.md, backgroundColor: colors.surface2, borderWidth: 1, borderColor: colors.border },
  tabActive: { borderColor: colors.brand, backgroundColor: colors.brandTint },
  tabText: { color: colors.onSurface2, fontWeight: "700", fontSize: 12 },
  tabAdd: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: colors.brand, alignItems: "center", justifyContent: "center" },
  fileBar: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingTop: spacing.md, paddingBottom: spacing.sm },
  fileName: { color: colors.onSurface3, fontFamily: mono, fontSize: 12, flexShrink: 1 },
  entryBadge: { color: colors.brand, fontSize: 11, fontWeight: "800" },
  smallBtn: { paddingHorizontal: 10, paddingVertical: 5, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.borderStrong },
  smallBtnText: { color: colors.onSurface2, fontSize: 11, fontWeight: "700" },
  codeInput: { flex: 1, marginHorizontal: spacing.md, padding: spacing.md, backgroundColor: colors.surface2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, color: colors.onSurface, fontFamily: mono, fontSize: 13 },
  hint: { color: colors.onSurface3, fontSize: 11, marginHorizontal: spacing.lg, marginTop: spacing.sm },
  consoleBox: { maxHeight: 180, margin: spacing.md, backgroundColor: colors.surface, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong },
  consoleLine: { color: colors.onSurface2, fontFamily: mono, fontSize: 12 },
  consoleMeta: { color: colors.onSurface3, fontSize: 11, marginTop: 6 },
  empty: { flex: 1, alignItems: "center", justifyContent: "center", padding: spacing.xl, gap: spacing.md },
  emptyTitle: { color: colors.onSurface, fontSize: 18, fontWeight: "800" },
  emptyText: { color: colors.onSurface3, fontSize: 13, textAlign: "center" },
  primaryBtn: { backgroundColor: colors.brand, paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, alignItems: "center" },
  primaryBtnText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
  ghostBtn: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: radius.pill, borderWidth: 1, borderColor: colors.borderStrong, alignItems: "center" },
  ghostBtnText: { color: colors.onSurface2, fontWeight: "700", fontSize: 13 },
  dialogBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.65)", alignItems: "center", justifyContent: "center", padding: spacing.xl },
  dialogBox: { width: "100%", maxWidth: 380, backgroundColor: colors.surface, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 1, borderColor: colors.borderStrong },
  dialogTitle: { color: colors.onSurface, fontSize: 16, fontWeight: "800", marginBottom: spacing.md },
  dialogInput: { backgroundColor: colors.surface2, color: colors.onSurface, fontSize: 15, borderRadius: radius.md, paddingHorizontal: 14, paddingVertical: 10, borderWidth: 1, borderColor: colors.border },
  dialogErr: { color: colors.error, fontSize: 12, marginTop: 8 },
  dialogRow: { flexDirection: "row", justifyContent: "flex-end", gap: 8, marginTop: spacing.md },
});