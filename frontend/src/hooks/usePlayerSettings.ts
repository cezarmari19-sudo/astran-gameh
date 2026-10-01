// frontend/src/hooks/usePlayerSettings.ts
// Preferintele PLAYERULUI (Graphics Quality, Render Distance, etc) - persistente per
// utilizator, NU per joc (vezi backend/astran_sandbox/user_settings_routes.py). Oricine
// foloseste acest hook (orice ecran Play Mode) primeste aceleasi valori, incarcate automat
// la montare si salvate automat (fara buton de Save) la orice schimbare.
//
// Setarile de GAMEPLAY ale jocului (Max Players, etc) raman in game.scene/game.max_players,
// configurate de creator - nu ating niciodata acelea de aici.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/src/api/client";

export type GraphicsQuality = "low" | "medium" | "high";

export type PlayerSettings = {
  graphics_quality: GraphicsQuality; // "Viziune" 1/2/3
  render_level: number;              // 1..10 - distanta de randare (vezi RENDER_DISTANCES in play/[id].tsx)
  graphics_level: number;            // 1..10 - calitatea iluminarii (vezi GRAPHICS_LEVELS in play/[id].tsx)
  master_volume: number;             // 0..1 - rezervat, neconectat inca la audio
  music_volume: number;              // 0..1 - rezervat
  sfx_volume: number;                // 0..1 - rezervat
  camera_sensitivity: number;        // 0.2..3 - rezervat
};

// Trebuie sa ramana identic cu default_settings() din backend/astran_sandbox/user_settings_routes.py -
// e doar valoarea afisata INAINTE sa raspunda serverul (vezi loaded mai jos), ca UI-ul sa nu
// "sara" vizual cand raspunsul chiar soseste.
export const DEFAULT_PLAYER_SETTINGS: PlayerSettings = {
  graphics_quality: "medium",
  render_level: 4,
  graphics_level: 5,
  master_volume: 1,
  music_volume: 1,
  sfx_volume: 1,
  camera_sensitivity: 1,
};

const SAVE_DEBOUNCE_MS = 500;

export function usePlayerSettings() {
  const [settings, setSettings] = useState<PlayerSettings>(DEFAULT_PLAYER_SETTINGS);
  const [loaded, setLoaded] = useState(false); // true dupa primul raspuns de la server (sau esec - vezi mai jos)

  // Starea "adevarata" pentru salvare traieste intr-un ref, ca save-ul debounced sa vada
  // mereu ultima valoare chiar daca se declanseaza dupa mai multe schimbari rapide.
  const latestRef = useRef<PlayerSettings>(DEFAULT_PLAYER_SETTINGS);
  const pendingPatchRef = useRef<Partial<PlayerSettings>>({});
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    (async () => {
      try {
        const r = await api("/settings/me");
        if (!alive.current) return;
        if (r?.settings) {
          const merged = { ...DEFAULT_PLAYER_SETTINGS, ...r.settings };
          latestRef.current = merged;
          setSettings(merged);
        }
      } catch (e) {
        // esec de retea: ramanem pe DEFAULT_PLAYER_SETTINGS local (cerinta punctul 5) -
        // jocul tot porneste, doar ca nu s-au putut incarca preferintele salvate
        console.log("[usePlayerSettings] load failed", e);
      } finally {
        if (alive.current) setLoaded(true);
      }
    })();
    return () => {
      alive.current = false;
      // Flush sincron la demontare: daca mai era un save "in asteptare" (debounce), il
      // trimitem acum, imediat, ca o schimbare facuta chiar inainte de iesirea din joc
      // sa nu se piarda (cerinta: "daca utilizatorul inchide aplicatia imediat dupa
      // modificare, valoarea trebuie pastrata daca este posibil").
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
        const patch = pendingPatchRef.current;
        pendingPatchRef.current = {};
        if (Object.keys(patch).length > 0) {
          api("/settings/me", { method: "PUT", body: JSON.stringify({ settings: patch }) }).catch(() => {});
        }
      }
    };
  }, []);

  function flushSave() {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = null;
    const patch = pendingPatchRef.current;
    pendingPatchRef.current = {};
    if (Object.keys(patch).length === 0) return;
    api("/settings/me", { method: "PUT", body: JSON.stringify({ settings: patch }) }).catch(e => {
      console.log("[usePlayerSettings] save failed", e);
    });
  }

  function scheduleSave(patch: Partial<PlayerSettings>) {
    pendingPatchRef.current = { ...pendingPatchRef.current, ...patch };
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(flushSave, SAVE_DEBOUNCE_MS);
  }

  // update: aplica local IMEDIAT (UI reactioneaza instant - cerinta punctul 6), apoi
  // programeaza save-ul automat, debounced (cerinta punctul 3: fara buton de Save).
  const update = useCallback(<K extends keyof PlayerSettings>(key: K, value: PlayerSettings[K]) => {
    const next = { ...latestRef.current, [key]: value };
    latestRef.current = next;
    setSettings(next);
    scheduleSave({ [key]: value } as Partial<PlayerSettings>);
  }, []);

  return { settings, loaded, update };
}