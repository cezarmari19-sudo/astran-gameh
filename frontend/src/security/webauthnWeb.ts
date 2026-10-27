import { Platform } from "react-native";

/**
 * WebAuthn / Passkeys - foloseste mecanismul biometric SECURIZAT al platformei (Touch ID /
 * Face ID / Windows Hello / amprenta Android) prin API-ul standard al browserului
 * (navigator.credentials). Nu colecteaza, nu vede si nu stocheaza niciodata date biometrice
 * brute - doar o cheie publica si o semnatura, exact ca orice site cu "Sign in with a passkey".
 *
 * IMPORTANT (transparenta, nu simulare): acest API exista NATIV doar in browser (web build,
 * Platform.OS === "web"). Pe iOS/Android, Expo/React Native nu expune navigator.credentials
 * fara un modul nativ suplimentar (ex. un plugin dedicat WebAuthn/passkeys) care NU face
 * parte din dependintele acestui proiect - vezi raportul final. De aceea biometricsAvailable()
 * raporteaza cinstit "indisponibil" pe mobil in loc sa afiseze un buton fals de "Amprenta
 * verificata".
 */
export function biometricsAvailable(): boolean {
  return (
    Platform.OS === "web" &&
    typeof navigator !== "undefined" &&
    !!(navigator as any).credentials &&
    typeof (navigator as any).PublicKeyCredential !== "undefined"
  );
}

function base64urlToBuffer(b64url: string): ArrayBuffer {
  const pad = "=".repeat((4 - (b64url.length % 4)) % 4);
  const b64 = (b64url + pad).replace(/-/g, "+").replace(/_/g, "/");
  const str = atob(b64);
  const buf = new Uint8Array(str.length);
  for (let i = 0; i < str.length; i++) buf[i] = str.charCodeAt(i);
  return buf.buffer;
}

function bufferToBase64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let str = "";
  for (let i = 0; i < bytes.byteLength; i++) str += String.fromCharCode(bytes[i]);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** optionsJson = ce intoarce backend-ul la /auth/webauthn/register/options (options_to_json). */
export async function registerPasskey(optionsJson: string): Promise<string> {
  if (!biometricsAvailable()) throw new Error("WebAuthn/passkeys not available on this device/platform");
  const opts = JSON.parse(optionsJson);
  const publicKey: any = {
    ...opts,
    challenge: base64urlToBuffer(opts.challenge),
    user: { ...opts.user, id: base64urlToBuffer(opts.user.id) },
    excludeCredentials: (opts.excludeCredentials || []).map((c: any) => ({ ...c, id: base64urlToBuffer(c.id) })),
  };
  const cred: any = await (navigator as any).credentials.create({ publicKey });
  const out = {
    id: cred.id,
    rawId: bufferToBase64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToBase64url(cred.response.clientDataJSON),
      attestationObject: bufferToBase64url(cred.response.attestationObject),
    },
  };
  return JSON.stringify(out);
}

/** optionsJson = ce intoarce backend-ul la /auth/webauthn/authenticate/options. */
export async function authenticateWithPasskey(optionsJson: string): Promise<string> {
  if (!biometricsAvailable()) throw new Error("WebAuthn/passkeys not available on this device/platform");
  const opts = JSON.parse(optionsJson);
  const publicKey: any = {
    ...opts,
    challenge: base64urlToBuffer(opts.challenge),
    allowCredentials: (opts.allowCredentials || []).map((c: any) => ({ ...c, id: base64urlToBuffer(c.id) })),
  };
  const cred: any = await (navigator as any).credentials.get({ publicKey });
  const out = {
    id: cred.id,
    rawId: bufferToBase64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufferToBase64url(cred.response.clientDataJSON),
      authenticatorData: bufferToBase64url(cred.response.authenticatorData),
      signature: bufferToBase64url(cred.response.signature),
      userHandle: cred.response.userHandle ? bufferToBase64url(cred.response.userHandle) : null,
    },
  };
  return JSON.stringify(out);
}