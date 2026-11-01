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
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
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

// Limite de securitate aplicate DE COMPONENTA (nu depind de pagina-gazda din runtime.ts).
// MAX_MESSAGE_CHARS: un worker compromis nu poate inunda thread-ul JS al aplicatiei.
// WATCHDOG_GRACE_MS: dupa timeoutMs + marja, daca nu a venit niciun raspuns, WebView-ul
// e distrus si recreat (singura oprire sigura pentru un worker blocat).
const MAX_MESSAGE_CHARS = 2_000_000;
const MAX_OUTPUT_CHARS = 1_500_000;
const WATCHDOG_GRACE_MS = 3000;

type Props = {
  // Identitatea jocului. Folosit INTERN in key-ul WebView-ului, deci izolarea intre
  // doua jocuri diferite nu mai depinde doar de key-ul pus de cel care foloseste componenta.
  gameId: string;
};

const ScriptSandboxWebView = forwardRef<ScriptSandboxHandle, Props>(
  function ScriptSandboxWebView({ gameId }, ref) {
    const webRef = useRef<WebView>(null);
    // O singura rulare "in zbor" per instanta - suficient pentru cazul de folosire de
    // azi (rulezi jocul, astepti rezultatul, abia apoi rulezi din nou). Daca mai tarziu
    // e nevoie sa rulezi mai multe scripturi client in paralel (ex: mai multe "obiecte"
    // independente), asta ar trebui extins la o coada de cereri cu id-uri, nu refacut
    // de la zero - structura de mesaje (runtime.ts) deja suporta adaugarea unui id per
    // cerere, doar componenta de aici nu-l foloseste inca.
    const pending = useRef<{
      id: number;
      resolve: (r: SandboxResult) => void;
      timer: ReturnType<typeof setTimeout>;
    } | null>(null);
    const nextId = useRef(1);
    // epoch++ => WebView remontat => worker-ul vechi e distrus (oprire/recuperare sigura).
    const [epoch, setEpoch] = useState(0);
    useEffect(() => () => {
      if (pending.current) {
        clearTimeout(pending.current.timer);
        pending.current.resolve(EMPTY_RESULT(["Sandbox unmounted"]));
        pending.current = null;
      }
    }, []);
    const htmlRef = useRef<string | null>(null);
    if (htmlRef.current === null) {
      htmlRef.current = buildHostHtml();
    }

    const onMessage = useCallback((e: WebViewMessageEvent) => {
      const raw = e.nativeEvent.data;
      if (typeof raw !== "string" || raw.length > MAX_MESSAGE_CHARS) return;
      let msg: any;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      if (!msg || typeof msg !== "object" || msg.type !== "result") return;

      const current = pending.current;
      if (!current) return; // raspuns intarziat la o cerere deja rezolvata - ignorat
      // Daca pagina-gazda returneaza id-ul cererii, trebuie sa corespunda; un raspuns
      // vechi nu are voie sa fie atribuit cererii curente.
      // Pagina-gazda trimite mereu id-ul; un raspuns fara id sau cu alt id e ignorat.
      if (msg.id !== current.id) return;

      pending.current = null;
      clearTimeout(current.timer);
      const output = typeof msg.output === "string" ? msg.output : "";
      if (output.length > MAX_OUTPUT_CHARS) {
        current.resolve(EMPTY_RESULT(["Sandbox output too large"]));
        return;
      }
      current.resolve(parseSandboxOutput(output, msg.fatal === true));
    }, []);

    // Navigarea/incarcarea oricarui URL in afara paginii-gazda e respinsa.
    const onShouldStart = useCallback((req: { url: string }) => {
      const u = req?.url ?? "";
      return u === "about:blank" || u.startsWith("about:srcdoc");
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
              clearTimeout(pending.current.timer);
              pending.current.resolve(EMPTY_RESULT(["Superseded by a newer run"]));
              pending.current = null;
            }

            let message: ReturnType<typeof buildRunMessage>;
            try {
              message = buildRunMessage(opts);
            } catch (err: any) {
              resolve(EMPTY_RESULT([String(err?.message || err)]));
              return;
            }

            const id = nextId.current++;
            // Folosim timeout-ul deja VALIDAT/clamp-uit de buildRunMessage (50..5000 ms), nu
            // opts.timeoutMs brut: NaN/negativ declansa watchdog-ul instant, iar o valoare
            // uriasa il dezactiva de fapt (singura oprire daca thread-ul JS al paginii e blocat).
            const timeoutMs = message.timeoutMs + WATCHDOG_GRACE_MS;
            const timer = setTimeout(() => {
              const cur = pending.current;
              if (!cur || cur.id !== id) return;
              pending.current = null;
              cur.resolve(EMPTY_RESULT(["Sandbox watchdog: script did not finish, sandbox restarted"]));
              setEpoch((n) => n + 1); // distruge si recreeaza WebView-ul => worker blocat oprit
            }, timeoutMs);
            pending.current = { id, resolve, timer };
            webRef.current?.postMessage(JSON.stringify({ ...message, id }));
          });
        },
      }),
      []
    );

    return (
      <WebView
        key={`${gameId}:${epoch}`}
        ref={webRef}
        source={{ html: htmlRef.current }}
        originWhitelist={["about:blank"]}
        onShouldStartLoadWithRequest={onShouldStart}
        onMessage={onMessage}
        javaScriptEnabled
        // --- Hardening: tot ce nu e necesar pentru a rula logica jocului e oprit ---
        incognito
        domStorageEnabled={false}
        cacheEnabled={false}
        thirdPartyCookiesEnabled={false}
        sharedCookiesEnabled={false}
        allowFileAccess={false}
        allowFileAccessFromFileURLs={false}
        allowUniversalAccessFromFileURLs={false}
        allowsLinkPreview={false}
        mixedContentMode="never"
        geolocationEnabled={false}
        setSupportMultipleWindows={false}
        javaScriptCanOpenWindowsAutomatically={false}
        mediaPlaybackRequiresUserAction
        allowsInlineMediaPlayback={false}
        allowsAirPlayForMediaPlayback={false}
        allowsFullscreenVideo={false}
        webviewDebuggingEnabled={false}
        style={{ width: 0, height: 0 }}
        pointerEvents="none"
      />
    );
  }
);

export default ScriptSandboxWebView;