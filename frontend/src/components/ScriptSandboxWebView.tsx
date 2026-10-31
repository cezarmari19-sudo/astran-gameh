// frontend/src/components/ScriptSandboxWebView.tsx
// Componenta RN care gazduieste sandbox-ul de scripturi CLIENT (vezi
// src/sandbox/runtime.ts pentru arhitectura: WebView -> Worker dedicat -> Luau WASM).
// Nu are UI vizibil (style width/height 0) - doar executa logica jocului si intoarce
// rezultatul, la fel cum runner.py face pe server.
//
// FOARTE IMPORTANT, responsabilitatea CELUI CARE FOLOSESTE componenta (play/[id].tsx
// sau echivalentul pentru Studio): monteaz-o cu `key={gameId}` (sau orice se schimba
// intre doua jocuri diferite). Fara asta, React ar putea pastra ACEEASI instanta de
// WebView cand jucatorul trece de la un joc la altul, ceea ce ar insemna ca jocul B
// porneste pe o pagina care a mai rulat deja cod de la jocul A. Izolarea intre doua
// RULARI ale ACELUIASI joc e garantata de componenta insasi (worker nou de fiecare
// data - vezi runtime.ts); izolarea intre doua JOCURI DIFERITE depinde de acest key.
import React, { forwardRef, useCallback, useImperativeHandle, useRef } from "react";
import { WebView, WebViewMessageEvent } from "react-native-webview";
import {
  buildHostHtml,
  buildRunMessage,
  parseSandboxOutput,
  SandboxRunOptions,
  SandboxResult,
} from "@/src/sandbox/runtime";

export type ScriptSandboxHandle = {
  run: (opts: SandboxRunOptions) => Promise<SandboxResult>;
};

const EMPTY_RESULT = (errors: string[]): SandboxResult => ({
  ok: false,
  output: [],
  errors,
  ops: [],
  duration: 0,
  truncated: false,
});

const ScriptSandboxWebView = forwardRef<ScriptSandboxHandle, Record<string, never>>(
  function ScriptSandboxWebView(_props, ref) {
    const webRef = useRef<WebView>(null);
    // O singura rulare "in zbor" per instanta - suficient pentru cazul de folosire de
    // azi (rulezi jocul, astepti rezultatul, abia apoi rulezi din nou). Daca mai tarziu
    // e nevoie sa rulezi mai multe scripturi client in paralel (ex: mai multe "obiecte"
    // independente), asta ar trebui extins la o coada de cereri cu id-uri, nu refacut
    // de la zero - structura de mesaje (runtime.ts) deja suporta adaugarea unui id per
        // cerere, doar componenta de aici nu-l foloseste inca.
    const pending = useRef<{ resolve: (r: SandboxResult) => void } | null>(null);
    const htmlRef = useRef<string | null>(null);
    if (htmlRef.current === null) {
      htmlRef.current = buildHostHtml();
    }

    const onMessage = useCallback((e: WebViewMessageEvent) => {
      let msg: any;
      try {
        msg = JSON.parse(e.nativeEvent.data);
      } catch {
        return;
      }
      if (!msg || msg.type !== "result") return;

      const current = pending.current;
      pending.current = null;
      if (!current) return; // raspuns intarziat la o cerere deja rezolvata (watchdog) - ignorat
      current.resolve(parseSandboxOutput(typeof msg.output === "string" ? msg.output : "", msg.fatal));
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        run(opts: SandboxRunOptions) {
          return new Promise<SandboxResult>((resolve) => {
            if (pending.current) {
              // O cerere noua inlocuieste una veche inca neterminata: pagina oricum
              // porneste un worker nou pentru cererea noua, deci cea veche nu ar mai
              // primi niciodata raspunsul ei - o incheiem explicit, nu o lasam agatata.
              pending.current.resolve(EMPTY_RESULT(["Superseded by a newer run"]));
            }

            let message: ReturnType<typeof buildRunMessage>;
            try {
              message = buildRunMessage(opts);
            } catch (err: any) {
              resolve(EMPTY_RESULT([String(err?.message || err)]));
              return;
            }

            pending.current = { resolve };
            webRef.current?.postMessage(JSON.stringify(message));
          });
        },
      }),
      []
    );

    return (
      <WebView
        ref={webRef}
        source={{ html: htmlRef.current }}
        originWhitelist={["*"]}
        onMessage={onMessage}
        javaScriptEnabled
        // Fara stocare persistenta (cookies/localStorage/etc.) in acest WebView - fiecare
        // montare e "de unica folosinta"; oricum CSP-ul paginii (runtime.ts) blocheaza
        // reteaua, dar asta inchide si eventuala stocare locala ramasa intre montari.
        incognito
        // Fara UI vizibil - componenta doar executa logica, nu afiseaza nimic.
        style={{ width: 0, height: 0 }}
        pointerEvents="none"
      />
    );
  }
);

export default ScriptSandboxWebView;