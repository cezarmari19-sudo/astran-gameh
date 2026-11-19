import AsyncStorage from "@react-native-async-storage/async-storage";
import { consentApi } from "@/src/api/consent";

// Sesiunea Google e de unica folosinta, deci acceptarea pentru un cont NOU se colecteaza INAINTE de a repeta
// autentificarea si se trimite impreuna cu session_id. Serverul o valideaza si ignora pentru conturi existente.
// Se pastreaza doar local si temporar (scoasa dupa incercare); nu e dovada - dovada o scrie serverul.
const KEY = "astran_google_consent_pending";

export type GoogleConsentPayload = {
  accept_terms: true; accept_privacy: true;
  presented_versions: Record<string, string>; consent_language: string;
};

export async function saveGoogleConsent(lang: string): Promise<void> {
  const r = await consentApi.documents(lang);
  const payload: GoogleConsentPayload = {
    accept_terms: true, accept_privacy: true, consent_language: lang,
    presented_versions: Object.fromEntries(r.documents.map(d => [d.doc_type, d.current_version])),
  };
  await AsyncStorage.setItem(KEY, JSON.stringify(payload));
}

export async function loadGoogleConsent(): Promise<GoogleConsentPayload | null> {
  try { const raw = await AsyncStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
}

export async function clearGoogleConsent(): Promise<void> {
  try { await AsyncStorage.removeItem(KEY); } catch {}
}