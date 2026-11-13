import AsyncStorage from "@react-native-async-storage/async-storage";
import type { ProjectFile } from "./projectTree";

/**
 * Ciorna locala a proiectului (cod / fisiere) din Game Studio.
 * Studio nu are salvare automata pe server (ar putea publica cod neterminat intr-un joc public), asa ca
 * schimbarile nesalvate se copiaza periodic local: un crash / o inchidere a aplicatiei / pierderea
 * conexiunii nu mai inseamna munca pierduta. La urmatoarea deschidere userul poate restaura ciorna.
 */
export type ProjectDraft = { files: ProjectFile[]; folders: string[]; ts: number };

const key = (gameId: string) => `astran_project_draft_${gameId}`;

export async function saveDraft(gameId: string, files: ProjectFile[], folders: string[]): Promise<void> {
  try {
    await AsyncStorage.setItem(key(gameId), JSON.stringify({ files, folders, ts: Date.now() } as ProjectDraft));
  } catch {}
}

export async function loadDraft(gameId: string): Promise<ProjectDraft | null> {
  try {
    const raw = await AsyncStorage.getItem(key(gameId));
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (!d || !Array.isArray(d.files) || !Array.isArray(d.folders)) return null;
    return d as ProjectDraft;
  } catch {
    return null;
  }
}

export async function clearDraft(gameId: string): Promise<void> {
  try {
    await AsyncStorage.removeItem(key(gameId));
  } catch {}
}

/** Aceeasi continut (cale, sursa, target) si aceleasi foldere? Ordinea fisierelor nu conteaza. */
export function sameProject(a: { files: ProjectFile[]; folders: string[] }, b: { files: ProjectFile[]; folders: string[] }): boolean {
  const norm = (p: { files: ProjectFile[]; folders: string[] }) =>
    JSON.stringify({
      f: [...p.files].map(f => [f.path, f.source, f.target === "client" ? "client" : "server"]).sort((x, y) => (x[0] < y[0] ? -1 : 1)),
      d: [...p.folders].sort(),
    });
  return norm(a) === norm(b);
}