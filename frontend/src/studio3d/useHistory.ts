// frontend/src/studio3d/useHistory.ts
// Undo / Redo: tine minte starile anterioare ale modelului.
// begin()/end() grupeaza mai multe modificari (ex: un gest de tras in viewport) intr-un singur pas de istoric.
import { useCallback, useState } from "react";

type State<T> = { past: T[]; present: T; future: T[]; batchBase: T | null };

export function useHistory<T>(initial: T, limit = 100) {
  const [state, setState] = useState<State<T>>({ past: [], present: initial, future: [], batchBase: null });

  const set = useCallback((next: T | ((cur: T) => T)) => {
    setState(s => {
      const value = typeof next === "function" ? (next as (cur: T) => T)(s.present) : next;
      if (value === s.present) return s;
      // in timpul unui gest nu adaugam un pas de istoric la fiecare miscare
      if (s.batchBase !== null) return { ...s, present: value };
      return { past: [...s.past.slice(-(limit - 1)), s.present], present: value, future: [], batchBase: null };
    });
  }, [limit]);

  const begin = useCallback(() => {
    setState(s => (s.batchBase !== null ? s : { ...s, batchBase: s.present }));
  }, []);

  const end = useCallback(() => {
    setState(s => {
      if (s.batchBase === null) return s;
      if (s.batchBase === s.present) return { ...s, batchBase: null };
      return { past: [...s.past.slice(-(limit - 1)), s.batchBase], present: s.present, future: [], batchBase: null };
    });
  }, [limit]);

  const undo = useCallback(() => {
    setState(s => (s.batchBase !== null || s.past.length === 0 ? s : {
      past: s.past.slice(0, -1),
      present: s.past[s.past.length - 1],
      future: [s.present, ...s.future],
      batchBase: null,
    }));
  }, []);

  const redo = useCallback(() => {
    setState(s => (s.batchBase !== null || s.future.length === 0 ? s : {
      past: [...s.past, s.present],
      present: s.future[0],
      future: s.future.slice(1),
      batchBase: null,
    }));
  }, []);

  // schimba starea curenta fara sa o pui in istoric (ex: dupa incarcarea de pe server)
  const reset = useCallback((value: T) => setState({ past: [], present: value, future: [], batchBase: null }), []);

  return { present: state.present, set, begin, end, undo, redo, reset, canUndo: state.past.length > 0, canRedo: state.future.length > 0 };
}