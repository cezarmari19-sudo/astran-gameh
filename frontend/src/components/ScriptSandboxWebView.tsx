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
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { WebView, WebViewMessageEvent } from "react-native-webview";
import {
  buildHostHtml,
  buildRunMessage,
  SandboxRunOptions,
  SandboxResult,
} from "@/src/sandbox/runtime";
import { HostChannel, emptyResult } from "@/src/sandbox/hostChannel";

export type ScriptSandboxHandle = {
  run: (opts: SandboxRunOptions) => Promise<SandboxResult>;
  // Opreste orice rulare in curs si recreeaza WebView-ul (Worker-ul vechi e distrus garantat).
  // Rularea in curs se incheie cu o eroare "Stopped". O noua rulare porneste de la zero.
  stop: () => void;
};

type Props = {
  // Identitatea jocului. Folosit INTERN in key-ul WebView-ului, deci izolarea intre
  // doua jocuri diferite nu mai depinde doar de key-ul pus de cel care foloseste componenta.
  gameId: string;
};

const ScriptSandboxWebView = forwardRef<ScriptSandboxHandle, Props>(
  function ScriptSandboxWebView({ gameId }, ref) {
    const webRef = useRef<WebView>(null);
    // epoch++ => WebView remontat => worker-ul vechi e distrus (oprire/recuperare sigura).
    const [epoch, setEpoch] = useState(0);
    // Logica de protocol (handshake "ready", coada, watchdog, recuperare) e in HostChannel,
    // testata cu pagina-gazda reala (src/sandbox/runtime.test.mts).
    const channel = useMemo(
      () =>
        new HostChannel({
          post: (json) => webRef.current?.postMessage(json),
          restart: () => setEpoch((n) => n + 1),
        }),
      []
    );
    useEffect(() => {
      channel.attach();
      return () => channel.dispose();
    }, [channel]);

    const htmlRef = useRef<string | null>(null);
    if (htmlRef.current === null) {
      htmlRef.current = buildHostHtml();
    }

    const onMessage = useCallback((e: WebViewMessageEvent) => channel.onHostMessage(e.nativeEvent.data), [channel]);
    const onLost = useCallback(() => channel.onHostLost("Sandbox process was terminated by the system"), [channel]);

    // Navigarea/incarcarea oricarui URL in afara paginii-gazda e respinsa.
    const onShouldStart = useCallback((req: { url: string }) => {
      const u = req?.url ?? "";
      return u === "about:blank" || u.startsWith("about:srcdoc");
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        run(opts: SandboxRunOptions) {
          let message: ReturnType<typeof buildRunMessage>;
          try {
            message = buildRunMessage(opts);
          } catch (err: any) {
            return Promise.resolve(emptyResult([String(err?.message || err)]));
          }
          return channel.run(message);
        },
        stop() {
          channel.stop();
        },
      }),
      [channel]
    );

    return (
      <WebView
        key={`${gameId}:${epoch}`}
        ref={webRef}
        source={{ html: htmlRef.current }}
        originWhitelist={["about:blank"]}
        onShouldStartLoadWithRequest={onShouldStart}
        onMessage={onMessage}
        onRenderProcessGone={onLost}
        onContentProcessDidTerminate={onLost}
        onError={onLost}
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