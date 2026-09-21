import { Redirect, useLocalSearchParams } from "expo-router";

// Vechiul ecran de script: acum scripturile se editeaza in Studio (butonul "scripts").
export default function ScriptRedirect() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <Redirect href={`/studio/edit/${id}` as any} />;
}