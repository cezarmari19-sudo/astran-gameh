import React, { useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  StyleSheet,
  Pressable,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { api } from "@/src/api/client";
import { colors, radius, spacing } from "@/src/theme";

type RunResult = {
  ok: boolean;
  output: { level: "print" | "warn"; msg: string }[];
  errors: string[];
  ops: any[];
  duration: number;
  truncated: boolean;
};

const DEFAULT_SCRIPT = `-- Script Luau (ca in Roblox Studio)
local part = Instance.new("Part")
part.Shape = "Ball"
part.Color = Color3.fromRGB(255, 80, 80)
part.Position = Vector3.new(0, 6, 0)
part.Parent = workspace

print("salut din sandbox!")

for i = 1, 20 do
  task.wait(0.1)
  part.Position = part.Position - Vector3.new(0, 0.25, 0)
end
`;

export default function ScriptEditorScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"" | "save" | "run">("");
  const [status, setStatus] = useState("");
  const [result, setResult] = useState<RunResult | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api(`/games/${id}`);
        setSource(res?.game?.script || DEFAULT_SCRIPT);
      } catch (e: any) {
        setStatus(e?.message || "Could not load the game");
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  const save = async () => {
    setBusy("save");
    setStatus("");
    try {
      await api(`/games/${id}`, { method: "PATCH", body: JSON.stringify({ script: source }) });
      setStatus("Saved");
    } catch (e: any) {
      setStatus(e?.message || "Save failed");
    } finally {
      setBusy("");
    }
  };

  const run = async () => {
    setBusy("run");
    setStatus("");
    setResult(null);
    try {
      const res = await api<RunResult>("/sandbox/run", { method: "POST", body: JSON.stringify({ source }) });
      setResult(res);
    } catch (e: any) {
      setStatus(e?.message || "Run failed");
    } finally {
      setBusy("");
    }
  };

  return (
    <SafeAreaView edges={["top", "bottom"]} style={styles.root}>
      <View style={styles.topBar}>
        <Pressable testID="script-back-btn" onPress={() => router.back()} style={styles.iconBtn}>
          <MaterialCommunityIcons name="chevron-left" size={24} color={colors.onSurface} />
        </Pressable>
        <Text style={styles.title}>Script</Text>
        <View style={styles.actions}>
          <Pressable testID="script-run-btn" onPress={run} disabled={busy !== ""} style={[styles.btn, styles.btnGhost]}>
            {busy === "run" ? (
              <ActivityIndicator size="small" color={colors.brand} />
            ) : (
              <MaterialCommunityIcons name="play" size={18} color={colors.brand} />
            )}
            <Text style={styles.btnGhostText}>Run</Text>
          </Pressable>
          <Pressable testID="script-save-btn" onPress={save} disabled={busy !== ""} style={[styles.btn, styles.btnBrand]}>
            {busy === "save" ? (
              <ActivityIndicator size="small" color={colors.onBrand} />
            ) : (
              <MaterialCommunityIcons name="content-save" size={18} color={colors.onBrand} />
            )}
            <Text style={styles.btnBrandText}>Save</Text>
          </Pressable>
        </View>
      </View>

      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.brand} />
          </View>
        ) : (
          <>
            <TextInput
              testID="script-input"
              style={styles.editor}
              value={source}
              onChangeText={setSource}
              multiline
              autoCapitalize="none"
              autoCorrect={false}
              spellCheck={false}
              textAlignVertical="top"
              placeholder="-- write Luau here"
              placeholderTextColor={colors.onSurface3}
            />

            {status ? <Text style={styles.status}>{status}</Text> : null}

            {result ? (
              <ScrollView style={styles.console} contentContainerStyle={{ padding: spacing.md, gap: 4 }}>
                {result.output.map((o, i) => (
                  <Text key={`o${i}`} style={[styles.line, o.level === "warn" && { color: colors.warning }]}>
                    {o.msg}
                  </Text>
                ))}
                {result.errors.map((e, i) => (
                  <Text key={`e${i}`} style={[styles.line, { color: colors.error }]}>
                    {e}
                  </Text>
                ))}
                <Text style={styles.meta}>
                  {result.ok ? "OK" : "Failed"} · {result.ops.length} scene changes · {result.duration.toFixed(1)}s
                  {result.truncated ? " · stopped at the limit" : ""}
                </Text>
              </ScrollView>
            ) : null}
          </>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const mono = Platform.OS === "ios" ? "Menlo" : "monospace";

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  topBar: { flexDirection: "row", alignItems: "center", padding: spacing.md, gap: spacing.sm },
  iconBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: colors.surface2, alignItems: "center", justifyContent: "center" },
  title: { flex: 1, color: colors.onSurface, fontWeight: "800", fontSize: 18 },
  actions: { flexDirection: "row", gap: spacing.sm },
  btn: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 9, borderRadius: radius.pill },
  btnGhost: { borderWidth: 1, borderColor: colors.brand },
  btnGhostText: { color: colors.brand, fontWeight: "800", fontSize: 13 },
  btnBrand: { backgroundColor: colors.brand },
  btnBrandText: { color: colors.onBrand, fontWeight: "900", fontSize: 13 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  editor: {
    flex: 1,
    marginHorizontal: spacing.md,
    padding: spacing.md,
    backgroundColor: colors.surface2,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.onSurface,
    fontFamily: mono,
    fontSize: 13,
  },
  status: { color: colors.onSurface3, fontSize: 12, marginHorizontal: spacing.lg, marginTop: spacing.sm },
  console: {
    maxHeight: 180,
    margin: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  line: { color: colors.onSurface2, fontFamily: mono, fontSize: 12 },
  meta: { color: colors.onSurface3, fontSize: 11, marginTop: 6 },
});