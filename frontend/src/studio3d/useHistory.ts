// frontend/src/studio3d/useHistory.ts
// Undo / Redo: tine minte starile anterioare ale modelului.
import { useCallback, useState } from "react";

type State<T> = { past: T[]; present: T; future: T[] };

export function useHistory<T>(initial: T, limit = 100) {
  const [state, setState] = useState<State<T>>({ past: [], present: initial, future: [] });

  const set = useCallback((next: T | ((cur: T) => T)) => {
    setState(s => {
      const value = typeof next === "function" ? (next as (cur: T) => T)(s.present) : next;
      if (value === s.present) return s;
      return { past: [...s.past.slice(-(limit - 1)), s.present], present: value, future: [] };
    });
  }, [limit]);

  const undo = useCallback(() => {
    setState(s => (s.past.length === 0 ? s : {
      past: s.past.slice(0, -1),
      present: s.past[s.past.length - 1],
      future: [s.present, ...s.future],
    }));
  }, []);

  const redo = useCallback(() => {
    setState(s => (s.future.length === 0 ? s : {
      past: [...s.past, s.present],
      present: s.future[0],
      future: s.future.slice(1),
    }));
  }, []);

  // schimba starea curenta fara sa o pui in istoric (ex: dupa incarcarea de pe server)
  const reset = useCallback((value: T) => setState({ past: [], present: value, future: [] }), []);

  return { present: state.present, set, undo, redo, reset, canUndo: state.past.length > 0, canRedo: state.future.length > 0 };
}